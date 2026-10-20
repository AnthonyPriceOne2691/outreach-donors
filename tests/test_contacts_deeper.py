"""«Искать глубже» на настоящей базе: кому можно, что пишется, как отвечает экрану.

Правило «кому» одно на кнопку, карточку, строку списка и задачу (`deeper_rule`), а
слова отказа (`deeper_refusal`) только называют невыполненное условие — тест гоняет
обе стороны по сочетаниям состояний, как у «Найти адрес». Тело задачи исполняется
целиком на заглушке сайта: сеть, DNS и браузер подменены, платной ступени нет вовсе.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.deeper import deeper_ready, deeper_refusal, deeper_words
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.quality import Candidate
from backend.features.contacts.search import NO_BROWSER, SearchReport
from backend.features.core.domain import ContactSource, ContactStatus, DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.ops.job_outcome import KINDS
from backend.shared.queue import DEEPER_JOB, FORM_LETTER_JOB
from backend.workers import contact_jobs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NOW = datetime.now(UTC)
SEARCHED = NOW - timedelta(days=3)


async def _donor(session: AsyncSession, host: str, **given: Any) -> DonorModel:
    """Донор с исходом поиска; поля по умолчанию — «адреса нет» три дня назад."""
    fields: dict[str, Any] = {
        "contact_status": ContactStatus.NOT_FOUND,
        "attempted_at": SEARCHED,
        "deeper_at": None,
        "review": "accepted",
        "status": DonorStatus.SUITABLE,
        "dr": 40,
        "email": None,
        **given,
    }
    deeper_at = fields["deeper_at"]
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=fields["status"],
        dr=fields["dr"],
        review=fields["review"],
        contact_status=fields["contact_status"],
        contact_attempted_at=fields["attempted_at"],
        contact_deeper_at=deeper_at,
        contact_deeper_note="адреса нет" if deeper_at else None,
    )
    session.add(donor)
    if fields["email"] is not None:
        email = fields["email"]
        session.add(ContactModel(domain_id=domain.id, email=email, source=ContactSource.PAGE))
    await session.flush()
    return donor


#: Состояние донора → ждёт ли он поиска глубже и о чём слова отказа.
STATES: list[tuple[str, dict[str, Any], str | None]] = [
    ("адреса нет после обычного поиска", {}, None),
    ("глубже искали до прошлого поиска", {"deeper_at": SEARCHED - timedelta(days=200)}, None),
    ("глубже уже искали", {"deeper_at": SEARCHED + timedelta(hours=1)}, "Глубже уже искали"),
    ("ещё не искали", {"contact_status": None, "attempted_at": None}, "сначала «Найти адрес»"),
    ("только форма", {"contact_status": ContactStatus.FORM_ONLY}, "очереди «Формы»"),
    ("сайт не ответил", {"contact_status": ContactStatus.NO_ANSWER}, "повторится сам"),
    ("квота платной ступени", {"contact_status": ContactStatus.NO_QUOTA}, "не закончен"),
    ("платный поиск выключен", {"contact_status": ContactStatus.PAID_OFF}, "без платной ступени"),
    ("адрес есть", {"email": "editor@x.example.test"}, "есть адрес"),
    ("кандидат, не донор", {"review": None}, "после решения человека"),
    ("отклонён человеком", {"review": "rejected"}, "отклонил человек"),
    ("не прошёл пороги", {"status": DonorStatus.UNSUITABLE}, "не прошёл пороги"),
    ("крупный сайт", {"dr": 92}, "Адрес не ищут"),
]


class TestWhoMaySearchDeeper:
    pytestmark = pytest.mark.asyncio

    @pytest.mark.parametrize(("state", "fields", "words"), STATES, ids=[s[0] for s in STATES])
    async def test_rule_and_its_words_agree(
        self, session: AsyncSession, state: str, fields: dict[str, Any], words: str | None
    ) -> None:
        donor = await _donor(session, f"{len(state)}.deeper.example.test", **fields)

        ready = donor.id in await deeper_ready(session, [donor.id])
        refusal = await deeper_refusal(session, donor)

        assert ready is (words is None)
        if words is None:
            assert refusal is None
        else:
            assert refusal is not None
            assert words in refusal

    async def test_ready_is_counted_for_a_page_of_rows_at_once(self, session: AsyncSession) -> None:
        ready = await _donor(session, "ready.example.test")
        done = await _donor(session, "done.example.test", deeper_at=NOW)

        assert await deeper_ready(session, [ready.id, done.id]) == {ready.id}
        assert await deeper_ready(session, []) == set()


class TestWords:
    def _report(self, **counters: int) -> SearchReport:
        return SearchReport(counters=dict(counters))

    def test_found_names_the_page(self) -> None:
        result = LadderResult(
            host="x.example.test",
            status=ContactStatus.FOUND,
            contact=Candidate(
                "a@x.example.test", ContactSource.PAGE, page_url="https://x.example.test/p/k/"
            ),
        )
        assert deeper_words(result, self._report()) == "адрес нашёлся на странице /p/k/"

    def test_nothing_found_says_what_was_read_and_that_the_browser_is_missing(self) -> None:
        result = LadderResult(
            host="x.example.test", status=ContactStatus.NOT_FOUND, pages_read=9, reason=""
        )
        report = SearchReport(
            counters={"pages_fetched": 40, "sitemap_sections": 2}, notes=[NO_BROWSER]
        )

        words = deeper_words(result, report)

        assert words.startswith("адреса нет; прочитано страниц 9, запросов к сайту 40")
        assert "разделов из карты сайта 2" in words
        assert "браузера на сервере нет" in words

    def test_unread_site_names_why(self) -> None:
        result = LadderResult(
            host="x.example.test",
            status=ContactStatus.NOT_FOUND,
            reason="сайт не пустил нас (403)",
        )
        words = deeper_words(result, self._report(browser_pages=2))

        assert words.startswith("адреса нет: сайт не пустил нас (403)")
        assert "браузером открыто страниц 2" in words

    def test_form_and_silence_and_nobody(self) -> None:
        form = LadderResult(host="x.example.test", status=ContactStatus.FORM_ONLY)
        silent = LadderResult(
            host="x.example.test", status=ContactStatus.NO_ANSWER, reason="таймаут"
        )

        assert "очередь «Формы»" in deeper_words(form, self._report())
        assert deeper_words(silent, self._report()).startswith("сайт не ответил: таймаут")
        assert "искать было некого" in deeper_words(None, self._report())


EMPTY = "<html><body><p>Новости</p></body></html>"
SITEMAP = """<urlset><url><loc>https://deep.example.test/info/contact-the-team/</loc></url>
</urlset>"""
SECTION = '<html><body><a href="mailto:editor@deep.example.test">почта</a></body></html>'
FORM = '<html><body><form><input name="email"><textarea name="m"></textarea></form></body></html>'


class _NoBrowser:
    """Браузера нет — как в образе прода: пакет не установлен."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class _Closable:
    async def dispose(self) -> None:
        return None


