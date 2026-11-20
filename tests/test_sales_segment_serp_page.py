"""Главная не открылась — судья читает саму страницу из выдачи (`segment_text.serp_page`).

Замер 19.11 на прогонах сбора: 2 сайта из 102 ушли человеку словами «главная не открылась — судья
видел только сниппет выдачи». Теперь, когда не открылись главная поддомена и главная корня, судья
скачивает страницу, которую показал поисковик, — одной попыткой, на языке писем прогона, тем же
загрузчиком, — и видит её текст строкой «Текст страницы из выдачи». Здесь: какие адреса качаются и
какие нет, заглушка и отказ, сохранённый текст читается обратно (пересуд, лид), язык считает слова
страницы словами сайта, подпись reCAPTCHA в подвале — не заглушка, проход судьи кладёт строку в
текст кандидата.

Сайты — `httpx.MockTransport`, сеть не ходит. Домены выдуманы, ниши, рынки и письменности разные.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.donors.home_signals import HomeSignals
from backend.features.sales import segment_judge as sj
from backend.features.sales import segment_window as sw
from backend.features.sales.segment_facts import read
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import Lane
from backend.features.sales.segment_rules import SNIPPET_ONLY, UNCLEAR
from backend.features.sales.segment_text import (
    HOME_LABELS,
    PAGE_TEXT,
    PAGE_TITLE,
    SiteText,
    serp_page,
    site_language,
    site_text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from tests.segment_facts_fakes import decide_any, facts
from tests.test_sales_segment_judge import client
from tests.test_sales_segment_judging import candidates, judge
from tests.test_sales_segment_run import Model, hypothesis, make_run
from tests.test_sales_segment_second_chance import in_turn

PAGE = (
    "<html><head><title>{title}</title></head><body><nav><a href='/'>Menu</a></nav>"
    "<main><p>{body}</p></main></body></html>"
)


@dataclass
class Web:
    """Страницы по хосту без `www.` и пути: (код, HTML); не названная — 503. Помнит хост, путь и
    язык каждого запроса."""

    pages: dict[tuple[str, str], tuple[int, str]]
    asked: list[tuple[str, str, str]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host.removeprefix("www."), request.url.path or "/"
        self.asked.append((host, path, request.headers.get("Accept-Language", "")))
        status, html = self.pages.get((host, path), (503, ""))
        if not html:
            return httpx.Response(status, text="нет", request=request)
        return httpx.Response(status, html=html, request=request)

    def paths(self) -> list[str]:
        return [path for _, path, _ in self.asked]


def opened(host: str, path: str, body: str, title: str = "Страница") -> Web:
    """Главные не открываются, страница выдачи — открывается."""
    return Web({(host, path): (200, PAGE.format(title=title, body=body))})


async def text_of(
    web: Web, host: str, page_url: str | None, letters: str | None = None
) -> SiteText:
    """Текст судье, как его собирает окно прохода (`segment_window._text`)."""
    site = sw.Site(1, 1, 1, host, "Заголовок выдачи", None, page_url=page_url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(web)) as http:
        ask = sw.Ask("сегмент", JudgeMode.SHADOW, None, http, datetime.now(UTC), letters=letters)
        return await sw._text(site, ask)


#: (домен, адрес страницы выдачи, путь, видимый текст страницы)
SITES = [
    ("klinika-sample.pl", "https://klinika-sample.pl/uslugi/implanty", "/uslugi/implanty",
     "Klinika stomatologiczna w Krakowie: implanty, leczenie kanałowe i higienizacja"),
    ("shahn-sample.ae", "https://ar.shahn-sample.ae/services", "/services",
     "شركة شحن بحري وجوي من دبي إلى جميع دول الخليج"),
    ("kagu-sample.jp", "https://kagu-sample.jp/products?id=12", "/products",
     "東京の家具工房 無垢材のテーブルと椅子を製作しています"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("host", "url", "path", "body"), SITES, ids=["pl", "ar-subdomain", "ja-query"]
)
async def test_a_home_that_did_not_open_gives_the_judge_the_text_of_the_search_result_page(
    host: str, url: str, path: str, body: str
) -> None:
    page_host = url.split("/")[2]
    web = Web({(page_host.removeprefix("www."), path): (200, PAGE.format(title="T", body=body))})

    text = await text_of(web, host, url)

    assert (PAGE_TEXT, body) in text.lines
    assert (PAGE_TITLE, "Заголовок выдачи") in text.lines
    assert not [label for label, _ in text.lines if label in HOME_LABELS]
    assert web.paths().count(path) == 1  # одна попытка: без вариантов `www.` и `http`


async def test_the_page_is_asked_in_the_language_of_the_run_letters() -> None:
    web = opened("salon-sample.gr", "/ypiresies", "Κομμωτήριο στην Αθήνα με κουρέματα και βαφές")

    await text_of(web, "salon-sample.gr", "https://salon-sample.gr/ypiresies", letters="el")

    assert {language for _, path, language in web.asked if path == "/ypiresies"} == {"el,en;q=0.9"}


@pytest.mark.parametrize(
    "url",
    [
        None,
        "https://bistro-sample.fr/",
        "https://www.bistro-sample.fr",
        "https://paris.bistro-sample.fr/",
        "https://elsewhere-sample.com/bistro-sample.fr/menu",
        "ftp://bistro-sample.fr/menu.pdf",
    ],
    ids=["no-address", "root", "www-root", "subdomain-root", "another-domain", "not-http"],
)
async def test_a_page_that_is_a_home_page_or_not_of_the_site_is_not_fetched(
    url: str | None,
) -> None:
    """Главную уже спросил `page_home`; чужой домен или не `http(s)` — не страница этого сайта."""
    web = Web(
        {
            ("elsewhere-sample.com", "/bistro-sample.fr/menu"): (
                200,
                PAGE.format(title="x", body="y"),
            )
        }
    )

    text = await text_of(web, "bistro-sample.fr", url)

    assert set(web.paths()) == {"/"}
    # Главная хоста — четыре вида адреса (`https`/`http`, с `www.` и без), и не пятый раз.
    assert max(Counter((host, path) for host, path, _ in web.asked).values()) == 4
    assert PAGE_TEXT not in {label for label, _ in text.lines}


@pytest.mark.parametrize(
    ("status", "html"),
    [
        (200, PAGE.format(title="Just a moment...", body="Checking your browser")),
        (
            200,
            PAGE.format(title="Access denied", body="You do not have permission to view this page"),
        ),
        (
            403,
            "<html><head><title>Attention Required</title></head><body>cf-chl- challenge-platform</body></html>",
        ),
        (404, PAGE.format(title="Not found", body="Seite nicht gefunden")),
    ],
    ids=["stub-200", "denied-200", "challenge-403", "not-found"],
)
async def test_a_stub_or_a_refusal_is_not_the_text_of_the_site(
    status: int, html: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="backend.features.sales.segment_text")
    web = Web({("werkstatt-sample.at", "/reparatur"): (status, html)})

    text = await text_of(web, "werkstatt-sample.at", "https://werkstatt-sample.at/reparatur")

    assert [label for label, _ in text.lines] == [PAGE_TITLE]
    assert web.paths().count("/reparatur") == 1
    logged = [r for r in caplog.records if "страница из выдачи не открылась" in r.getMessage()]
    assert [(r.__dict__["host"], bool(r.__dict__["error"])) for r in logged] == [
        ("werkstatt-sample.at", True)
    ]


async def test_the_page_is_not_fetched_when_page_text_is_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Потолок текста страниц 0 — строки текста нет ни у главной, ни у страницы: качать незачем."""
    monkeypatch.setattr(sales_cfg, "SEGMENT_HOME_TEXT_CHARS", 0)
    web = opened("kafe-sample.kz", "/menu", "Кафе в Алматы: завтраки и обеды")

    async with httpx.AsyncClient(transport=httpx.MockTransport(web)) as http:
        found = await serp_page(http, "kafe-sample.kz", "https://kafe-sample.kz/menu")

    assert found is None
    assert web.asked == []


