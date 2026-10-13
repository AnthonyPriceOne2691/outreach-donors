"""Автосборка очереди продаж: писем в очереди хватает на ближайшие сутки — без кнопки.

**Сколько нужно.** Сутки ящиков продаж — сумма их дневных капов с разгоном
(`senders.warmup_state`): столько первых писем ящики отправят за день. В очереди меньше — не
хватает разницы; столько и собирается, но не больше предела сборки (`LIMIT_MAX`).

**Та же сборка, что у кнопки** (`queue_start.start`, задача `queue_jobs.build_sales_queue`):
одна сборка гипотезы за раз, потолок модели продаж — до каждого письма, остановка после трёх
лидов подряд вне коридора — внутри сборки. Проход сверх того:
- не ставит сборку, пока идёт любая сборка продаж: недостача считается по письмам, уже стоящим
  в очереди, и вторая сборка разом собрала бы ту же недостачу ещё раз;
- не ставит, пока потолок модели продаж набран: сборка встала бы на первом письме;
- не собирает гипотезу, чья прошлая сборка встала на серии писем вне коридора, пока человек не
  соберёт её кнопкой, поправив цепочку: иначе каждый проход тратил бы модель на те же лиды впустую.

**Какие гипотезы.** Признака «активна» у гипотезы нет — собираются те, у кого есть полная
цепочка хоть на одном языке и лиды `ready` без письма.

**Недостача — по очереди.** Проход отдаёт всю недостачу одной гипотезе: той, чья сборка (кнопкой
или сама) начиналась давнее всех по журналу; ни разу не собранная — первой. Не хватило её лидов —
следующий проход посчитает недостачу заново и отдаст следующей. «Поровну» требовало бы нескольких
сборок разом или счёта долей между проходами, «по доле ready» отдавало бы всё большой базе, и
малая гипотеза не получала бы писем вовсе.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import DuplicateJobError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import sales as sales_cfg
from backend.config import storage
from backend.features.access.repository import AccessRepository
from backend.features.core import usage
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.outreach import SenderModel
from backend.features.letters.repository import LetterRepository
from backend.features.outreach.senders import warmup_state
from backend.features.sales import chain, chain_text, connection, corridor
from backend.features.sales.models import (
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.queue_start import LIMIT_MAX, RUNNING, build_job_id, start
from backend.features.sales.usage_cap import sales_cap
from backend.shared.queue import sales_queue

logger = logging.getLogger(__name__)

#: Действие в журнале: сборку поставил проход, а не человек (у кнопки — «сборка очереди продаж»).
AUTO = "автосборка очереди продаж"
NO_CANDIDATES = "нет гипотезы с полной цепочкой и лидами, готовыми к письму"
QUEUE_SILENT = "очередь задач не отвечает — идёт ли сборка, сейчас не узнать"
STREAK = (
    "прошлые сборки гипотез встали: шаблон первого письма не даёт писем в коридоре отличия — "
    "поправьте цепочку и соберите очередь кнопкой, дальше автосборка подхватит"
)


@dataclass(frozen=True, slots=True)
class Plan:
    """Хватает ли очереди на сутки и кого собрать — или почему не собирается."""

    #: Сумма дневных капов ящиков продаж с разгоном.
    need: int
    #: Первых писем продаж в очереди.
    have: int
    hypothesis_id: int | None = None
    name: str = ""
    why: str | None = None

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


async def candidates(session: AsyncSession) -> list[tuple[int, str]]:
    """Гипотезы с лидами `ready` без письма и полной цепочкой хоть на одном языке — по номеру."""
    rows = await session.execute(
        select(SalesHypothesisModel.id, SalesHypothesisModel.name)
        .join(SalesLeadModel, SalesLeadModel.hypothesis_id == SalesHypothesisModel.id)
        .outerjoin(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(SalesLeadModel.status == LeadStatus.READY, SalesThreadModel.thread_id.is_(None))
        .group_by(SalesHypothesisModel.id, SalesHypothesisModel.name)
        .order_by(SalesHypothesisModel.id)
    )
    return [item for item in rows.tuples().all() if await _has_chain(session, item[0])]


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
        job = jobs.fetch_job(build_job_id(hypothesis_id))
        if job is not None and job.get_status() in RUNNING:
            return name
    return None


def _streak_stopped(jobs: Queue, hypothesis_id: int) -> bool:
    """Прошлая сборка гипотезы встала на серии писем вне коридора — и человек её ещё не пересобрал."""
    job = jobs.fetch_job(build_job_id(hypothesis_id))
    result = job.latest_result() if job is not None else None
    report = result.return_value if result is not None else None
    return isinstance(report, dict) and report.get("stopped") == corridor.streak_stop()


async def plan(session: AsyncSession, jobs: Queue, *, now: datetime) -> Plan:
    """Хватает ли очереди и кого собрать — без записи и без постановки."""
    have = await LetterRepository(session).queued_count(stage=Stage.SALES)
    base = Plan(await needed(session, now), have)
    if not base.short:
        return base
    found = await _choose(session, jobs, now)
    if isinstance(found, str):
        return replace(base, why=found)
    return replace(base, hypothesis_id=found[0], name=found[1])


async def _choose(session: AsyncSession, jobs: Queue, now: datetime) -> tuple[int, str] | str:
    """Чью сборку поставить — или почему ничью, словами."""
    why = await _blocked(session, now)
    if why is not None:
        return why
    found = await candidates(session)
    if not found:
        return NO_CANDIDATES
    every = await session.execute(select(SalesHypothesisModel.id, SalesHypothesisModel.name))
    try:
        busy = _busy(jobs, list(every.tuples().all()))
        stuck = {hypothesis for hypothesis, _ in found if _streak_stopped(jobs, hypothesis)}
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
        return str(exc)
    return None


async def run(session: AsyncSession, jobs: Queue, *, now: datetime) -> Plan:
    """Один проход: не хватает — та же сборка, что у кнопки, с пределом недостачи и записью в
    журнал без автора; хватает или нельзя — план со словами."""
    found = await plan(session, jobs, now=now)
    if found.hypothesis_id is None:
        return found
    try:
        job = start(jobs, found.hypothesis_id, found.limit, auto=True)
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


async def tick() -> None:
    """Проход по расписанию (`workers/reaper.py`): тумблеры, своя сессия, очередь продаж."""
    if not (sales_cfg.ENABLED and sales_cfg.AUTOBUILD):
        return
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            SAID.tell(await run(session, sales_queue(), now=datetime.now(UTC)))
    finally:
        await engine.dispose()
