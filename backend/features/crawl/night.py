"""Ночной обход: в полночь доноры обходятся заново сами, за окно не длиннее семи часов.

Сайты растут, а «Переобойти» жмёт человек, и до большинства доноров он не доходит. Ночной круг
ставит их сам: один раз за ночь, в полночь по Москве, в том порядке, где пользы больше.

**Расчёт — до запуска, по настоящей мощности.** Сколько доноров войдёт: обходчики минус занятые
глубокими обходами и те, кого держит сторож нагрузки, настоящая длительность из последних
законченных обходов (`donor_page.crawl_seconds`) и то, что уже ждёт в очереди загрузки: новые
доноры идут первыми. Мощность не вмещает всех — остальные ждут следующей ночи и видны числом.

**Окно жёсткое со стороны выдачи.** Обход начатый доходит сам: обрывать его незачем, он уже
работает. Поэтому выдача кончается раньше конца окна на длину долгого обхода — девяностый
процентиль последних законченных, а не медиана: иначе половина начатых под конец вылезала бы за
окно. После этого срока ночные строки снимаются и это записано.

**Порядок — кому повтор нужнее.** Первыми доноры, дошедшие до потолка страниц: у них точно
осталось необойдённое. Вторыми те, кого не удалось начать, и те, кого ещё не обходили. Третьими
обойдённые не до потолка. Внутри — от самого давнего обхода: так за следующие ночи очередь
доходит до всех, а не крутится на первых в списке.

**Кто** — те же условия, что у кнопки (`targets.choose`), кроме идущих и уже ждущих. Глубоко
обойдённый недавно не ставится (`DEEP_REST_DAYS`): кандидаты донора — по последнему обходу
(`gate.py`), и обычный ночной обход заменил бы найденное глубоким найденным на тысяче страниц.

**Живёт внутри прохода очереди загрузки** (`backlog.feed`): новый цикл в `workers/reaper` пошёл
бы живым в чужих тестах, а строки круга — обычные строки очереди, и выдача у них общая. Обходы
человека идут мимо очереди загрузки, загруженные и глубокие — раньше ночных.

**Один раз за ночь держит база**: уникальная строка круга на дату. Процесс лежал в полночь —
круг начнётся при подъёме, но не позже `CATCH_UP_UNTIL`: начатому позже уже не хватит окна. Дальше
ночь записывается пропущенной. Тревога — только если круг не смог начаться.
Юнитов Ahrefs нет (`units_stop.py`) — круг так же ждёт до конца окна начала, а о них говорит сторож.
"""

from __future__ import annotations

import asyncio
import logging
import math
import statistics
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import crawl as cfg
from backend.features.core.domain import StopReason
from backend.features.core.models.crawl import (
    CrawlBacklogModel,
    CrawlNightModel,
    CrawlRunModel,
)
from backend.features.crawl import deep, targets
from backend.features.crawl.donor_page import ACTIVE, TYPICAL_FALLBACK_SEC, crawl_seconds
from backend.features.crawl.launch import crawl_host
from backend.features.ops.alarms import cause, marked
from backend.shared.alerts import send_alert
from backend.shared.crawl_marks import crawlers_held

logger = logging.getLogger(__name__)

#: Кто велел обойти — у строки очереди загрузки и у обхода: по нему узнаются строки круга.
NIGHT_BY = "ночной обход"

#: Сутки круга — по Москве, как у потолка модели (`core/llm_caps.py`): часы читает человек.
MOSCOW = ZoneInfo("Europe/Moscow")

#: Позже круг не начинается: до утра осталось бы меньше окна, а ночь не сдвигается на день.
CATCH_UP_UNTIL = time(2, 0)

#: Сколько дней глубоко обойдённый донор не ставится обычным ночным обходом (шапка).
DEEP_REST_DAYS = 30

#: Расчёта нет через столько после начала — процесс упал между захватом строки и расчётом.
STALE_CLAIM = timedelta(minutes=10)

