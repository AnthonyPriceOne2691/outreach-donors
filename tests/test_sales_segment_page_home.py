"""Главная судье сегмента — хоста страницы выдачи, при сбое — корня (`segment_text.page_home`).

Страница выдачи на поддомене страны или города: у поддомена своя главная, а главная корня бывает
другим сайтом — магазином на другом языке (замер 20.10 на наборе трёх гипотез: 14 из 191 страницы
выдачи — на поддомене, у 9 главная поддомена другая или корень не открылся). Разбор главной —
общий `check_home`, здесь — только выбор хоста. Домены выдуманы, ниши и письменности разные.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from backend.config.judge import JudgeMode
from backend.features.sales import segment_window as sw
from backend.features.sales.segment_text import HOME_TITLE, page_home

PAGE = "<html><head><title>{title}</title></head><body><p>{title}</p></body></html>"


@dataclass
class Homes:
    """Главные по хосту (`www.` — тот же сайт); не названная — 503."""

    titles: dict[str, str]
    hits: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host.removeprefix("www.")
        self.hits.append(host)
        if request.url.path in ("", "/") and host in self.titles:
            return httpx.Response(200, html=PAGE.format(title=self.titles[host]), request=request)
        return httpx.Response(503, text="нет", request=request)


def client(homes: Homes) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(homes))


async def test_a_page_on_a_country_subdomain_takes_the_home_of_that_subdomain() -> None:
    homes = Homes(
        {
            "uz.stroy-sample.com": "Qurilish va ta'mirlash Toshkentda",
            "stroy-sample.com": "House plans shop",
        }
    )

    async with client(homes) as http:
        found = await page_home(http, "stroy-sample.com", "https://uz.stroy-sample.com/remont")

    assert (found.reached, found.title) == (True, "Qurilish va ta'mirlash Toshkentda")
    assert "stroy-sample.com" not in homes.hits  # корень не нужен — главная поддомена открылась


async def test_a_subdomain_home_that_does_not_open_falls_back_to_the_root() -> None:
    homes = Homes({"kliniki-sample.pl": "Klinika stomatologiczna w Krakowie"})

    async with client(homes) as http:
        found = await page_home(http, "kliniki-sample.pl", "https://krakow.kliniki-sample.pl/")

    assert (found.reached, found.title) == (True, "Klinika stomatologiczna w Krakowie")
    assert homes.hits[0] == "krakow.kliniki-sample.pl"  # сначала поддомен
    assert homes.hits[-1] == "kliniki-sample.pl"


async def test_a_page_on_www_or_the_root_or_without_an_address_opens_only_the_root() -> None:
    for url in ("https://www.ryokan-sample.jp/plan", "https://ryokan-sample.jp/", None):
        homes = Homes({"ryokan-sample.jp": "温泉旅館の公式サイト"})

        async with client(homes) as http:
            found = await page_home(http, "ryokan-sample.jp", url)

        assert (found.reached, found.title) == (True, "温泉旅館の公式サイト")
        assert set(homes.hits) == {"ryokan-sample.jp"}, url


async def test_a_page_of_another_domain_never_chooses_the_host() -> None:
    """Хост страницы — только поддомен домена базы: чужой хост в адресе главную не подменяет."""
    homes = Homes({"bakery-sample.de": "Bäckerei in Köln", "elsewhere-sample.com": "Other site"})

    async with client(homes) as http:
        found = await page_home(
            http, "bakery-sample.de", "https://elsewhere-sample.com/bakery-sample.de"
        )

    assert found.title == "Bäckerei in Köln"
    assert set(homes.hits) == {"bakery-sample.de"}


async def test_the_judge_text_carries_the_home_of_the_search_result_page() -> None:
    """Окно судьи (`segment_window._text`) берёт главную хоста страницы выдачи кандидата."""
    homes = Homes(
        {"am.law-sample.com": "Իրավաբանական ընկերություն Երևանում", "law-sample.com": "Law"}
    )
    site = sw.Site(
        1, 1, 1, "law-sample.com", "Իրավաբան", None, page_url="https://am.law-sample.com/"
    )

    async with client(homes) as http:
        ask = sw.Ask("сегмент", JudgeMode.SHADOW, None, http, datetime.now(UTC))
        text = await sw._text(site, ask)

    assert (HOME_TITLE, "Իրավաբանական ընկերություն Երևանում") in text.lines