# --- судья по тексту страницы ------------------------------------------------------------------------

FURNITURE = "Мебельная фабрика в Минске: кухни и шкафы на заказ с доставкой по стране"


def _page_text(body: str) -> SiteText:
    return site_text(
        title="Кухни на заказ",
        description=None,
        home=HomeSignals(reached=False, error="не ответила"),
        page=HomeSignals(reached=True, text=body),
    )


async def test_the_judge_quotes_the_page_text_and_the_site_is_not_snippet_only() -> None:
    model, seen = client(in_turn(facts("yes", "кухни и шкафы на заказ", "фабрика мебели")))

    verdict = await decide_any(
        model, host="mebel-sample.by", segment="Мебельные фабрики", text=_page_text(FURNITURE)
    )

    assert (verdict.lane, verdict.quote) == (Lane.ACCEPT, "кухни и шкафы на заказ")
    assert verdict.facts is not None
    assert not verdict.facts.snippet_only
    assert f"{PAGE_TEXT}: {FURNITURE}" in str(seen[0]["messages"][-1]["content"])


async def test_unclear_by_the_page_text_keeps_the_former_words_not_the_snippet_words() -> None:
    model, _ = client(in_turn(facts("unsure", "кухни и шкафы на заказ", "непонятно, чей сайт")))

    verdict = await decide_any(
        model, host="mebel-sample.by", segment="Мебельные фабрики", text=_page_text(FURNITURE)
    )

    assert verdict.reason == f"{UNCLEAR}. Непонятно, чей сайт"
    assert not verdict.reason.startswith(SNIPPET_ONLY)


