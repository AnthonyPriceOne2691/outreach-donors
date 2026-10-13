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
   направление: у компании бывает несколько лидов;
5. годность адреса — общий `contacts/quality.rejection_reason` целиком
   и ролевой ящик на бесплатной почте;
6. почта домена самого адреса — `contacts/mx.mail_route`: один запрос DNS
   на уникальный домен, параллельно, повтор на таймауте внутри;
7. платный проверяльщик — только для тех, кто дошёл.

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
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.known_addresses import FREE_MAILBOX_DOMAINS, ROLE_LOCAL_PARTS
from backend.features.contacts.mx import DELIVERABLE, MailRoute, mail_route
from backend.features.contacts.provider import ProviderError
from backend.features.contacts.quality import rejection_reason
from backend.features.core import usage
from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.letters import attempts
from backend.features.sales import clean_write, elsewhere, off_queue, unconfirmed
from backend.features.sales.clean_report import CleaningReport
from backend.features.sales.intake import host_key
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesLeadModel,
    SalesStoplistModel,
)
from backend.features.sales.suppressions import holds_sales
from backend.features.sales.verifier import (
    FIXTURE,
    OPERATION,
    STOPS,
    UNKNOWN,
    EmailVerifier,
    Verdict,
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
#: Значений в одном `IN`: параметров у Postgres не больше 32 767 на запрос.
CHUNK = 1000
NOT_VERIFIED = "проверка не выполнена"


@dataclass(frozen=True, slots=True)
class Outcome:
    """Итог очистки одного лида: куда он ушёл и почему."""

    lead_id: int
    status: LeadStatus
    reason: RejectionReason | None = None
    note: str | None = None
    verdict: Verdict | None = None
    verifier: str = ""
    #: Проверка не выполнена у лида с вердиктом: прежний вердикт остаётся (`_unverified`).
    kept: bool = False


@dataclass(frozen=True, slots=True)
class _Known:
    """Что база знает о партии лидов — одним чтением на партию, а не на лида."""

    #: Адрес → наименьший id лида с ним: он и есть оригинал.
    first_lead: dict[str, int]
    hosts: dict[int, str]
    stop_hosts: frozenset[str]
    stop_emails: frozenset[str]
    unsub_emails: frozenset[str]
    unsub_domains: frozenset[int]
    #: Домен → направление, у которого сайт в работе (`elsewhere.roles`).
    busy_domains: dict[int, Stage]


@dataclass
class _Halt:
    """Первая неисправимая причина — остальным лидам партии и прохода она же."""

    reason: str | None = None


def _mailbox(email: str) -> str:
    """Домен самого адреса: `ivan@mail.acme.example.test` → `mail.acme.example.test`."""
    return email.rpartition("@")[2]


# --- правила отказа: бесплатные, без сети ------------------------------------


def _duplicate(lead: SalesLeadModel, known: _Known) -> str | None:
    first = known.first_lead.get(lead.email, lead.id)
    return f"дубль: адрес уже у лида №{first}" if first < lead.id else None


def _stoplisted(lead: SalesLeadModel, known: _Known) -> str | None:
    if lead.email in known.stop_emails:
        return f"стоп-лист продаж: адрес {lead.email}"
    for host in sorted(_hosts_of(lead, known)):
        if host in known.stop_hosts:
            return f"стоп-лист продаж: домен {host}"
    return None


def _unsubscribed(lead: SalesLeadModel, known: _Known) -> str | None:
    if lead.email in known.unsub_emails:
        return f"отписка: {lead.email} просил не писать"
    if lead.domain_id in known.unsub_domains:
        return f"отписка: домен {known.hosts[lead.domain_id]} в общем стоп-листе"
    return None


def _busy(lead: SalesLeadModel, known: _Known) -> str | None:
    stage = known.busy_domains.get(lead.domain_id)
    if stage is None:
        return None
    return f"{attempts.elsewhere_reason(stage)} ({known.hosts[lead.domain_id]})"


def _unusable(lead: SalesLeadModel, _known: _Known) -> str | None:
    """Общие правила годности целиком — и ролевой ящик на бесплатной почте:
    `info@gmail.com` — чужой личный ящик, а не роль компании."""
    if reason := rejection_reason(lead.email):
        return reason
    local, _, domain = lead.email.partition("@")
    if domain in FREE_MAILBOX_DOMAINS and local in ROLE_LOCAL_PARTS:
        return f"ролевой ящик на бесплатной почте — чужой личный, а не роль компании: {lead.email}"
    return None


def _hosts_of(lead: SalesLeadModel, known: _Known) -> set[str]:
    """Домены, по которым лида закрывает стоп-лист: компания, домен адреса и его
    корень — тем же ключом, каким стоп-лист записан (`host_key`)."""
    mailbox = _mailbox(lead.email)
    return {known.hosts.get(lead.domain_id, ""), mailbox, host_key(mailbox)} - {""}


#: Порядок — от самого дешёвого и самого говорящего: дубль называет оригинал,
#: у которого своя причина; правило, добавленное сюда, само попадает в сводку.
_RULES: tuple[tuple[Callable[[SalesLeadModel, _Known], str | None], RejectionReason], ...] = (
    (_duplicate, RejectionReason.DUPLICATE),
    (_stoplisted, RejectionReason.STOPLIST),
    (_unsubscribed, RejectionReason.UNSUBSCRIBED),
    (_busy, RejectionReason.OTHER_DIRECTION),
    (_unusable, RejectionReason.UNUSABLE),
)


def _by_rules(lead: SalesLeadModel, known: _Known) -> Outcome | None:
    for rule, reason in _RULES:
        if note := rule(lead, known):
            return Outcome(lead.id, LeadStatus.REJECTED, reason, note)
    return None


def _no_mail(lead: SalesLeadModel, route: MailRoute) -> Outcome:
    how = "нулевой MX" if route is MailRoute.NULL_MX else "нет ни MX, ни A"
    note = f"домен не принимает почту ({how}): {_mailbox(lead.email)}"
    return Outcome(lead.id, LeadStatus.REJECTED, RejectionReason.NO_MAIL, note)


def _unverified(lead: SalesLeadModel, why: str) -> Outcome:
    """Проверка не выполнена — статус прежний, лид ждёт следующей очистки с причиной, и прежний
    вердикт цел: `ready` на перепроверке `new` снял бы с цепочки, а первый `unknown` без вердикта
    при следующем ответе `unknown` сошёл бы снова за первый (`unconfirmed.judged`)."""
    kept = lead.verification_status is not None
    return Outcome(lead.id, lead.status, note=f"{NOT_VERIFIED}: {why}", kept=kept)


# --- что знает база ------------------------------------------------------------


def _chunks(values: Sequence[Any]) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), CHUNK):
        yield values[start : start + CHUNK]


