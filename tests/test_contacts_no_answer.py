"""Сайт, который не ответил, — не «адреса нет»: повтор по сроку и с пределом.

Поиск по базе записывал обрыв, таймаут, 5xx и 429 как `not_found`, и донор,
недоступный в момент сбоя, ждал повтора 180 дней. Решение Anthony 01.10.2026:
у такого прохода свой исход `no_answer` с причиной; повтор — следующий
прогон, через день, через неделю; четвёртый проход без ответа — `not_found`.
Платная ступень в проходе без ответа не зовётся — кроме последнего.

Граница «не ответил» уже, чем у прогона по файлу: закрытый сайт (401/403)
и частично открытый — ответ; исключение — обрыв на разделе рекламы или
контактов, где и лежит адрес (ревью e6). Расписание и запись исхода —
`test_contacts_attempts.py`.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.ladder import ContactLadder, LadderResult
from backend.features.contacts.provider import Quota
from backend.features.contacts.quality import Candidate
from backend.features.contacts.search import search_contacts
from backend.features.contacts.sweep_trace import SiteTrace, current_silence, traced, watch
from backend.features.core.domain import ContactSource, ContactStatus
from backend.features.core.models.donor import DonorModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_contacts_attempts import _state

pytestmark = pytest.mark.asyncio

#: Соединение не установилось.
DOWN = "<обрыв>"
#: Соединение есть, ответа не дождались.
SLOW = "<таймаут>"

HOME = (
    '<html><body><a href="/contact/">Contact</a><a href="/privacy/">Privacy</a>'
    "<p>адреса нет</p></body></html>"
)
NOTHING = "<html><body>адреса нет</body></html>"


@pytest.fixture(autouse=True)
def _mx_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


class Site:
    """Сайт-заглушка: путь → ответ (страница, код, `DOWN`, `SLOW`); остальное — `default`."""

    def __init__(self, pages: dict[str, str | int] | None = None, *, default: str | int = 404):
        self.pages = pages or {}
        self.default = default

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host not in ("site.com", "www.site.com"):
            return httpx.Response(404, request=request)  # RDAP и прочие чужие хосты
        reply = self.pages.get(request.url.path, self.default)
        if reply == DOWN:
            raise httpx.ConnectError("соединение не установилось", request=request)
        if reply == SLOW:
            raise httpx.ReadTimeout("ответа не дождались", request=request)
        if isinstance(reply, int):
            return httpx.Response(reply, text="нет", request=request)
        return httpx.Response(200, html=reply, request=request)


class Paid:
    """Платная ступень: считает обращения, по ним видно, за что платим."""

    name = "fake"

    def __init__(self, emails: tuple[str, ...] = ()) -> None:
        self.emails = emails
        self.calls: list[str] = []

    async def quota(self) -> Quota:
        return Quota(used=0, available=100)

    async def find_emails(self, host: str) -> list[Candidate]:
        self.calls.append(host)
        return [Candidate(e, ContactSource.PROVIDER, confidence=90) for e in self.emails]


async def _find(site: Site, paid: Paid, *, last_try: bool = False) -> LadderResult:
    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
        watch(http)
        ladder = ContactLadder(http, provider=paid, silence=current_silence)
        with traced("site.com"):
            return await ladder.find("site.com", last_try=last_try)


class TestTheLadderHearsSilence:
    async def test_silent_site_is_no_answer_and_not_paid_for(self) -> None:
        paid = Paid(("editor@site.com",))
        result = await _find(Site(default=DOWN), paid)
        assert result.status is ContactStatus.NO_ANSWER
        assert result.reason == "нет ответа: обрыв или таймаут"
        assert paid.calls == []

    async def test_the_last_try_pays_even_for_silence(self) -> None:
        """Платному сервису живой сайт не нужен, а «не ответил» часто значит,
        что сайт молча режет адрес нашего сервера."""
        paid = Paid(("editor@site.com",))
        result = await _find(Site(default=SLOW), paid, last_try=True)
        assert paid.calls == ["site.com"]
        assert result.status is ContactStatus.FOUND

    @pytest.mark.parametrize("code", [429, 500, 503])
    async def test_rate_limit_and_server_errors_are_silence(self, code: int) -> None:
        paid = Paid()
        result = await _find(Site(default=code), paid)
        assert result.status is ContactStatus.NO_ANSWER
        assert str(code) in result.reason
        assert paid.calls == []

    @pytest.mark.parametrize("code", [401, 403])
    async def test_closed_site_is_an_answer(self, code: int) -> None:
        """Повтор закрытый сайт не откроет: лестница идёт до конца, как раньше."""
        paid = Paid()
        result = await _find(Site(default=code), paid)
        assert result.status is ContactStatus.NOT_FOUND
        assert paid.calls == ["site.com"]

    async def test_walk_cut_on_the_contact_page_is_silence(self) -> None:
        """Адрес лежит на странице контактов: платить за худший, пока лучший
        просто не ответил, — худшее из двух (ревью e6)."""
        paid = Paid()
        result = await _find(Site({"/": HOME, "/contact/": SLOW}), paid)
        assert result.status is ContactStatus.NO_ANSWER
        assert result.reason.startswith("обход оборван на /contact/")
        assert paid.calls == []

    async def test_walk_cut_elsewhere_is_an_answer(self) -> None:
        paid = Paid()
        result = await _find(Site({"/": HOME, "/privacy/": SLOW}), paid)
        assert result.status is ContactStatus.NOT_FOUND
        assert paid.calls == ["site.com"]

    async def test_open_site_without_contacts_is_an_answer(self) -> None:
        paid = Paid()
        result = await _find(Site({"/": NOTHING}), paid)
        assert result.status is ContactStatus.NOT_FOUND
        assert paid.calls == ["site.com"]

    async def test_without_the_hook_the_ladder_is_as_before(self) -> None:
        """Прогон по файлу крючка не даёт: там своё правило повтора."""
        paid = Paid()
        async with httpx.AsyncClient(transport=httpx.MockTransport(Site(default=DOWN))) as http:
            result = await ContactLadder(http, provider=paid).find("site.com")
        assert result.status is ContactStatus.NOT_FOUND
        assert paid.calls == ["site.com"]


class TestTheTraceMidPass:
    """Лестница спрашивает след посреди прохода — перед платной ступенью и
    в конце спуска, — а запрос без ответа засчитывается отказом только
    следующим запросом или при закрытии следа: последний ещё «ждёт»."""

    async def test_a_lone_request_without_a_reply_is_silence(self) -> None:
        trace = SiteTrace.of("site.com")
        trace.sent(httpx.URL("https://site.com/"))
        assert trace.silence() == "нет ответа: обрыв или таймаут"

    async def test_a_contact_page_without_a_reply_is_silence(self) -> None:
        trace = SiteTrace.of("site.com")
        trace.sent(httpx.URL("https://site.com/"))
        home = httpx.Request("GET", "https://site.com/")
        trace.received(httpx.Response(200, html=NOTHING, request=home))
        trace.sent(httpx.URL("https://site.com/contact/"))
        assert trace.silence() == "обход оборван на /contact/ — нет ответа: обрыв или таймаут"


class TestTheWholePass:
    async def test_a_silent_donor_end_to_end(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Четыре настоящих прохода поиска по базе: три без платной ступени,
        четвёртый — с ней, и после него домен «адреса нет» на обычный срок."""
        paid = Paid()

        async def paid_step(_http: httpx.AsyncClient, _report: object) -> Paid:
            return paid

        site = Site(default=DOWN)
        monkeypatch.setattr("backend.features.contacts.search._paid_step", paid_step)
        monkeypatch.setattr(
            "backend.features.contacts.search.guarded_client",
            lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(site)),
        )
        domain = await make_donor(session, "site.com")

        async def back_in_time(days: int) -> None:
            await session.execute(
                update(DonorModel)
                .where(DonorModel.domain_id == domain.id)
                .values(contact_attempted_at=DonorModel.contact_attempted_at - timedelta(days=days))
            )

        reports = []
        for days in (0, 0, 1, 7):
            await back_in_time(days)
            reports.append(await search_contacts(session, limit=10))

        assert [report.walked for report in reports] == [1, 1, 1, 1]
        assert [report.counters["no_answer"] for report in reports] == [1, 1, 1, 1]
        assert paid.calls == ["site.com"], "платили не только в последний проход"
        status, tries, reason = await _state(session, "site.com")
        assert (status, tries) == (ContactStatus.NOT_FOUND, 0)
        assert reason is not None
        assert reason.startswith("сдались после 4 проходов без ответа")
        assert (await search_contacts(session, limit=10)).walked == 0
