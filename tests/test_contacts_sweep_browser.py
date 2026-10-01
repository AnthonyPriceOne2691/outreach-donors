"""Ступень браузера: отрисованная страница разбирается так же, как скачанная.

Ревью #118, находка 11: у браузера спрашивали одни адреса, а HTML мимо
`_harvest` не проходил. `contacts-file --browser` поэтому не собирал
каналы связи и отметку о форме ровно с тех сайтов, которые открывает
только браузер, — то есть там, ради чего браузер и включают.

Попутно две вещи той же ступени:
- ссылки с отрисованной главной не открывались никогда: цикл шёл по срезу
  очереди, снятому до того, как ссылки в неё дописывались;
- страница отказа (403 «вы не робот», 503) считалась страницей сайта:
  её форма — форма проверки, а не контактов.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from backend.features.contacts import browser as browser_step
from backend.features.contacts import file_sweep, mx
from backend.features.contacts.ladder import ContactLadder, LadderResult
from backend.features.core.domain import ContactStatus
from tests.contacts_sweep_fakes import FakeRenderer, Web, install, page

HOME = "https://site.com/"
WITH_CHANNELS = page(
    '<a href="https://t.me/sitedesk">Telegram</a><form class="wpcf7"><input name="email"></form>'
)


@pytest.fixture(autouse=True)
def _mx_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


async def _closed_site(renderer: FakeRenderer) -> LadderResult:
    """Лестница прогона по файлу по сайту, который обычному запросу отвечает 403 на всё."""
    closed = httpx.MockTransport(lambda request: httpx.Response(403, request=request))
    async with httpx.AsyncClient(transport=closed) as http:
        ladder = ContactLadder(
            http, renderer=renderer, stop_without_mail=False, collect_handles=True
        )
        return await ladder.find("site.com")


class TestRenderedPageIsHarvested:
    async def test_channels_and_form_from_a_page_only_the_browser_opened(self) -> None:
        result = await _closed_site(FakeRenderer({HOME: WITH_CHANNELS}))

        assert {handle.value for handle in result.handles} == {"sitedesk"}
        assert result.has_form
        assert result.status is ContactStatus.FORM_ONLY

    async def test_address_written_in_words_on_the_site_domain(self) -> None:
        """Тот же разбор, что у обычного обхода: «ads [at] site [dot] com» — адрес."""
        result = await _closed_site(FakeRenderer({HOME: page("пишите: ads [at] site [dot] com")}))

        assert result.contact is not None
        assert result.contact.email == "ads@site.com"

    async def test_file_sweep_row_carries_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, Web({"site.com": 403}), renderer=FakeRenderer({HOME: WITH_CHANNELS}))
        checkpoint = tmp_path / "c.jsonl"

        await file_sweep.sweep(["site.com"], checkpoint=checkpoint, use_browser=True)

        (row,) = file_sweep.rows_from_checkpoint(checkpoint)
        assert (row["telegram"], row["has_form"]) == ("sitedesk", "true")


class TestWhichPagesTheBrowserOpens:
    async def test_section_linked_from_the_home_is_opened(self) -> None:
        """Раздел по ссылке главной лежит где угодно — `/p/42` слагом не угадать."""
        renderer = FakeRenderer(
            {
                HOME: page('<a href="/p/42">Контакты</a>'),
                "https://site.com/p/42": page('<a href="mailto:ads@site.com">почта</a>'),
            }
        )
        result = await _closed_site(renderer)

        assert "https://site.com/p/42" in renderer.opened
        assert result.contact is not None
        assert result.contact.email == "ads@site.com"
        assert len(renderer.opened) <= browser_step.MAX_BROWSER_PAGES

    async def test_link_to_another_domain_is_not_opened(self) -> None:
        renderer = FakeRenderer({HOME: page('<a href="https://other.com/contact/">Contact</a>')})
        await _closed_site(renderer)
        assert not any("other.com" in url for url in renderer.opened)


class _Answer:
    def __init__(self, status: int) -> None:
        self.status = status


class _Tab:
    def __init__(self, status: int, html: str) -> None:
        self._status, self._html = status, html

    async def goto(self, _url: str, **_kwargs: object) -> _Answer:
        return _Answer(self._status)

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def content(self) -> str:
        return self._html


class _Context:
    def __init__(self, tab: _Tab) -> None:
        self._tab = tab

    async def new_page(self) -> _Tab:
        return self._tab

    async def close(self) -> None:
        return None


class _Chromium:
    """Браузер Playwright в той части, которой пользуется `render`."""

    def __init__(self, status: int, html: str) -> None:
        self._tab = _Tab(status, html)

    async def new_context(self, **_kwargs: object) -> _Context:
        return _Context(self._tab)


class TestRefusalPageIsNotTheSite:
    @pytest.mark.parametrize("status", [403, 429, 503])
    async def test_refusal_page_is_no_page(self, status: int) -> None:
        """Проверка «вы не робот» рисует свою форму — это не форма контактов."""
        renderer = browser_step.PlaywrightRenderer()
        renderer._browser = _Chromium(status, page('<form id="challenge-form"></form>'))
        assert await renderer.render(HOME) is None

    async def test_ordinary_page_is_returned(self) -> None:
        renderer = browser_step.PlaywrightRenderer()
        renderer._browser = _Chromium(200, WITH_CHANNELS)
        assert await renderer.render(HOME) == WITH_CHANNELS
