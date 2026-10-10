"""Числа формы сборки — с границами на сервере, а не только в поле экрана.

Аудит 10.10.2026: сроки добивок проверялись только длиной списка, и «через 0 дней»
отправляло обе добивки ближайшими проходами, вслед за первым письмом, — три письма
донору за час. «За раз» ограничивал только экран: отрицательное число роняло задачу
ошибкой SQL три раза подряд, повторами. Отказ — до очереди задач и словами: экран
показывает первую строку отказа разбора как есть.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.letters.schemas import LIMIT_MAX, followups_for
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.letters.chain import MAX_DELAY_DAYS, MAX_STEPS
from httpx import AsyncClient, Response
from tests.conftest import bearer
from tests.test_letter_draft import FakeQueue

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: fake)
    return fake


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


async def _build(client: AsyncClient, token: str, **body: Any) -> Response:
    return await client.post(
        "/api/letters/build", json={"campaign": "Май", **body}, headers=bearer(token)
    )


def _refusal(response: Response) -> str:
    """Что увидит человек: экран берёт первую строку отказа разбора."""
    return str(response.json()["detail"][0]["msg"])


class TestFollowupDays:
    @pytest.mark.parametrize("days", [[0, 14], [7, 0], [-1], [MAX_DELAY_DAYS + 1]])
    async def test_a_term_outside_a_day_to_90_is_refused_before_the_queue(
        self, client: AsyncClient, token: str, queue: FakeQueue, days: list[int]
    ) -> None:
        response = await _build(client, token, followup_days=days)

        assert response.status_code == 422, response.text
        assert _refusal(response).startswith("Добивка уходит через 1–90 дней после предыдущего")
        assert "три письма адресату за час" in _refusal(response)
        assert queue.kwargs == []

    @pytest.mark.parametrize("days", [[1, MAX_DELAY_DAYS], [3], []])
    async def test_a_day_to_90_goes_to_the_job_as_is(
        self, client: AsyncClient, token: str, queue: FakeQueue, days: list[int]
    ) -> None:
        """Одна добивка — законная настройка; пусто — сроки по умолчанию."""
        response = await _build(client, token, followup_days=days)

        assert response.status_code == 200, response.text
        assert queue.kwargs[0]["followup_days"] == days


class TestLimit:
    @pytest.mark.parametrize("limit", [-5, 0, LIMIT_MAX + 1])
    async def test_outside_one_to_500_is_refused_in_words(
        self, client: AsyncClient, token: str, queue: FakeQueue, limit: int
    ) -> None:
        response = await _build(client, token, limit=limit)

        assert response.status_code == 422, response.text
        assert _refusal(response) == f"За одну сборку — от 1 до 500 писем, пришло {limit}"
        assert queue.kwargs == []

    async def test_the_ceiling_of_the_screen_is_accepted(
        self, client: AsyncClient, token: str, queue: FakeQueue
    ) -> None:
        response = await _build(client, token, limit=LIMIT_MAX)

        assert response.status_code == 200, response.text
        assert queue.kwargs[0]["limit"] == LIMIT_MAX == 500


class TestPreviewShowsOnlyFollowupsThatGo:
    """Вкладки добивок у письма — те, что уйдут. Раньше их было всегда две, и добивка,
    которой не будет, стояла «через 0 дн.»."""

    def test_one_term_is_one_followup(self) -> None:
        cards = followups_for("donor.example.test", [7])

        assert [(card.step, card.in_days) for card in cards] == [(1, 7)]

    def test_a_zero_term_of_an_old_campaign_shows_none(self) -> None:
        """Рассылка «через 0 дней» из базы: добивок она не отправит — и не показывает."""
        assert followups_for("donor.example.test", [0, 0]) == []

    def test_no_terms_are_the_defaults(self) -> None:
        cards = followups_for("donor.example.test", None)

        assert [card.in_days for card in cards] == list(
            outreach_cfg.FOLLOWUP_DAYS[: MAX_STEPS - 1]
        )