def _unsub(column: Any, chunk: Sequence[Any], moment: datetime) -> Any:
    """Строки общего стоп-листа, которые держат продажи (`suppressions.holds_sales`)."""
    return select(column).where(holds_sales(moment), column.in_(chunk))


async def _load_known(
    session: AsyncSession, leads: Sequence[SalesLeadModel], moment: datetime
) -> _Known:
    emails = sorted({lead.email for lead in leads})
    domain_ids = sorted({lead.domain_id for lead in leads})
    first: dict[str, int] = {}
    hosts: dict[int, str] = {}
    unsub_emails: set[str] = set()
    unsub_domains: set[int] = set()
    busy: dict[int, Stage] = {}
    for chunk in _chunks(emails):
        same = select(SalesLeadModel.email, func.min(SalesLeadModel.id))
        rows = await session.execute(
            same.where(SalesLeadModel.email.in_(chunk)).group_by(SalesLeadModel.email)
        )
        first.update(dict(rows.all()))  # type: ignore[arg-type]
        unsub_emails.update(
            (await session.scalars(_unsub(SuppressionModel.email, chunk, moment))).all()
        )
    for chunk in _chunks(domain_ids):
        named = select(DomainModel.id, DomainModel.host).where(DomainModel.id.in_(chunk))
        hosts.update(dict((await session.execute(named)).all()))  # type: ignore[arg-type]
        unsub_domains.update(
            (await session.scalars(_unsub(SuppressionModel.domain_id, chunk, moment))).all()
        )
        busy.update(await elsewhere.roles(session, chunk))
    stop = (await session.execute(select(SalesStoplistModel.host, SalesStoplistModel.email))).all()
    return _Known(
        first_lead=first,
        hosts=hosts,
        stop_hosts=frozenset(host for host, _ in stop if host),
        stop_emails=frozenset(email for _, email in stop if email),
        unsub_emails=frozenset(unsub_emails),
        unsub_domains=frozenset(unsub_domains),
        busy_domains=busy,
    )


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


