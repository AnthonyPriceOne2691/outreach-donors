"""Глубокий обход: сайт целиком, а не последняя тысяча страниц (02.11.2026).

Обычный обход идёт до тысячи страниц, свежие адреса карты первыми: на большом сайте старые
статьи ему не достаются. Глубокий — тот же обход с начала, с потолком в десять тысяч. Слово
Anthony 02.11.2026: потолок времени поднять, число таких обходов разом ограничить.

**Потолков пять, и они двигаются вместе** (`Ceilings`). Подними одни страницы — обход кончится
раньше: отказы съедят две тысячи попыток, адреса кончатся на пяти тысячах из карты, а
двадцать файлов карты дадут их ещё меньше, и время выйдет через три часа. Кнопка обещала бы
десять тысяч, а вышло бы меньше без всякой причины со стороны сайта.

**Потолок — в строке обхода** (`CrawlRunModel.max_pages`): продолжение после выкатки, разбор
мёртвых и экран читают его оттуда, а обход не становится обычным посреди дороги.

**Пачки реже.** Чекпоинт — весь список открытых страниц и очередь, и пишется целиком: на
десяти тысячах страниц это около 3 МБ, а не 300 КБ (замер 02.11.2026), и прежние пачки по 25
страниц записали бы за обход около 600 МБ. Смерть процесса теряет не больше сотни страниц.

**Разом — не больше `CRAWL_DEEP_MAX_RUNNING`.** Такой обход идёт часами, и шестнадцать
обходчиков, занятых ими, означали бы, что ни один обычный обход не идёт сутками. Поэтому
глубокие всегда ждут в очереди загрузки (`backlog.py`), и кнопка одного донора тоже: выдача
одна, и предел держится в одном месте. Обычные обходы и обходы человека идут мимо них.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import crawl as cfg
from backend.features.core.domain import CrawlStatus
from backend.features.core.models.crawl import CrawlBacklogModel, CrawlRunModel
from backend.features.crawl.donor_page import ACTIVE, TYPICAL_MIN_PAGES, typical_seconds
from backend.features.crawl.progress import BATCH_PAGES, BATCH_SECONDS

#: Пачка глубокого обхода: реже обычной, потому что чекпоинт вдесятеро длиннее (шапка).
DEEP_BATCH_PAGES = 100
DEEP_BATCH_SECONDS = 180.0
#: Сколько законченных глубоких обходов нужно, чтобы мерить срок ими, а не прикидкой.
DEEP_SAMPLE_MIN = 3


@dataclass(frozen=True, slots=True)
class Ceilings:
    """Потолки одного обхода — все, что двигаются вместе (шапка)."""

    pages: int
    attempts: int
    seconds: float
    sitemap_files: int
    sitemap_urls: int
    batch_pages: int
    batch_sec: float


def ceilings_of(max_pages: int | None) -> Ceilings:
    """Потолки обхода по его строке: пусто — обычный, из настроек. Попыток не меньше двух на
    страницу — то же правило, что у обычного обхода: потолок строки мог быть задан иным, чем
    настройка сегодня."""
    if max_pages is None:
        return Ceilings(
            pages=cfg.MAX_PAGES_PER_DONOR,
            attempts=cfg.MAX_ATTEMPTS_PER_DONOR,
            seconds=cfg.MAX_SECONDS_SLOW,
            sitemap_files=cfg.MAX_SITEMAP_FILES,
            sitemap_urls=cfg.MAX_SITEMAP_URLS,
            batch_pages=BATCH_PAGES,
            batch_sec=BATCH_SECONDS,
        )
    return Ceilings(
        pages=max_pages,
        attempts=max(cfg.MAX_ATTEMPTS_DEEP, max_pages * 2),
        seconds=cfg.MAX_SECONDS_DEEP,
        sitemap_files=cfg.MAX_SITEMAP_FILES_DEEP,
        sitemap_urls=cfg.MAX_SITEMAP_URLS_DEEP,
        batch_pages=DEEP_BATCH_PAGES,
        batch_sec=DEEP_BATCH_SECONDS,
    )


async def going(session: AsyncSession) -> int:
    """Сколько глубоких обходов идёт и ждёт обходчика."""
    found = await session.scalar(
        select(func.count())
        .select_from(CrawlRunModel)
        .where(CrawlRunModel.status.in_(ACTIVE), CrawlRunModel.max_pages.is_not(None))
    )
    return int(found or 0)


async def waiting(session: AsyncSession) -> int:
    """Сколько глубоких обходов ждёт в очереди загрузки."""
    found = await session.scalar(
        select(func.count())
        .select_from(CrawlBacklogModel)
        .where(CrawlBacklogModel.max_pages.is_not(None))
    )
    return int(found or 0)


async def busy_hosts(session: AsyncSession, hosts: list[str]) -> list[str]:
    """Кто из `hosts` уже идёт или ждёт обходчика: второго обхода донора база не заведёт
    (`queue_crawl`), и строка очереди загрузки потом пропала бы молча."""
    if not hosts:
        return []
    rows = await session.scalars(
        select(CrawlRunModel.host).where(
            CrawlRunModel.status.in_(ACTIVE), CrawlRunModel.host.in_(hosts)
        )
    )
    return sorted(set(rows.all()))


async def typical_deep_seconds(session: AsyncSession) -> float:
    """Сколько обычно идёт глубокий обход: медиана законченных, а пока их меньше
    `DEEP_SAMPLE_MIN` — обычный обход, растянутый на потолок страниц, но не дольше срока."""
    elapsed = CrawlRunModel.stats["elapsed_sec"].as_float()
    rows = await session.execute(
        select(elapsed)
        .where(
            CrawlRunModel.status == CrawlStatus.DONE,
            CrawlRunModel.max_pages.is_not(None),
            CrawlRunModel.pages_opened >= TYPICAL_MIN_PAGES,
            elapsed.is_not(None),
        )
        .order_by(CrawlRunModel.id.desc())
        .limit(50)
    )
    spent = [float(value) for (value,) in rows.all() if value is not None and value > 0]
    if len(spent) >= DEEP_SAMPLE_MIN:
        return statistics.median(spent)
    usual = await typical_seconds(session)
    return min(usual * cfg.MAX_PAGES_DEEP / cfg.MAX_PAGES_PER_DONOR, cfg.MAX_SECONDS_DEEP)