@pytest.fixture
def site(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> Callable[[dict[str, str]], None]:
    """Задача ходит в сайт-заглушку, сессией теста, без браузера и без DNS."""

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    monkeypatch.setattr("backend.features.contacts.search.PlaywrightRenderer", _NoBrowser)
    monkeypatch.setattr(contact_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        contact_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )

    def serve(pages: dict[str, str]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = pages.get(request.url.path)
            if body is None:
                return httpx.Response(404, text="нет")
            kind = "application/xml" if request.url.path.endswith(".xml") else "text/html"
            return httpx.Response(200, text=body, headers={"content-type": kind})

        monkeypatch.setattr(
            "backend.features.contacts.search.guarded_client",
            lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    return serve


class TestTheJob:
    pytestmark = pytest.mark.asyncio

    async def test_deeper_finds_a_section_from_the_sitemap(
        self, session: AsyncSession, site: Callable[[dict[str, str]], None]
    ) -> None:
        donor = await _donor(session, "deep.example.test")
        await session.commit()
        site({"/": EMPTY, "/sitemap.xml": SITEMAP, "/info/contact-the-team/": SECTION})

        report = await contact_jobs._deeper(donor.id)

        assert report["status"] == "found"
        assert report["note"] == "адрес нашёлся на странице /info/contact-the-team/"
        await session.refresh(donor)
        assert donor.contact_status is ContactStatus.FOUND
        assert donor.contact_deeper_at is not None
        assert donor.contact_deeper_note == report["note"]
        emails = await session.scalars(
            select(ContactModel.email).where(ContactModel.domain_id == donor.domain_id)
        )
        assert list(emails) == ["editor@deep.example.test"]
        assert (
            await deeper_refusal(session, donor) == "У донора есть адрес — искать глубже незачем."
        )

    async def test_nothing_found_is_said_and_not_repeated(
        self, session: AsyncSession, site: Callable[[dict[str, str]], None]
    ) -> None:
        donor = await _donor(session, "deep.example.test")
        await session.commit()
        site({"/": EMPTY})

        report = await contact_jobs._deeper(donor.id)

        assert report["status"] == "not_found"
        assert "браузера на сервере нет" in report["note"]
        await session.refresh(donor)
        refusal = await deeper_refusal(session, donor)
        assert refusal is not None
        assert refusal.startswith("Глубже уже искали")

    async def test_form_is_kept_with_its_address(
        self, session: AsyncSession, site: Callable[[dict[str, str]], None]
    ) -> None:
        donor = await _donor(session, "deep.example.test")
        await session.commit()
        site({"/": EMPTY, "/contact/": FORM})

        report = await contact_jobs._deeper(donor.id)

        assert report["status"] == "form_only"
        form_url = await session.scalar(
            select(DomainModel.contact_form_url).where(DomainModel.id == donor.domain_id)
        )
        assert form_url == "https://deep.example.test/contact/"

    async def test_donor_that_stopped_waiting_is_not_walked(
        self, session: AsyncSession, site: Callable[[dict[str, str]], None]
    ) -> None:
        """Между нажатием и задачей донору вписали адрес — задача никуда не ходит."""
        donor = await _donor(session, "deep.example.test", email="hand@deep.example.test")
        await session.commit()
        site({"/": EMPTY})

        report = await contact_jobs._deeper(donor.id)

        assert report["status"] is None
        assert report["walked"] == 0
        assert "искать было некого" in report["note"]


class TestAnUnreadSiteKeepsItsReason:
    pytestmark = pytest.mark.asyncio

    async def test_unread_site_keeps_the_form_it_had(
        self, session: AsyncSession, site: Callable[[dict[str, str]], None]
    ) -> None:
        """Сайт не прочитан — о форме он не сказал ничего: прежняя ссылка остаётся (ревью
        20.10.2026), а не стирается его «адреса нет»."""
        donor = await _donor(session, "deep.example.test")
        domain = await session.get(DomainModel, donor.domain_id)
        assert domain is not None
        domain.contact_form_url = "https://deep.example.test/contact/"
        await session.commit()
        site({})  # всё — 404: главная не открылась ни в одном виде

        await contact_jobs._deeper(donor.id)

        form_url = await session.scalar(
            select(DomainModel.contact_form_url)
            .where(DomainModel.id == donor.domain_id)
            .execution_options(populate_existing=True)
        )
        assert form_url == "https://deep.example.test/contact/"

    async def test_reason_is_stored_with_not_found(
        self, session: AsyncSession, site: Callable[[dict[str, str]], None]
    ) -> None:
        """«Адреса нет» у сайта, который не пустил, хранит причину — карточка её покажет."""
        donor = await _donor(session, "deep.example.test")
        await session.commit()
        site({})  # всё — 404: главная не открылась ни в одном виде

        await contact_jobs._deeper(donor.id)

        await session.refresh(donor)
        assert donor.contact_status is ContactStatus.NOT_FOUND
        assert donor.contact_reason == "главная не открылась (ответ 404)"

    async def test_job_words_are_known_to_the_screen(self) -> None:
        assert KINDS[DEEPER_JOB] == "поиск адреса глубже"
        assert KINDS[FORM_LETTER_JOB] == "текст письма для формы"
