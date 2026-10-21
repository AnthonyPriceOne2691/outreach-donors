"""Автосборка очереди продаж: писем в очереди хватает на ближайшие сутки — без кнопки.

**Сколько нужно.** Сутки ящиков продаж — сумма их дневных капов с разгоном
(`senders.warmup_state`): столько первых писем ящики отправят за день. В очереди меньше — не
хватает разницы; столько и собирается, но не больше предела сборки (`LIMIT_MAX`).

**В очереди — только письма, которые уйдут** (`queued`, ревью стыков отправки). Письмо, собранное
с прежними подписью и адресом «Отправителя» или до правки лида, отправка не пустит («соберите
очередь заново»), а письмо лида, которого сняли (стоп-лист продаж, отклонён) или чей адрес проверен
выдуманным проверяльщиком при живом или не подтверждён (`unknown`), не уйдёт вовсе. Считай проход
их очередью — после правки подписи или пополнения стоп-листа он ждал бы писем, которые не уйдут
никогда, и поток стоял бы молча. Письма первого рода — повод собрать их гипотезу: сборка
пересобирает их на том же месте, как кнопкой; второго — сборка не трогает, и в счёт они не идут.
Адрес судят раньше правки лида — тем же правилом, что сборка и отправка
(`unconfirmed.first_letter`): письмо лида с неподтверждённым адресом пересборка не выпустит, и
гипотеза из-за него к сборке не встаёт — иначе проход каждые десять минут ставил бы пустую сборку.

**Та же сборка, что у кнопки** (`queue_start.start`, задача `queue_jobs.build_sales_queue`):
одна сборка гипотезы за раз, потолок модели продаж — до каждого письма, остановка после трёх
лидов подряд вне коридора — внутри сборки. Проход сверх того:
- не ставит сборку, пока идёт любая сборка продаж: недостача считается по письмам, уже стоящим
  в очереди, и вторая сборка разом собрала бы ту же недостачу ещё раз;
- не ставит, пока потолок модели продаж набран: сборка встала бы на первом письме;
- не собирает гипотезу, чья прошлая сборка встала на серии писем вне коридора, пока человек не
  соберёт её кнопкой, поправив цепочку: иначе каждый проход тратил бы модель на те же лиды впустую.

**Какие гипотезы.** Признака «активна» у гипотезы нет — собираются те, у кого есть полная
цепочка хоть на одном языке и лиды `ready` без письма или письма в очереди, которые сборка
соберёт заново. Без письма — и лид, чьё первое письмо снято до отправки (`off_queue.again`): лида
снимали, а он снова «готов», или сайт был в работе у другого направления. Лид, чей сайт в работе
у другого направления сейчас, ждёт (`elsewhere.writable`): письмо ему отправка сняла бы.

**Недостача — по очереди.** Проход отдаёт всю недостачу одной гипотезе: той, чья сборка (кнопкой
или сама) начиналась давнее всех по журналу; ни разу не собранная — первой. Не хватило её лидов —
следующий проход посчитает недостачу заново и отдаст следующей. «Поровну» требовало бы нескольких
сборок разом или счёта долей между проходами, «по доле ready» отдавало бы всё большой базе, и
малая гипотеза не получала бы писем вовсе.

**«Кому не пишем» — и здесь** (`not_writing.py`, решение владельца 13.10): письмо лида из списка
не в счёт очереди — отправка его не пустит, — а гипотеза, у которой без письма остались только
такие лиды, не кандидат: сборка их не возьмёт, и проход ставил бы её каждые десять минут впустую.

**Очередь пуста, а собрать нельзя — тревога** (F7, решение владельца 13.10): потолок модели,
серия вне коридора (`Plan.stuck`) или упавшая сборка при включённой автоотправке — одна тревога
при переходе и «прошло» при выходе; память и слова — `autobuild_alarm.py`, проход — `step`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import DuplicateJobError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import sales as sales_cfg
from backend.config import storage
from backend.features.access.repository import AccessRepository
from backend.features.core import usage
from backend.features.core.domain import AuditAction, MessageStatus, Stage
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import MessageModel, SenderModel
from backend.features.letters.chain import FIRST_STEP
from backend.features.outreach.senders import warmup_state
from backend.features.sales import (
    autobuild_alarm,
    autosend,
    chain,
    chain_text,
    connection,
    corridor,
    elsewhere,
    letter,
    sender,
)
from backend.features.sales.autobuild_alarm import Outcome, Watch
from backend.features.sales.job_holder import holder
from backend.features.sales.lead_edit import edited
from backend.features.sales.models import (
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.not_writing import excluded
from backend.features.sales.queue_start import LIMIT_MAX, RUNNING, build_job_id, start
from backend.features.sales.unconfirmed import first_letter, holds
from backend.features.sales.usage_cap import sales_cap
from backend.shared.queue import sales_bulk_queue

logger = logging.getLogger(__name__)

#: Действие в журнале: сборку поставил проход, а не человек (у кнопки — «сборка очереди продаж»).
AUTO = "автосборка очереди продаж"
NO_CANDIDATES = "нет гипотезы с полной цепочкой и лидами, готовыми к письму"
QUEUE_SILENT = "очередь задач не отвечает — идёт ли сборка, сейчас не узнать"
#: Лидов без письма читать за раз, ища того, кому пишем (`_writable`).
CHUNK = 500


class Stuck(str):
    """Почему не собирается — словами, и автосборка сама не соберёт: потолок модели продаж или
    серия писем вне коридора (F7). Строка как строка: план и экран читают слова как прежде, а
    тревога узнаёт причину по типу."""

    __slots__ = ()


STREAK = Stuck(
    "прошлые сборки гипотез встали: шаблон первого письма не даёт писем в коридоре отличия — "
    "поправьте цепочку и соберите очередь кнопкой, дальше автосборка подхватит"
)


@dataclass(frozen=True, slots=True)
class Plan:
    """Хватает ли очереди на сутки и кого собрать — или почему не собирается."""

    #: Сумма дневных капов ящиков продаж с разгоном.
    need: int
    #: Первых писем продаж в очереди, которые уйдут как есть (`queued`).
    have: int
    hypothesis_id: int | None = None
    name: str = ""
    why: str | None = None

    @property
    def stuck(self) -> bool:
        """Автосборка сама не соберёт: потолок модели продаж или серия вне коридора (`Stuck`)."""
        return isinstance(self.why, Stuck)

    @property
    def short(self) -> int:
        return max(0, self.need - self.have)

    @property
    def limit(self) -> int:
        """Сколько поставить собрать: недостача, но не больше предела сборки."""
        return min(self.short, LIMIT_MAX)


async def needed(session: AsyncSession, now: datetime) -> int:
    """Сколько первых писем ящики продаж отправят за сутки: дневные капы с разгоном."""
    boxes = await session.scalars(
        select(SenderModel).where(SenderModel.stage == Stage.SALES, SenderModel.enabled.is_(True))
    )
    return sum(warmup_state(box, now=now).allowance for box in boxes)


@dataclass(frozen=True, slots=True)
class Queued:
    """Первые письма продаж в очереди глазами отправки: какие уйдут как есть и какие сборка
    соберёт заново. Тем же счётом строка автоотправки узнаёт, что не уйдёт ни одно письмо окна
    (`auto_lines.autosend_line`)."""

    #: Письма, которые уйдут как есть.
    ready: frozenset[int] = frozenset()
    #: Письма, собранные с прежними подписью и адресом или до правки лида: их соберёт сборка.
    redo: frozenset[int] = frozenset()
    #: Гипотезы этих писем.
    stale: frozenset[int] = frozenset()
    #: Письма лидов, чей адрес не проверен по-настоящему или не подтверждён: не уйдут, и
    #: пересборка не поможет — лида решит очистка (`unconfirmed.first_letter`).
    held: frozenset[int] = frozenset()

    @property
    def going(self) -> int:
        return len(self.ready)


async def queued(session: AsyncSession) -> Queued:
    """Первые письма продаж в очереди: уйдут как есть — в счёт; собраны с прежними подписью и
    адресом или до правки лида (`letter.problem`, `lead_edit.edited` — отказ отправки) — гипотеза
    к сборке; адрес лида не проверен по-настоящему или не подтверждён — отдельно, без сборки; лид
    снят или в «кому не пишем» (`not_writing.py`) — ни то ни другое. Адрес — раньше правки, как у
    отправки (`mail._first_problem`)."""
    found = await sender.read(session)
    rows = await session.execute(
        select(MessageModel, SalesLeadModel, DomainModel.host, SalesThreadModel.language)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
        .where(MessageModel.status == MessageStatus.QUEUED, MessageModel.step == FIRST_STEP)
    )
    ready: set[int] = set()
    redo: dict[int, int] = {}
    held: set[int] = set()
    for message, lead, host, language in rows.tuples().all():
        if _closed(lead, host):
            continue
        signer = found.in_language(language)  # письмо подписано набором языка его цепочки
        if first_letter(lead) is not None:
            held.add(message.id)
        elif letter.problem(message.body or "", signer) is not None or edited(lead, message):
            redo[message.id] = lead.hypothesis_id
        else:
            ready.add(message.id)
    return Queued(frozenset(ready), frozenset(redo), frozenset(redo.values()), frozenset(held))


def _closed(lead: SalesLeadModel, host: str) -> bool:
    """Письмо лида не уйдёт вовсе: лид снят или в «кому не пишем» — сборка его не тронет. Адрес
    не подтверждён — не здесь: такое письмо ждёт очистки (`Queued.held`)."""
    return lead.status is not LeadStatus.READY or excluded(lead.email, host) is not None


async def candidates(
    session: AsyncSession, stale: frozenset[int] = frozenset()
) -> list[tuple[int, str]]:
    """Гипотезы с полной цепочкой хоть на одном языке и лидами `ready`, которым сборка напишет
    первое письмо (`elsewhere.writable`), не из «кому не пишем», чей адрес письму не мешает
    (`unconfirmed.holds`), — или с письмами в очереди, которые сборка соберёт заново (`stale`), —
    по номеру."""
    unwritten = (
        select(SalesLeadModel.hypothesis_id)
        .outerjoin(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(elsewhere.writable(), ~holds())
    )
    rows = await session.execute(
        select(SalesHypothesisModel.id, SalesHypothesisModel.name)
        .where(
            or_(
                SalesHypothesisModel.id.in_(unwritten),
                SalesHypothesisModel.id.in_(sorted(stale)),
            )
        )
        .order_by(SalesHypothesisModel.id)
    )
    return [
        item
        for item in rows.tuples().all()
        if (item[0] in stale or await _writable(session, item[0]))
        and await _has_chain(session, item[0])
    ]


async def _writable(session: AsyncSession, hypothesis_id: int) -> bool:
    """Есть ли у гипотезы лид, которому сборка напишет первое письмо (`elsewhere.writable`), не из
    «кому не пишем» (`not_writing.py`), чей адрес письму не мешает (`unconfirmed.holds`): лиды —
    пачками по номеру, до первого такого."""
    after = 0
    while True:
        rows = await session.execute(
            select(SalesLeadModel.id, SalesLeadModel.email, DomainModel.host)
            .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
            .outerjoin(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
            .where(
                SalesLeadModel.hypothesis_id == hypothesis_id,
                elsewhere.writable(),
                ~holds(),
                SalesLeadModel.id > after,
            )
            .order_by(SalesLeadModel.id)
            .limit(CHUNK)
        )
        leads = rows.tuples().all()
        if not leads:
            return False
        if any(excluded(email, host) is None for _, email, host in leads):
            return True
        after = leads[-1][0]


async def _has_chain(session: AsyncSession, hypothesis_id: int) -> bool:
    for code in chain_text.LANGUAGES:
        found = await chain.resolve(session, hypothesis_id=hypothesis_id, language=code)
        if not found.missing:
            return True
    return False


async def in_turn(session: AsyncSession, found: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Гипотезы по очереди: ни разу не собранные — первыми, затем чья сборка начиналась давнее
    всех. Начало сборки — запись журнала с номером её задачи (кнопкой или самой)."""
    targets = {f"job:{build_job_id(hypothesis_id)}": hypothesis_id for hypothesis_id, _ in found}
    rows = await session.execute(
        select(AuditLogModel.target, func.max(AuditLogModel.id))
        .where(
            AuditLogModel.action == AuditAction.RUN_STARTED,
            AuditLogModel.target.in_(list(targets)),
        )
        .group_by(AuditLogModel.target)
    )
    last = {targets[target]: row for target, row in rows.tuples().all() if target is not None}
    return sorted(found, key=lambda item: (last.get(item[0], 0), item[0]))