#: Пометка тревоги о круге, который не начался: сам он сегодня уже не начнётся.
NOT_STARTED = cause(
    "ночной обход этой ночью не шёл, и доноры не обновились",
    "проверить, работает ли процесс разбора; следующая ночь начнётся сама",
)


class NightStatus(StrEnum):
    """Что с кругом ночи."""

    RUNNING = "running"
    DONE = "done"
    MISSED = "missed"  # процесс не работал до `CATCH_UP_UNTIL`
    FAILED = "failed"  # расчёт упал


class Rank(StrEnum):
    """Очередь круга: кому повтор нужнее. Порядок значений — порядок обхода."""

    LIMIT = "limit"  # дошёл до потолка страниц: у него точно осталось необойдённое
    NO_START = "no_start"  # начать не удалось или ещё не обходили
    PARTIAL = "partial"  # обойдён не до потолка


RANKS = tuple(Rank)


class Add(Protocol):
    """Поставить доноров в очередь загрузки (`backlog.add`): модуль её не импортирует, потому что
    она зовёт его из своего прохода."""

    def __call__(
        self,
        session: AsyncSession,
        hosts: Sequence[str],
        *,
        by: str | None,
        author_id: int | None,
        max_pages: int | None = None,
    ) -> Awaitable[int]: ...


@dataclass(frozen=True, slots=True)
class LastRun:
    """Последний обход донора — то, что о нём нужно кругу."""

    pages: int
    stop_reason: StopReason | None
    status: str
    max_pages: int | None
    created_at: datetime


def rank_of(last: LastRun | None) -> Rank:
    """Очередь донора по последнему обходу. Не обходили — как не сумели начать: о сайте ничего
    не известно, а новый донор важнее того, кого уже обошли."""
    if last is None or last.pages == 0:
        return Rank.NO_START
    if last.stop_reason is StopReason.MAX_PAGES or last.pages >= cfg.MAX_PAGES_PER_DONOR:
        return Rank.LIMIT
    return Rank.PARTIAL


@dataclass(frozen=True, slots=True)
class Power:
    """Что есть на ночь: из чего считается, сколько доноров войдёт."""

    workers: int
    #: Сколько обходчиков займут глубокие обходы и скольких держит сторож нагрузки.
    deep: int
    held: int
    #: Обычных доноров уже в очереди загрузки: они идут первыми.
    ahead: int
    typical_sec: float
    longest_sec: float
    window_sec: float
    #: Сколько выдача отдаёт за проход и как часто — её потолок (`backlog.PER_PASS`).
    per_minute: int

    @property
    def slots(self) -> int:
        return max(self.workers - self.deep - self.held, 0)

    @property
    def usable_sec(self) -> float:
        """Сколько идёт выдача: окно минус длина долгого обхода — начатый последним успеет."""
        return max(self.window_sec - self.longest_sec, 0.0)

    @property
    def capacity(self) -> int:
        """Сколько доноров войдёт: стартов за срок выдачи минус ждущие. Меньше одного — ноль."""
        starts = self.slots * self.usable_sec / self.typical_sec if self.typical_sec > 0 else 0.0
        pace = self.per_minute * self.usable_sec / 60
        return max(math.floor(min(starts, pace)) - self.ahead, 0)

    def estimate_sec(self, count: int) -> float:
        """Сколько займёт круг из `count` доноров, вместе с ждущими: выдача плюс долгий обход."""
        if count <= 0 or self.slots <= 0:
            return 0.0
        return (count + self.ahead) * self.typical_sec / self.slots + self.longest_sec


def longest_of(seconds: Sequence[float], typical: float) -> float:
    """Длина долгого обхода — девяностый процентиль; замеров нет — обычная длина."""
    if not seconds:
        return typical
    ordered = sorted(seconds)
    return max(ordered[math.ceil(len(ordered) * 0.9) - 1], typical)


