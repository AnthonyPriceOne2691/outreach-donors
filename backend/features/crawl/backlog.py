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

Снять очередь целиком — человеку с правом запуска (`api/donors/bulk.py`); снятое — в журнал.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import filters
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, CrawlOutcome, CrawlStatus
from backend.features.core.models.crawl import CrawlBacklogModel, CrawlRunModel
from backend.features.crawl import targets
from backend.features.crawl.launch import Enqueue, JobId, launch

logger = logging.getLogger(__name__)

#: Строк за одну вставку: три значения на строку, а у Postgres предел — 32 767 на запрос.
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
    session: AsyncSession, hosts: Sequence[str], *, by: str | None, author_id: int | None
) -> int:
    """Поставить доноров в очередь загрузки — сколько встало впервые. Уже стоящий — не второй
    раз: загруженный дважды обходится раз. Без коммита."""
    added = 0
    unique = list(dict.fromkeys(hosts))
    for start in range(0, len(unique), CHUNK):
        rows = [
            {"host": host, "requested_by": by, "author_id": author_id}
            for host in unique[start : start + CHUNK]
        ]
        result = await session.execute(
            insert(CrawlBacklogModel)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["host"])
            .returning(CrawlBacklogModel.id)
        )
        added += len(result.scalars().all())
    return added


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


async def eta_sec(session: AsyncSession, ahead: int, workers: int | None) -> float | None:
    """Сколько секунд займёт обойти `ahead` доноров при `workers` обходчиках — по медиане
    последних законченных обходов, а без них — по получасу на донора. Обходчиков не знаем
    или их нет — `None`: срок назвать нечем."""
    if not workers or ahead <= 0:
        return None
    elapsed = CrawlRunModel.stats["elapsed_sec"].as_float()
    rows = await session.scalars(
        select(elapsed)
        .where(CrawlRunModel.status == CrawlStatus.DONE)
        .where(CrawlRunModel.outcome.in_((CrawlOutcome.OK, CrawlOutcome.PARTIAL)))
        .where(elapsed.is_not(None))
        .order_by(CrawlRunModel.finished_at.desc().nullslast())
        .limit(SAMPLE)
    )
    spent = [value for value in rows.all() if value is not None and value > 0]
    typical = statistics.median(spent) if spent else TYPICAL_SEC
    return ahead * typical / workers


async def feed(session: AsyncSession, *, workers: int, enqueue: Enqueue, job_id: JobId) -> Fed:
    """Отдать свободным обходчикам доноров очереди загрузки — один проход.

    В очереди обходов кто-то ждёт — человек или отданные раньше — не отдаём никого: ждущий
    уйдёт первым. Иначе — `workers` минус идущие, но не больше `PER_PASS`. Коммит — у
    вызывающего: постановка фиксирует сама (`launch`), запись журнала — после неё.
    """
    fed = Fed()
    running, queued = await _counts(session)
    free = min(workers - running, PER_PASS)
    if queued or free <= 0:
        return fed
    rows = list(
        (
            await session.scalars(
                select(CrawlBacklogModel).order_by(CrawlBacklogModel.id).limit(SCAN)
            )
        ).all()
    )
    if not rows:
        return fed
    verdicts = _verdicts(await targets.choose(session, limit=None))
    picks: list[CrawlBacklogModel] = []
    for row in rows:
        why = verdicts.get(row.host.lower(), _OTHER)
        if why is not None:
            fed.dropped[row.host] = why
            await session.delete(row)
        elif len(picks) < free:
            picks.append(row)
            await session.delete(row)
    for (by, author_id), hosts in _by_requester(picks).items():
        done = await launch(session, hosts, by=by, job_id=job_id, enqueue=enqueue)
        fed.busy += done.busy
        fed.failed += done.failed
        if done.failed:  # очередь не ответила — вернуть строки, проход повторит позже
            await add(session, done.failed, by=by, author_id=author_id)
        for host, run_id in done.queued.items():
            fed.queued[host] = run_id
            await AccessRepository(session).record(
                AuditAction.RUN_STARTED,
                author_id=author_id,
                target=f"crawl:{run_id}",
                details={"этап": "2 — обход донора", "донор": host, "как": "очередь загрузки"},
            )
    if fed:
        logger.info(
            "очередь загрузки: поставлено %s, уже шли %s, снято %s, очередь не ответила %s",
            len(fed.queued),
            len(fed.busy),
            len(fed.dropped),
            len(fed.failed),
        )
    return fed


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
) -> dict[tuple[str | None, int | None], list[str]]:
    """Постановка — от имени того, кто велел обойти: у загрузок разных людей — свои вызовы."""
    groups: dict[tuple[str | None, int | None], list[str]] = {}
    for row in rows:
        groups.setdefault((row.requested_by, row.author_id), []).append(row.host)
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
