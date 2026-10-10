"""Право «Продажи» закрывает данные этапа продаж на общих экранах — решение Anthony 10.10.2026, П2.

До П2 снятое право прятало раздел «Продажи» и маршруты модуля, а письма продаж читались
в «Письмах» (`GET /api/letters?stage=sales`), переписки — в «Диалогах», пачка продаж уходила
правом `send`, агент продаж правился правом `settings`. Теперь письмо, переписка, ответ и
черновик продаж без права — 403 словами, в общих списках и числах их нет, а этапа продаж нет
в настройках агента.

Каждое правило — с двух сторон: без права «Продажи» — скрыто или отказ; с ним (и с `send`
или `settings`, где они нужны) — как до П2. Учётки — операторы: право «Продажи» у них в роли
и снимается поимённо; письма отправляет тот, кому выдано `send`.
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.access.permissions import (
    AccessDeniedError,
    Actor,
    require_stage,
    sees_stage,
    visible_stages,
)
from backend.features.agent import stages as agent_stages
from backend.features.agent.stages import SALES_STAGE
from backend.features.core.domain import (
    DraftStatus,
    MessageStatus,
    ReplyKind,
    Stage,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
CLOSED = "— только с правом «Продажи»"
LETTERS, THREADS, REPLIES = "Письма продаж", "Переписки продаж", "Ответы в переписках продаж"
DRAFTS, AGENT = "Черновики агента продаж", "Настройки агента продаж"
AGENT_BODY = {"enabled": True, "goal": "Созвон", "tone": "Коротко", "points": [], "stop_topics": []}


# --- правило без базы ---------------------------------------------------------------------


class TestRule:
    OPERATOR = Actor(user_id=1, role=UserRole.OPERATOR)
    OUTSIDER = Actor(user_id=2, role=UserRole.OPERATOR, overrides={"sales": False})
    ADMIN_WITHOUT = Actor(user_id=3, role=UserRole.ADMIN, overrides={"sales": False})

    def test_operator_sees_every_stage_by_role(self) -> None:
        assert visible_stages(self.OPERATOR) == frozenset(Stage)

    @pytest.mark.parametrize("who", [OUTSIDER, ADMIN_WITHOUT], ids=["оператор", "админ"])
    def test_without_the_right_sales_are_closed_and_the_rest_is_not(self, who: Actor) -> None:
        """Правило — о праве, а не о роли: админ, у которого право сняли, тоже не видит."""
        assert visible_stages(who) == {Stage.DONORS, Stage.ADVERTISERS}
        assert not sees_stage(who, Stage.SALES)

    def test_refusal_says_what_is_closed_and_which_right_opens_it(self) -> None:
        with pytest.raises(AccessDeniedError) as refused:
            require_stage(self.OUTSIDER, Stage.SALES, LETTERS)

        assert str(refused.value) == f"{LETTERS} {CLOSED}"

    @pytest.mark.parametrize("stage", [Stage.DONORS, Stage.ADVERTISERS, None])
    def test_other_stages_and_rows_without_a_stage_pass(self, stage: Stage | None) -> None:
        require_stage(self.OUTSIDER, stage, LETTERS)


# --- мир: письма, переписка, ответ и черновик продаж рядом с донором -----------------------


@dataclass(frozen=True, slots=True)
class World:
    """Номера строк мира: переписка продаж с письмом, ответом и черновиком — и донор."""

    sales_thread: int
    #: Ушедшее письмо переписки продаж: на него ответил лид; по нему — исход и вложения.
    sales_sent: int
    #: Письмо продаж в очереди: правка, «не писать», отправка.
    sales_queued: int
    sales_reply: int
    sales_draft: int
    donor_thread: int
    donor_queued: int


async def _campaign(session: AsyncSession, stage: Stage) -> CampaignModel:
    campaign = CampaignModel(stage=stage, name=f"Проверка {stage.value}", status="draft")
    session.add(campaign)
    await session.flush()
    return campaign


async def _domain(session: AsyncSession, host: str) -> DomainModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    return domain


async def _letter(
    session: AsyncSession, campaign: CampaignModel, domain: DomainModel, thread: int | None = None
) -> MessageModel:
    """Первое письмо домену: в переписке — ушедшее, без неё — в очереди."""
    letter = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread,
        domain_id=domain.id,
        step=0,
        status=MessageStatus.QUEUED if thread is None else MessageStatus.DELIVERED,
        subject="A question about your team",
        body="Good afternoon!",
        sent_at=None if thread is None else NOW - timedelta(days=1),
        idempotency_key=f"stage-access:{campaign.stage.value}:{domain.host}",
    )
    session.add(letter)
    await session.flush()
    return letter


async def _thread(session: AsyncSession, campaign: CampaignModel, domain: DomainModel) -> int:
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    return thread.id


async def _draft(session: AsyncSession, reply: ReplyModel) -> AgentDraftModel:
    """Черновик агента продаж, отданный человеку, — по версии настроек этапа."""
    settings = AgentSettingsModel(
        stage=Stage.SALES,
        version=1,
        enabled=True,
        goal="Созвон",
        tone="Коротко",
        points=[],
        stop_topics=[],
    )
    session.add(settings)
    await session.flush()
    draft = AgentDraftModel(
        reply_id=reply.id,
        settings_id=settings.id,
        status=DraftStatus.ESCALATED,
        body="Let us talk on Monday.",
        reason="лид спросил цену",
        model="test-model",
        prompt_version="test-v1",
    )
    session.add(draft)
    await session.flush()
    return draft


@pytest.fixture
async def world(session: AsyncSession) -> World:
    sales = await _campaign(session, Stage.SALES)
    lead = await _domain(session, "lead-co.example.com")
    thread = await _thread(session, sales, lead)
    sent = await _letter(session, sales, lead, thread)
    reply = ReplyModel(
        thread_id=thread,
        message_id=sent.id,
        kind=ReplyKind.HUMAN,
        raw_body="Tell me more, please.",
        from_email="ceo@lead-co.example.com",
    )
    session.add(reply)
    await session.flush()
    queued = await _letter(session, sales, await _domain(session, "lead-two.example.com"))
    draft = await _draft(session, reply)
    donors = await _campaign(session, Stage.DONORS)
    donor_thread = await _thread(session, donors, await _domain(session, "donor.example.com"))
    donor_queued = await _letter(session, donors, await _domain(session, "donor-two.example.com"))
    await session.commit()
    return World(
        sales_thread=thread,
        sales_sent=sent.id,
        sales_queued=queued.id,
        sales_reply=reply.id,
        sales_draft=draft.id,
        donor_thread=donor_thread,
        donor_queued=donor_queued.id,
    )


@pytest.fixture(autouse=True)
def _null_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Письмо из теста не уходит наружу, даже если в `.env` разработчика настоящая почта."""
    monkeypatch.setattr(outreach_cfg, "TRANSPORT", "null")


