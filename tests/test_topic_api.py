"""Тематика на экранах: тема в строках «Доноров» и «Рассмотрения», фильтр и счётчики.

Фильтр и счётчик пункта — одним правилом (тема основная или вторая): пункт «Финансы · 2»
находит ровно две строки. Подпись — из общего списка, своей у экрана нет; адрес с кодом
не той формы — отказ словами, а не пустая таблица.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.api.topics.schemas import NOT_DEFINED_LABEL, TopicMark, topic_counts, with_chosen
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.review.candidates import RunReview
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.donor_metrics import measured
from tests.test_review import _run

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@team.example.test", role=UserRole.OPERATOR)
    return await sign_in("оператор@team.example.test")


async def _sites(session: AsyncSession) -> None:
    """Четыре донора: газета со второй темой «Финансы», финансовый блог, путеводитель
    и сайт без темы."""
    rows = {
        "ledger.example.test": ("news", "finance", "городская газета"),
        "money.example.test": ("finance", None, "кредиты и вклады"),
        "trip.example.test": ("travel", None, "путеводитель"),
        "blank.example.test": (None, None, None),
    }
    for host, (topic, second, why) in rows.items():
        domain = await make_donor(session, host)
        domain.site_topic, domain.site_topic_secondary, domain.topic_reason = topic, second, why
    await session.commit()


class TestDonors:
    async def test_rows_carry_the_topic_with_its_label(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        await _sites(session)

        response = await client.get("/api/donors", headers=bearer(token))

        assert response.status_code == 200, response.text
        rows = {row["host"]: row for row in response.json()["rows"]}
        ledger = rows["ledger.example.test"]
        assert ledger["topic"] == {"code": "news", "label": "Новости"}
        assert ledger["topic_secondary"] == {"code": "finance", "label": "Финансы"}
        assert ledger["topic_reason"] == "городская газета"
        assert rows["blank.example.test"]["topic"] is None

    async def test_filter_finds_what_its_item_counts(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        await _sites(session)

        page = (await client.get("/api/donors", headers=bearer(token))).json()
        items = {item["code"]: item for item in page["topics"]}
        assert items["finance"] == {"code": "finance", "label": "Финансы", "count": 2}
        assert page["topics"][-1] == {"code": "none", "label": NOT_DEFINED_LABEL, "count": 1}

        for code, item in items.items():
            found = await client.get(f"/api/donors?topic={code}", headers=bearer(token))
            assert found.status_code == 200, found.text
            assert found.json()["total"] == item["count"], code
        finance = await client.get("/api/donors?topic=finance", headers=bearer(token))
        assert {row["host"] for row in finance.json()["rows"]} == {
            "ledger.example.test",
            "money.example.test",
        }, "и по второй теме"

    async def test_the_chosen_topic_stays_an_item_at_zero(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        """Тема выбрана, а у строк экрана её нет (сменили «Решение») — пункт с нулём и
        подписью: иначе поле показало бы код, а пустой экран назвал бы тему кодом."""
        await _sites(session)

        page = (await client.get("/api/donors?topic=pets", headers=bearer(token))).json()

        assert page["total"] == 0
        assert page["topics"][-1] == {"code": "pets", "label": "Животные", "count": 0}
        assert [item["code"] for item in page["topics"]].count("pets") == 1

    async def test_a_code_of_the_wrong_shape_is_refused(
        self, client: AsyncClient, token: str
    ) -> None:
        response = await client.get("/api/donors?topic=Финансы", headers=bearer(token))
        assert response.status_code == 422


class TestReview:
    async def test_candidates_carry_the_topic(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        run = await _run(session)
        news = await make_donor(session, "ledger.queued.test", review=None)
        news.site_topic, news.topic_reason = "news", "городская газета"
        blank = await make_donor(session, "blank.queued.test", review=None)
        # Очередь кладёт годных по вердикту прогона — донору нужны данные замера.
        for domain in (news, blank):
            await measured(session, domain)
        await RunReview(session).queue_run(run.id, ["ledger.queued.test", "blank.queued.test"])
        await session.commit()

        response = await client.get(f"/api/review/runs/{run.id}", headers=bearer(token))

        assert response.status_code == 200, response.text
        rows = {row["host"]: row for row in response.json()["rows"]}
        assert rows["ledger.queued.test"]["topic"] == {"code": "news", "label": "Новости"}
        assert rows["ledger.queued.test"]["topic_reason"] == "городская газета"
        assert rows["blank.queued.test"]["topic"] is None


def test_items_go_by_count_then_label_and_undefined_last() -> None:
    items = topic_counts({"none": 3, "travel": 2, "finance": 2, "news": 5, "retired": 1})
    # Поровну — по подписи: «Путешествия» раньше «Финансов».
    assert [item.code for item in items] == ["news", "travel", "finance", "retired", "none"]
    assert items[-2].label == "retired", "тему убрали из списка — подпись кодом, а не пропуск"
    assert topic_counts({}) == []


def test_chosen_is_added_once_and_undefined_by_its_words() -> None:
    items = topic_counts({"news": 2})
    assert with_chosen(items, None) == items
    assert with_chosen(items, "news") == items
    assert with_chosen(items, "none")[-1].label == NOT_DEFINED_LABEL


def test_mark_of_nothing_is_nothing() -> None:
    assert TopicMark.of(None) is None
    assert TopicMark.of("") is None
    assert TopicMark.of("pets") == TopicMark(code="pets", label="Животные")
