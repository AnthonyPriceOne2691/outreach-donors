"""Срок обхода донора — по паузе сайта, а не один час на всех (23.10.2026).

Потолок времени был одинаков для всех доноров (`CRAWL_MAX_SECONDS`, час), а темп обхода —
нет: пауза между страницами — та, что просит сайт в robots.txt, до десяти секунд. Сайт с
долгой паузой за час не успевал и половины потолка и кончался «частично» не потому, что
закрылся, а потому, что ждали его же паузу.

**Срок — столько, чтобы при паузе сайта успеть потолок страниц**: на каждый запрос пауза
плюс среднее время ответа, на потолок страниц и файлы карты сайта. Не меньше общего срока и
не больше потолка медленных (`CRAWL_MAX_SECONDS_SLOW`): один донор не держит обходчик сутки.

**Первый раз — по первым ответам**, когда пауза известна, после robots.txt и главной: среднее
время ответа — по ним же, сделанным до этого запросам. Продолжение с чекпоинта берёт срок из
чекпоинта, а время продолжает считать с прошлых попыток (`progress.Checkpoint`).

**Срок вышел — по фактическому темпу** (`replan`, 29.10.2026). Первые ответы обещают меньше,
чем сайт держит потом: тест-группа — сайт кончился «частично» на 976 страницах из 1 000, он
отвечал медленнее, чем на robots.txt и главной, а обходчик ещё и сбавляет темп сам, если
сайт начал отказывать. Поэтому в миг, когда срок вышел, он пересчитывается по тому, сколько
ушло на уже открытые страницы: тем же темпом — оставшиеся до потолка, и запас. Срок
продлевается, но не дальше потолка медленных — на нём держится и таймаут задачи очереди
(`shared/queue.crawl_job_timeout`), — и никогда не короче обещанного. Открыто меньше
`REPLAN_MIN_PAGES` страниц — темп по ним не понять (продолжение, у которого срок вышел ещё
в прошлой попытке, или сайт, отдающий страницу раз в минуты), срок прежний.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.config import crawl as cfg

logger = logging.getLogger(__name__)

#: Меньше открытых страниц — темп по ним не понять, и срок по ним не продлевается.
REPLAN_MIN_PAGES = 20
#: Запас над темпом: последние страницы бывают медленнее средних, а второй пересчёт стоит
#: ещё одного «срок вышел» посреди обхода.
REPLAN_MARGIN = 1.1


@dataclass(slots=True)
class ResponseClock:
    """Сколько сайт отвечает: сумма и число ответов. Ожидание паузы сюда не входит —
    меряется сам запрос, внутри слота ограничителя."""

    spent: float = 0.0
    count: int = 0

    def record(self, seconds: float) -> None:
        self.spent += seconds
        self.count += 1

    @property
    def average(self) -> float:
        """Среднее время ответа; ответов не было — ноль: срок тогда — по одной паузе."""
        return self.spent / self.count if self.count else 0.0


def time_budget(*, pause: float, response: float, pages: int, base: float, ceiling: float) -> float:
    """Срок донора, с: потолок страниц и файлы карты — каждый за паузу плюс ответ, — но не
    меньше общего срока `base` и не больше потолка медленных `ceiling`. Потолок ниже общего
    срока срок не урезает: он про продление, а не про сам срок."""
    needed = (pages + cfg.MAX_SITEMAP_FILES) * (pause + response)
    return max(base, min(ceiling, needed))


def replan(*, budget: float, elapsed: float, opened: int, pages: int, ceiling: float) -> float:
    """Срок, когда прежний вышел: `elapsed` секунд ушло на `opened` открытых страниц — тем же
    темпом оставшиеся до потолка `pages`, с запасом `REPLAN_MARGIN`. Не короче `budget` и не
    длиннее потолка медленных `ceiling`; открытых меньше `REPLAN_MIN_PAGES` или потолок уже
    набран — срок прежний."""
    if opened < REPLAN_MIN_PAGES or opened >= pages:
        return budget
    needed = elapsed * pages / opened * REPLAN_MARGIN
    return max(budget, min(ceiling, needed))


def extended(
    host: str, *, budget: float, elapsed: float, opened: int, pages: int, ceiling: float
) -> float | None:
    """Срок донора `host` вышел — продлённый по темпу (`replan`), целыми секундами, как
    первый; `None` — продлевать некуда: темп не понять или потолок медленных уже достигнут."""
    longer = round(
        replan(budget=budget, elapsed=elapsed, opened=opened, pages=pages, ceiling=ceiling)
    )
    if longer <= elapsed:
        return None
    logger.info(
        "обход %s: срок %.0f с вышел на %s страницах из %s — по темпу продлён до %s с",
        host,
        budget,
        opened,
        pages,
        longer,
    )
    return longer
