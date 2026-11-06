"""Очередь загрузки: доноры, загруженные списком, идут в обход по мере свободных обходчиков.

Загрузка доноров таблицей (`donors/bulk_entry.py`, 29.10.2026) приносит до десяти тысяч доноров
с ценами, и по галочке их обходят. Обходчиков шестнадцать (`CRAWL_WORKERS`), донор — до часа
(`config/crawl.py`): десять тысяч — недели обхода. **Ставить их в очередь обходов все сразу
нельзя**: обход, запущенный
человеком позже, встал бы за десятью тысячами и ждал бы месяц, а «Последние обходы» закрылись бы
строками «в очереди». Кнопка «Обойти отмеченных» по той же причине ставит не больше пятидесяти
за раз (`api/crawls/schemas.MAX_HOSTS`).

**Загруженные ждут здесь, а выдача — в пустую очередь и понемногу.** Проход разбора раз в минуту
(`workers/reaper.py`) отдаёт отсюда доноров, только когда в очереди обходов никто не ждёт, —
не больше свободных обходчиков и не больше `PER_PASS` за раз. Сторож нагрузки обходчика
(`workers/load_gate.py`) пускает новые обходы по одному за отрезок и только под свободные ядра
машины: отданные разом стояли бы в очереди обходов, и обход, запущенный человеком позже, ждал
бы за ними. Так впереди человека — не больше `PER_PASS` загруженных, а загруженные идут без
перерыва, пока очередь загрузки не кончится. Порядок — порядок загрузки.

**Выдача — той же постановкой и тем же отбором, что у кнопки** (`launch.py`, `targets.choose`).
Донор, который к своему часу перестал подходить — цена протухла, попал в стоп-лист, отклонён, —
не обходится и строку теряет: отказ — строкой журнала процесса, а не обходом впустую. Уже идущий
или ждущий обход донора строку тоже снимает: второго не заводим. Строка снимается до постановки
и одной фиксацией с ней (`launch` фиксирует каждого донора): упади процесс между ними — донор
обойдётся раз, а не дважды. Очередь не ответила — строка возвращается, проход ждёт следующего раза.

**Глубокие обходы ждут здесь всегда** (`deep.py`): выдача держит их предел, и ни одна кнопка не
ставит их мимо неё. Строка помнит потолок (`max_pages`). Предел выбран — их строки в проходе не
видны, и обычные доноры за ними не стоят. Обычный донор, ставший глубоким, строку не удваивает:
она становится глубокой.

**Ночной круг живёт здесь же** (`night.py`): проход начинает его и закрывает, а строки круга
идут последними — загруженные и поставленные человеком важнее. Его строку забирает любая
постановка: иначе донора, которого человек велел обойти, снял бы срок круга.

**Кончились юниты Ahrefs — выдачи нет** (`units_stop.py`): DR новых кандидатов спросить не на что.
Строки ждут здесь и не теряются: снимется стоп — пойдут сами, а ночные снимет срок их круга.

Снять очередь целиком — человеку с правом запуска (`api/donors/bulk.py`); снятое — в журнал.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import crawl as crawl_cfg
from backend.config import filters
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, CrawlOutcome, CrawlStatus
from backend.features.core.models.crawl import CrawlBacklogModel, CrawlRunModel
from backend.features.crawl import deep, night, targets
from backend.features.crawl.launch import Enqueue, JobId, launch
from backend.features.crawl.units_stop import units_stop

logger = logging.getLogger(__name__)

#: Строк за одну вставку: четыре значения на строку, а у Postgres предел — 32 767 на запрос.
CHUNK = 2_000
#: Сколько строк очереди смотреть за проход: свободных мест — единицы, но часть строк может
#: оказаться негодной, и проход идёт дальше по очереди, а не ждёт минуту.
SCAN = 200
#: Сколько доноров отдавать за проход (раз в минуту): сторож нагрузки и так пускает новый обход
#: не чаще раза в `CRAWL_GATE_STAGGER_SEC` (20 с), а шестнадцати обходчикам на смену при обходе
#: в 20–60 минут нужно меньше одного старта в минуту.
PER_PASS = 2
#: Сколько последних законченных обходов берёт прогноз и сколько идёт донор, если их нет:
#: «обход донора — до получаса» (`launch.py`).
SAMPLE = 50
TYPICAL_SEC = 1_800.0


@dataclass(slots=True)
class Fed:
    """Что сделал проход: поставлено, уже шли, снято с причиной, очередь не ответила."""

    queued: dict[str, int] = field(default_factory=dict)
    busy: list[str] = field(default_factory=list)
    dropped: dict[str, str] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.queued or self.busy or self.dropped or self.failed)


async def add(
    session: AsyncSession,
    hosts: Sequence[str],
    *,
    by: str | None,
    author_id: int | None,
    max_pages: int | None = None,
) -> int:
    """Поставить доноров в очередь загрузки — сколько встало впервые. Уже стоящий — не второй
    раз: загруженный дважды обходится раз. `max_pages` — потолок глубокого обхода (`deep.py`):
    стоящий обычным становится глубоким и считается встал. Без коммита."""
    added = 0
    unique = list(dict.fromkeys(hosts))
    for start in range(0, len(unique), CHUNK):
        rows = [
            {"host": host, "requested_by": by, "author_id": author_id, "max_pages": max_pages}
            for host in unique[start : start + CHUNK]
        ]
        result = await session.execute(_upsert(rows, by=by, max_pages=max_pages))
        added += len(result.scalars().all())
    return added


def _upsert(rows: list[dict[str, Any]], *, by: str | None, max_pages: int | None) -> Any:
    """Вставка строк очереди. Уже стоящая меняется только в двух случаях: глубокий забирает
    обычную, а человек — строку ночного круга (`night.py`): её снял бы срок круга."""
    statement = insert(CrawlBacklogModel).values(rows)
    if by == night.NIGHT_BY:
        return statement.on_conflict_do_nothing(index_elements=["host"]).returning(
            CrawlBacklogModel.id
        )
    takes_night = CrawlBacklogModel.requested_by == night.NIGHT_BY
    excluded = statement.excluded
    if max_pages is None:
        return statement.on_conflict_do_update(
            index_elements=["host"],
            set_={"requested_by": excluded.requested_by, "author_id": excluded.author_id},
            where=takes_night,
        ).returning(CrawlBacklogModel.id)
    return statement.on_conflict_do_update(
        index_elements=["host"],
        set_={
            "max_pages": excluded.max_pages,
            "requested_by": excluded.requested_by,
            "author_id": excluded.author_id,
        },
        where=or_(takes_night, CrawlBacklogModel.max_pages.is_(None)),
    ).returning(CrawlBacklogModel.id)


async def waiting(session: AsyncSession) -> int:
    """Сколько доноров ждёт в очереди загрузки."""
    return int(await session.scalar(select(func.count()).select_from(CrawlBacklogModel)) or 0)


async def drop_all(session: AsyncSession, *, author_id: int | None) -> int:
    """Снять очередь загрузки целиком — сколько снято. В журнал — кто и сколько. Без коммита."""
    result = await session.execute(delete(CrawlBacklogModel).returning(CrawlBacklogModel.id))
    dropped = len(result.scalars().all())
    if dropped:
        await AccessRepository(session).record(
            AuditAction.CRAWL_BACKLOG_DROPPED,
            author_id=author_id,
            target="crawl_backlog",
            details={"этап": "2 — обход донора", "снято доноров": dropped},
        )
    return dropped


def _rounds(count: int, places: int) -> int:
    """Сколько сроков идут `count` доноров на `places` местах разом — то же правило, что у экрана
    (`crawlEta`)."""
    return math.ceil(count / places)


async def eta_sec(
    session: AsyncSession, ahead: int, workers: int | None, *, deep_rows: int = 0
) -> float | None:
    """Сколько секунд займёт обойти `ahead` доноров при `workers` обходчиках — по медиане
    последних законченных обходов, а без них — по получасу на донора. Обходчиков не знаем
    или их нет — `None`: срок назвать нечем.

    Срок — целыми кругами (`_rounds`): донор не идёт быстрее своего срока, сколько бы обходчиков
    ни стояло без дела. Деление без круга обещало бы одного глубокого донора за четверть срока.

    `deep_rows` из них — глубокие: идут не больше своего предела разом и бок о бок с обычными,
    на занятых ими обходчиках; срок очереди — тот из двух, что дольше."""
    if not workers or ahead <= 0:
        return None
    if deep_rows > 0:
        slots = min(crawl_cfg.DEEP_MAX_RUNNING, workers)
        rest = ahead - deep_rows
        long = _rounds(deep_rows, slots) * await deep.typical_deep_seconds(session)
        usual = await eta_sec(session, rest, max(workers - slots, 1)) or 0.0
        return max(long, usual)
    elapsed = CrawlRunModel.stats["elapsed_sec"].as_float()
    rows = await session.scalars(
        select(elapsed)
        .where(CrawlRunModel.status == CrawlStatus.DONE, CrawlRunModel.max_pages.is_(None))
        .where(CrawlRunModel.outcome.in_((CrawlOutcome.OK, CrawlOutcome.PARTIAL)))
        .where(elapsed.is_not(None))
        .order_by(CrawlRunModel.finished_at.desc().nullslast())
        .limit(SAMPLE)
    )
    spent = [value for value in rows.all() if value is not None and value > 0]
    typical = statistics.median(spent) if spent else TYPICAL_SEC
    return _rounds(ahead, workers) * typical


async def feed(
    session: AsyncSession,
    *,
    workers: int,
    enqueue: Enqueue,
    job_id: JobId,
    now: datetime | None = None,
) -> Fed:
    """Отдать свободным обходчикам доноров очереди загрузки — один проход. Сначала — шаг
    ночного круга (`night.py`): он сам кладёт сюда строки и сам снимает их по сроку.

    В очереди обходов кто-то ждёт — человек или отданные раньше — не отдаём никого: ждущий
    уйдёт первым. Юнитов Ahrefs нет — тоже никого (`units_stop.py`). Иначе — `workers` минус
    идущие, но не больше `PER_PASS`; глубоких — и не больше их предела (`deep.py`). Коммит — у вызывающего: постановка фиксирует сама
    (`launch`), запись журнала — после неё.
    """
    stop = await units_stop(session)
    why = None if stop is None else stop.why
    await night.tick(session, workers=workers, per_minute=PER_PASS, add=add, now=now, stop=why)
    fed = Fed()
    running, queued = await _counts(session)
    free = min(workers - running, PER_PASS)
    if queued or free <= 0 or stop is not None:
        return fed
    room = crawl_cfg.DEEP_MAX_RUNNING - await deep.going(session)
    picks = await _pick(session, free=free, room=room, fed=fed)
    for (by, author_id, max_pages), hosts in _by_requester(picks).items():
        done = await launch(
            session, hosts, by=by, job_id=job_id, enqueue=enqueue, max_pages=max_pages
        )
        fed.busy += done.busy
        fed.failed += done.failed
        if done.failed:  # очередь не ответила — вернуть строки, проход повторит позже
            await add(session, done.failed, by=by, author_id=author_id, max_pages=max_pages)
        await _journal_queued(session, done.queued, fed, author_id=author_id)
    if fed:
        logger.info(
            "очередь загрузки: поставлено %s, уже шли %s, снято %s, очередь не ответила %s",
            len(fed.queued),
            len(fed.busy),
            len(fed.dropped),
            len(fed.failed),
        )
    return fed


async def _pick(
    session: AsyncSession, *, free: int, room: int, fed: Fed
) -> list[CrawlBacklogModel]:
    """Строки очереди на этот проход: снимает негодных (`fed.dropped`) и выбранных — не больше
    `free`, глубоких — не больше `room`. Предел глубоких выбран — их строк в проходе нет:
    обычные доноры за ними не ждут, сколько бы их ни стояло."""
    # Строки ночного круга — последними: `IS TRUE`, потому что поле бывает пустым.
    last = CrawlBacklogModel.requested_by.is_not_distinct_from(night.NIGHT_BY)
    listed = select(CrawlBacklogModel).order_by(last, CrawlBacklogModel.id).limit(SCAN)
    if room <= 0:
        listed = listed.where(CrawlBacklogModel.max_pages.is_(None))
    rows = list((await session.scalars(listed)).all())
    if not rows:
        return []
    verdicts = _verdicts(await targets.choose(session, limit=None))
    picks: list[CrawlBacklogModel] = []
    for row in rows:
        why = verdicts.get(row.host.lower(), _OTHER)
        if why is not None:
            fed.dropped[row.host] = why
            await session.delete(row)
        elif len(picks) < free and (row.max_pages is None or room > 0):
            room -= row.max_pages is not None
            picks.append(row)
            await session.delete(row)
    return picks


async def _journal_queued(
    session: AsyncSession, queued: dict[str, int], fed: Fed, *, author_id: int | None
) -> None:
    """Поставленные — в отчёт прохода и в журнал действий: строка на обход."""
    for host, run_id in queued.items():
        fed.queued[host] = run_id
        await AccessRepository(session).record(
            AuditAction.RUN_STARTED,
            author_id=author_id,
            target=f"crawl:{run_id}",
            details={"этап": "2 — обход донора", "донор": host, "как": "очередь загрузки"},
        )


async def _counts(session: AsyncSession) -> tuple[int, int]:
    """Обходов идёт и ждёт в очереди обходов."""
    rows = await session.execute(
        select(CrawlRunModel.status, func.count())
        .where(CrawlRunModel.status.in_((CrawlStatus.RUNNING, CrawlStatus.QUEUED)))
        .group_by(CrawlRunModel.status)
    )
    counts = dict(rows.tuples().all())
    return int(counts.get(CrawlStatus.RUNNING, 0)), int(counts.get(CrawlStatus.QUEUED, 0))


def _by_requester(
    rows: Sequence[CrawlBacklogModel],
) -> dict[tuple[str | None, int | None, int | None], list[str]]:
    """Постановка — от имени того, кто велел обойти, и с его потолком: у загрузок разных людей и
    у глубоких — свои вызовы."""
    groups: dict[tuple[str | None, int | None, int | None], list[str]] = {}
    for row in rows:
        groups.setdefault((row.requested_by, row.author_id, row.max_pages), []).append(row.host)
    return groups


#: Не годен обходу ни по одной из причин отбора: не принят, не годен по порогам, «кому не пишем».
_OTHER = "уже не подходит обходу: не принят, не годен по порогам или «кому не пишем»"


def _verdicts(chosen: targets.Targets) -> dict[str, str | None]:
    """Тот же отбор, что у кнопки (`targets.choose`), — словами журнала: домен → почему не
    обходим, а `None` — обходим. Домена нет среди отобранных — `_OTHER`."""
    found: dict[str, str | None] = {}
    for hosts, why in (
        (chosen.supplier, "донор-поставщик"),
        (chosen.no_price, "цены нет"),
        (chosen.stale_price, f"цена старше {filters.PRICE_TTL_DAYS} дней"),
        (chosen.hosts, None),
    ):
        found.update(dict.fromkeys((host.lower() for host in hosts), why))
    return found
