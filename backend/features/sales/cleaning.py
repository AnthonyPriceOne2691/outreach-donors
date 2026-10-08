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
   `replies/pipeline.py`; человек отписывается от нас, а не от рассылки) — то же правило,
   что у сборки и отправки (`mail.stopped_by`);
4. домен в работе у доноров или рекламодателей — идущий диалог (диалог
   продаж — не другое направление: у компании бывает несколько лидов);
5. годность адреса — общий `contacts/quality.rejection_reason` целиком
   и ролевой ящик на бесплатной почте;
6. почта домена самого адреса — `contacts/mx.mail_route`: один запрос DNS
   на уникальный домен, параллельно, повтор на таймауте внутри;
7. платный проверяльщик — только для тех, кто дошёл.

**Каждый отказ назван дважды**: кодом (`RejectionReason`, по нему
фильтрует экран) и словами (`cleaning_note`, их читает человек).
Правило без имени нельзя настроить.

**«Провайдер не знает» и «мы не поняли» — разные исходы** (конституция,
«Failure visibility»). Вердикт `unknown` записывается, лид готов. Отказ
сервиса — сеть, 429, 5xx, непонятный ответ — оставляет лида `new`
с причиной «проверка не выполнена»: следующая очистка повторит его.
Квота и закрытая учётка останавливают платную часть прохода — повторять
их бессмысленно, а остальные лиды ждут человека с той же причиной.

**Проход коммитит каждую партию сам.** Платные вердикты — деньги; обрыв
посреди прохода не должен терять ни их, ни строку расхода (политика
сбоев: оплаченная пачка фиксируется сразу).

Доля «DNS не ответил» — отдельное число в сводке: ступень, которая ничего
не отсеивает, в поломке выглядит как работающая (замер соседей 18.09).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.known_addresses import FREE_MAILBOX_DOMAINS, ROLE_LOCAL_PARTS
from backend.features.contacts.mx import DELIVERABLE, MailRoute, mail_route
from backend.features.contacts.provider import (
    ProviderBlockedError,
    ProviderError,
    ProviderQuotaError,
)
from backend.features.contacts.quality import rejection_reason
from backend.features.core import usage
from backend.features.core.domain import Stage, SuppressionReason, ThreadStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, ThreadModel
from backend.features.sales.intake import host_key
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesLeadModel,
    SalesStoplistModel,
)
from backend.features.sales.verifier import EmailVerifier, Verdict

logger = logging.getLogger(__name__)

#: Лидов в партии: партия — одна транзакция и одна строка расхода.
BATCH = 200
#: Запросов DNS одновременно — как у прогона по файлу (`contacts/file_sweep.py`).
MX_CONCURRENCY = 8
#: Обращений к платному сервису одновременно: он отвечает за секунду,
#: а частоту ограничивает сам — 429 оставляет лида на следующий проход.
VERIFY_CONCURRENCY = 4
#: Значений в одном `IN`: параметров у Postgres не больше 32 767 на запрос.
CHUNK = 1000
#: Диалог, который идёт: лид на таком домене — помеха чужому разговору.
IN_WORK = (ThreadStatus.OPEN, ThreadStatus.REPLIED)
#: Чей идущий диалог мешает: другие направления. Диалог продаж — свой.
OTHER_DIRECTIONS = (Stage.DONORS, Stage.ADVERTISERS)
#: Сам просил не писать — держит при любом этапе записи, не только без этапа.
ASKED_NOT_TO_WRITE = (SuppressionReason.UNSUBSCRIBED, SuppressionReason.COMPLAINED)
OPERATION = "sales_verify"
NOT_VERIFIED = "проверка не выполнена"

#: Причина отказа словами — для сводки и консоли. Экран берёт код.
REASON_LABELS = {
    RejectionReason.DUPLICATE: "дубль",
    RejectionReason.STOPLIST: "стоп-лист продаж",
    RejectionReason.UNSUBSCRIBED: "отписка",
    RejectionReason.OTHER_DIRECTION: "домен в работе у другого направления",
    RejectionReason.UNUSABLE: "негодный адрес",
    RejectionReason.NO_MAIL: "домен не принимает почту",
    RejectionReason.UNDELIVERABLE: "адрес не существует",
}


@dataclass(frozen=True, slots=True)
class Outcome:
    """Итог очистки одного лида: куда он ушёл и почему."""

    lead_id: int
    status: LeadStatus
    reason: RejectionReason | None = None
    note: str | None = None
    verdict: Verdict | None = None
    verifier: str = ""


@dataclass
class CleaningReport:
    """Сводка прохода: каждый исход — отдельным числом, отказы — по причинам."""

    checked: int = 0
    ready: int = 0
    #: Код причины → сколько лидов.
    rejected: Counter[str] = field(default_factory=Counter)
    #: Остались `new`: проверка не выполнена.
    unverified: int = 0
    #: Доменов, по которым DNS не ответил: они прошли дальше непроверенными.
    mx_unknown: int = 0
    #: Вердиктов получено и сколько из них платных единиц.
    verified: int = 0
    paid_units: int = 0
    verifier: str = ""
    #: Платная часть остановлена: почему. Непроверенные лиды остались `new`.
    stopped: str | None = None

    @property
    def rejected_total(self) -> int:
        return sum(self.rejected.values())


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
    busy_domains: frozenset[int]


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
    if lead.domain_id in known.busy_domains:
        return f"домен в работе у другого направления: {known.hosts[lead.domain_id]}"
    return None


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
    return Outcome(lead.id, LeadStatus.NEW, note=f"{NOT_VERIFIED}: {why}")