@pytest.fixture
def sales_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Строка продаж в реестре этапов агента — как с тумблером `SALES_AGENT_ENABLED`: реестр
    берут своим именем, и подмена — в каждом загруженном модуле, где лежит тот же реестр."""
    original = agent_stages.AGENT_STAGES
    registry = MappingProxyType({**original, Stage.SALES: SALES_STAGE})
    for name, module in list(sys.modules.items()):
        if name.startswith(("backend.", "tests.")) and vars(module).get("AGENT_STAGES") is original:
            monkeypatch.setattr(module, "AGENT_STAGES", registry)


@pytest.fixture
async def outsider(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор с отправкой, у которого право «Продажи» снято поимённо."""
    await make_user("outsider@team.example.com", permissions={"sales": False, "send": True})
    return bearer(await sign_in("outsider@team.example.com"))


@pytest.fixture
async def seller(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор с отправкой: право «Продажи» у него в роли."""
    await make_user("seller@team.example.com", permissions={"send": True})
    return bearer(await sign_in("seller@team.example.com"))


# --- маршруты по строке продаж: без права — 403 словами, с правом — как до П2 ---------------

#: Метод, адрес, тело и что закрыто. Номера `999999` — строк, которых нет: с правом маршрут
#: отвечает своим «не найдено», а без права до них не доходит. Черновик агента — ответу,
#: которого нет: с правом модель не зовётся, отказ «нет ответа» приходит раньше.
SALES_ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/letters?stage=sales", None, LETTERS),
    ("GET", "/api/letters/unknown?stage=sales", None, LETTERS),
    ("POST", "/api/letters/build", {"campaign": "Продажи", "stage": "sales"}, LETTERS),
    ("POST", "/api/letters/send-queue", {"stage": "sales"}, LETTERS),
    ("PATCH", "/api/letters/{queued}", {"subject": "S", "body": "B"}, LETTERS),
    ("POST", "/api/letters/{queued}/skip", None, LETTERS),
    ("POST", "/api/letters/{queued}/send", None, LETTERS),
    ("POST", "/api/letters/{sent}/resolve", {"outcome": "queued"}, LETTERS),
    ("GET", "/api/messages/{sent}/attachments/999999", None, LETTERS),
    ("GET", "/api/threads/{thread}", None, THREADS),
    ("POST", "/api/threads/{thread}/answer", {"reply_id": 999_999, "body": "Thanks"}, THREADS),
    ("POST", "/api/threads/{thread}/replies/999999/draft", None, THREADS),
    ("POST", "/api/threads/{thread}/files", None, THREADS),
    ("DELETE", "/api/threads/{thread}/files/999999", None, THREADS),
    ("GET", "/api/replies/{reply}/attachments/999999", None, REPLIES),
    ("GET", "/api/replies/{reply}/attachments/999999/text", None, REPLIES),
    ("POST", "/api/replies/{reply}/lead", None, REPLIES),
    ("POST", "/api/replies/{reply}/lead/send", None, REPLIES),
    ("PATCH", "/api/replies/{reply}", {"price_white": "300", "currency": "USD"}, REPLIES),
    ("GET", "/api/agent/drafts/{draft}", None, DRAFTS),
    ("POST", "/api/agent/drafts/{draft}/send", {}, DRAFTS),
    ("POST", "/api/agent/drafts/{draft}/reject", {"reason": "другое: не тот тон"}, DRAFTS),
    ("POST", "/api/agent/settings/sales", AGENT_BODY, AGENT),
]


def _path(template: str, world: World) -> str:
    return template.format(
        queued=world.sales_queued,
        sent=world.sales_sent,
        thread=world.sales_thread,
        reply=world.sales_reply,
        draft=world.sales_draft,
    )


@pytest.mark.parametrize(("method", "path", "body", "what"), SALES_ROUTES)
async def test_without_the_right_every_sales_route_is_refused_in_words(
    client: AsyncClient,
    world: World,
    outsider: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
    what: str,
) -> None:
    response = await client.request(method, _path(path, world), json=body, headers=outsider)

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == f"{what} {CLOSED}"


@pytest.mark.usefixtures("sales_agent")
@pytest.mark.parametrize(("method", "path", "body", "what"), SALES_ROUTES)
async def test_with_the_right_the_route_answers_as_before(
    client: AsyncClient,
    world: World,
    seller: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
    what: str,
) -> None:
    """С правом «Продажи» отказа в праве нет: дальше маршрут отвечает своим — письмом,
    «не найдено», «продажи не подключены», — как до П2."""
    response = await client.request(method, _path(path, world), json=body, headers=seller)

    assert response.status_code not in (401, 403), response.text
    assert CLOSED not in response.text


async def test_donor_rows_stay_open_without_the_right(
    client: AsyncClient, world: World, outsider: dict[str, str]
) -> None:
    """Снятое право закрывает только продажи: переписка и письмо донора — как были."""
    thread = await client.get(f"/api/threads/{world.donor_thread}", headers=outsider)
    skipped = await client.post(f"/api/letters/{world.donor_queued}/skip", headers=outsider)

    assert thread.status_code == 200, thread.text
    assert skipped.status_code == 200, skipped.text


# --- списки и числа: без права продаж в них нет ------------------------------------------


async def test_letters_without_a_stage_are_donors_and_count_no_sales(
    client: AsyncClient, world: World, outsider: dict[str, str]
) -> None:
    response = await client.get("/api/letters", headers=outsider)

    shown = response.json()
    assert response.status_code == 200, response.text
    assert [letter["id"] for letter in shown["letters"]] == [world.donor_queued]
    assert shown["queued_total"] == 1


async def test_thread_list_leaves_sales_out_without_the_right(
    client: AsyncClient, world: World, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    hidden = await client.get("/api/threads", headers=outsider)
    shown = await client.get("/api/threads", headers=seller)

    assert [row["id"] for row in hidden.json()] == [world.donor_thread]
    assert {row["id"] for row in shown.json()} == {world.donor_thread, world.sales_thread}


async def test_overview_and_menu_count_sales_only_with_the_right(
    client: AsyncClient, world: World, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    """Ответ лида ждёт человека: у пункта «Диалоги» — только с правом. Письма продаж — тоже:
    без права этапа продаж в сводке писем нет вовсе, а не нулями."""
    hidden = (await client.get("/api/overview", headers=outsider)).json()
    shown = (await client.get("/api/overview", headers=seller)).json()
    hidden_menu = (await client.get("/api/overview/work", headers=outsider)).json()
    shown_menu = (await client.get("/api/overview/work", headers=seller)).json()

    assert set(hidden["letters"]) == {"donors", "advertisers"}
    assert (shown["letters"]["sales"]["queued"], shown["letters"]["sales"]["sent"]) == (1, 1)
    assert hidden["letters"]["donors"] == shown["letters"]["donors"]
    assert (hidden_menu["threads"], shown_menu["threads"]) == (0, 1)


async def test_draft_list_leaves_sales_out_without_the_right(
    client: AsyncClient, world: World, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    hidden = await client.get("/api/agent/drafts", headers=outsider)
    shown = await client.get("/api/agent/drafts", headers=seller)

    assert hidden.json() == []
    assert [draft["id"] for draft in shown.json()] == [world.sales_draft]


@pytest.mark.usefixtures("sales_agent")
async def test_agent_settings_show_and_save_sales_only_with_the_right(
    client: AsyncClient, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    """Агент продаж — право «Продажи» вместе с правом «настройки»: без первого этапа нет
    на экране и правка отказана, с обоими — сохраняется версия, как до П2."""
    hidden = await client.get("/api/agent/settings", headers=outsider)
    shown = await client.get("/api/agent/settings", headers=seller)
    saved = await client.post("/api/agent/settings/sales", json=AGENT_BODY, headers=seller)

    assert [one["stage"] for one in hidden.json()["stages"]] == ["donors", "advertisers"]
    assert [one["stage"] for one in shown.json()["stages"]] == ["donors", "advertisers", "sales"]
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1


async def test_agent_sales_settings_need_the_settings_right_too(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    """Право «Продажи» не заменяет «настройки»: без них правка этапа продаж — отказ."""
    await make_user("reader@team.example.com", permissions={"settings": False})
    headers = bearer(await sign_in("reader@team.example.com"))

    response = await client.post("/api/agent/settings/sales", json=AGENT_BODY, headers=headers)

    assert response.status_code == 403
    assert "«settings»" in response.json()["detail"]


async def test_sales_batch_needs_the_send_right_too(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    """Право «Продажи» не заменяет отправку: пачка продаж без `send` — отказ, как у всех."""
    await make_user("watcher@team.example.com")
    headers = bearer(await sign_in("watcher@team.example.com"))

    response = await client.post(
        "/api/letters/send-queue", json={"stage": "sales"}, headers=headers
    )

    assert response.status_code == 403
    assert "«send»" in response.json()["detail"]
