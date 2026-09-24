"""Текст первого письма у рассылки: правка на экране перед созданием.

Три вещи здесь стоят дорого, если ошибиться: правка, прошедшая мимо
разбора шаблона (письмо без подписи или с условиями в модели), правка,
молча проигнорированная у найденной рассылки (адресат получает не тот
текст, что утвердили), и сборка, взявшая умолчание вместо текста рассылки.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import draft
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.guards import ForbiddenContentError
from backend.features.letters.template import TemplateError, ZoneKind
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.test_letters_queue import FakeRewriter

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


def _zones(**changes: str) -> dict[str, str]:
    zones = {z.name: z.text for z in draft.default_draft().zones}
    zones.update(changes)
    return zones


def _subject() -> str:
    return draft.default_draft().subject


class TestDraft:
    def test_default_draft_is_the_template_in_code(self) -> None:
        """Экран показывает тот же текст, что лежит в коде, и с видами зон:
        человек должен видеть, что перепишет модель, а что уйдёт как есть."""
        shown = draft.default_draft()

        kinds = {z.name: z.kind for z in shown.zones}
        assert kinds["ask"] is ZoneKind.REWRITE
        assert kinds["terms"] is ZoneKind.FIXED
        assert all(z.title != z.name for z in shown.zones)

    def test_unchanged_draft_parses_back(self) -> None:
        text = draft.to_text(_subject(), _zones())

        assert "[ask] rewrite" in text
        assert "[terms] fixed" in text

    def test_edit_lands_in_its_zone(self) -> None:
        text = draft.to_text(_subject(), _zones(terms="We pay within 48 hours of publication."))

        assert "[terms] fixed\nWe pay within 48 hours of publication." in text

    def test_missing_signature_is_refused(self) -> None:
        with pytest.raises(TemplateError, match="sender_name"):
            draft.to_text(_subject(), _zones(signature="Best regards,\nThe team"))

    def test_dropped_zone_is_refused(self) -> None:
        zones = _zones()
        del zones["offer"]

        with pytest.raises(TemplateError, match="offer"):
            draft.to_text(_subject(), zones)

    def test_screen_cannot_add_a_zone(self) -> None:
        """Набор зон задан требованиями: экран правит содержимое, а не устройство."""
        with pytest.raises(TemplateError, match="bonus"):
            draft.to_text(_subject(), _zones(bonus="Free article!"))

    def test_hash_line_would_vanish_and_is_refused(self) -> None:
        """«#» в начале строки разбор считает комментарием: абзац пропал бы
        из письма молча."""
        with pytest.raises(TemplateError, match="«#»"):
            draft.to_text(_subject(), _zones(terms="#1 priority: we pay promptly."))

    def test_zone_header_inside_text_is_refused(self) -> None:
        with pytest.raises(TemplateError, match="заголовок зоны"):
            draft.to_text(_subject(), _zones(terms="We pay.\n[legal] fixed"))

    def test_unknown_placeholder_names_the_allowed_ones(self) -> None:
        with pytest.raises(TemplateError, match=r"\{\{first_name\}\}.*\{\{host\}\}"):
            draft.to_text(_subject(), _zones(greeting="Hi {{first_name}},"))

    def test_metrics_are_refused_from_the_screen_too(self) -> None:
        """Файл шаблона проходит ревью, экран — нет. Правило Ahrefs то же."""
        with pytest.raises(ForbiddenContentError):
            draft.to_text(_subject(), _zones(offer="We only work with sites above DR 50."))

    def test_rewritable_part_too_short_is_refused(self) -> None:
        """Урезанные переписываемые зоны делают коридор недостижимым —
        об этом надо узнать на экране, а не по сотне писем «ниже коридора»."""
        with pytest.raises(TemplateError, match="недостижим"):
            draft.to_text(_subject(), _zones(greeting="Hi,", opening="Hello.", ask="Price?"))

    def test_multiline_subject_is_refused(self) -> None:
        with pytest.raises(TemplateError, match="одна строка"):
            draft.to_text("Guest article\non {{host}}", _zones())

    def test_same_text_is_no_conflict(self) -> None:
        draft.assert_same(campaign="Май", stored="text", sent="text")

    def test_other_text_of_a_found_campaign_is_a_conflict(self) -> None:
        with pytest.raises(draft.LetterConflictError, match="Май"):
            draft.assert_same(campaign="Май", stored=None, sent="text")


class FakeJob:
    id = "job-из-теста"


class FakeQueue:
    def __init__(self) -> None:
        self.kwargs: list[dict[str, Any]] = []

    def enqueue(self, _: str, *__: Any, **kwargs: Any) -> FakeJob:
        self.kwargs.append(kwargs)
        return FakeJob()


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: fake)
    return fake


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


def _letter(**changes: str) -> dict[str, Any]:
    return {"subject": _subject(), "zones": _zones(**changes)}


class TestBuildRoute:
    async def test_screen_gets_the_default_letter(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        response = await client.get("/api/letters", headers=bearer(admin_token))

        shown = response.json()["letter_default"]
        assert shown["subject"] == _subject()
        assert shown["zones"][0]["name"] == "greeting"
        assert {z["kind"] for z in shown["zones"]} == {"rewrite", "fixed"}

    async def test_edited_letter_travels_to_the_job(
        self, client: AsyncClient, admin_token: str, queue: FakeQueue
    ) -> None:
        response = await client.post(
            "/api/letters/build",
            json={"campaign": "Май", "letter": _letter(terms="We pay within 48 hours.")},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200
        assert "We pay within 48 hours." in queue.kwargs[0]["letter_template"]

    async def test_untouched_letter_sends_nothing(
        self, client: AsyncClient, admin_token: str, queue: FakeQueue
    ) -> None:
        await client.post(
            "/api/letters/build", json={"campaign": "Май"}, headers=bearer(admin_token)
        )

        assert queue.kwargs[0]["letter_template"] is None
        # Сборка уходит с повторами временных сбоев и с итогом на неделю:
        # без планировщика и этих параметров упавшая сборка молчала бы.
        assert queue.kwargs[0]["retry"].max == 3
        assert queue.kwargs[0]["retry"].intervals == [30, 120, 600]
        assert queue.kwargs[0]["result_ttl"] == 7 * 24 * 60 * 60

    async def test_broken_letter_is_refused_before_the_queue(
        self, client: AsyncClient, admin_token: str, queue: FakeQueue
    ) -> None:
        """Отказ из задачи человек увидел бы через минуты и не у формы."""
        response = await client.post(
            "/api/letters/build",
            json={"campaign": "Май", "letter": _letter(signature="Thanks")},
            headers=bearer(admin_token),
        )

        assert response.status_code == 400
        assert "sender_name" in response.json()["detail"]
        assert queue.kwargs == []

    async def test_other_text_for_a_found_campaign_is_refused(
        self, client: AsyncClient, admin_token: str, queue: FakeQueue, session: AsyncSession
    ) -> None:
        session.add(CampaignModel(stage=Stage.DONORS, name="Май", status="draft"))
        await session.commit()

        response = await client.post(
            "/api/letters/build",
            json={"campaign": "Май", "letter": _letter(terms="We pay within 48 hours.")},
            headers=bearer(admin_token),
        )

        assert response.status_code == 409
        assert "новая рассылка" in response.json()["detail"]
        assert queue.kwargs == []


class TestBuilderUsesTheCampaignText:
    async def test_campaign_text_wins_over_the_default(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")
        text = draft.to_text(_subject(), _zones(terms="We pay within 48 hours."))

        await QueueBuilder(session, FakeRewriter()).build(  # type: ignore[arg-type]
            BuildRequest(campaign_name="Май", letter_template=text)
        )
        await session.flush()

        letter = (await session.execute(select(MessageModel))).scalars().one()
        campaign = (await session.execute(select(CampaignModel))).scalars().one()
        assert letter.body is not None
        assert "We pay within 48 hours." in letter.body
        assert campaign.letter_template == text

    async def test_found_campaign_keeps_its_own_text(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Сборка дополняет рассылку тем текстом, что утвердили при создании."""
        first = draft.to_text(_subject(), _zones(terms="We pay within 48 hours."))
        session.add(
            CampaignModel(stage=Stage.DONORS, name="Май", status="draft", letter_template=first)
        )
        await make_donor(session, "one.example.test", email="info@one.example.test")
        await session.flush()

        await QueueBuilder(session, FakeRewriter()).build(  # type: ignore[arg-type]
            BuildRequest(campaign_name="Май")
        )
        await session.flush()

        letter = (await session.execute(select(MessageModel))).scalars().one()
        assert letter.body is not None
        assert "We pay within 48 hours." in letter.body