def order(
    hosts: Sequence[str], last: dict[str, LastRun], *, now: datetime
) -> tuple[list[str], int]:
    """Доноры круга по порядку — и сколько отложено как недавно обойдённых глубоко."""
    rest = now - timedelta(days=DEEP_REST_DAYS)
    rested = 0
    keyed: list[tuple[int, datetime, str]] = []
    for host in hosts:
        run = last.get(host)
        if run is not None and run.max_pages is not None and run.created_at > rest:
            rested += 1
            continue
        when = run.created_at if run is not None else datetime.min.replace(tzinfo=UTC)
        keyed.append((RANKS.index(rank_of(run)), when, host))
    return [host for _, _, host in sorted(keyed)], rested


def _tally(hosts: Sequence[str], last: dict[str, LastRun]) -> dict[str, int]:
    """Сколько доноров в каждой очереди."""
    counts = dict.fromkeys((rank.value for rank in RANKS), 0)
    for host in hosts:
        counts[rank_of(last.get(host)).value] += 1
    return counts


async def _last_runs(session: AsyncSession) -> dict[str, LastRun]:
    """Последний обход каждого донора — хост как его пишет обход."""
    rows = await session.execute(
        select(
            CrawlRunModel.host,
            CrawlRunModel.pages_opened,
            CrawlRunModel.stop_reason,
            CrawlRunModel.status,
            CrawlRunModel.max_pages,
            CrawlRunModel.created_at,
        )
        .distinct(CrawlRunModel.host)
        .order_by(CrawlRunModel.host, CrawlRunModel.id.desc())
    )
    return {
        host: LastRun(pages, stop, status.value, max_pages, created)
        for host, pages, stop, status, max_pages, created in rows.tuples().all()
    }


async def _waiting_hosts(session: AsyncSession) -> set[str]:
    return set((await session.scalars(select(CrawlBacklogModel.host))).all())


async def _usual_ahead(session: AsyncSession) -> int:
    """Обычных доноров в очереди загрузки: глубокие идут на своих местах (`Power.deep`)."""
    found = await session.scalar(
        select(func.count())
        .select_from(CrawlBacklogModel)
        .where(CrawlBacklogModel.max_pages.is_(None))
    )
    return int(found or 0)


async def _power(session: AsyncSession, *, workers: int, per_minute: int) -> Power:
    seconds = await crawl_seconds(session)
    typical = statistics.median(seconds) if seconds else TYPICAL_FALLBACK_SEC
    held = await asyncio.to_thread(crawlers_held)
    long = await deep.going(session) + await deep.waiting(session)
    return Power(
        workers=workers,
        deep=min(long, cfg.DEEP_MAX_RUNNING),
        held=0 if held is None else held.count,
        ahead=await _usual_ahead(session),
        typical_sec=typical,
        longest_sec=longest_of(seconds, typical),
        window_sec=cfg.NIGHT_WINDOW_SEC,
        per_minute=per_minute,
    )


@dataclass(frozen=True, slots=True)
class Plan:
    """Расчёт круга: кого ставим и всё, из чего это вышло."""

    hosts: list[str]
    facts: dict[str, Any]
    issue_sec: float


async def plan(session: AsyncSession, *, workers: int, per_minute: int, now: datetime) -> Plan:
    """Расчёт круга до запуска (шапка)."""
    power = await _power(session, workers=workers, per_minute=per_minute)
    last = await _last_runs(session)
    chosen = {crawl_host(host) for host in (await targets.choose(session, limit=None)).hosts}
    going = {host for host, run in last.items() if run.status in {s.value for s in ACTIVE}}
    free = chosen - going - await _waiting_hosts(session)
    queue, rested = order(sorted(free), last, now=now)
    taken = queue[: power.capacity]
    return Plan(
        hosts=taken,
        issue_sec=power.usable_sec,
        facts={
            "workers": power.workers,
            "deep": power.deep,
            "held": power.held,
            "slots": power.slots,
            "ahead": power.ahead,
            "typical_sec": round(power.typical_sec),
            "longest_sec": round(power.longest_sec),
            "window_sec": round(power.window_sec),
            "capacity": power.capacity,
            "donors": len(queue),
            "left_out": len(queue) - len(taken),
            "rested": rested,
            "estimate_sec": round(power.estimate_sec(len(taken))),
            "ranks": _tally(taken, last),
        },
    )