def _busy(jobs: Queue, hypotheses: list[tuple[int, str]]) -> str | None:
    """Имя гипотезы, чья сборка идёт или ждёт повтора; `None` — ни одной."""
    for hypothesis_id, name in hypotheses:
        job = holder(jobs, build_job_id(hypothesis_id))
        if job is not None and job.get_status() in RUNNING:
            return name
    return None


def _streak_stopped(jobs: Queue, hypothesis_id: int) -> bool:
    """Прошлая сборка гипотезы встала на серии писем вне коридора — и человек её ещё не пересобрал."""
    job = holder(jobs, build_job_id(hypothesis_id))
    result = job.latest_result() if job is not None else None
    report = result.return_value if result is not None else None
    return isinstance(report, dict) and report.get("stopped") == corridor.streak_stop()


async def plan(session: AsyncSession, jobs: Queue, *, now: datetime) -> Plan:
    """Хватает ли очереди и кого собрать — без записи и без постановки."""
    queue = await queued(session)
    base = Plan(await needed(session, now), queue.going)
    if not base.short:
        return base
    found = await _choose(session, jobs, now, queue.stale)
    if isinstance(found, str):
        return replace(base, why=found)
    return replace(base, hypothesis_id=found[0], name=found[1])


async def _choose(
    session: AsyncSession, jobs: Queue, now: datetime, stale: frozenset[int]
) -> tuple[int, str] | str:
    """Чью сборку поставить — или почему ничью, словами."""
    why = await _blocked(session, now)
    if why is not None:
        return why
    found = await candidates(session, stale)
    if not found:
        return NO_CANDIDATES
    every = await session.execute(select(SalesHypothesisModel.id, SalesHypothesisModel.name))
    try:
        busy = await asyncio.to_thread(_busy, jobs, list(every.tuples().all()))
        stuck = {h for h, _ in found if await asyncio.to_thread(_streak_stopped, jobs, h)}
    except RedisError as exc:
        logger.warning("продажи: автосборка — очередь задач не ответила", extra={"error": str(exc)})
        return QUEUE_SILENT
    if busy is not None:
        return f"идёт сборка гипотезы «{busy}» — недостача посчитается после неё"
    ready = [item for item in await in_turn(session, found) if item[0] not in stuck]
    return ready[0] if ready else STREAK


