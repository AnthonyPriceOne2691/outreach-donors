"""Обход и его ссылки в базе — против настоящей базы, а не по моделям.

Всё, что уходит в базу, проверяется на базе: типы в модели выглядели
верными, пока живой прогон не встретил домен с трафиком за два миллиарда.
Здесь то же самое про адреса: путь со списком параметров в `String(255)`
не укладывается, и узнать об этом лучше тут.

Отдельно проверяется то, ради чего запись обхода вообще заведена: обход,
который не нашёл ничего, обязан оставить строку. Иначе «у донора нет
исходящих ссылок» и «нас не пустили» выглядят в базе одинаково.
"""

from __future__ import annotations

import pytest
from backend.features.core.domain import CrawlOutcome, StopReason
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl.links import OutLink
from backend.features.crawl.repository import save_crawl
from backend.features.crawl.walk import CrawlReport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

HOST = "donor.example.test"


def _link(url: str, **kwargs: object) -> OutLink:
    defaults: dict[str, object] = {
        "page_url": f"https://{HOST}/post/1",
        "url": url,
        "target_host": "advertiser.com",
        "target_root": "advertiser.com",
        "anchor": "Бонус 100%",
        "anchor_key": "бонус 100%",
        "nofollow": False,
        "sponsored": False,
        "ugc": False,
    }
    defaults.update(kwargs)
    return OutLink(**defaults)  # type: ignore[arg-type]


def _report(**kwargs: object) -> CrawlReport:
    defaults: dict[str, object] = {
        "host": HOST,
        "outcome": CrawlOutcome.OK,
        "stop_reason": StopReason.EXHAUSTED,
        "pages": [f"https://{HOST}/post/1"],
        "articles": 1,
    }
    defaults.update(kwargs)
    return CrawlReport(**defaults)  # type: ignore[arg-type]


async def test_run_and_links_are_saved_together(session: AsyncSession) -> None:
    report = _report(links=[_link("https://advertiser.com/offer")])

    run = await save_crawl(session, report)
    await session.commit()

    stored = (await session.execute(select(OutLinkModel))).scalars().all()
    assert len(stored) == 1
    assert stored[0].crawl_run_id == run.id
    assert stored[0].target_root == "advertiser.com"
    assert stored[0].anchor == "Бонус 100%"


async def test_empty_crawl_still_leaves_a_record(session: AsyncSession) -> None:
    """Главное, ради чего запись заведена: «ссылок нет» и «не пустили» —
    разные ответы, а в таблице ссылок оба дают ноль строк."""
    report = _report(
        outcome=CrawlOutcome.BLOCKED, stop_reason=StopReason.NO_START, pages=[], articles=0
    )

    await save_crawl(session, report)
    await session.commit()

    runs = (await session.execute(select(CrawlRunModel))).scalars().all()
    assert len(runs) == 1
    assert runs[0].outcome is CrawlOutcome.BLOCKED
    assert runs[0].stop_reason is StopReason.NO_START
    assert runs[0].pages_opened == 0


async def test_long_url_survives_the_database(session: AsyncSession) -> None:
    """Адрес со списком параметров длиннее 255 символов — обычное дело
    у партнёрских ссылок, а обрезанный адрес нельзя ни открыть,
    ни сверить."""
    long_url = "https://advertiser.com/go?" + "&".join(f"utm{n}=value{n}" for n in range(40))
    assert len(long_url) > 255

    await save_crawl(session, _report(links=[_link(long_url)]))
    await session.commit()

    stored = (await session.execute(select(OutLinkModel))).scalars().one()
    assert stored.url == long_url


async def test_marks_travel_to_the_database(session: AsyncSession) -> None:
    report = _report(
        links=[
            _link("https://a.com/1", nofollow=True, sponsored=True, ugc=True),
            _link("https://b.com/2", in_body=False, root_guessed=True),
        ]
    )

    await save_crawl(session, report)
    await session.commit()

    rows = {row.url: row for row in (await session.execute(select(OutLinkModel))).scalars().all()}
    first = rows["https://a.com/1"]
    assert (first.nofollow, first.sponsored, first.ugc) == (True, True, True)
    assert first.in_body is True
    second = rows["https://b.com/2"]
    assert second.in_body is False
    assert second.root_guessed is True


async def test_degradation_and_stats_are_stored(session: AsyncSession) -> None:
    """Уровень каскада, который не поднялся, лежит в записи, а не в логе:
    иначе обход выглядит зелёным и врёт, что закрытые страницы проверены."""
    report = _report(
        degradation={"browser": "не установлен"},
        health={"requests": 12, "blocked": 3, "blocked_share": 0.25},
    )

    run = await save_crawl(session, report)
    await session.commit()

    assert run.degradation == {"browser": "не установлен"}
    assert run.stats is not None
    assert run.stats["blocked_share"] == 0.25
    assert run.stats["host"] == HOST
