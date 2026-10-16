"""Маршрут помощника описания сегмента `POST /api/sales/segment/helper`: право `sales`, отказы
словами — черновика нет (422), потолок модели выбран (429), модель не ответила (502) — и кэш.

Модель — подставным клиентом с транспортом `httpx.MockTransport` (`test_sales_segment_helper`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.sales.segment_schemas import SEGMENT_LENGTH
from backend.config import llm as llm_cfg
from backend.features.access.permissions import no_right
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import segment_helper as sh
from httpx import AsyncClient
from tests.conftest import bearer
from tests.test_sales_segment_helper import (
    CONCRETE,
    DRAFTS,
    LOOSE,
    MODEL,
    PROBLEMS,
    QUESTIONS,
    Handler,
    client,
    refuses,
    says,
    suggestion,
)

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

HELPER = "/api/sales/segment/helper"
SELLER = "seller@ours.example.test"
OUTSIDER = "outsider@ours.example.test"


@pytest.fixture(autouse=True)
def no_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "SALES_CAP_SHARE", 0.0)
    monkeypatch.setattr(llm_cfg, "SALES_SEGMENT_MODEL", MODEL)


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


class Model:
    """Подставная модель маршрута: каждый вызов — новый клиент на том же ответе; счёт вызовов."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[Any] = []
        self.handler: Handler = says(suggestion())
        monkeypatch.setattr(sh, "HelperClient", self._make)

    def _make(self) -> sh.HelperClient:
        made, seen = client(self.handler)
        self.calls.append(seen)
        return made

    @property
    def asked(self) -> int:
        return sum(len(seen) for seen in self.calls)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Model:
    return Model(monkeypatch)


async def test_the_suggestion_comes_back_and_the_same_draft_is_free_the_second_time(
    client: AsyncClient, headers: dict[str, str], model: Model
) -> None:
    first = await client.post(HELPER, json={"description": LOOSE}, headers=headers)
    again = await client.post(HELPER, json={"description": f" {LOOSE} "}, headers=headers)

    assert first.status_code == 200, first.text
    assert first.json() == {
        "description": CONCRETE,
        "unclear": QUESTIONS,
        "problems": PROBLEMS,
        "loose": True,
        "cached": False,
    }
    assert again.json() == {**first.json(), "cached": True}
    assert model.asked == 1


async def test_a_draft_in_another_script_is_answered_as_is(
    client: AsyncClient, headers: dict[str, str], model: Model
) -> None:
    model.handler = says(suggestion(DRAFTS["ar"], unclear=[DRAFTS["zh"]], problems=[]))

    response = await client.post(HELPER, json={"description": DRAFTS["ar"]}, headers=headers)

    assert response.json() == {
        "description": DRAFTS["ar"],
        "unclear": [DRAFTS["zh"]],
        "problems": [],
        "loose": False,
        "cached": False,
    }


async def test_without_the_sales_right_the_helper_is_refused_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, model: Model
) -> None:
    await make_user(OUTSIDER, permissions={Permission.SALES.value: False})

    response = await client.post(
        HELPER, json={"description": LOOSE}, headers=bearer(await sign_in(OUTSIDER))
    )

    assert response.status_code == 403
    assert response.json()["detail"] == no_right(Permission.SALES)
    assert model.asked == 0


async def test_an_empty_draft_is_422_in_words(
    client: AsyncClient, headers: dict[str, str], model: Model
) -> None:
    response = await client.post(HELPER, json={"description": "  \n "}, headers=headers)

    assert response.status_code == 422
    assert response.json()["detail"] == sh.EMPTY
    assert model.asked == 0


async def test_a_draft_longer_than_the_description_field_is_refused(
    client: AsyncClient, headers: dict[str, str], model: Model
) -> None:
    response = await client.post(
        HELPER, json={"description": "а" * (SEGMENT_LENGTH + 1)}, headers=headers
    )

    assert response.status_code == 422
    assert model.asked == 0


async def test_the_spent_sales_cap_is_429_in_words_before_the_model(
    client: AsyncClient,
    headers: dict[str, str],
    model: Model,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model.handler = says(suggestion(), tokens=60)
    await client.post(HELPER, json={"description": LOOSE}, headers=headers)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 50)

    response = await client.post(HELPER, json={"description": DRAFTS["en"]}, headers=headers)

    assert response.status_code == 429
    assert response.json()["detail"].startswith("дневной потолок вызовов модели продаж выбран")
    assert model.asked == 1


async def test_a_model_that_does_not_answer_is_502_in_words(
    client: AsyncClient, headers: dict[str, str], model: Model
) -> None:
    model.handler = refuses

    response = await client.post(HELPER, json={"description": LOOSE}, headers=headers)

    assert response.status_code == 502
    assert response.json()["detail"].startswith("Помощник не ответил (модель не ответила:")