async def _blocked(session: AsyncSession, now: datetime) -> str | None:
    """Что не даёт собирать никого: продажи не подключены или потолок модели продаж набран."""
    missing = await connection.missing(session)
    if missing:
        return f"продажи к почте не подключены — {'; '.join(missing)}"
    try:
        await usage.ensure_llm_within_cap(session, run_id=None, now=now, own=sales_cap())
    except usage.LlmCapExceededError as exc:
        # План спрашивает и экран: строку на смене причины пишет проход (`Said`), а не каждый взгляд.
        logger.debug("продажи: автосборка ждёт — потолок модели", extra={"why": str(exc)})
        return Stuck(exc)
    return None


async def run(session: AsyncSession, jobs: Queue, *, now: datetime) -> Plan:
    """Один проход: не хватает — та же сборка, что у кнопки, с пределом недостачи и записью в
    журнал без автора; хватает или нельзя — план со словами."""
    found = await plan(session, jobs, now=now)
    if found.hypothesis_id is None:
        return found
    try:
        job = await asyncio.to_thread(start, jobs, found.hypothesis_id, found.limit, auto=True)
    except DuplicateJobError:
        logger.info("продажи: автосборка — сборку гипотезы поставили секундой раньше")
        return replace(found, hypothesis_id=None, why=f"сборка «{found.name}» уже идёт")
    except RedisError as exc:
        logger.warning("продажи: автосборка не поставлена", extra={"error": str(exc)})
        return replace(found, hypothesis_id=None, why=QUEUE_SILENT)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=None,
        target=f"job:{job.id}",
        details={
            "действие": AUTO,
            "гипотеза": found.name,
            "писем": found.limit,
            "не хватало": found.short,
        },
        # Этап — в записи: журнал прячет её от учётки без права «Продажи» не по словам
        # «действия», которые правят (`access/journal.sales_entry`).
        stage=Stage.SALES,
    )
    await session.commit()
    logger.info(
        "продажи: автосборка поставила сборку",
        extra={"hypothesis_id": found.hypothesis_id, "limit": found.limit, "short": found.short},
    )
    return found


