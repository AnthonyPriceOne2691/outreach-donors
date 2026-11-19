"""Обход 24/7: доноры обходятся частями без остановки, пока не пройдены все страницы всех сайтов.

Заказ 17.11.2026: обход доноров идёт постоянно, кнопкой «старт / пауза». Сначала — сайты, которых
ни разу не обходили, каждый до тысячи страниц; когда их нет — по 10 000 страниц каждого; сайт, у
которого на них меньше X % страниц с платной ссылкой, получает пометку «мало рекламодателей» и дальше
пропускается (`always_switch`, `always_depth.weak_of`); остальные — ещё по 10 000, циклами, пока не
кончатся все. Тогда — сообщение в Telegram.

**Порядок** (`plan`): (1) ни разу не обходившиеся — часть с обычным потолком в тысячу страниц;
(2) первые 10 000 не пройдены — часть до 10 000 всего, с чекпоинта цепочки; (3) дальше — часть «ещё
10 000», сначала доходным: по доле продающих страниц, по убыванию (решение владельца 17.11.2026).
Пройдены ли первые 10 000 — `always_depth.first_done_at`: часть, упёршаяся в их потолок, проходит
их, даже если разных страниц в цепочке легло меньше.
Слабые пропускаются, а с «переобходом слабых» идут после сильных; пройденные до конца — нет. Внутри
очереди — порядок отбора (`targets.choose`: свежая цена первой).

**Когда новых частей нет** (`tick`): выключен рубильник или выключатель; ночное окно — с полуночи по
Москве, пока строка ночного круга не кончилась, и не раньше семи, если ночной обход включён (ночь
остаётся как есть — `night.py`, решение владельца 17.11.2026); кончились юниты Ahrefs
(`units_stop.py`); в очереди загрузки ждут обходы людей — они первыми. Иначе — только в свободные
обходчики и только когда в очереди обходов никто не ждёт, не больше `PER_PASS` за проход и не больше
`CRAWL_ALWAYS_SLOTS` частей разом: обход, поставленный человеком, не встаёт за ними.

**Кто** — как у ночного круга (`targets.choose` без каталогов), кроме тех, чей обход идёт или ждёт.
Части ставятся той же постановкой, что у кнопок и очереди загрузки (`launch`), от имени `ALWAYS_BY`;
номер части — в строке обхода. Как часть идёт и кончается — `always_part.py`.

**Не пустил и впустую** (`always_last.py`): последняя часть не открыла нового, сайт закрылся или
цепочка не выросла, а потолок следующей тот же — донор ждёт, пока его снова обойдут иначе (ночью
или человек). Иначе закрытый сайт или адреса, уводящие на открытое, ставились бы каждую минуту.

**Передышка после обычного обхода** (`CRAWL_ALWAYS_REST_MIN`, 19.11.2026): часть не начинается на
сайте, пока с конца его обычного обхода — ночного или поставленного человеком — не прошло два часа.
Утром 19.11 все шесть частей, вставших по доле отказов, начались в 07:03–07:36 на сайтах, которые
ночной обход только что прошёл: сайт отсекает вторую нагрузку подряд. Такой донор не пройден и не
ждёт после отказа — он в очереди (`Survey.remaining`), просто часть ему выдаётся позже (`plan`).

**Пройдено всё** (`_say_done`): включён, частей не идёт, а у каждого донора — сайт кончился, он
слабый или ждёт после отказа. Сообщение одно на эпоху (`CrawlAlwaysSignalModel`): следующее — только
после того, как появилась новая работа и она тоже кончилась. Повтор после отказа новой работой не
считается: иначе закрытый сайт будил бы сообщение каждую ночь.

Живёт внутри прохода очереди загрузки (`backlog.feed`) сразу после ночного круга — как и он.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import crawl as crawl_cfg
from backend.config import crawl_always as cfg
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction
from backend.features.core.models.crawl import (
    CrawlAlwaysSignalModel,
    CrawlBacklogModel,
    CrawlNightModel,
    CrawlRunModel,
)
from backend.features.crawl import always_depth, always_switch, deep, targets
from backend.features.crawl.always_last import LastPart, last_parts, rests
from backend.features.crawl.always_part import night_hours
from backend.features.crawl.always_switch import Switch
from backend.features.crawl.donor_page import ACTIVE
from backend.features.crawl.gate_union import usable
from backend.features.crawl.launch import Enqueue, JobId, crawl_host, launch, queue_counts
from backend.features.crawl.night import MOSCOW, NIGHT_BY, NightStatus
from backend.features.ops.alarms import marked, no_cause
from backend.shared.alerts import send_alert
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

#: Кто велел обойти — у строки обхода части: по нему её видно в «Последних обходах».
ALWAYS_BY = "обход 24/7"

#: Сколько частей за проход (раз в минуту) — как у очереди загрузки (`backlog.PER_PASS`): сторож
#: нагрузки и так пускает новый обход не чаще раза в двадцать секунд.
PER_PASS = 2

#: Пометка сообщения «пройдено всё»: это итог, а не поломка.
DONE = no_cause("обход 24/7 прошёл всё, что мог, и новые части начнёт сам")


class Stage(StrEnum):
    """Где донор в обходе 24/7. Порядок первых трёх — порядок выдачи."""

    NEW = "new"  # ни разу не обходили
    FIRST = "first"  # цепочка ещё не прошла первые 10 000 страниц
    DEEPER = "deeper"  # идёт дальше 10 000
    WEAK = "weak"  # пропускается: мало рекламодателей
    EXHAUSTED = "exhausted"  # сайт пройден до конца


#: Очереди, в которых донору есть что обходить, — по порядку выдачи.
GOING = (Stage.NEW, Stage.FIRST, Stage.DEEPER)


@dataclass(frozen=True, slots=True)
class Donor:
    """Донор обхода 24/7 — где он и можно ли дать ему часть сейчас."""

    host: str
    stage: Stage
    pages: int
    yield_pct: float | None
    weak: bool
    #: Обход донора идёт или ждёт обходчика — любой: второго база не заведёт.
    busy: bool
    #: Последняя часть не дала нового, а другим обходом сайт с тех пор не открывали.
    resting: bool
    #: Последняя часть не дала нового: следующая — повтор, а не новая работа.
    retry: bool
    #: Номер следующей части и место в порядке отбора.
    part: int
    rank: int
    #: Пометку решает порог: первые 10 000 пройдены, доля известна, руками не ставили.
    scored: bool = False
    #: Последний пересчёт кандидатов не читал старые части цепочки — потолок ссылок в памяти.
    frozen: bool = False
    #: Обычный обход сайта кончился меньше `CRAWL_ALWAYS_REST_MIN` минут назад — сайт отдыхает.
    cooling: bool = False


@dataclass(frozen=True, slots=True)
class Survey:
    """Доноры обхода 24/7 и сколько частей идёт — для выдачи и экрана."""

    donors: list[Donor]
    #: Частей 24/7 идёт и ждёт обходчика.
    running: int
    #: Доноров-каталогов: их обход 24/7, как и ночной, не обходит (`targets.choose`).
    catalogs: int = 0

    def remaining(self) -> list[Donor]:
        """Кому ещё есть что обходить: не пройден, не слабый и не ждёт после отказа."""
        return [donor for donor in self.donors if donor.stage in GOING and not donor.resting]

    def counts(self) -> dict[str, int]:
        """Сколько доноров в каждой очереди, сколько частей идёт и сколько всего."""
        found = {stage.value: 0 for stage in Stage}
        for donor in self.donors:
            found[donor.stage.value] += 1
        return {**found, "running": self.running, "total": len(self.donors)}

    def resting(self) -> int:
        """Скольким есть что обходить, но они ждут после отказа (шапка, «Не пустил»)."""
        return sum(1 for donor in self.donors if donor.stage in GOING and donor.resting)


@dataclass(frozen=True, slots=True)
class Spec:
    """Часть к выдаче: кому, какой номер, потолок страниц всей цепочки и повтор ли это."""

    host: str
    part: int
    max_pages: int | None
    retry: bool


@dataclass(slots=True)
class _Known:
    """Что база знает об обходах доноров — одним набором запросов на проход."""

    depths: dict[str, always_depth.Facts] = field(default_factory=dict)
    crawled: set[str] = field(default_factory=set)
    busy: set[str] = field(default_factory=set)
    running: int = 0
    last: dict[str, LastPart] = field(default_factory=dict)
    #: Последний пригодный обход не из цепочки, открывший страницы, — номер.
    regular: dict[str, int] = field(default_factory=dict)
    #: Сайты, чей обычный обход кончился меньше `CRAWL_ALWAYS_REST_MIN` минут назад (шапка).
    cooling: set[str] = field(default_factory=set)


async def _known(session: AsyncSession, now: datetime) -> _Known:
    known = _Known()
    # Колонками, без чекпоинта: выдача живёт в процессе разбора с потолком памяти (`always_depth`).
    known.depths = await always_depth.facts(session)
    known.crawled = set((await session.scalars(select(CrawlRunModel.host).distinct())).all())
    active = await session.execute(
        select(CrawlRunModel.host, CrawlRunModel.depth_part).where(CrawlRunModel.status.in_(ACTIVE))
    )
    for host, part in active.tuples().all():
        known.busy.add(host)
        known.running += part is not None
    known.last = await last_parts(session)
    regular = await session.execute(
        select(CrawlRunModel.host, func.max(CrawlRunModel.id))
        .where(usable(), CrawlRunModel.depth_part.is_(None), CrawlRunModel.pages_opened > 0)
        .group_by(CrawlRunModel.host)
    )
    known.regular = {host: int(newest) for host, newest in regular.tuples().all()}
    since = now - timedelta(minutes=cfg.REST_MIN)
    cooling = await session.scalars(
        select(CrawlRunModel.host)
        .distinct()
        .where(CrawlRunModel.depth_part.is_(None), CrawlRunModel.finished_at >= since)
    )
    known.cooling = set(cooling.all())
    return known


def _stage(
    host: str, depth: always_depth.Facts | None, weak: bool, known: _Known, switch: Switch
) -> Stage:
    if depth is not None and depth.exhausted_at is not None:
        return Stage.EXHAUSTED
    if weak and not switch.recrawl_weak:
        return Stage.WEAK
    if host not in known.crawled:
        return Stage.NEW
    return Stage.DEEPER if always_depth.past_first(depth) else Stage.FIRST


def _ceiling(stage: Stage, pages: int) -> int | None:
    """Потолок следующей части — страниц всей цепочки: новый — обычный обход, первые — до 10 000,
    дальше — ещё 10 000 к пройденному."""
    step = always_depth.first_step()
    if stage is Stage.NEW:
        return None
    if stage is Stage.FIRST:
        return step
    return pages + step


def _donor(host: str, rank: int, known: _Known, switch: Switch) -> Donor:
    depth = known.depths.get(host)
    weak, _ = always_depth.weak_of(depth, switch.threshold_pct)
    stage = _stage(host, depth, weak, known, switch)
    pages = depth.pages if depth is not None else 0
    last = known.last.get(host)
    retry, resting = rests(last, _ceiling(stage, pages), known.regular.get(host, 0))
    return Donor(
        host=host,
        stage=stage,
        pages=pages,
        yield_pct=depth.yield_pct if depth is not None else None,
        weak=weak,
        busy=host in known.busy,
        resting=resting,
        retry=retry,
        part=(last.number if last is not None else 0) + 1,
        rank=rank,
        scored=always_depth.by_threshold(depth),
        frozen=depth is not None and depth.frozen_parts > 0,
        cooling=host in known.cooling,
    )


async def survey(session: AsyncSession, switch: Switch, now: datetime | None = None) -> Survey:
    """Доноры обхода 24/7 — отбором ночного круга — и где каждый; `now` — от него считается
    передышка после обычного обхода (шапка)."""
    picked = await targets.choose(session, limit=None, skip_catalogs=True)
    hosts = list(dict.fromkeys(crawl_host(host) for host in picked.hosts))
    known = await _known(session, now or datetime.now(UTC))
    donors = [_donor(host, rank, known, switch) for rank, host in enumerate(hosts)]
    return Survey(donors=donors, running=known.running, catalogs=len(picked.catalog))


def _order(donor: Donor) -> tuple[int, bool, bool, float, int]:
    """Очередь выдачи: новые, первые 10 000, дальше; слабые — после сильных своей очереди; дальше
    10 000 — доходные первыми, без доли — последними; иначе — порядок отбора."""
    known = donor.yield_pct is not None
    return GOING.index(donor.stage), donor.weak, not known, -(donor.yield_pct or 0.0), donor.rank


def _spec(donor: Donor) -> Spec:
    """Часть к выдаче — с потолком своей очереди (`_ceiling`)."""
    pages = _ceiling(donor.stage, donor.pages)
    return Spec(host=donor.host, part=donor.part, max_pages=pages, retry=donor.retry)


def plan(found: Survey) -> list[Spec]:
    """Части к выдаче по порядку (шапка): кому есть что обходить, чей обход сейчас не идёт и чей
    сайт не отдыхает после обычного обхода."""
    ready = [donor for donor in found.remaining() if not (donor.busy or donor.cooling)]
    return [_spec(donor) for donor in sorted(ready, key=_order)]


async def at_night(session: AsyncSession, moment: datetime) -> bool:
    """Ночное окно (шапка): по часам — и пока строка ночного круга этой даты идёт."""
    if not crawl_cfg.NIGHT_ROUND_ENABLED:
        return False
    if night_hours(moment):
        return True
    status = await session.scalar(
        select(CrawlNightModel.status).where(
            CrawlNightModel.night == moment.astimezone(MOSCOW).date()
        )
    )
    return status == NightStatus.RUNNING.value


async def humans_waiting(session: AsyncSession) -> bool:
    """В очереди загрузки ждут обходы людей, которые проход может выдать: глубокие — только если
    их предел не выбран (`backlog._pick`). Строки ночного круга — не люди."""
    query = select(CrawlBacklogModel.id).where(
        CrawlBacklogModel.requested_by.is_distinct_from(NIGHT_BY)
    )
    if crawl_cfg.DEEP_MAX_RUNNING - await deep.going(session) <= 0:
        query = query.where(CrawlBacklogModel.max_pages.is_(None))
    return await session.scalar(query.limit(1)) is not None


async def _parts_running(session: AsyncSession) -> int:
    found = await session.scalar(
        select(func.count())
        .select_from(CrawlRunModel)
        .where(CrawlRunModel.depth_part.is_not(None), CrawlRunModel.status.in_(ACTIVE))
    )
    return int(found or 0)


async def _open(session: AsyncSession, moment: datetime, stop: str | None) -> Switch | None:
    """Выключатель — если окна выдачи открыты (шапка, «Когда новых частей нет»); иначе пусто."""
    if not cfg.ENABLED or stop is not None:
        return None
    switch = await always_switch.state(session)
    if not switch.enabled or await at_night(session, moment) or await humans_waiting(session):
        return None
    return switch


async def _room(session: AsyncSession, workers: int) -> tuple[int, int]:
    """Сколько частей выдать сейчас — в свободные обходчики, когда никто не ждёт, в пределах
    частей разом и прохода, — и сколько их уже идёт."""
    running, queued = await queue_counts(session)
    parts = await _parts_running(session)
    room = 0 if queued else min(workers - running, PER_PASS, cfg.SLOTS - parts)
    return room, parts


async def tick(
    session: AsyncSession,
    *,
    workers: int,
    enqueue: Enqueue,
    job_id: JobId,
    now: datetime | None = None,
    stop: str | None = None,
) -> list[str]:
    """Шаг обхода 24/7 — из прохода очереди загрузки: выдать части свободным обходчикам или
    сказать «пройдено всё». Ответ — кому поставлена часть. Фиксирует постановка (`launch`) и
    сигнал; запись журнала — вызывающий."""
    moment = now or datetime.now(UTC)
    switch = await _open(session, moment, stop)
    if switch is None:
        return []
    room, parts = await _room(session, workers)
    if room <= 0 and parts > 0:
        return []
    found = await survey(session, switch, moment)
    if found.donors and not found.remaining() and found.running == 0:
        await _say_done(session, found, moment)
        return []
    return await _issue(session, plan(found)[: max(room, 0)], moment, (enqueue, job_id))


async def _issue(
    session: AsyncSession,
    specs: Sequence[Spec],
    moment: datetime,
    queue: tuple[Enqueue, JobId],
) -> list[str]:
    """Поставить части той же постановкой, что у кнопок: строка обхода, номер задачи, задача. Новая
    работа после «пройдено всё» открывает новую эпоху сигнала; повтор после отказа — нет."""
    enqueue, job_id = queue
    issued: list[str] = []
    fresh = False
    for spec in specs:
        done = await launch(
            session,
            [spec.host],
            by=ALWAYS_BY,
            job_id=job_id,
            enqueue=enqueue,
            max_pages=spec.max_pages,
            part=spec.part,
        )
        for host, run_id in done.queued.items():
            await AccessRepository(session).record(
                AuditAction.RUN_STARTED,
                target=f"crawl:{run_id}",
                details={
                    "этап": "2 — обход донора",
                    "донор": host,
                    "как": f"обход 24/7, часть {spec.part}",
                },
            )
            issued.append(host)
            fresh = fresh or not spec.retry
    if fresh:
        await session.execute(
            update(CrawlAlwaysSignalModel)
            .where(CrawlAlwaysSignalModel.reopened_at.is_(None))
            .values(reopened_at=moment)
        )
    if issued:
        logger.info("обход 24/7: поставлены части — %s", ", ".join(issued))
    return issued


def _facts(found: Survey) -> dict[str, Any]:
    counts = found.counts()
    return {
        "сайтов": counts["total"],
        "до конца": counts[Stage.EXHAUSTED.value],
        "мало рекламодателей": counts[Stage.WEAK.value],
        "ждут после отказа": found.resting(),
    }


def done_text(facts: dict[str, Any]) -> str:
    """Слова сообщения «пройдено всё»: сколько сайтов и почему каждый больше не идёт."""
    parts = [
        f"до конца — {spaced(facts['до конца'])}",
        f"мало рекламодателей — {spaced(facts['мало рекламодателей'])}",
    ]
    if facts.get("ждут после отказа"):
        parts.append(f"не пустили — {spaced(facts['ждут после отказа'])}")
    return (
        f"Обход 24/7: пройдено всё, что можно, — {spaced(facts['сайтов'])} сайтов: "
        f"{', '.join(parts)}. Новые части начнутся сами: новый сайт, другой порог или переобход "
        "слабых."
    )


async def _say_done(session: AsyncSession, found: Survey, moment: datetime) -> None:
    """«Пройдено всё» — один раз на эпоху (шапка): уникальный номер эпохи держит его при двух
    процессах. Фиксация — до сообщения: сказать о строке, которая откатится, — соврать."""
    latest = await session.scalar(
        select(CrawlAlwaysSignalModel).order_by(CrawlAlwaysSignalModel.id.desc()).limit(1)
    )
    if latest is not None and latest.reopened_at is None:
        return
    facts = _facts(found)
    inserted = await session.execute(
        insert(CrawlAlwaysSignalModel)
        .values(epoch=1 if latest is None else latest.epoch + 1, sent_at=moment, facts=facts)
        .on_conflict_do_nothing(index_elements=["epoch"])
        .returning(CrawlAlwaysSignalModel.id)
    )
    first = inserted.scalar_one_or_none() is not None
    await session.commit()
    if first:
        logger.info("обход 24/7: пройдено всё — %s", facts)
        await send_alert(marked(done_text(facts), DONE))