async def tick(
    session: AsyncSession,
    *,
    workers: int,
    per_minute: int,
    add: Add,
    now: datetime | None = None,
    stop: str | None = None,
) -> None:
    """Шаг круга — из прохода очереди загрузки: закрыть, что пора, и начать ночь, если настала."""
    moment = now or datetime.now(UTC)
    await _close(session, moment)
    if not cfg.NIGHT_ROUND_ENABLED:
        return
    local = moment.astimezone(MOSCOW)
    known = await session.scalar(
        select(CrawlNightModel.id).where(CrawlNightModel.night == local.date())
    )
    if known is not None:
        return
    if local.time() >= CATCH_UP_UNTIL:
        await _note_missed(session, local.date(), moment, stop)
        return
    if stop is None:
        await _start(session, local.date(), moment, workers=workers, per_minute=per_minute, add=add)


async def _claim(session: AsyncSession, night: date, moment: datetime) -> int | None:
    """Строка круга на дату. `None` — её уже завёл другой процесс: ночь одна."""
    found = await session.execute(
        insert(CrawlNightModel)
        .values(
            night=night,
            status=NightStatus.RUNNING.value,
            started_at=moment,
            issue_until=moment + timedelta(seconds=cfg.NIGHT_WINDOW_SEC),
        )
        .on_conflict_do_nothing(index_elements=["night"])
        .returning(CrawlNightModel.id)
    )
    claimed = found.scalar_one_or_none()
    await session.commit()
    return claimed


async def _start(
    session: AsyncSession,
    night: date,
    moment: datetime,
    *,
    workers: int,
    per_minute: int,
    add: Add,
) -> None:
    """Захватить ночь, посчитать и поставить. Захват фиксируется до расчёта: упавший расчёт
    не повторяется каждую минуту с тревогой, а записан и сказан один раз."""
    claimed = await _claim(session, night, moment)
    if claimed is None:
        return
    try:
        found = await plan(session, workers=workers, per_minute=per_minute, now=moment)
        await add(session, found.hosts, by=NIGHT_BY, author_id=None)
    except Exception as exc:
        logger.exception("ночной обход %s: расчёт упал", night)
        await session.rollback()
        await _failed(session, claimed, night, f"расчёт не удался: {exc}"[:500], moment)
        return
    row = await session.get(CrawlNightModel, claimed)
    if row is None:
        raise ValueError(f"строка ночного обхода №{claimed} исчезла посреди начала круга")
    row.plan, row.taken = found.facts, len(found.hosts)
    row.issue_until = moment + timedelta(seconds=found.issue_sec)
    if not found.hosts:
        row.status, row.finished_at = NightStatus.DONE.value, moment
    await session.commit()
    logger.info("ночной обход %s: поставлено %s, расчёт %s", night, len(found.hosts), found.facts)


async def _failed(
    session: AsyncSession, claimed: int, night: date, why: str, moment: datetime
) -> None:
    row = await session.get(CrawlNightModel, claimed)
    if row is not None:
        row.status, row.note, row.finished_at = NightStatus.FAILED.value, why, moment
    await session.commit()
    await send_alert(marked(f"Ночной обход {night:%d.%m} не начался: {why}", NOT_STARTED))