async def _verify_one(
    lead: SalesLeadModel, verifier: EmailVerifier, halt: _Halt, moment: datetime
) -> Outcome:
    try:
        verdict = await verifier.verify(lead.email)
    except STOPS as exc:
        halt.reason = str(exc)
        logger.exception("продажи: проверка адресов остановлена", extra={"reason": str(exc)})
        return _unverified(lead, str(exc))
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
        status, reason, note = unconfirmed.judged(lead, verifier.name, moment)
        return Outcome(lead.id, status, reason, note, verdict, verifier.name)
    return Outcome(lead.id, LeadStatus.READY, verdict=verdict, verifier=verifier.name)


async def _verify_all(
    leads: Sequence[SalesLeadModel], verifier: EmailVerifier, halt: _Halt, moment: datetime
) -> list[Outcome]:
    limiter = asyncio.Semaphore(VERIFY_CONCURRENCY)

    async def one(lead: SalesLeadModel) -> Outcome:
        async with limiter:
            if halt.reason is not None:
                return _unverified(lead, halt.reason)
            return await _verify_one(lead, verifier, halt, moment)

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
    session: AsyncSession,
    leads: Sequence[SalesLeadModel],
    verifier: EmailVerifier,
    moment: datetime,
    halt: _Halt,
    report: CleaningReport,
    mx_concurrency: int,
) -> list[Outcome]:
    known = await _load_known(session, leads, moment)
    outcomes = [outcome for lead in leads if (outcome := _by_rules(lead, known))]
    decided = {outcome.lead_id for outcome in outcomes}
    alive = [lead for lead in leads if lead.id not in decided]
    routes = await mail_routes({_mailbox(lead.email) for lead in alive}, concurrency=mx_concurrency)
    report.mx_unknown += sum(1 for route in routes.values() if route is MailRoute.UNKNOWN)
    reachable: list[SalesLeadModel] = []
    for lead in alive:
        route = routes[_mailbox(lead.email)]
        if route in DELIVERABLE:
            reachable.append(lead)
        else:
            outcomes.append(_no_mail(lead, route))
    return outcomes + await _verify_all(reachable, verifier, halt, moment)


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


def _tally(report: CleaningReport, outcomes: Sequence[Outcome], moment: datetime) -> None:
    for outcome in outcomes:
        if outcome.reason is not None:
            report.rejected[outcome.reason.value] += 1
        elif outcome.verdict is not None and outcome.verdict.status == UNKNOWN:
            report.unconfirmed += 1
            report.recheck_on = unconfirmed.recheck_on(moment)
        elif outcome.status is LeadStatus.READY and not outcome.kept:
            report.ready += 1
        else:
            report.unverified += 1
        if outcome.verdict is not None:
            report.verified += 1
            report.paid_units += outcome.verdict.units


async def clean_leads(
    session: AsyncSession,
    leads: Sequence[SalesLeadModel],
    verifier: EmailVerifier,
    *,
    now: datetime | None = None,
) -> CleaningReport:
    """Очистить названных лидов тем же порядком, что проход. Коммит — за вызывающим:
    лид из ответа «пишите другому» чистится в транзакции задачи ответа."""
    moment = now or datetime.now(UTC)
    verifier = await capped(session, verifier, now=moment)  # месячный потолок у живого
    report = CleaningReport(checked=len(leads), verifier=verifier.name)
    halt = _Halt()
    outcomes = await _clean_batch(session, leads, verifier, moment, halt, report, MX_CONCURRENCY)
    await _write(session, outcomes, moment)
    _tally(report, outcomes, moment)
    report.stopped = halt.reason
    return report


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
    заново; с первым `unknown` — в день перепроверки (`unconfirmed.py`).
    """
    moment = now or datetime.now(UTC)
    verifier = await capped(session, verifier, now=moment)  # месячный потолок у живого
    leads = await _due_leads(session, hypothesis_id, verifier.name != FIXTURE, moment)
    report = CleaningReport(checked=len(leads), verifier=verifier.name)
    halt = _Halt()
    for start in range(0, len(leads), BATCH):
        batch = leads[start : start + BATCH]
        outcomes = await _clean_batch(
            session, batch, verifier, moment, halt, report, mx_concurrency
        )
        await _write(session, outcomes, moment)
        await session.commit()
        _tally(report, outcomes, moment)
    report.stopped = halt.reason
    logger.info(
        "продажи: очистка прошла",
        extra={
            "checked": report.checked,
            "ready": report.ready,
            "rejected": report.rejected_total,
            "unconfirmed": report.unconfirmed,
            "unverified": report.unverified,
            "verifier": verifier.name,
        },
    )
    return report