@dataclass
class Said:
    """Почему автосборка не собирает — сказанное журналом последним: строка только при смене."""

    why: str | None = None

    def tell(self, found: Plan) -> None:
        if found.why != self.why and found.why is not None:
            logger.info("продажи: автосборка не собирает — %s", found.why)
        self.why = found.why


SAID = Said()


async def last_started(session: AsyncSession) -> AuditLogModel | None:
    """Запись журнала о последней сборке, поставленной автосборкой: когда, гипотеза, задача."""
    found = await session.execute(
        select(AuditLogModel)
        .where(
            AuditLogModel.action == AuditAction.RUN_STARTED,
            AuditLogModel.details["действие"].astext == AUTO,
        )
        .order_by(AuditLogModel.id.desc())
        .limit(1)
    )
    return found.scalar_one_or_none()


async def _last(session: AsyncSession, jobs: Queue) -> tuple[Outcome | None, str]:
    """Исход последней сборки, поставленной автосборкой, и имя её гипотезы."""
    row = await last_started(session)
    if row is None or row.target is None:
        return None, ""
    job_id = row.target.removeprefix("job:")
    try:
        found = await asyncio.to_thread(autobuild_alarm.outcome_of, jobs, job_id)
    except RedisError as exc:
        logger.warning("продажи: итог автосборки не прочитан", extra={"error": str(exc)})
        return None, ""
    return found, str((row.details or {}).get("гипотеза", ""))


async def step(
    session: AsyncSession, jobs: Queue, *, now: datetime, watch: Watch | None = None
) -> Plan:
    """Проход с памятью: исход прошлой автосборки — до новой (новая сборка той же гипотезы
    займёт номер задачи прежней), проход, строка журнала по смене и — при включённой
    автоотправке — тревога F7 (`autobuild_alarm.py`)."""
    memory = watch or autobuild_alarm.WATCH
    memory.saw(*await _last(session, jobs))
    found = await run(session, jobs, now=now)
    SAID.tell(found)
    if autosend.switched_on():
        await memory.see(await autobuild_alarm.stalled(session, found, memory.failed, now))
    return found


async def tick() -> None:
    """Проход по расписанию (`workers/reaper.py`): тумблеры, своя сессия, очередь долгих задач
    продаж (`sales-bulk`) — та же, что у кнопки сборки."""
    if not (sales_cfg.ENABLED and sales_cfg.AUTOBUILD):
        return
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await step(session, sales_bulk_queue(), now=datetime.now(UTC))
    finally:
        await engine.dispose()
