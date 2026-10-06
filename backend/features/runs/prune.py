"""Чистка: убрать прогоны и то, что принесли только они, и пробные ответы.

Зачем. Пилотные прогоны и проверки оставляют в базе очереди, доноров и адреса,
которые никто не станет разбирать: экран доноров и счёт «ждут решения»
показывают работу, которой нет, а новые прогоны теряются среди старых. Убрать
их можно было только руками в базе — а запись в боевую базу допускается
только штатной командой (`outreach prune`).

**Что уходит.** Сами прогоны с очередями разбора (`run_candidates` — каскадом);
версии порогов, снятые для этих прогонов, если на них больше никто не ссылается
и это не действующая версия; домены, которые принесли только эти прогоны, —
с донором и адресами (каскадом). Отдельно — названные ответы, если это пробные
письма, не привязанные ни к чему.

**Что остаётся всегда — у каждого правила своя причина.**

- Домен из выдачи или очереди оставшегося прогона: его метрики оплачены и нужны
  тому прогону.
- Домен с решением человека или знанием из переписки — принят или отклонён,
  судья поправлен, ответ продавца, цена, адрес вписан руками: чистка такого
  решения не принимала, а восстановить его нечем.
- Домен в стоп-листе: удаление стёрло бы «не писать» каскадом, и следующий
  прогон написал бы ему снова.
- Домен с перепиской и рекламодатель, обход донора: это история работы,
  а не данные прогона.
- Домен лида продаж: строка домена общая, лид ссылается на неё, и база не даст
  её удалить — а решать за продажи чистка доноров не вправе.
- Журнал расхода, рассылки, собранные по прогону, рекламодатели, найденные им:
  запись теряет номер прогона, но остаётся — потраченное потрачено, отправленное
  отправлено. План называет, сколько их. Журнал действий не трогается.

Каждая ссылка на то, что удаляет чистка, разобрана в тесте по схеме
(`tests/test_prune.py`): новая таблица со ссылкой на домены или прогоны роняет
его, пока не решено, держит ли она домен, уходит с ним или теряет номер.

**Идущий прогон — отказ:** у него задача в очереди, и она писала бы в удалённое.
**Привязанный ответ — отказ:** это переписка, а не проверка приёма.

Отдельно — следы проверки на настоящих доменах (`--test-traces`): всё, что
адресовано своим ящикам из предохранителя, — с перепиской, ответами и ценой
из них (`outreach/own_inboxes.py`). Домен и донор при этом остаются.

Правила оставления проверяются дважды: планом — чтобы показать человеку, что
останется и почему, — и самим удалением, условиями в том же запросе. Между
показом и записью домену могли написать; удаление его тогда не тронет.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, ContactSource
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel, UsageRecordModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.core.models.run import RunCandidateModel, RunModel, RunSettingsModel
from backend.features.donors.probe import ProbeTrace, probe_domain, probe_trace, remove_probes
from backend.features.outreach.own_inboxes import (
    InboxTrace,
    inbox_trace,
    own_inboxes,
    remove_inbox_trace,
)
from backend.features.runs.repository import RunRepository
from backend.features.sales.models import SalesLeadModel

#: Причины оставить домен — в порядке проверки. Домен считается по первой
#: подошедшей: одна строка отчёта на домен, а не сумма пересечений.
KEPT_OTHER_RUN = "есть в оставшемся прогоне"
KEPT_DECISION = "решение человека или знание из переписки"
KEPT_STOPLIST = "в стоп-листе"
KEPT_HISTORY = "переписка, рекламодатель или обход"
KEPT_SALES = "лид продаж"

#: Что остаётся без номера прогона: запись живёт, ссылка на удалённый прогон гаснет.
DETACHED_USAGE = "записи журнала расхода"
DETACHED_CAMPAIGNS = "рассылки, собранные по прогону"
DETACHED_ADVERTISERS = "рекламодатели, найденные прогоном"
_RUN_LINKS: tuple[tuple[str, InstrumentedAttribute[int | None]], ...] = (
    (DETACHED_USAGE, UsageRecordModel.run_id),
    (DETACHED_CAMPAIGNS, CampaignModel.run_id),
    (DETACHED_ADVERTISERS, AdvertiserModel.found_run_id),
)


class PruneRefusedError(ValueError):
    """Чистку нельзя выполнить как названо. Сообщение говорит почему и что делать."""


@dataclass(slots=True)
class PrunePlan:
    """Что уйдёт и что останется. Ничего не меняет — только показывает."""

    runs: list[int] = field(default_factory=list)
    #: Строк очереди разбора уйдёт вместе с прогонами.
    candidates: int = 0
    #: Версии порогов, которые уйдут: снятые только для этих прогонов.
    settings: list[int] = field(default_factory=list)
    #: Домены, которые принесли только эти прогоны и ничто их не держит.
    domains: list[int] = field(default_factory=list)
    #: Адресов уйдёт вместе с доменами.
    contacts: int = 0
    #: Причина → сколько доменов прогонов оставлено.
    kept: dict[str, int] = field(default_factory=dict)
    #: Пробные ответы к удалению: номер → тема.
    replies: dict[int, str] = field(default_factory=dict)
    #: Что останется без номера прогона → сколько записей.
    detached: dict[str, int] = field(default_factory=dict)
    #: Липовые доноры (`--probes`): уходят целиком, с перепиской. `None` — не просили.
    probes: ProbeTrace | None = None
    #: Следы проверки на настоящих доменах (`--test-traces`). `None` — не просили.
    test_traces: InboxTrace | None = None

    def as_details(self) -> dict[str, Any]:
        """Запись в журнал действий: из неё потом видно, что и почему ушло."""
        return {
            "прогоны": self.runs,
            "очередь разбора": self.candidates,
            "версии порогов": self.settings,
            "доменов": len(self.domains),
            "адресов": self.contacts,
            "оставлено": self.kept,
            "ответы": sorted(self.replies),
            "без номера прогона": self.detached,
            **(self.probes.as_details() if self.probes is not None else {}),
            **(self.test_traces.as_details() if self.test_traces is not None else {}),
        }


@dataclass(frozen=True, slots=True)
class _Hosts:
    """Чем прогоны держат домены: выдачей и очередью разбора."""

    hosts: frozenset[str]
    domain_ids: frozenset[int]


async def _hosts_of(session: AsyncSession, run_ids: Iterable[int], *, keep: bool) -> _Hosts:
    """Выдача и очередь названных прогонов — или всех остальных (`keep`)."""
    ids = list(run_ids)
    chosen = RunModel.id.not_in(ids) if keep else RunModel.id.in_(ids)
    rows = await session.execute(select(RunModel.candidates).where(chosen))
    hosts: set[str] = set()
    for (candidates,) in rows.all():
        hosts.update(str(host) for host in (candidates or {}).get("hosts") or [])
    in_queue = RunCandidateModel.run_id.not_in(ids) if keep else RunCandidateModel.run_id.in_(ids)
    queued = await session.scalars(select(RunCandidateModel.domain_id).where(in_queue))
    return _Hosts(frozenset(hosts), frozenset(queued.all()))


def _decision() -> ColumnElement[bool]:
    """Решение человека или знание из переписки — у домена или его донора."""
    donor = exists().where(
        DonorModel.domain_id == DomainModel.id,
        or_(DonorModel.review.is_not(None), DonorModel.last_price.is_not(None)),
    )
    manual = exists().where(
        ContactModel.domain_id == DomainModel.id, ContactModel.source == ContactSource.MANUAL
    )
    return or_(
        DomainModel.human_intent.is_not(None),
        DomainModel.seller_answer.is_not(None),
        donor,
        manual,
    )


def _stoplisted() -> ColumnElement[bool]:
    return exists().where(SuppressionModel.domain_id == DomainModel.id)


def _history() -> ColumnElement[bool]:
    """Переписка, рекламодатель или обход донора — история работы с доменом."""
    return or_(
        exists().where(MessageModel.domain_id == DomainModel.id),
        exists().where(ThreadModel.domain_id == DomainModel.id),
        exists().where(AdvertiserModel.domain_id == DomainModel.id),
        exists().where(CrawlRunModel.host == DomainModel.host),
    )


def _sales_lead() -> ColumnElement[bool]:
    """Лид продаж на этом домене: ссылка без каскада — база удалить не даст."""
    return exists().where(SalesLeadModel.domain_id == DomainModel.id)


def _held() -> ColumnElement[bool]:
    """Всё, что держит домен, кроме оставшихся прогонов, — одним условием."""
    return or_(_decision(), _stoplisted(), _history(), _sales_lead())


async def _count_where(session: AsyncSession, ids: Sequence[int], rule: ColumnElement[bool]) -> int:
    if not ids:
        return 0
    found = await session.scalar(
        select(func.count()).select_from(DomainModel).where(DomainModel.id.in_(ids), rule)
    )
    return int(found or 0)


async def _domains(session: AsyncSession, plan: PrunePlan, run_ids: Sequence[int]) -> None:
    """Какие домены уйдут с прогонами, а какие останутся и почему."""
    pruned = await _hosts_of(session, run_ids, keep=False)
    others = await _hosts_of(session, run_ids, keep=True)
    # Липовые доноры — не здесь: их держит своя переписка, и убирает их только `--probes`.
    brought = and_(
        or_(
            DomainModel.host.in_(sorted(pruned.hosts)),
            DomainModel.id.in_(sorted(pruned.domain_ids)),
        ),
        ~probe_domain(),
    )
    in_other_run = or_(
        DomainModel.host.in_(sorted(others.hosts)), DomainModel.id.in_(sorted(others.domain_ids))
    )
    rows = await session.scalars(select(DomainModel.id).where(brought).order_by(DomainModel.id))
    candidates = list(rows.all())

    other = await _count_where(session, candidates, in_other_run)
    rest = ~in_other_run
    decided = await _count_where(session, candidates, and_(rest, _decision()))
    rest = and_(rest, ~_decision())
    stoplisted = await _count_where(session, candidates, and_(rest, _stoplisted()))
    rest = and_(rest, ~_stoplisted())
    history = await _count_where(session, candidates, and_(rest, _history()))
    rest = and_(rest, ~_history())
    sales = await _count_where(session, candidates, and_(rest, _sales_lead()))

    kept = {
        KEPT_OTHER_RUN: other,
        KEPT_DECISION: decided,
        KEPT_STOPLIST: stoplisted,
        KEPT_HISTORY: history,
        KEPT_SALES: sales,
    }
    plan.kept = {reason: count for reason, count in kept.items() if count}
    gone = await session.scalars(
        select(DomainModel.id)
        .where(DomainModel.id.in_(candidates), ~in_other_run, ~_held())
        .order_by(DomainModel.id)
    )
    plan.domains = list(gone.all())
    if plan.domains:
        plan.contacts = int(
            await session.scalar(
                select(func.count())
                .select_from(ContactModel)
                .where(ContactModel.domain_id.in_(plan.domains))
            )
            or 0
        )


async def _runs(session: AsyncSession, plan: PrunePlan, run_ids: Sequence[int]) -> None:
    """Прогоны: есть ли такие, не идут ли, что уйдёт с ними."""
    found = {
        run.id: run
        for run in await session.scalars(select(RunModel).where(RunModel.id.in_(run_ids)))
    }
    missing = [run_id for run_id in run_ids if run_id not in found]
    if missing:
        raise PruneRefusedError(
            "Прогонов нет: " + ", ".join(f"№{n}" for n in missing) + " — проверьте номера."
        )
    active = [run.id for run in found.values() if run.status in RunRepository.ACTIVE_STATUSES]
    if active:
        raise PruneRefusedError(
            "Прогоны ещё идут: "
            + ", ".join(f"№{n}" for n in sorted(active))
            + " — у них задача в очереди, и она писала бы в удалённое. Дождитесь конца "
            "или остановки."
        )
    plan.runs = sorted(found)
    plan.candidates = int(
        await session.scalar(
            select(func.count())
            .select_from(RunCandidateModel)
            .where(RunCandidateModel.run_id.in_(plan.runs))
        )
        or 0
    )
    plan.detached = await _detached(session, plan.runs)
    plan.settings = await _settings(session, plan.runs)


async def _detached(session: AsyncSession, run_ids: Sequence[int]) -> dict[str, int]:
    """Что останется без номера прогона → сколько записей. Нулевые не называются."""
    counts: dict[str, int] = {}
    for what, link in _RUN_LINKS:
        count = int(await session.scalar(select(func.count()).where(link.in_(run_ids))) or 0)
        if count:
            counts[what] = count
    return counts


async def _settings(session: AsyncSession, run_ids: Sequence[int]) -> list[int]:
    """Версии порогов, снятые только для этих прогонов. Действующая — никогда:
    следующий прогон пошёл бы по другим порогам, и никто бы этого не заметил."""
    mine = set(
        (await session.scalars(select(RunModel.settings_id).where(RunModel.id.in_(run_ids)))).all()
    )
    shared = set(
        (
            await session.scalars(select(RunModel.settings_id).where(RunModel.id.not_in(run_ids)))
        ).all()
    )
    current = await session.scalar(
        select(RunSettingsModel.id).order_by(RunSettingsModel.version.desc()).limit(1)
    )
    return sorted(mine - shared - {current})


def _bound() -> ColumnElement[bool]:
    """Ответ — часть переписки, а не проверка приёма."""
    answered = exists().where(MessageModel.answers_reply_id == ReplyModel.id)
    seller = exists().where(DomainModel.seller_answer_reply_id == ReplyModel.id)
    return or_(
        ReplyModel.thread_id.is_not(None),
        ReplyModel.message_id.is_not(None),
        ReplyModel.reviewed_at.is_not(None),
        answered,
        seller,
    )


async def _replies(session: AsyncSession, plan: PrunePlan, reply_ids: Sequence[int]) -> None:
    if not reply_ids:
        return
    rows = await session.execute(
        select(ReplyModel.id, ReplyModel.subject, _bound().label("bound")).where(
            ReplyModel.id.in_(reply_ids)
        )
    )
    found = {reply_id: (subject, bound) for reply_id, subject, bound in rows.all()}
    missing = [reply_id for reply_id in reply_ids if reply_id not in found]
    if missing:
        raise PruneRefusedError(
            "Ответов нет: " + ", ".join(f"№{n}" for n in missing) + " — проверьте номера."
        )
    bound = sorted(reply_id for reply_id, (_, is_bound) in found.items() if is_bound)
    if bound:
        raise PruneRefusedError(
            "Ответы привязаны к переписке или их смотрел человек: "
            + ", ".join(f"№{n}" for n in bound)
            + " — это переписка, а не проверка приёма; чистка их не удаляет."
        )
    plan.replies = {reply_id: subject or "" for reply_id, (subject, _) in sorted(found.items())}


async def plan_prune(
    session: AsyncSession,
    *,
    run_ids: Sequence[int],
    reply_ids: Sequence[int] = (),
    probes: bool = False,
    test_traces: bool = False,
) -> PrunePlan:
    """Что уйдёт при чистке. Отказ словами — до того, как что-либо удалено.

    `probes` — липовые доноры целиком и их проверочные прогоны (`donors/probe.py`);
    `test_traces` — следы проверки на настоящих доменах (`outreach/own_inboxes.py`)."""
    if not run_ids and not reply_ids and not probes and not test_traces:
        raise PruneRefusedError(
            "Нечего чистить: назовите прогоны (--runs), ответы (--replies), --probes "
            "или --test-traces."
        )
    plan = PrunePlan()
    if test_traces:
        plan.test_traces = await _test_traces(session)
    if probes:
        plan.probes = await probe_trace(session)
        run_ids = sorted({*run_ids, *plan.probes.runs})
    if run_ids:
        await _runs(session, plan, sorted(set(run_ids)))
        await _domains(session, plan, plan.runs)
    await _replies(session, plan, sorted(set(reply_ids)))
    return plan


async def _test_traces(session: AsyncSession) -> InboxTrace:
    """Следы проверки — или отказ: без своих ящиков в предохранителе «следов нет»
    было бы неправдой, а не чистотой: отличить их не по чему."""
    inboxes = own_inboxes()
    if not inboxes.addresses:
        raise PruneRefusedError(inboxes.refusal())
    return await inbox_trace(session, inboxes)


async def apply_prune(session: AsyncSession, plan: PrunePlan, *, author: str) -> None:
    """Удалить по плану одной транзакцией и записать в журнал. Без коммита:
    решает вызывающий. Условия оставления повторены в запросе удаления."""
    if plan.replies:
        await session.execute(
            delete(ReplyModel).where(ReplyModel.id.in_(sorted(plan.replies)), ~_bound())
        )
    if plan.test_traces is not None:
        await remove_inbox_trace(session, plan.test_traces)
    if plan.runs:
        # Расход, рассылки и рекламодатели остаются: номер прогона гасит внешний
        # ключ, но запрос явный — чтобы поведение не зависело от того, как когда-то
        # объявили ограничение.
        for _, link in _RUN_LINKS:
            await session.execute(
                update(link.class_).where(link.in_(plan.runs)).values({link: None})
            )
        await session.execute(delete(RunModel).where(RunModel.id.in_(plan.runs)))
    if plan.settings:
        in_use = select(RunModel.settings_id)
        await session.execute(
            delete(RunSettingsModel).where(
                RunSettingsModel.id.in_(plan.settings), RunSettingsModel.id.not_in(in_use)
            )
        )
    if plan.domains:
        others = await _hosts_of(session, [], keep=True)
        in_other_run = or_(
            DomainModel.host.in_(sorted(others.hosts)),
            DomainModel.id.in_(sorted(others.domain_ids)),
        )
        await session.execute(
            delete(DomainModel).where(DomainModel.id.in_(plan.domains), ~in_other_run, ~_held())
        )
    if plan.probes is not None:
        await remove_probes(session, plan.probes)
    await AccessRepository(session).record(
        AuditAction.DATA_PRUNED, target=_target(plan), details={**plan.as_details(), "кто": author}
    )


#: Длина поля цели в журнале (`audit_log.target`). Перечень сверх неё — в `details`.
TARGET_LIMIT = 64


def _target(plan: PrunePlan) -> str:
    """Цель записи журнала: перечень, если влезает в поле, иначе — счёт.

    Поле короткое, а прогонов в чистке может быть десяток: длинный перечень
    ронял запись журнала, а с ней — всю чистку. Точные номера лежат в `details`.
    """
    marks = [
        *([] if plan.probes is None else [f"probes:{len(plan.probes.domains)}"]),
        *([] if plan.test_traces is None else [f"test-traces:{len(plan.test_traces.domains)}"]),
    ]
    named = ", ".join(
        [*(f"run:{n}" for n in plan.runs), *(f"reply:{n}" for n in plan.replies), *marks]
    )
    if len(named) <= TARGET_LIMIT:
        return named
    counts = [f"runs:{len(plan.runs)}", f"replies:{len(plan.replies)}", *marks]
    return " ".join([*counts, "(номера — в details)"])