async def _note_missed(session: AsyncSession, day: date, moment: datetime, why: str | None) -> None:
    """Окно начала вышло, а круга нет. `why` — стоп обходов: о нём говорит сторож. Иначе круг шёл и
    прошлой ночью — значит, процесс лежал: об этом тревога. Прошлой строки нет — только включили."""
    yesterday = await session.scalar(
        select(CrawlNightModel.id).where(CrawlNightModel.night == day - timedelta(days=1))
    )
    note = why or (
        f"процесс не работал до {CATCH_UP_UNTIL:%H:%M}"
        if yesterday is not None
        else "круг включён после окна начала"
    )
    found = await session.execute(
        insert(CrawlNightModel)
        .values(night=day, status=NightStatus.MISSED.value, note=note, finished_at=moment)
        .on_conflict_do_nothing(index_elements=["night"])
        .returning(CrawlNightModel.id)
    )
    first = found.scalar_one_or_none() is not None
    await session.commit()
    if first and yesterday is not None and why is None:
        await send_alert(marked(f"Ночной обход {day:%d.%m} пропущен: {note}", NOT_STARTED))


async def _close(session: AsyncSession, moment: datetime) -> None:
    """Открытые круги: срок выдачи вышел — снять остаток; все начатые кончились — закрыть."""
    rows = await session.scalars(
        select(CrawlNightModel).where(CrawlNightModel.status == NightStatus.RUNNING.value)
    )
    for row in rows.all():
        if row.plan is not None:
            await _settle(session, row, moment)
        elif row.started_at is not None and moment - row.started_at > STALE_CLAIM:
            row.status = NightStatus.FAILED.value
            row.note, row.finished_at = "процесс упал до расчёта", moment
    await session.commit()


async def _settle(session: AsyncSession, row: CrawlNightModel, moment: datetime) -> None:
    if row.closed_at is None and row.issue_until is not None and moment >= row.issue_until:
        dropped = await session.execute(
            delete(CrawlBacklogModel)
            .where(CrawlBacklogModel.requested_by == NIGHT_BY)
            .returning(CrawlBacklogModel.id)
        )
        row.dropped, row.closed_at = len(dropped.scalars().all()), moment
        logger.info("ночной обход %s: срок выдачи вышел, снято %s", row.night, row.dropped)
    if await _waiting(session) or await _going(session):
        return
    row.status, row.finished_at = NightStatus.DONE.value, moment
    row.given = await _given(session, row)
    logger.info("ночной обход %s кончился: пошло %s, снято %s", row.night, row.given, row.dropped)


async def _waiting(session: AsyncSession) -> int:
    """Строк круга ещё ждёт в очереди загрузки."""
    found = await session.scalar(
        select(func.count())
        .select_from(CrawlBacklogModel)
        .where(CrawlBacklogModel.requested_by == NIGHT_BY)
    )
    return int(found or 0)


async def _going(session: AsyncSession) -> int:
    """Обходов круга идёт и ждёт обходчика."""
    found = await session.scalar(
        select(func.count())
        .select_from(CrawlRunModel)
        .where(CrawlRunModel.requested_by == NIGHT_BY, CrawlRunModel.status.in_(ACTIVE))
    )
    return int(found or 0)


async def _given(session: AsyncSession, row: CrawlNightModel) -> int:
    """Сколько обходов круг отдал обходчикам: с его начала и до этой минуты."""
    found = await session.scalar(
        select(func.count())
        .select_from(CrawlRunModel)
        .where(CrawlRunModel.requested_by == NIGHT_BY, CrawlRunModel.created_at >= row.started_at)
    )
    return int(found or 0)


@dataclass(frozen=True, slots=True)
class NightLine:
    """Последний круг для экрана: строка и живые числа открытого."""

    row: CrawlNightModel
    given: int
    going: int
    waiting: int


async def latest(session: AsyncSession) -> NightLine | None:
    """Последний круг; кругов не было — `None`."""
    row = await session.scalar(select(CrawlNightModel).order_by(CrawlNightModel.night.desc()))
    if row is None:
        return None
    if row.status != NightStatus.RUNNING.value:
        return NightLine(row, row.given, 0, 0)
    return NightLine(
        row, await _given(session, row), await _going(session), await _waiting(session)
    )
