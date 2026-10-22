"""Главная судье сегмента — хоста страницы выдачи, при сбое — корня (`segment_text.page_home`).

Страница выдачи на поддомене страны или города: у поддомена своя главная, а главная корня бывает
другим сайтом — магазином на другом языке (замер 20.10 на наборе трёх гипотез: 14 из 191 страницы
выдачи — на поддомене, у 9 главная поддомена другая или корень не открылся). Разбор главной —
общий `check_home`, здесь — выбор хоста и язык, на котором главная просится: язык писем прогона.
Домены выдуманы, ниши и письменности разные.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.sales import segment_window as sw
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_text import HOME_TITLE, page_home
from sqlalchemy.ext.asyncio import AsyncSession
from tests.segment_facts_fakes import facts
from tests.test_sales_segment_judging import candidates, judge
from tests.test_sales_segment_run import Model, hypothesis, make_run

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


#: Главная сайта с версиями по языку браузера — на каждом языке своя.
AGRO = {
    "ru": "Сельхозтехника с доставкой по стране",
    "el": "Γεωργικά μηχανήματα με παράδοση",
    "ar": "معدات زراعية مع التوصيل",
    "en": "Farm machinery with delivery",
}


@dataclass
class Versions:
    """Сайт с версиями по языку браузера: главная корня — на первом языке `Accept-Language`, а
    какого у сайта нет — английская; поддомен не открывается. Помнит хост и язык каждого запроса."""

    root: str
    asked: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        accept = request.headers["Accept-Language"]
        host = request.url.host.removeprefix("www.")
        self.asked.append((host, accept))
        if host != self.root:
            return httpx.Response(503, text="нет", request=request)
        title = AGRO.get(accept.split(",")[0].split("-")[0], AGRO["en"])
        return httpx.Response(200, html=PAGE.format(title=title), request=request)


@pytest.mark.parametrize(
    ("letters", "accept", "seen"),
    [
        ("ru", "ru,en;q=0.9", "ru"),
        ("el", "el,en;q=0.9", "el"),
        ("ar", "ar,en;q=0.9", "ar"),
        ("en-US", "en", "en"),
        (None, "en-US,en;q=0.9", "en"),
    ],
)
async def test_the_judge_reads_the_home_in_the_language_of_the_run_letters(
    letters: str | None, accept: str, seen: str
) -> None:
    """Главная судье — на языке писем прогона (`Ask.letters`): сайт с версиями по языку браузера
    отдаёт её на нём, и модель читает тот же сайт, что человек и письмо. Главная поддомена и корня —
    на одном языке; `en-US` — основой кода; язык писем не задан — как раньше."""
    versions = Versions("agro-sample.com")
    site = sw.Site(
        1, 1, 1, "agro-sample.com", "Agro", None, page_url="https://b2b.agro-sample.com/"
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(versions)) as http:
        ask = sw.Ask("сегмент", JudgeMode.SHADOW, None, http, datetime.now(UTC), letters=letters)
        text = await sw._text(site, ask)

    assert {host for host, _ in versions.asked} == {"b2b.agro-sample.com", "agro-sample.com"}
    assert {language for _, language in versions.asked} == {accept}
    assert (HOME_TITLE, AGRO[seen]) in text.lines


async def test_the_judge_pass_of_a_run_asks_the_homes_in_its_letters(session: AsyncSession) -> None:
    """Настоящий проход судьи — с языком писем прогона, как его зовёт прогон
    (`letters=run.language`): главная кандидата просится на нём, и судят по её версии на нём."""
    run = await make_run(session, await hypothesis(session))
    run.language = "ar"
    [found] = await candidates(session, run.id, run.hypothesis_id, "agro-sample.com")
    versions = Versions("agro-sample.com")
    model = Model({"agro-sample.com": facts("yes", AGRO["ar"], "продаёт технику у себя")})

    async with httpx.AsyncClient(transport=httpx.MockTransport(versions)) as http:
        await judge_run(
            session, run_id=run.id, segment="Продавцы сельхозтехники", mode=JudgeMode.SHADOW,
            client=judge(model), home=http, letters=run.language,
        )  # fmt: skip

    assert {language for _, language in versions.asked} == {"ar,en;q=0.9"}
    assert f"{HOME_TITLE}: {AGRO['ar']}" in (found.site_text or "")
