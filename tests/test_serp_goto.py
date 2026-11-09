"""Редирект Google вместо адреса сайта: позиция выдачи читается по видимому адресу.

Проверка на проде 06.11.2026: у прогона на один ключ все позиции с адресом
`google.com/goto?url=<жетон>`; они сводились к домену поисковика, гейт снимал его как
платформу, и прогон заканчивался как обычный: ноль кандидатов, ни тревоги.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from backend.features.runs.planning import gather_candidates
from backend.features.serp.dataforseo import LIVE_PATH, DataForSeoProvider
from backend.features.serp.dataforseo_answers import organic_results
from backend.features.serp.modes import SerpMode

GOTO = "https://google.com/goto?url=opaque-token-"


def organic(
    position: int, url: str, *, breadcrumb: str | None = None, title: str = "Заголовок"
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "organic",
        "rank_absolute": position,
        "url": url,
        "domain": "google.com",
        "title": title,
        "description": "Описание",
    }
    if breadcrumb is not None:
        item["breadcrumb"] = breadcrumb
    return item


class TestRedirectIsRead:
    @pytest.mark.parametrize(
        ("breadcrumb", "site"),
        [
            ("https://www.site.com › blog › post", "https://www.site.com/"),
            ("https://www.site.com > Advisor > Retirement", "https://www.site.com/"),
            ("https://site.co.uk", "https://site.co.uk/"),
            ("http://site.com › a", "http://site.com/"),
            ("site.com › a", "https://site.com/"),
            ("  https://SITE.com:8080 › a ", "https://site.com/"),
        ],
    )
    def test_site_comes_from_the_shown_address(self, breadcrumb: str, site: str) -> None:
        [row] = organic_results([organic(1, GOTO, breadcrumb=breadcrumb)], 10)
        assert row.url == site

    def test_position_keeps_its_text(self) -> None:
        """Заголовок и описание уже оплачены и настоящие: судья читает их, а не редирект."""
        [row] = organic_results(
            [organic(3, GOTO, breadcrumb="https://site.com › a", title="Write for us")], 10
        )
        assert (row.position, row.title, row.description) == (3, "Write for us", "Описание")

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.google.com/goto?url=token",
            "https://google.de/goto?url=token",
            "https://www.google.co.uk/goto/?url=token",
        ],
    )
    def test_any_google_address_of_the_redirect(self, url: str) -> None:
        [row] = organic_results([organic(1, url, breadcrumb="https://site.com")], 10)
        assert row.url == "https://site.com/"

    def test_old_wrapper_names_the_page_itself(self) -> None:
        """Обёртка `url?q=` называет страницу целиком: это лучше видимого адреса."""
        wrapped = "https://www.google.com/url?q=https%3A%2F%2Fsite.com%2Fwrite-for-us&sa=U"
        [row] = organic_results([organic(1, wrapped, breadcrumb="https://other.com")], 10)
        assert row.url == "https://site.com/write-for-us"


class TestNoSiteToRead:
    @pytest.mark.parametrize(
        "breadcrumb",
        [
            None,
            "",
            "https://www.site-with-a-very-long-na…",
            "https://www.site-with-a-very-long-na... › a",
            "2,3K+ comments · 3 weeks ago",
            "YouTube · Channel",
            "Reddit",
        ],
    )
    def test_position_is_skipped_not_taken_for_google(
        self, breadcrumb: str | None, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            rows = organic_results([organic(1, GOTO, breadcrumb=breadcrumb)], 10)
        assert rows == []
        assert "пропущено позиций: 1" in caplog.text

    def test_not_a_string_breadcrumb(self) -> None:
        item = organic(1, GOTO)
        item["breadcrumb"] = ["https://site.com"]
        assert organic_results([item], 10) == []


class TestOrdinaryPositions:
    @pytest.mark.parametrize(
        "url",
        [
            "https://site.com/write-for-us",
            # Настоящие страницы Google не редирект: снять их — дело гейта, а не разбора.
            "https://support.google.com/answer/1",
            "https://www.google.com/goto-the-store",
            "https://notgoogle.com/goto?url=x",
        ],
    )
    def test_address_stays_as_it_came(self, url: str, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            [row] = organic_results([organic(1, url, breadcrumb="https://other.com")], 10)
        assert row.url == url
        assert "редирект" not in caplog.text

    def test_broken_address_stays_for_the_next_step(self) -> None:
        """Битый адрес, как и раньше, считает планирование: `urls_unparsed`."""
        [row] = organic_results([organic(1, "http://[broken")], 10)
        assert row.url == "http://[broken"

    def test_wanted_counts_only_kept_positions(self) -> None:
        items = [
            organic(1, GOTO),
            organic(2, GOTO, breadcrumb="https://a.com"),
            organic(3, "https://b.com/x"),
            organic(4, "https://c.com/x"),
        ]
        assert [row.url for row in organic_results(items, 2)] == [
            "https://a.com/",
            "https://b.com/x",
        ]


class TestWholeRun:
    """Прогон целиком: ответ провайдера → разные домены, как на проверке прода, только верно."""

    @staticmethod
    def _provider(items: list[dict[str, Any]]) -> DataForSeoProvider:
        def answer(request: httpx.Request) -> httpx.Response:
            assert request.url.path == LIVE_PATH
            keyword = json.loads(request.content)[0]["keyword"]
            task = {
                "status_code": 20000,
                "cost": 0.002,
                "data": {"keyword": keyword},
                "result": [{"items": items, "pages_count": 1}],
            }
            return httpx.Response(200, json={"status_code": 20000, "cost": 0.002, "tasks": [task]})

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(answer), base_url="https://sandbox.invalid"
        )
        return DataForSeoProvider(client, login="l", password="p", mode=SerpMode.LIVE)

    @pytest.mark.asyncio
    async def test_nine_redirects_are_nine_sites(self) -> None:
        items = [
            organic(n, f"{GOTO}{n}", breadcrumb=f"https://www.site{n}.com › write-for-us")
            for n in range(1, 10)
        ]
        found = await gather_candidates(self._provider(items), ["ключ"], "us")
        assert found.hosts == [f"site{n}.com" for n in range(1, 10)]
        assert found.results == 9
        assert found.texts["site1.com"].url == "https://www.site1.com/"
        assert found.found_by["site1.com"] == ["ключ"]

    @pytest.mark.asyncio
    async def test_nothing_readable_is_a_key_without_results(self) -> None:
        """Сайта не узнать ни у одной позиции — экран говорит «ключ без результатов», а не молчит."""
        found = await gather_candidates(
            self._provider([organic(n, f"{GOTO}{n}") for n in range(1, 4)]), ["ключ"], "us"
        )
        assert found.hosts == []
        assert found.empty_keywords == ["ключ"]
