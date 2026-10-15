"""Очистка лидов продаж до первого письма: дубли, стоп-листы, годность и живость адреса.

Норма отказов доставки — до 2%, в унаследованной базе было вдвое больше,
а каждый отказ бьёт по репутации почтового домена — единственному ресурсу,
который не восстанавливается. Поэтому до очереди писем лид проходит
проверки, и порядок их — от бесплатных к платной, как у лестницы контактов
(`okf/contact-ladder.md`):

1. дубль — адрес уже у лида продаж, в этом же файле или раньше;
2. ручной стоп-лист продаж — клиенты и партнёры;
3. отписка где угодно — общие `suppressions`: строки без этапа, этапа продаж и отписки
   с жалобами любого этапа (приём ответов пишет отписку с этапом кампании,
   `replies/pipeline.py`; человек отписывается от нас, а не от рассылки) — одно правило
   с отправкой и экраном стоп-листа (`suppressions.holds_sales`);
4. сайт в работе у другого направления — живое письмо доноров или рекламодателей: в очереди,
   в пути, дошедшее, остановленное человеком, — тем же правилом, что у отправки (одна роль на
   сайт, `elsewhere.roles`: письмо такому лиду отправка сняла бы). Письмо продаж — не другое
   направление: у компании бывает несколько лидов одной гипотезы (чужую — правило 7);
5. годность адреса — общий `contacts/quality.rejection_reason` целиком
   и ролевой ящик на бесплатной почте;
6. почта домена самого адреса — `contacts/mx.mail_route`: один запрос DNS
   на уникальный домен, параллельно, повтор на таймауте внутри;
7. компания другой гипотезы: держит «готовый» — отказ, непроверенный — ждать (`company_hold`);
8. платный проверяльщик — только для тех, кто дошёл; домен, с которым он не работает, —
   отказ сразу (`hunter_codes.refused`).

**Проход идёт кругами** (`_pass`): решённые кругом лиды будят ждущих на своих доменах, а на
доменах отклонённых освободившаяся компания возвращает раннего отклонённого из-за держателя
(`company_hold.after`) — следующий круг чистит их тем же порядком, пока круг решает лидов.
Свободные компании `clean` ищет и в начале прохода, по всей базе (`company_hold.freed`). Платит
проход только за свою область — гипотезу очистки (`company_hold.mine`): лидам чужих гипотез —
бесплатные исходы, их проверит очистка их гипотезы.

**Каждый отказ назван дважды**: кодом (`RejectionReason`, по нему
фильтрует экран) и словами (`cleaning_note`, их читает человек).
Правило без имени нельзя настроить. Отклонённому лиду (перепроверка «готов» —
`verifier.due`) письмо могло быть уже собрано: оно снимается с очереди в момент
отклонения, с теми же словами (`off_queue.of_leads`), а не ждёт отказа отправки.

**«Провайдер не знает» и «мы не поняли» — разные исходы** (конституция,
«Failure visibility»). Вердикт `unknown` записывается, но к письмам лид не идёт: первый —
статус прежний и день перепроверки словами, второй — отказ (`unconfirmed.py`, решение
владельца 13.10). Отказ сервиса оставляет лида в прежнем статусе с прежним вердиктом и
причиной «проверка не выполнена»: следующая очистка повторит его. Квота, закрытая
учётка и 429 после повторов (`verifier.STOPS`) останавливают платную часть прохода.

**Проход коммитит каждую партию сам.** Платные вердикты — деньги; обрыв
посреди прохода не должен терять ни их, ни строку расхода (политика
сбоев: оплаченная пачка фиксируется сразу).

Доля «DNS не ответил» — отдельное число в сводке: ступень, которая ничего
не отсеивает, в поломке выглядит как работающая (замер соседей 18.09).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.known_addresses import FREE_MAILBOX_DOMAINS, ROLE_LOCAL_PARTS
from backend.features.contacts.mx import DELIVERABLE, MailRoute, mail_route
from backend.features.contacts.provider import ProviderDomainRefusedError, ProviderError
from backend.features.contacts.quality import rejection_reason
from backend.features.core import usage
from backend.features.letters import attempts
from backend.features.sales import clean_write, company_hold, off_queue, unconfirmed
from backend.features.sales.clean_known import Known, load_known
from backend.features.sales.clean_report import CleaningReport, Outcome, tally
from backend.features.sales.hunter_codes import refused
from backend.features.sales.intake import host_key
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesLeadModel,
)
from backend.features.sales.verifier import (
    FIXTURE,
    OPERATION,
    STOPS,
    UNKNOWN,
    EmailVerifier,
    due,
)
from backend.features.sales.verify_cap import capped

logger = logging.getLogger(__name__)

#: Лидов в партии: партия — одна транзакция и одна строка расхода.
BATCH = 200
#: Запросов DNS одновременно — как у прогона по файлу (`contacts/file_sweep.py`).
MX_CONCURRENCY = 8
#: Обращений к платному сервису одновременно; частоту держит проверяльщик (`verifier.py`).
VERIFY_CONCURRENCY = 4
NOT_VERIFIED = "проверка не выполнена"
#: Кругов в проходе — не больше. Круги сходятся сами: каждый решает лидов, а решённый лид кругом не
#: возвращается (`company_hold.after`); предел — страховка от зацикливания, если правило однажды
#: ошибётся. Столько ждущих у одной компании не бывает, а лиды последнего круга остаются как есть —
#: их решит следующий проход.
ROUNDS = 50
#: Круги не сошлись за предел — словами журнала; домены последнего круга — полем записи.
UNSETTLED = (
    "продажи: круги очистки не сошлись за предел — проход остановлен, "
    "лиды последнего круга решит следующий проход"
)


@dataclass
class _Run:
    """Проход очистки: проверяльщик, момент, сводка и своя область — за проверку лидов вне её
    проход не платит (`company_hold.mine`)."""

    verifier: EmailVerifier
    moment: datetime
    report: CleaningReport
    scope: company_hold.Scope = None
    mx_concurrency: int = MX_CONCURRENCY
    #: Первая неисправимая причина — остальным лидам партии и прохода она же.
    halt: str | None = None


def _mailbox(email: str) -> str:
    """Домен самого адреса: `ivan@mail.acme.example.test` → `mail.acme.example.test`."""
    return email.rpartition("@")[2]


# --- правила отказа: бесплатные, без сети ------------------------------------


def _duplicate(lead: SalesLeadModel, known: Known) -> str | None:
    first = known.first_lead.get(lead.email, lead.id)
    return f"дубль: адрес уже у лида №{first}" if first < lead.id else None


def _stoplisted(lead: SalesLeadModel, known: Known) -> str | None:
    if lead.email in known.stop_emails:
        return f"стоп-лист продаж: адрес {lead.email}"
    for host in sorted(_hosts_of(lead, known)):
        if host in known.stop_hosts:
            return f"стоп-лист продаж: домен {host}"
    return None


def _unsubscribed(lead: SalesLeadModel, known: Known) -> str | None:
    if lead.email in known.unsub_emails:
        return f"отписка: {lead.email} просил не писать"
    if lead.domain_id in known.unsub_domains:
        return f"отписка: домен {known.hosts[lead.domain_id]} в общем стоп-листе"
    return None


def _busy(lead: SalesLeadModel, known: Known) -> str | None:
    stage = known.busy_domains.get(lead.domain_id)
    if stage is None:
        return None
    return f"{attempts.elsewhere_reason(stage)} ({known.hosts[lead.domain_id]})"


def _unusable(lead: SalesLeadModel, _known: Known) -> str | None:
    """Общие правила годности целиком — и ролевой ящик на бесплатной почте:
    `info@gmail.com` — чужой личный ящик, а не роль компании."""
    if reason := rejection_reason(lead.email):
        return reason
    local, _, domain = lead.email.partition("@")
    if domain in FREE_MAILBOX_DOMAINS and local in ROLE_LOCAL_PARTS:
        return f"ролевой ящик на бесплатной почте — чужой личный, а не роль компании: {lead.email}"
    return None


def _hosts_of(lead: SalesLeadModel, known: Known) -> set[str]:
    """Домены, по которым лида закрывает стоп-лист: компания, домен адреса и его
    корень — тем же ключом, каким стоп-лист записан (`host_key`)."""
    mailbox = _mailbox(lead.email)
    return {known.hosts.get(lead.domain_id, ""), mailbox, host_key(mailbox)} - {""}


#: Порядок — от самого дешёвого и самого говорящего: дубль называет оригинал,
#: у которого своя причина; правило, добавленное сюда, само попадает в сводку.
_RULES: tuple[tuple[Callable[[SalesLeadModel, Known], str | None], RejectionReason], ...] = (
    (_duplicate, RejectionReason.DUPLICATE),
    (_stoplisted, RejectionReason.STOPLIST),
    (_unsubscribed, RejectionReason.UNSUBSCRIBED),
    (_busy, RejectionReason.OTHER_DIRECTION),
    (_unusable, RejectionReason.UNUSABLE),
)


def _by_rules(lead: SalesLeadModel, known: Known) -> Outcome | None:
    for rule, reason in _RULES:
        if note := rule(lead, known):
            return Outcome(lead.id, LeadStatus.REJECTED, reason, note)
    return None


def _no_mail(lead: SalesLeadModel, route: MailRoute) -> Outcome:
    how = "нулевой MX" if route is MailRoute.NULL_MX else "нет ни MX, ни A"
    note = f"домен не принимает почту ({how}): {_mailbox(lead.email)}"
    return Outcome(lead.id, LeadStatus.REJECTED, RejectionReason.NO_MAIL, note)


def _held(found: company_hold.Split) -> list[Outcome]:
    """Компанию держит лид другой гипотезы: «готовый» — отказ кодом дубля, непроверенный — лид ждёт
    держателя в прежнем статусе с прежним вердиктом (`company_hold.split`). Лид чужой гипотезы со
    свободной компанией — тоже в прежнем статусе: его проверит очистка его гипотезы."""
    held = [Outcome(i, LeadStatus.REJECTED, RejectionReason.DUPLICATE, w) for i, w in found.held]
    waits = [Outcome(i, status, note=w, kept=True, waits=True) for i, status, w in found.waiting]
    return held + waits + [Outcome(i, status, note=w, kept=True) for i, status, w in found.handed]


def _unverified(lead: SalesLeadModel, why: str) -> Outcome:
    """Проверка не выполнена — статус прежний, лид ждёт следующей очистки с причиной, и прежний
    вердикт цел: `ready` на перепроверке `new` снял бы с цепочки, а первый `unknown` без вердикта
    при следующем ответе `unknown` сошёл бы снова за первый (`unconfirmed.judged`)."""
    kept = lead.verification_status is not None
    return Outcome(lead.id, lead.status, note=f"{NOT_VERIFIED}: {why}", kept=kept)


# --- сеть: DNS и платный проверяльщик ----------------------------------------


async def mail_routes(
    domains: Iterable[str], *, concurrency: int = MX_CONCURRENCY
) -> dict[str, MailRoute]:
    """Как домены принимают почту: каждый уникальный домен — один раз, параллельно.

    Повтор на таймауте — внутри `mail_route`: системный резолвер, следом
    запасные; «не ответил» приходит как `UNKNOWN`, а не как отказ.
    """
    limiter = asyncio.Semaphore(max(1, concurrency))

    async def one(domain: str) -> tuple[str, MailRoute]:
        async with limiter:
            return domain, await mail_route(domain)

    return dict(await asyncio.gather(*(one(domain) for domain in sorted(set(domains)))))


async def _verify_one(lead: SalesLeadModel, run: _Run) -> Outcome:
    verifier = run.verifier
    try:
        verdict = await verifier.verify(lead.email)
    except STOPS as exc:
        run.halt = str(exc)
        logger.exception("продажи: проверка адресов остановлена", extra={"reason": str(exc)})
        return _unverified(lead, str(exc))
    except ProviderDomainRefusedError as exc:  # не проверить никогда: отказ сразу, без повторов
        logger.info("продажи: адрес не проверить", extra={"lead_id": lead.id, "reason": str(exc)})
        return Outcome(
            lead.id, LeadStatus.REJECTED, RejectionReason.UNCONFIRMED, refused(lead.email)
        )
    except ProviderError as exc:
        logger.warning(
            "продажи: адрес не проверен, повторим", extra={"lead_id": lead.id, "reason": str(exc)}
        )
        return _unverified(lead, str(exc))
    if verdict.deliverable is False:
        note = f"{verdict.words}: {lead.email}"
        return Outcome(
            lead.id,
            LeadStatus.REJECTED,
            RejectionReason.UNDELIVERABLE,
            note,
            verdict,
            verifier.name,
        )
    if verdict.status == UNKNOWN:  # адрес не подтверждён — к письмам не идёт
        status, reason, note = unconfirmed.judged(lead, verifier.name, run.moment)
        return Outcome(lead.id, status, reason, note, verdict, verifier.name)
    return Outcome(lead.id, LeadStatus.READY, verdict=verdict, verifier=verifier.name)


async def _verify_all(leads: Sequence[SalesLeadModel], run: _Run) -> list[Outcome]:
    limiter = asyncio.Semaphore(VERIFY_CONCURRENCY)

    async def one(lead: SalesLeadModel) -> Outcome:
        async with limiter:
            if run.halt is not None:
                return _unverified(lead, run.halt)
            return await _verify_one(lead, run)

    return list(await asyncio.gather(*(one(lead) for lead in leads)))


# --- проход ---------------------------------------------------------------------


async def _due_leads(
    session: AsyncSession, hypothesis_id: int | None, real: bool, moment: datetime
) -> list[SalesLeadModel]:
    query = select(SalesLeadModel).where(due(real, moment))  # и перепроверка в свой день
    if hypothesis_id is not None:
        query = query.where(SalesLeadModel.hypothesis_id == hypothesis_id)
    return list((await session.scalars(query.order_by(SalesLeadModel.id))).all())


async def _clean_batch(
    session: AsyncSession, leads: Sequence[SalesLeadModel], run: _Run
) -> list[Outcome]:
    known = await load_known(session, leads, run.moment)
    outcomes = [outcome for lead in leads if (outcome := _by_rules(lead, known))]
    decided = {outcome.lead_id for outcome in outcomes}
    alive = [lead for lead in leads if lead.id not in decided]
    hosts = {_mailbox(lead.email) for lead in alive}
    routes = await mail_routes(hosts, concurrency=run.mx_concurrency)
    run.report.mx_unknown += sum(1 for route in routes.values() if route is MailRoute.UNKNOWN)
    reachable: list[SalesLeadModel] = []
    for lead in alive:
        route = routes[_mailbox(lead.email)]
        if route in DELIVERABLE:
            reachable.append(lead)
        else:
            outcomes.append(_no_mail(lead, route))
    found = await company_hold.split(session, leads, reachable, run.scope)  # до платной проверки
    return outcomes + _held(found) + await _verify_all(found.rest, run)


def _row(outcome: Outcome, moment: datetime) -> dict[str, Any]:
    verdict = outcome.verdict
    row = {
        clean_write.LEAD_ID: outcome.lead_id,
        "status": outcome.status,
        "rejection_reason": None if outcome.reason is None else outcome.reason.value,
        "cleaning_note": outcome.note,
    }
    if outcome.kept:  # прежний вердикт лида остаётся
        return row
    return row | {
        "verification_status": None if verdict is None else f"{outcome.verifier}:{verdict.status}",
        "verification_score": None if verdict is None else verdict.score,
        "verified_at": None if verdict is None else moment,
    }


async def _write(session: AsyncSession, outcomes: Sequence[Outcome], moment: datetime) -> None:
    """Исходы партии (`clean_write`: время правки лида не двигается, лид, удалённый посреди
    прохода, пропускается), затем письма отклонённых лидов — с очереди (`off_queue.of_leads`),
    и одна строка расхода — за все её платные проверки: Hunter списал и их."""
    if not outcomes:
        return
    await clean_write.write(session, [_row(outcome, moment) for outcome in outcomes])
    await off_queue.of_leads(session, {o.lead_id: o.note for o in outcomes if o.reason})
    units = sum(outcome.verdict.units for outcome in outcomes if outcome.verdict is not None)
    if units:
        usage.record(session, operation=OPERATION, units=units)


async def _round(
    session: AsyncSession, todo: Sequence[SalesLeadModel], run: _Run, *, commit: bool
) -> list[Outcome]:
    """Круг прохода — партиями: исходы, запись и, если `commit`, коммит каждой партии."""
    outcomes: list[Outcome] = []
    for start in range(0, len(todo), BATCH):
        found = await _clean_batch(session, todo[start : start + BATCH], run)
        await _write(session, found, run.moment)
        if commit:
            await session.commit()
        outcomes += found
    return outcomes


async def _pass(
    session: AsyncSession, leads: Sequence[SalesLeadModel], run: _Run, *, commit: bool
) -> CleaningReport:
    """Лиды — партиями по порядку заведения, затем кругами: кого круг разбудил или вернул
    (`company_hold.after`), — не больше `ROUNDS` кругов. Остановка платной части — на весь
    проход; сводка — по последнему исходу лида своей области: лиды чужих гипотез проход только
    отдаёт их очистке."""
    final: dict[int, Outcome] = {}
    todo = sorted(leads, key=lambda lead: lead.id)
    for _ in range(ROUNDS):
        outcomes = await _round(session, todo, run, commit=commit)
        todo = await _after(session, todo, outcomes, run, final)
        if not todo:
            break
    else:
        logger.error(UNSETTLED, extra={"domains": sorted({lead.domain_id for lead in todo})})
    run.report.checked = len(final)
    tally(run.report, final.values(), run.moment)
    run.report.stopped = run.halt
    return run.report


async def _after(
    session: AsyncSession,
    todo: Sequence[SalesLeadModel],
    outcomes: Sequence[Outcome],
    run: _Run,
    final: dict[int, Outcome],
) -> list[SalesLeadModel]:
    """Исходы круга своей области — в `final`; кого круг разбудил или вернул — следующему кругу."""
    domains = {lead.id: lead.domain_id for lead in todo}
    own = {lead.id for lead in company_hold.only(todo, run.scope)}
    final.update((outcome.lead_id, outcome) for outcome in outcomes if outcome.lead_id in own)
    decided = {domains[outcome.lead_id] for outcome in outcomes if outcome.decided}
    rejected = {domains[o.lead_id] for o in outcomes if o.status is LeadStatus.REJECTED}
    return await company_hold.after(session, decided, rejected, run.scope)


async def clean_leads(
    session: AsyncSession,
    leads: Sequence[SalesLeadModel],
    verifier: EmailVerifier,
    *,
    now: datetime | None = None,
) -> CleaningReport:
    """Очистить названных лидов тем же проходом, что `clean`, — с кругами; своя область — их
    гипотезы. Коммит — за вызывающим: лид из ответа «пишите другому» чистится в транзакции задачи
    ответа."""
    moment = now or datetime.now(UTC)
    verifier = await capped(session, verifier, now=moment)  # месячный потолок у живого
    report = CleaningReport(verifier=verifier.name)
    run = _Run(verifier, moment, report, {lead.hypothesis_id for lead in leads})
    return await _pass(session, leads, run, commit=False)


async def clean(
    session: AsyncSession,
    verifier: EmailVerifier,
    *,
    hypothesis_id: int | None = None,
    now: datetime | None = None,
    mx_concurrency: int = MX_CONCURRENCY,
) -> CleaningReport:
    """Очистить лидов `new` — всех или одной гипотезы. Коммит — партиями, здесь.

    Лид `ready` второй раз не проверяется: платить дважды незачем, — кроме проверенного
    выдуманным, когда проверяльщик живой, и `unknown`, которому не писали, в его день
    (`verifier.due`). Лид, оставшийся `new` из-за отказа сервиса, следующим проходом пройдёт
    заново; с первым `unknown` — в день перепроверки (`unconfirmed.py`). Свободные компании
    (держателя отклонили и не очисткой) проход ищет по всей базе: вернувшихся своей области
    проверяет тем же проходом, чужих отдаёт очистке их гипотезы.
    """
    moment = now or datetime.now(UTC)
    verifier = await capped(session, verifier, now=moment)  # месячный потолок у живого
    scope = None if hypothesis_id is None else {hypothesis_id}
    leads = await _due_leads(session, hypothesis_id, verifier.name != FIXTURE, moment)
    leads += company_hold.only(await company_hold.freed(session), scope)  # были отклонены
    run = _Run(verifier, moment, CleaningReport(verifier=verifier.name), scope, mx_concurrency)
    report = await _pass(session, leads, run, commit=True)
    logger.info(
        "продажи: очистка прошла",
        extra={
            "checked": report.checked,
            "ready": report.ready,
            "rejected": report.rejected_total,
            "unconfirmed": report.unconfirmed,
            "unverified": report.unverified,
            "awaiting_holder": report.awaiting_holder,
            "verifier": verifier.name,
        },
    )
    return report
