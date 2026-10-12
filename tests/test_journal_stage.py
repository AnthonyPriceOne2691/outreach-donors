"""Этап записи журнала — в момент действия (проверка ветки 12.10.2026).

Учётка без права «Продажи» не видит действий продаж (П2). Узнавать этап потом по строке,
о которой запись, мало: строку удаляют — снятая запись стоп-листа, чистка проб, — и
запись с адресом лида всплывала бы на общем экране. Поэтому общие писатели кладут этап
в саму запись (`AccessRepository.record`, ключ «этап»), а признаки по строкам остаются
для прежних записей.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from backend.features.access.journal import SALES_TRIAL_KEY
from backend.features.access.repository import STAGE_KEY, AccessRepository
from backend.features.agent.drafts import Decider, reject_draft
from backend.features.core.domain import (
    AuditAction,
    DraftStatus,
    MessageStatus,
    ReplyKind,
    Stage,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters.settle import Witness, record_sent
from backend.features.outreach.own_inboxes import OwnInboxes
from backend.features.sales.trials import TrialTrace
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_sender

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ADMIN = "админ@team.example.test"
OUTSIDER = "без-продаж@team.example.test"


@pytest.fixture
async def admin(make_user: MakeUser) -> UserModel:
    return await make_user(ADMIN, role=UserRole.ADMIN)


@pytest.fixture
async def tokens(admin: UserModel, make_user: MakeUser, sign_in: SignIn) -> tuple[str, str]:
    """Пропуск админа со всеми правами и админа без права «Продажи»."""
    await make_user(OUTSIDER, role=UserRole.ADMIN, permissions={"sales": False})
    return await sign_in(ADMIN), await sign_in(OUTSIDER)


async def _targets(client: AsyncClient, token: str) -> set[str]:
    response = await client.get("/api/journal?limit=200", headers=bearer(token))
    assert response.status_code == 200, response.text
    return {entry["target"] for entry in response.json()["entries"]}


async def _campaign(session: AsyncSession, stage: Stage, host: str) -> tuple[int, int]:
    """Рассылка этапа и домен — номера."""
    domain = DomainModel(host=host)
    campaign = CampaignModel(stage=stage, name=f"рассылка {host}")
    session.add_all([domain, campaign])
    await session.flush()
    return campaign.id, domain.id


class TestStageAtTheAction:
    async def test_sales_box_switch_is_hidden_and_the_donor_box_is_not(
        self, client: AsyncClient, session: AsyncSession, tokens: tuple[str, str]
    ) -> None:
        admin, outsider = tokens
        sales_box = await make_sender(session, "hi@mail-sales.example.test")
        sales_box.stage = Stage.SALES
        donor_box = await make_sender(session, "hi@mail-donor.example.test")
        await session.commit()

        for box in (sales_box, donor_box):
            switched = await client.post(
                f"/api/senders/{box.id}/disable", json={"reason": "проверка"}, headers=bearer(admin)
            )
            assert switched.status_code == 200, switched.text

        shown = await _targets(client, outsider)
        assert f"sender:{donor_box.id}" in shown
        assert f"sender:{sales_box.id}" not in shown
        assert f"sender:{sales_box.id}" in await _targets(client, admin)

    async def test_removed_sales_stop_entry_stays_hidden(
        self, client: AsyncClient, tokens: tuple[str, str]
    ) -> None:
        """После снятия строки стоп-листа нет — этап знает только сама запись."""
        admin, outsider = tokens
        added = await client.post(
            "/api/suppressions",
            json={"target": "lead@lead.example.test", "reason": "manual", "stage": "sales"},
            headers=bearer(admin),
        )
        assert added.status_code == 200, added.text
        row_id = added.json()["id"]
        removed = await client.post(
            f"/api/suppressions/{row_id}/remove", json={"reason": "проверка"}, headers=bearer(admin)
        )
        assert removed.status_code == 200, removed.text

        assert f"suppression:{row_id}" not in await _targets(client, outsider)
        assert f"suppression:{row_id}" in await _targets(client, admin)

    async def test_sales_draft_decision_carries_its_stage(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin: UserModel,
        tokens: tuple[str, str],
    ) -> None:
        settings = AgentSettingsModel(
            stage=Stage.SALES,
            version=1,
            enabled=True,
            goal="Довести до созвона",
            tone="коротко",
            points=[],
            stop_topics=[],
        )
        reply = ReplyModel(kind=ReplyKind.HUMAN, raw_body="Интересно, расскажите")
        session.add_all([settings, reply])
        await session.flush()
        draft = AgentDraftModel(
            reply_id=reply.id,
            settings_id=settings.id,
            status=DraftStatus.DRAFTED,
            body="Здравствуйте!",
            model="test",
            prompt_version="test",
            tokens=0,
        )
        session.add(draft)
        await session.flush()

        await reject_draft(
            session, draft.id, reason="другое: проверка", by=Decider(ADMIN, admin.id)
        )
        await session.commit()

        record = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.target == f"agent_draft:{draft.id}")
        )
        assert record is not None
        assert record.details is not None
        assert record.details[STAGE_KEY] == Stage.SALES.value
        assert f"agent_draft:{draft.id}" not in await _targets(client, tokens[1])

    async def test_letter_record_stays_hidden_after_the_letter_is_deleted(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin: UserModel,
        tokens: tuple[str, str],
    ) -> None:
        """Чистка проб удаляет письмо и рассылку: запись «кому» знает этап сама."""
        campaign_id, domain_id = await _campaign(session, Stage.SALES, "lead.example.test")
        message = MessageModel(
            campaign_id=campaign_id,
            domain_id=domain_id,
            idempotency_key="sales:lead.example.test:0",
            status=MessageStatus.SENDING,
        )
        session.add(message)
        await session.flush()
        witness = Witness(
            host="lead.example.test",
            email="ceo@lead.example.test",
            sender_email=None,
            transport="null",
            real=False,
        )
        assert await record_sent(
            session,
            message,
            moment=datetime.now(UTC),
            provider_id=None,
            author_id=admin.id,
            witness=witness,
        )
        await session.commit()
        target = f"message:{message.id}"
        await session.execute(delete(MessageModel).where(MessageModel.id == message.id))
        await session.execute(delete(CampaignModel).where(CampaignModel.id == campaign_id))
        await session.commit()

        assert target not in await _targets(client, tokens[1])
        assert target in await _targets(client, tokens[0])


class TestRecordsBeforeTheStageWasWritten:
    """Записи до этапа в подробностях — по строкам, на которые они ссылаются."""

    async def test_reply_by_its_thread_when_the_letter_is_gone(
        self, client: AsyncClient, session: AsyncSession, tokens: tuple[str, str]
    ) -> None:
        """Этап ответа — правилом ответа: по диалогу, а без диалога — по письму."""
        campaign_id, domain_id = await _campaign(session, Stage.SALES, "lead.example.test")
        thread = ThreadModel(domain_id=domain_id, campaign_id=campaign_id)
        session.add(thread)
        await session.flush()
        reply = ReplyModel(thread_id=thread.id, kind=ReplyKind.HUMAN, raw_body="да")
        session.add(reply)
        await session.flush()
        await AccessRepository(session).record(
            AuditAction.LEAD_TAKEN,
            target=f"reply:{reply.id}",
            details={"от кого ответ": "ceo@lead.example.test"},
        )
        await session.commit()

        assert f"reply:{reply.id}" not in await _targets(client, tokens[1])

    async def test_sales_box_and_sales_trial_prune_without_a_stage(
        self, client: AsyncClient, session: AsyncSession, tokens: tuple[str, str]
    ) -> None:
        box = await make_sender(session, "hi@mail-sales.example.test")
        box.stage = Stage.SALES
        journal = AccessRepository(session)
        await journal.record(AuditAction.USER_UPDATED, target=f"sender:{box.id}")
        await journal.record(
            AuditAction.DATA_PRUNED,
            target="probes:1",
            details={SALES_TRIAL_KEY: {"адреса": ["ceo@lead.example.test"]}},
        )
        await session.commit()

        shown = await _targets(client, tokens[1])
        assert f"sender:{box.id}" not in shown
        assert "probes:1" not in shown


def test_trial_key_is_the_one_the_prune_writes() -> None:
    """Признак прежних записей чистки — ключ, который пишет проба продаж."""
    assert SALES_TRIAL_KEY in TrialTrace(inboxes=OwnInboxes(addresses=())).as_details()