def test_the_stored_text_with_the_page_line_reads_back_and_a_labeled_quote_holds() -> None:
    """Пересуд и лид читают текст кандидата обратно (`SiteText.parse`): строка страницы — на месте,
    и цитата, которую модель взяла вместе с подписью, находится."""
    text = _page_text(FURNITURE)

    back = SiteText.parse(text.for_model())

    assert back == text
    assert back.holds("кухни и шкафы на заказ")
    labeled = facts("yes", f"{PAGE_TEXT}: кухни и шкафы на заказ", "фабрика")
    found = read(json.dumps(labeled, ensure_ascii=False), back).facts
    assert found is not None
    assert found.quote == "кухни и шкафы на заказ"


#: Длинный текст страницы на украинском: буквы называют язык — при русских письмах «отказал бы».
UKRAINIAN = (
    "Ми виготовляємо меблі для кухні та вітальні з натурального дерева. Наші майстри працюють у "
    "Львові вже п'ятнадцять років, доставляємо замовлення по всій країні та допомагаємо з "
    "монтажем. Замовте безкоштовний замір і отримайте проєкт кухні за три дні."
)


def test_the_language_check_counts_the_words_of_the_page_as_the_words_of_the_site() -> None:
    snippet = site_text(title="Меблі на замовлення", description=None)
    with_page = _page_text(UKRAINIAN)

    found = site_language(with_page, "ru")

    assert site_language(snippet, "ru") is None  # сниппет короткий — не уверены
    assert found is not None
    assert (found.code, found.words) == ("uk", "язык сайта — украинский, письма — русский")


def test_a_switch_to_the_letters_language_in_the_page_text_is_a_version_not_a_mismatch() -> None:
    assert site_language(_page_text(f"UA · RU · {UKRAINIAN}"), "ru") is None


async def test_a_recaptcha_note_in_the_page_text_is_not_a_stub() -> None:
    body = "Fahrradwerkstatt in Graz · Reparatur und Verkauf · This site is protected by reCAPTCHA"
    model, seen = client(in_turn(facts("yes", "Reparatur und Verkauf", "веломастерская")))

    verdict = await decide_any(
        model, host="rad-sample.at", segment="Веломастерские", text=_page_text(body)
    )

    assert sj.by_text(_page_text(body)) is None
    assert (verdict.lane, len(seen)) == (Lane.ACCEPT, 1)


# --- проход судьи: строка страницы — в тексте кандидата ---------------------------------------------


async def test_the_judge_pass_puts_the_page_line_into_the_text_of_the_candidate(
    session: AsyncSession,
) -> None:
    run = await make_run(session, await hypothesis(session))
    host = "dent-sample.pt"
    [found] = await candidates(session, run.id, run.hypothesis_id, host)
    found.page_url = f"https://{host}/implantes"
    await session.commit()
    body = "Clínica dentária em Lisboa: implantes, ortodontia e branqueamento"
    web = opened(host, "/implantes", body)
    model = Model({host: facts("yes", "implantes, ortodontia e branqueamento", "клиника")})

    async with httpx.AsyncClient(transport=httpx.MockTransport(web)) as http:
        await judge_run(
            session, run_id=run.id, segment="Стоматологии", mode=JudgeMode.SHADOW,
            client=judge(model), home=http, letters="pt",
        )  # fmt: skip

    assert f"{PAGE_TEXT}: {body}" in (found.site_text or "")
    assert (found.lane, found.quote) == (Lane.ACCEPT, "implantes, ortodontia e branqueamento")
    assert SiteText.parse(found.site_text or "").holds(found.quote or "")
