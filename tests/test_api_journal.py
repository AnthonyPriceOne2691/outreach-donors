"""Журнал действий по HTTP: только тому, кто заводит учётки; постранично; с фильтрами
по действию, автору и времени; без действий продаж — тому, у кого нет права «Продажи».

Записи журнала пишутся с первого дня, а прочитать их было негде (сверка ТЗ 11.10.2026).
Здесь проверяется то, ради чего маршрут заведён: кто видит, в каком порядке и что
отсекают фильтры — и что действия этапа продаж не утекают на общий экран (П2).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.access.permissions import no_right
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import (
    AuditAction,
    Permission,
    ReplyKind,
    Stage,
    SuppressionReason,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ReplyModel
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ADMIN = "админ@team.example.test"


@pytest.fixture
async def admin(make_user: MakeUser) -> UserModel:
    return await make_user(ADMIN, role=UserRole.ADMIN)


@pytest.fixture
async def token(admin: UserModel, sign_in: SignIn) -> str:
    return await sign_in(ADMIN)


async def _page(client: AsyncClient, token: str, query: str = "") -> dict[str, object]:
    response = await client.get(f"/api/journal{query}", headers=bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def _entries(body: dict[str, object]) -> list[dict[str, object]]:
    entries = body["entries"]
    assert isinstance(entries, list)
    return entries


def _actions(body: dict[str, object]) -> list[str]:
    return [str(entry["action"]) for entry in _entries(body)]


class TestWhoReads:
    async def test_without_pass_nobody(self, client: AsyncClient) -> None:
        assert (await client.get("/api/journal")).status_code == 403

    async def test_operator_is_refused_in_words(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        """Журнал — разбор доступа: входы, права, пароли. Читает тот, кто заводит учётки."""
        await make_user("оператор@team.example.test", role=UserRole.OPERATOR)
        token = await sign_in("оператор@team.example.test")

        response = await client.get("/api/journal", headers=bearer(token))

        assert response.status_code == 403
        assert response.json()["detail"] == no_right(Permission.USERS)

    async def test_admin_reads_who_did_what(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await AccessRepository(session).record(
            AuditAction.THRESHOLDS_CHANGED,
            author_id=admin.id,
            target="settings:4",
            details={"версия": 4, "dr": 25},
        )
        await session.commit()

        entry = _entries(await _page(client, token, "?action=thresholds_changed"))[0]

        assert entry["author_id"] == admin.id
        assert entry["author_email"] == ADMIN
        assert entry["target"] == "settings:4"
        assert entry["details"] == {"версия": 4, "dr": 25}


class TestPages:
    async def test_newest_first_and_no_entry_on_two_pages(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        """Записи одного действия — одним временем транзакции: порядок держит номер записи,
        иначе строка со стыка страниц показывалась бы на обеих или ни на одной."""
        journal = AccessRepository(session)
        for number in range(5):
            await journal.record(
                AuditAction.RUN_STARTED, author_id=admin.id, target=f"run:{number}"
            )
        await session.commit()

        first = await _page(client, token, "?action=run_started&limit=3")
        second = await _page(client, token, "?action=run_started&limit=3&page=2")

        targets = [entry["target"] for entry in _entries(first) + _entries(second)]
        assert targets == [f"run:{number}" for number in reversed(range(5))]
        assert (first["total"], first["limit"], second["page"]) == (5, 3, 2)

    async def test_page_past_the_end_is_empty_but_knows_the_total(
        self, client: AsyncClient, token: str
    ) -> None:
        body = await _page(client, token, "?page=99")

        assert _entries(body) == []
        # Вход админа — уже запись: страница пуста, а журнал — нет.
        assert isinstance(body["total"], int)
        assert body["total"] >= 1


class TestFilters:
    async def test_by_author_and_without_author(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin: UserModel,
        token: str,
        make_user: MakeUser,
    ) -> None:
        other = await make_user("второй@team.example.test", role=UserRole.ADMIN)
        journal = AccessRepository(session)
        await journal.record(AuditAction.RUN_STARTED, author_id=admin.id, target="run:1")
        await journal.record(AuditAction.RUN_STARTED, author_id=other.id, target="run:2")
        # Неудачный вход с чужой почтой — без автора: в фильтре автора его нет.
        await journal.record(AuditAction.LOGIN_FAILED, details={"email": "чужой@x.example.test"})
        await session.commit()

        mine = await _page(client, token, f"?author={other.id}")

        assert {entry["target"] for entry in _entries(mine)} == {"run:2"}
        unknown = _entries(await _page(client, token, "?action=login_failed"))[0]
        assert (unknown["author_id"], unknown["author_email"]) == (None, None)

    async def test_by_time_range_from_start_inclusive_to_end_exclusive(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        moment = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
        journal = AccessRepository(session)
        for days, target in ((-1, "вчера"), (0, "в полдень"), (1, "завтра")):
            await journal.record(AuditAction.PRICE_REVIEWED, author_id=admin.id, target=target)
            await session.flush()
            await session.execute(
                update(AuditLogModel)
                .where(AuditLogModel.target == target)
                .values(created_at=moment + timedelta(days=days))
            )
        await session.commit()

        since, until = "2026-10-05T12:00:00Z", "2026-10-06T12:00:00Z"
        body = await _page(client, token, f"?action=price_reviewed&since={since}&until={until}")

        assert [entry["target"] for entry in _entries(body)] == ["в полдень"]

    async def test_range_that_ends_before_it_starts_is_refused_in_words(
        self, client: AsyncClient, token: str
    ) -> None:
        response = await client.get(
            "/api/journal?since=2026-10-06T00:00:00Z&until=2026-10-05T00:00:00Z",
            headers=bearer(token),
        )

        assert response.status_code == 422
        assert "Поменяйте даты местами" in response.text

    async def test_unknown_action_is_refused_not_ignored(
        self, client: AsyncClient, token: str
    ) -> None:
        """Опечатка в действии — отказ, а не весь журнал под видом отфильтрованного."""
        response = await client.get("/api/journal?action=nonsense", headers=bearer(token))

        assert response.status_code == 422


async def _letter(session: AsyncSession, stage: Stage, host: str) -> MessageModel:
    """Письмо рассылки этапа — строка, по которой узнаётся этап записи о письме."""
    domain = DomainModel(host=host)
    campaign = CampaignModel(stage=stage, name=f"рассылка {host}")
    session.add_all([domain, campaign])
    await session.flush()
    message = MessageModel(
        campaign_id=campaign.id, domain_id=domain.id, idempotency_key=f"{stage}:{host}:0"
    )
    session.add(message)
    await session.flush()
    return message


async def _stop(session: AsyncSession, stage: Stage | None, email: str) -> SuppressionModel:
    row = SuppressionModel(email=email, reason=SuppressionReason.MANUAL, stage=stage)
    session.add(row)
    await session.flush()
    return row


class TestSalesOnlyWithTheRight:
    """П2: данные продаж на общих экранах — только с правом «Продажи». Записи журнала
    этапа у себя не хранят, и он узнаётся по самой записи и по строкам, на которые она
    ссылается."""

    @pytest.fixture
    async def entries(self, session: AsyncSession, admin: UserModel) -> dict[str, bool]:
        """Цель записи → запись о продажах ли она."""
        journal = AccessRepository(session)
        sales_letter = await _letter(session, Stage.SALES, "lead.example.test")
        donor_letter = await _letter(session, Stage.DONORS, "donor.example.test")
        sales_stop = await _stop(session, Stage.SALES, "lead@lead.example.test")
        # Ответ лида — на письмо продаж: этап узнаётся через письмо.
        lead_reply = ReplyModel(message_id=sales_letter.id, kind=ReplyKind.HUMAN, raw_body="да")
        session.add(lead_reply)
        await session.flush()
        common_stop = await _stop(session, None, "anyone@donor.example.test")
        made = {
            "sales_hypothesis:3": (AuditAction.SALES_LEADS_IMPORTED, {"загружено": 10}, True),
            "sales_handoff:7": (AuditAction.USER_UPDATED, {"действие": "передан в CRM"}, True),
            "job:sales-build": (
                AuditAction.RUN_STARTED,
                {"действие": "сборка очереди продаж", "писем": 5},
                True,
            ),
            "agent_settings:9": (AuditAction.AGENT_SETTINGS_CHANGED, {"этап": "sales"}, True),
            "job:letters": (AuditAction.RUN_STARTED, {"этап": "продажи"}, True),
            f"message:{sales_letter.id}": (AuditAction.LETTER_SENT, {"кому": "lead"}, True),
            f"suppression:{sales_stop.id}": (AuditAction.SUPPRESSION_ADDED, {}, True),
            f"reply:{lead_reply.id}": (AuditAction.LEAD_TAKEN, {"действие": "взят"}, True),
            f"message:{donor_letter.id}": (AuditAction.LETTER_SENT, {"кому": "donor"}, False),
            f"suppression:{common_stop.id}": (AuditAction.SUPPRESSION_ADDED, {}, False),
            "run:5": (AuditAction.RUN_STARTED, {"ключей": 3}, False),
            "job:donor-letters": (AuditAction.RUN_STARTED, {"этап": "доноры"}, False),
        }
        for target, (action, details, _) in made.items():
            await journal.record(action, author_id=admin.id, target=target, details=details)
        await session.commit()
        return {target: sales for target, (_, _, sales) in made.items()}

    async def test_without_the_right_sales_entries_are_not_shown(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        entries: dict[str, bool],
    ) -> None:
        await make_user(
            "без-продаж@team.example.test", role=UserRole.ADMIN, permissions={"sales": False}
        )
        token = await sign_in("без-продаж@team.example.test")

        body = await _page(client, token, "?limit=200")

        shown = {entry["target"] for entry in _entries(body)}
        assert shown & set(entries) == {target for target, sales in entries.items() if not sales}
        assert body["sales_hidden"] is True

    async def test_with_the_right_everything_is_shown(
        self, client: AsyncClient, token: str, entries: dict[str, bool]
    ) -> None:
        body = await _page(client, token, "?limit=200")

        assert set(entries) <= {entry["target"] for entry in _entries(body)}
        assert body["sales_hidden"] is False
