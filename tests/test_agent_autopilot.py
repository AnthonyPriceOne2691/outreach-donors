"""Автопилот агента: ответ в границах уходит сам, остальное — человеку с причиной.

Из заготовки соседней сессии (110d89c), на шве этапов. Проверяется то, ради
чего границы механические и ключей три: без флага этапа в коде автопилота
нет ни на одном этапе, даже при включённом сервере; сумма за пределом по
стороне цены этапа, неуверенный разбор, переписка человека и отказ пути
отправки не уходят наружу, а отдают черновик человеку; выключенный сервер и
режим черновиков не трогают почту вовсе; включить может только тот, кому
можно отправлять.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import Decimal

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.agent import autopilot, drafting
from backend.features.agent.drafting import DraftOutcome
from backend.features.agent.settings import AUTOPILOT, AgentSettingsRepository, defaults
from backend.features.agent.stages import DraftNotice, PriceSide
from backend.features.agent.writer import Written
from backend.features.core.domain import DraftStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.sending import Sending
from backend.features.letters.transport import NullTransport, Outgoing
from backend.workers import agent_jobs
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_sender
from tests.migration_helpers import columns_down_and_up
from tests.test_agent_drafting import FakeWriter, donors_with, reply, stored
from tests.test_replies_inbox import sent

__all__ = ["reply", "sent"]  # фикстуры черновика — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

BODY = {
    "enabled": True,
    "goal": "Узнать цену",
    "tone": "Коротко",
    "points": [],
    "price_limit_usd": "150.00",
    "stop_topics": [],
    "mode": "autopilot",
    "max_turns": 2,
}


class Recording(NullTransport):
    def __init__(self) -> None:
        self.seen: list[Outgoing] = []

    async def send(self, outgoing: Outgoing) -> str:
        self.seen.append(outgoing)
        return await super().send(outgoing)


async def _pilot(
    session: AsyncSession, *, limit: str | None = "150", mode: str = AUTOPILOT
) -> None:
    settings = replace(
        defaults(Stage.DONORS),
        mode=mode,
        price_limit_usd=None if limit is None else Decimal(limit),
    )
    await AgentSettingsRepository(session).save(Stage.DONORS, settings, author="a@x")
    await session.commit()


async def _drafted(session: AsyncSession, reply: ReplyModel, body: str) -> int:
    if reply.confidence is None:
        reply.confidence = 0.95  # как после уверенного разбора: черновик пишется после него
    writer = FakeWriter(Written(body=body, needs_human=False, reason=None, tokens=10))
    outcome = await drafting.draft_answer(session, writer, reply.id, again=True)
    await session.commit()
    assert outcome.draft_id is not None, outcome.skipped
    return outcome.draft_id


@pytest.fixture
def allowed(monkeypatch: pytest.MonkeyPatch, filled_legal: None) -> None:
    """Все три ключа: флаг этапа в коде (подменой реестра), сервер, режим — в тесте."""
    monkeypatch.setattr(outreach_cfg, "AGENT_AUTOPILOT", True)
    donors_with(monkeypatch, autopilot=True)


async def _accepted(session: AsyncSession, sent: MessageModel) -> None:
    """Донор принят человеком, первое письмо ушло с ящика переписки — иначе
    путь отправки отказал бы любому письму."""
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == sent.domain_id))
    assert donor is not None
    donor.review = "accepted"
    sent.sender_id = (await make_sender(session, "anna@mail.test")).id
    await session.commit()


class TestBounds:
    @pytest.mark.parametrize(
        ("side", "limit", "text", "why"),
        [
            (PriceSide.BUY, "150", "We can pay $120 for one article.", None),
            (PriceSide.BUY, "150", "Would $180 work for you?", "дороже предела"),
            (PriceSide.SELL, "200", "The price is $250.", None),
            (PriceSide.SELL, "200", "We can do it for $150.", "дешевле предела"),
            (PriceSide.BUY, None, "Would $90 work?", "предела цены нет"),
            (PriceSide.BUY, None, "Which topics do you accept?", None),
        ],
    )
    def test_money_in_the_draft_against_the_limit(
        self, side: PriceSide, limit: str | None, text: str, why: str | None
    ) -> None:
        found = autopilot.money_beyond(side, None if limit is None else Decimal(limit), text)
        assert (found is None) is (why is None)
        if why is not None:
            assert why in (found or "")

    def test_human_led_thread_and_spent_turns(self) -> None:
        assert autopilot.turns_left([], [], 2) is None
        assert autopilot.turns_left([5], [5], 2) is None
        assert "ведёт человек" in (autopilot.turns_left([5, 6], [5], 2) or "")
        assert "уже ответил здесь 2 раз" in (autopilot.turns_left([5, 6], [5, 6], 2) or "")


@pytest.mark.usefixtures("allowed")
class TestFlight:
    async def test_draft_within_bounds_goes_out_by_the_human_path(
        self, session: AsyncSession, reply: ReplyModel, sent: MessageModel
    ) -> None:
        await _accepted(session, sent)
        await _pilot(session)
        draft_id = await _drafted(session, reply, "Thanks! We can pay $120 for a guide.")
        transport = Recording()

        flown = await autopilot.run(session, Sending(session, transport), draft_id)

        assert flown.held is None, flown.held
        [letter] = transport.seen
        assert letter.in_reply_to == reply.inbound_message_id  # ветка к его письму
        message = await session.get(MessageModel, flown.sent_message_id)
        assert message is not None
        assert (message.step, message.answers_reply_id) == (ANSWER_STEP, reply.id)
        draft = await stored(session, reply.id)
        assert (draft.status, draft.decided_by, draft.edited) == (
            DraftStatus.SENT,
            "autopilot",
            False,
        )
        assert draft.sent_message_id == message.id

    @pytest.mark.parametrize(
        ("body", "limit", "why"),
        [
            ("We can pay $500 for one post.", "150", "дороже предела"),
            ("Would $90 work for you?", None, "предела цены нет"),
        ],
    )
    async def test_money_beyond_the_limit_waits_for_a_human(
        self, session: AsyncSession, reply: ReplyModel, body: str, limit: str | None, why: str
    ) -> None:
        await _pilot(session, limit=limit)
        draft_id = await _drafted(session, reply, body)
        transport = Recording()

        flown = await autopilot.run(session, Sending(session, transport), draft_id)

        assert transport.seen == []
        assert why in (flown.held or "")
        draft = await stored(session, reply.id)
        assert draft.status is DraftStatus.ESCALATED
        assert "автопилот не отправил" in (draft.reason or "")

    async def test_send_path_refusal_goes_to_a_human_not_a_crash(
        self, session: AsyncSession, reply: ReplyModel
    ) -> None:
        """Донор не принят человеком — путь отправки отказывает, как отказал бы
        человеку; автопилот отдаёт черновик с этой причиной, а не падает."""
        await _pilot(session)
        draft_id = await _drafted(session, reply, "Thanks! Which topics do you accept?")
        transport = Recording()

        flown = await autopilot.run(session, Sending(session, transport), draft_id)

        assert transport.seen == []
        assert "отправка отказала" in (flown.held or "")

    async def test_unsure_price_parse_waits_for_a_human(
        self, session: AsyncSession, reply: ReplyModel
    ) -> None:
        reply.confidence = 0.1  # разбор не уверен, человек не смотрел
        await session.commit()
        await _pilot(session)
        draft_id = await _drafted(session, reply, "Thanks! Which topics do you accept?")

        flown = await autopilot.run(session, Sending(session, Recording()), draft_id)

        assert "разбор цены" in (flown.held or "")

    async def test_human_led_thread_is_left_to_the_human(
        self, session: AsyncSession, reply: ReplyModel, sent: MessageModel
    ) -> None:
        await _pilot(session)
        draft_id = await _drafted(session, reply, "Thanks! Which topics do you accept?")
        sent.answers_reply_id = reply.id  # человек уже ответил в этой переписке
        await session.commit()

        flown = await autopilot.run(session, Sending(session, Recording()), draft_id)

        assert "ведёт человек" in (flown.held or "")


@pytest.mark.usefixtures("filled_legal")
class TestOff:
    async def test_no_stage_has_it_even_with_the_server_switch_on(
        self, session: AsyncSession, reply: ReplyModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "AGENT_AUTOPILOT", True)
        await _pilot(session)
        draft_id = await _drafted(session, reply, "Thanks! Which topics do you accept?")
        transport = Recording()

        flown = await autopilot.run(session, Sending(session, transport), draft_id)

        assert flown == autopilot.AutopilotOutcome()
        assert transport.seen == []
        assert all(autopilot.refusal(stage) is not None for stage in Stage)

    async def test_server_switch_off_touches_nothing(
        self, session: AsyncSession, reply: ReplyModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        donors_with(monkeypatch, autopilot=True)
        monkeypatch.setattr(outreach_cfg, "AGENT_AUTOPILOT", False)
        await _pilot(session)
        draft_id = await _drafted(session, reply, "Thanks! Which topics do you accept?")

        flown = await autopilot.run(session, Sending(session, Recording()), draft_id)

        assert flown == autopilot.AutopilotOutcome()

    @pytest.mark.usefixtures("allowed")
    async def test_drafts_mode_touches_nothing(
        self, session: AsyncSession, reply: ReplyModel
    ) -> None:
        await _pilot(session, mode="drafts")
        draft_id = await _drafted(session, reply, "Thanks! Which topics do you accept?")

        flown = await autopilot.run(session, Sending(session, Recording()), draft_id)

        assert flown == autopilot.AutopilotOutcome()

    async def test_job_skips_the_mail_where_it_is_not_allowed(
        self, session: AsyncSession, reply: ReplyModel
    ) -> None:
        notice = DraftNotice(
            draft_id=1,
            reply_id=reply.id,
            thread_id=reply.thread_id or 0,
            stage=Stage.DONORS,
            status=DraftStatus.DRAFTED,
            reason=None,
        )
        outcome = DraftOutcome(reply_id=reply.id, notice=notice)
        held = agent_jobs._after(outcome, autopilot.AutopilotOutcome(held="дороже предела"))

        assert await agent_jobs._autopilot(session, outcome) == autopilot.AutopilotOutcome()
        assert held.notice is not None
        assert (held.notice.status, held.notice.reason) == (DraftStatus.ESCALATED, "дороже предела")


class TestSwitch:
    async def test_mode_is_refused_where_the_stage_or_server_does_not_allow_it(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await make_user("админ@site.com", role=UserRole.ADMIN)
        token = await sign_in("админ@site.com")
        path = "/api/agent/settings/donors"

        no_stage = await client.post(path, json=BODY, headers=bearer(token))
        shown = await client.get("/api/agent/settings", headers=bearer(token))
        donors_with(monkeypatch, autopilot=True)
        no_server = await client.post(path, json=BODY, headers=bearer(token))

        assert no_stage.status_code == 409
        assert "не разрешён" in no_stage.json()["detail"]
        assert [view["autopilot_allowed"] for view in shown.json()["stages"]] == [False, False]
        assert no_server.status_code == 409
        assert "OUTREACH_AGENT_AUTOPILOT" in no_server.json()["detail"]

    async def test_only_who_may_send_turns_it_on(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "AGENT_AUTOPILOT", True)
        donors_with(monkeypatch, autopilot=True)
        await make_user("оператор@site.com", role=UserRole.OPERATOR)  # settings есть, send нет
        await make_user("админ@site.com", role=UserRole.ADMIN)
        operator = await sign_in("оператор@site.com")
        admin = await sign_in("админ@site.com")
        path = "/api/agent/settings/donors"

        refused = await client.post(path, json=BODY, headers=bearer(operator))
        drafts_only = await client.post(
            path, json={**BODY, "mode": "drafts"}, headers=bearer(operator)
        )
        allowed = await client.post(path, json=BODY, headers=bearer(admin))

        assert refused.status_code == 403
        assert drafts_only.status_code == 200
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["settings"]["mode"] == "autopilot"


async def test_autopilot_migration_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()
    down, up = await connection.run_sync(
        columns_down_and_up,
        "75242c2ed7ba_agent_autopilot.py",
        "agent_settings",
        {"mode", "max_turns"},
    )

    assert (down, up) == (set(), {"mode", "max_turns"})
