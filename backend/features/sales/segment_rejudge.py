"""Пересуд прогона после правки описания сегмента: те же сайты и тот же текст — новый вопрос.

Судья сортирует, решает человек (`okf/run-review.md`), а описание сегмента — главный рычаг его
калибровки. Правка описания уже рассуженные сайты не пересуживала: сверить правку было не с чем.
Пересуд — инструмент калибровки, итог — «было → стало» по сайтам прогона.

- **Без новой выдачи**: сайты этого прогона и текст, который видел прошлый суд (`site_text`), —
  разница от правки описания, а не от сайта, успевшего поменяться. Сайт без сохранённого текста
  судится как обычно: выдача и главная.
- **Мимо кэша вердиктов**: правка описания и есть вопрос заново; новые вердикты ложатся в кэш.
- **Решения человека не пересуживаются** — ни принятые до пересуда, ни пришедшие посреди него.
- **Сайт с лидом**: новая полоса только показывается («стало бы»), полоса и лид не трогаются.
- **«Ещё не судили» не трогает** — их судит продолжение прогона; **известные по прошлым
  прогонам** сайты гипотезы (заведены не этим прогоном) не трогает.
- **Смета до запуска** (`segment_rejudge_estimate.py`), отказ словами, пока идёт прогон или
  пересуд (`api/sales/segment_sites.py`).
- **Расход** — строкой `sales_segment_judge`, потолок модели — тот же, что у судьи (`segment_window`).
- **Ход** («пересуд: X из Y») и итог — в `run.report["rejudge"]`, без миграции
  (`segment_rejudge_report.py`).

Повтор задачи (сбой базы, перезапуск воркера) продолжает тот же пересуд: снимок полос сделан до
первого вердикта, и пересуженный сайт второй раз к модели не идёт.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from rq import get_current_job
from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import judge as judge_cfg
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config import storage
from backend.config.judge import JudgeMode
from backend.config.startup_checks import check_storage
from backend.features.core.models.domain import DomainModel
from backend.features.runs.failures import described, is_permanent
from backend.features.runs.reasons import explained
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_estimate import NO_SEGMENT, SALES_OFF
from backend.features.sales.segment_judge import PROMPT_VERSION, SegmentClient, segment_digest
from backend.features.sales.segment_judging import cut, stoplisted
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.segment_rejudge_report import (
    DONE,
    GOING,
    HUMAN_KEPT,
    KEY,
    QUEUED,
    SPENT,
    STOPPED,
    add_totals,
    block_of,
    summary,
    write_block,
)
from backend.features.sales.segment_window import (
    SPLIT_TOKENS,
    Ask,
    JudgeTally,
    Landing,
    Site,
    judge_sites,
    land,
)
from backend.shared.logs import setup_logging
from backend.shared.net.url_guard import guarded_client
from backend.shared.queue import remember_job_error

logger = logging.getLogger(__name__)

Candidate = SalesSegmentCandidateModel
Run = SalesSegmentRunModel

#: Путь задачи строкой: очередь импортирует её в воркере долгих задач продаж.
REJUDGE_JOB = "backend.features.sales.segment_rejudge.rejudge_segment"
#: Числа пересуда, которые копятся от попытки к попытке задачи: токены — всего и раздельно
#: (ввод, вывод, рассуждение, «раздельно неизвестно»), «модель не ответила», сбои.
_SUMMED = ("tokens", *SPLIT_TOKENS, "unanswered", "failed")
#: Прогон шёл с выключенным судьёй: модель его сайтов не судила, сравнивать не с чем.
JUDGE_WAS_OFF = (
    "в этом прогоне судья был выключен — пересуживать некому: каждый сайт здесь решает человек"
)


def rejudge_job_id(run_id: int) -> str:
    """Номер задачи пересуда — от прогона: один пересуд прогона за раз."""
    return f"sales-segment-rejudge-{run_id}"


class SalesOffError(RuntimeError):
    """Модуль продаж выключили после постановки: пересуд не тратит. Повтор это не исправит."""

    permanent = True


class UnknownRunError(LookupError):
    """Прогона нет: удалён или номер чужой. Повтор это не исправит."""

    permanent = True


# --- пересуд -------------------------------------------------------------------------------------


def _open() -> ColumnElement[bool]:
    """Полоса ложится, пока сайт не решил человек и из него не завёлся лид."""
    return and_(Candidate.decided_by.is_distinct_from(Decider.HUMAN), Candidate.lead_id.is_(None))


async def snapshot(session: AsyncSession, run_id: int) -> dict[str, Any]:
    """Полосы сайтов прогона до пересуда: кого пересудить (не человек, не «ещё не судили»), сколько
    решил человек и сколько ещё не судили."""
    rows = await session.execute(
        select(Candidate.id, Candidate.lane, Candidate.decided_by)
        .where(Candidate.run_id == run_id)
        .order_by(Candidate.id)
    )
    sites: dict[str, list[str | None]] = {}
    human = pending = 0
    for candidate_id, lane, decided_by in rows.tuples().all():
        if decided_by == Decider.HUMAN:
            human += 1
        elif lane == Lane.PENDING:
            pending += 1
        else:
            sites[str(candidate_id)] = [lane, None]
    return {"sites": sites, "kept_human": human, "pending": pending}


async def _todo(session: AsyncSession, block: dict[str, Any]) -> list[Site]:
    """Сайты снимка, которых пересуд ещё не прошёл: повтор задачи продолжает с места."""
    left = [int(key) for key, (_, now) in block["sites"].items() if now is None]
    if not left:
        return []
    rows = await session.execute(
        select(Candidate, DomainModel.host)
        .join(DomainModel, DomainModel.id == Candidate.domain_id)
        .where(Candidate.id.in_(left))
        .order_by(Candidate.id)
    )
    return [Site.of(candidate, host) for candidate, host in rows.tuples().all()]


@dataclass(slots=True)
class Rejudged:
    """Куда пересуд кладёт вердикты (`segment_window.Sink`): полоса — под условием `_open`, у
    сайта с лидом — «стало бы» в отчёт; ход — в отчёт на каждом чекпоинте."""

    run_id: int
    block: dict[str, Any]
    tally: JudgeTally
    #: Числа прежних попыток той же задачи: повтор их не теряет.
    before: dict[str, int] = field(default_factory=dict)
    taken: list[tuple[Site, Landing]] = field(default_factory=list)

    def take(self, site: Site, found: Landing) -> None:
        self.taken.append((site, found))

    async def flush(self, session: AsyncSession, moment: datetime) -> None:
        for site, found in self.taken:
            self.block["sites"][str(site.candidate_id)][1] = await self._settle(
                session, site, found, moment
            )
        self.taken.clear()
        earlier = {key: int(self.block.get(key, 0)) for key in SPENT}
        for key in _SUMMED:
            self.block[key] = self.before.get(key, 0) + int(getattr(self.tally, key))
        await write_block(session, self.run_id, self.block)
        # В сумму всех пересудов прогона — токены с прошлого чекпоинта (`segment_rejudge_report`).
        spent = {key: self.block[key] - earlier[key] for key in SPENT}
        await add_totals(session, self.run_id, spent=spent)

    async def _settle(
        self, session: AsyncSession, site: Site, found: Landing, moment: datetime
    ) -> str:
        """Полоса сайта после пересуда — или «стало бы» у сайта с лидом, или «решил человек».
        Условие строки (`_open`) — единственная защита: лид мог завестись и посреди пересуда."""
        if await land(session, site.candidate_id, found, moment, guard=_open()):
            return found.lane.value
        decided_by = await session.scalar(
            select(Candidate.decided_by).where(Candidate.id == site.candidate_id)
        )
        if decided_by == Decider.HUMAN:
            return HUMAN_KEPT
        would = {"lane": found.lane.value, "reason": found.reason, "quote": found.quote}
        self.block.setdefault("would", {})[str(site.candidate_id)] = would
        return found.lane.value


async def _cut_stoplisted(
    session: AsyncSession, sites: Sequence[Site], sink: Rejudged, moment: datetime
) -> list[Site]:
    """Стоп-лист — до модели, как у судьи; вернуть тех, кого судить окном."""
    closed = await stoplisted(session, [site.domain_id for site in sites], moment)
    for site in sites:
        if (why := closed.get(site.domain_id)) is not None:
            sink.take(site, cut(why))
            sink.tally.by_rule += 1
    return [site for site in sites if site.domain_id not in closed]


@dataclass(frozen=True, slots=True)
class RejudgeDeps:
    """Модель судьи и клиент главных — для сайтов без сохранённого текста."""

    judge: SegmentClient
    home: httpx.AsyncClient | None


def refusal_of(run: Run, segment: str) -> str | None:
    """Почему пересуд этого прогона невозможен по существу — словами; `None` — возможен."""
    if JudgeMode(run.mode) is JudgeMode.OFF:
        return JUDGE_WAS_OFF
    if not segment:
        return NO_SEGMENT
    return None


async def segment_of(session: AsyncSession, run: Run) -> str:
    """Описание сегмента гипотезы прогона — нынешнее: его и спросит пересуд."""
    hypothesis = await session.get(SalesHypothesisModel, run.hypothesis_id)
    return (hypothesis.description or "").strip() if hypothesis is not None else ""


async def _start(session: AsyncSession, run: Run, segment: str, moment: datetime) -> dict[str, Any]:
    """Первый заход задачи: снимок полос до первого вердикта — с ним повтор продолжит с места."""
    block = block_of(run.report)
    if block.get("state") == QUEUED:
        block |= await snapshot(session, run.id)
        block |= {
            "state": GOING,
            "started_at": moment.isoformat(),
            "digest": segment_digest(segment),
            "prompt_version": PROMPT_VERSION,
            "mode": run.mode,
        }
        await write_block(session, run.id, block)
        await add_totals(session, run.id, started=True)
        await session.commit()
    return block


async def rejudge(
    session: AsyncSession, run_id: int, deps: RejudgeDeps, *, now: datetime | None = None
) -> dict[str, Any]:
    """Пересудить прогон с места, где пересуд стоит. Возвращает итог пересуда (`summary`)."""
    moment = now or datetime.now(UTC)
    run = await session.get(Run, run_id, populate_existing=True)
    if run is None:
        raise UnknownRunError(f"прогона сбора №{run_id} нет")
    if block_of(run.report).get("state") not in (QUEUED, GOING):
        return summary(run.report) or {}  # пересуд кончился или его не просили — продолжать нечего
    segment = await segment_of(session, run)
    if (why := refusal_of(run, segment)) is not None:
        return await stop(session, run_id, f"пересуд не выполнен: {why}", moment)
    block = await _start(session, run, segment, moment)
    tally = JudgeTally()
    before = {key: int(block.get(key, 0)) for key in _SUMMED}
    sink = Rejudged(run_id, block, tally, before)
    sites = await _cut_stoplisted(session, await _todo(session, block), sink, moment)
    ask = Ask(
        segment,
        JudgeMode(run.mode),
        deps.judge,
        deps.home,
        moment,
        cached=False,
        stored_text=True,
        letters=run.language,
    )
    await judge_sites(session, sites, ask, sink, tally)
    await sink.flush(session, moment)
    block |= {
        "state": STOPPED if tally.stopped else DONE,
        "finished_at": datetime.now(UTC).isoformat(),
        "error": None if tally.stopped is None else f"пересуд остановлен: {tally.stopped}",
    }
    await write_block(session, run_id, block)
    await session.commit()
    logger.info("сбор продаж: пересуд прошёл", extra={"run": run_id, "state": block["state"]})
    return summary({KEY: block}) or {}


async def stop(session: AsyncSession, run_id: int, words: str, moment: datetime) -> dict[str, Any]:
    """Пересуд остановлен с причиной словами — в отчёт прогона; сделанное остаётся. Кончившийся
    пересуд (или пересуда нет вовсе) отказ чужой задачи не переписывает."""
    run = await session.get(Run, run_id, populate_existing=True)
    block = block_of(run.report if run is not None else None)
    if block.get("state") not in (QUEUED, GOING):
        return summary({KEY: block}) or {}
    block |= {"state": STOPPED, "error": words, "finished_at": moment.isoformat()}
    block.setdefault("sites", {})
    await write_block(session, run_id, block)
    await session.commit()
    logger.warning("сбор продаж: пересуд остановлен", extra={"run": run_id, "error": words})
    return summary({KEY: block}) or {}


# --- задача очереди --------------------------------------------------------------------------------


async def run_once(run_id: int) -> dict[str, Any]:
    """Один проход пересуда: своя сессия и свои клиенты. Постоянный отказ — причиной в отчёте."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if not sales_cfg.ENABLED:
            raise SalesOffError(SALES_OFF)
        async with AsyncExitStack() as stack:
            # Модель — со сроком судьи (`LLM_TIMEOUT_S`), главные — с защитой исходящих: адреса
            # пришли из выдачи (`segment_jobs._with_clients`).
            model = await stack.enter_async_context(httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S))
            home = await stack.enter_async_context(
                guarded_client(timeout=judge_cfg.HOME_TIMEOUT_SEC)
            )
            session = await stack.enter_async_context(factory())
            deps = RejudgeDeps(SegmentClient(model), home if judge_cfg.HOME_CHECK else None)
            return await rejudge(session, run_id, deps)
    except Exception as exc:
        if is_permanent(exc):
            async with factory() as session:
                await stop(
                    session, run_id, f"пересуд не выполнен: {explained(exc)}", datetime.now(UTC)
                )
        raise
    finally:
        await engine.dispose()


def rejudge_segment(run_id: int) -> dict[str, Any]:
    """Задача очереди: пересудить прогон гипотезы. Писем не пишет, выдачу не покупает."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(run_once(run_id))
    except Exception as exc:
        if is_permanent(exc):
            logger.warning(
                "сбор продаж: пересуд не выполнен", extra={"run": run_id, "error": described(exc)}
            )
            return {"error": explained(exc), "permanent": True}
        if (job := get_current_job()) is not None:
            remember_job_error(job.id, described(exc))
        raise