# --- что знает база ------------------------------------------------------------


def _chunks(values: Sequence[Any]) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), CHUNK):
        yield values[start : start + CHUNK]


def _unsub(column: Any, chunk: Sequence[Any], moment: datetime) -> Any:
    """Строки общего стоп-листа, которые держат продажи: без этапа и этапа продаж — любые,
    другого этапа — только те, где человек сам просил не писать (как `mail.stopped_by`)."""
    anywhere = or_(
        SuppressionModel.stage.is_(None),
        SuppressionModel.stage == Stage.SALES,
        SuppressionModel.reason.in_(ASKED_NOT_TO_WRITE),
    )
    return select(column).where(anywhere, SuppressionModel.in_force(moment), column.in_(chunk))


async def _load_known(
    session: AsyncSession, leads: Sequence[SalesLeadModel], moment: datetime
) -> _Known:
    emails = sorted({lead.email for lead in leads})
    domain_ids = sorted({lead.domain_id for lead in leads})
    first: dict[str, int] = {}
    hosts: dict[int, str] = {}
    unsub_emails: set[str] = set()
    unsub_domains: set[int] = set()
    busy: set[int] = set()
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
        working = (
            select(ThreadModel.domain_id)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .where(ThreadModel.domain_id.in_(chunk), ThreadModel.status.in_(IN_WORK))
            .where(CampaignModel.stage.in_(OTHER_DIRECTIONS))
        )
        busy.update((await session.scalars(working)).all())
    stop = (await session.execute(select(SalesStoplistModel.host, SalesStoplistModel.email))).all()
    return _Known(
        first_lead=first,
        hosts=hosts,
        stop_hosts=frozenset(host for host, _ in stop if host),
        stop_emails=frozenset(email for _, email in stop if email),
        unsub_emails=frozenset(unsub_emails),
        unsub_domains=frozenset(unsub_domains),
        busy_domains=frozenset(busy),
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


async def _verify_one(lead: SalesLeadModel, verifier: EmailVerifier, halt: _Halt) -> Outcome:
    try:
        verdict = await verifier.verify(lead.email)
    except (ProviderQuotaError, ProviderBlockedError) as exc:
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
    return Outcome(lead.id, LeadStatus.READY, verdict=verdict, verifier=verifier.name)


async def _verify_all(
    leads: Sequence[SalesLeadModel], verifier: EmailVerifier, halt: _Halt
) -> list[Outcome]:
    limiter = asyncio.Semaphore(VERIFY_CONCURRENCY)

    async def one(lead: SalesLeadModel) -> Outcome:
        async with limiter:
            if halt.reason is not None:
                return _unverified(lead, halt.reason)
            return await _verify_one(lead, verifier, halt)

    return list(await asyncio.gather(*(one(lead) for lead in leads)))


# --- проход ---------------------------------------------------------------------


async def _new_leads(session: AsyncSession, hypothesis_id: int | None) -> list[SalesLeadModel]:
    query = select(SalesLeadModel).where(SalesLeadModel.status == LeadStatus.NEW)
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
    return outcomes + await _verify_all(reachable, verifier, halt)


def _row(outcome: Outcome, moment: datetime) -> dict[str, Any]:
    verdict = outcome.verdict
    return {
        "id": outcome.lead_id,
        "status": outcome.status,
        "rejection_reason": None if outcome.reason is None else outcome.reason.value,
        "cleaning_note": outcome.note,
        "verification_status": None if verdict is None else f"{outcome.verifier}:{verdict.status}",
        "verification_score": None if verdict is None else verdict.score,
        "verified_at": None if verdict is None else moment,
    }


async def _write(session: AsyncSession, outcomes: Sequence[Outcome], moment: datetime) -> None:
    """Исходы партии и одна строка расхода за её платные проверки."""
    if not outcomes:
        return
    await session.execute(update(SalesLeadModel), [_row(outcome, moment) for outcome in outcomes])
    units = sum(outcome.verdict.units for outcome in outcomes if outcome.verdict is not None)
    if units:
        usage.record(session, operation=OPERATION, units=units)


def _tally(report: CleaningReport, outcomes: Sequence[Outcome]) -> None:
    for outcome in outcomes:
        if outcome.status is LeadStatus.READY:
            report.ready += 1
        elif outcome.reason is not None:
            report.rejected[outcome.reason.value] += 1
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
    report = CleaningReport(checked=len(leads), verifier=verifier.name)
    halt = _Halt()
    outcomes = await _clean_batch(session, leads, verifier, moment, halt, report, MX_CONCURRENCY)
    await _write(session, outcomes, moment)
    _tally(report, outcomes)
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

    Лид `ready` второй раз не проверяется: платить дважды незачем. Лид,
    оставшийся `new` из-за отказа сервиса, следующим проходом пройдёт заново.
    """
    moment = now or datetime.now(UTC)
    leads = await _new_leads(session, hypothesis_id)
    report = CleaningReport(checked=len(leads), verifier=verifier.name)
    halt = _Halt()
    for start in range(0, len(leads), BATCH):
        batch = leads[start : start + BATCH]
        outcomes = await _clean_batch(
            session, batch, verifier, moment, halt, report, mx_concurrency
        )
        await _write(session, outcomes, moment)
        await session.commit()
        _tally(report, outcomes)
    report.stopped = halt.reason
    logger.info(
        "продажи: очистка прошла",
        extra={
            "checked": report.checked,
            "ready": report.ready,
            "rejected": report.rejected_total,
            "unverified": report.unverified,
            "verifier": verifier.name,
        },
    )
    return report
