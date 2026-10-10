"""Гипотеза из окна «Новая гипотеза» — `POST /api/sales/hypotheses` под правом `sales`.

Правило то же, что у команды `sales-hypothesis-add` (`features/sales/hypotheses.add`):
негодное имя — 422 словами, занятое — 409 словами, лишнее поле — отказ схемы. Описание
гипотезы в тексты писем не идёт — поэтому права `send` не нужно; проверено сборкой очереди
гипотезы на выдуманном описании. Имена и домены выдуманы (`*.example.test`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.sales.schemas import HypothesesView, HypothesisBody
from backend.cli.sales import EXIT_TAKEN, run_hypothesis_add
from backend.config import sales as sales_cfg
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import MessageModel
from backend.features.sales import queue
from backend.features.sales.models import NAME_LENGTH, SalesHypothesisModel
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_clean_api import _screen_fields

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
HYPOTHESES = "/api/sales/hypotheses"
NO_RIGHT = "Действие «sales» недоступно этой учётке"
#: Описание, которого не должно быть ни в одном письме и ни в одном вызове модели.
CANARY = "описание-канарейка 7319: кому и зачем пишем"


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def _count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(SalesHypothesisModel)) or 0)


async def test_new_hypothesis_is_created_and_comes_back_as_a_card_of_the_list(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post(
        HYPOTHESES,
        json={"name": "  сайты   EN ", "description": "  редакции и блоги "},
        headers=headers,
    )

    assert response.status_code == 201, response.text
    card = response.json()
    # Имя — ключ выбора: пробелы по краям и двойные внутри приводит ядро, как у консоли.
    assert (card["name"], card["description"]) == ("сайты EN", "редакции и блоги")
    # Карточка — та же, что в списке: каждое состояние названо нулём, время заведения есть.
    assert (card["leads"], card["total"]) == ({"new": 0, "ready": 0, "rejected": 0}, 0)
    assert card["created_at"] is not None
    listed = (await client.get(HYPOTHESES, headers=headers)).json()
    assert listed["rows"] == [card]
    stored = await session.get(SalesHypothesisModel, card["id"])
    assert stored is not None
    assert stored.name == "сайты EN"


async def test_empty_description_is_stored_as_none(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post(HYPOTHESES, json={"name": "сервисы RU"}, headers=headers)
    blank = await client.post(
        HYPOTHESES, json={"name": "сервисы DE", "description": "   "}, headers=headers
    )

    assert (response.status_code, response.json()["description"]) == (201, None)
    assert (blank.status_code, blank.json()["description"]) == (201, None)


async def test_taken_name_is_409_in_words_and_nothing_is_written(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    first = (await client.post(HYPOTHESES, json={"name": "сайты EN"}, headers=headers)).json()

    again = await client.post(HYPOTHESES, json={"name": " сайты  EN"}, headers=headers)

    assert (again.status_code, again.json()["detail"]) == (
        409,
        f"имя «сайты EN» уже у гипотезы №{first['id']}",
    )
    assert await _count(session) == 1


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("", "нет имени (передано '') — по нему гипотезу выбирают при загрузке базы"),
        ("   ", "нет имени (передано '   ') — по нему гипотезу выбирают при загрузке базы"),
        (
            "я" * (NAME_LENGTH + 1),
            f"имя длиннее {NAME_LENGTH} знаков ({NAME_LENGTH + 1}) — нужно короткое",
        ),
    ],
)
async def test_bad_name_is_422_in_words_and_nothing_is_written(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], name: str, words: str
) -> None:
    response = await client.post(HYPOTHESES, json={"name": name}, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (422, words)
    assert await _count(session) == 0


async def test_name_of_the_longest_allowed_length_is_taken(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post(HYPOTHESES, json={"name": "я" * NAME_LENGTH}, headers=headers)

    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    "body",
    [{}, {"name": "сайты EN", "owner": "кто-то"}, {"name": None}, {"name": "сайты EN", "x": 1}],
)
async def test_body_out_of_the_schema_is_refused_before_the_core(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> None:
    """Опечатка в имени поля не должна молча завести гипотезу без описания."""
    response = await client.post(HYPOTHESES, json=body, headers=headers)

    assert response.status_code == 422
    assert await _count(session) == 0


async def test_without_the_sales_right_a_hypothesis_is_refused_in_words(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user(SELLER, permissions={"sales": False})

    response = await client.post(
        HYPOTHESES, json={"name": "сайты EN"}, headers=bearer(await sign_in(SELLER))
    )

    assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)
    assert await _count(session) == 0


async def test_screen_and_console_follow_one_rule(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Урок L63: у заведения два пути — окно экрана и консоль. Заведённое окном консоль
    видит занятым, и слова отказа у обоих путей — одни."""
    made = await client.post(HYPOTHESES, json={"name": "сайты EN"}, headers=headers)
    again = await client.post(HYPOTHESES, json={"name": "сайты EN"}, headers=headers)

    assert await run_hypothesis_add(session, "сайты  EN", None) == EXIT_TAKEN

    assert made.status_code == 201
    assert capsys.readouterr().out == f"Гипотеза не заведена: {again.json()['detail']}\n"


async def test_description_does_not_reach_the_letters_or_the_model(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Почему хватает права `sales`: описание читают только экраны. Сборка очереди гипотезы
    не несёт его ни в тему и текст письма, ни в то, что видит модель."""
    await w.world(session, monkeypatch)
    await session.commit()
    made = await client.post(
        HYPOTHESES, json={"name": "сайты EN", "description": CANARY}, headers=headers
    )
    hypothesis_id = made.json()["id"]
    await w.lead(session, hypothesis_id, "jane@acme.example.test")
    rewriter = w.CorridorRewriter()

    report = await queue.build(session, rewriter, hypothesis_id=hypothesis_id, limit=5)

    assert report.prepared == 1
    letters = list(await session.scalars(select(MessageModel)))
    assert len(letters) == 1
    assert all(CANARY not in f"{letter.subject} {letter.body}" for letter in letters)
    assert rewriter.seen != []
    assert CANARY not in repr(rewriter.seen)


def test_screen_sends_the_hypothesis_by_the_server_names() -> None:
    """Поле, переименованное на сервере, экран отправлял бы мимо — без ошибки."""
    assert _screen_fields("HypothesisBody") == set(HypothesisBody.model_fields)
    assert _screen_fields("HypothesesView") == set(HypothesesView.model_fields)


@pytest.mark.parametrize("enabled", [True, False])
async def test_list_says_whether_the_sales_module_is_on(
    client: AsyncClient, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    """Список гипотез раздел грузит на любой вкладке и в мастере: по нему экран говорит строкой
    под шапкой, что модуль выключен (находка QA на проде: выключенный выглядел рабочим)."""
    monkeypatch.setattr(sales_cfg, "ENABLED", enabled)

    body = (await client.get(HYPOTHESES, headers=headers)).json()

    assert body["module_enabled"] is enabled
