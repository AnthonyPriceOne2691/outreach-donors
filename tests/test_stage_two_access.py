"""Право «Рекламодатели» закрывает Этап 2 маршрутами — решение Anthony 02.11.2026.

Другая сторона просила оставить учётке только поиск аутрича. Закрыт не пункт меню, а этап: весь
раздел, обходы, загрузка доноров в обход, лиды файлом и строки этапа на общих маршрутах — письма,
переписки, ответы, черновики, настройки агента, стоп-лист. Правило то же, что у права «Продажи»
(`test_stage_access.py`, `test_sales_rights_seams.py`).

**Закрытые маршруты берутся из схемы приложения, а не из списка**: маршрут, заведённый в разделе
завтра, проверяется сам, и без защиты роняет тест. Отказ стоит раньше разбора тела, поэтому тела
запросов нет; строк с такими номерами нет, и до тела маршрута дело не доходит.

Каждое правило — с двух сторон: роль «Только доноры» получает отказ, оператор и админ — нет.
Учётка с отправкой (`send`): иначе маршрут письма отказал бы правом отправки, а не этапа. Данные
этапа на общих экранах — `test_stage_two_screens.py`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from backend.api.errors import ACCESS_REFUSAL
from backend.config import outreach as outreach_cfg
from backend.features.access.permissions import no_right
from backend.features.core.domain import (
    DraftStatus,
    MessageStatus,
    Permission,
    ReplyKind,
    Stage,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.outreach import ReplyModel
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_stage_access import AGENT_BODY, _campaign, _domain, _letter, _thread

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Начала путей, закрытых целиком: раздел «Рекламодатели», обходы, доноры в обход.
SECTIONS = ("/api/advertisers", "/api/crawls", "/api/donors/import")

#: Маршруты Этапа 2 среди чужих путей: они кормят панель обхода и экран рекламодателей.
SINGLE_ROUTES = {
    ("POST", "/api/donors"),
    ("GET", "/api/replies/leads.csv"),
    ("GET", "/api/settings/advertisers"),
}

#: Сколько маршрутов закрыто на сегодня: снизу — чтобы сломанный обход схемы не прошёл молча.
AT_LEAST = 37

CLOSED = "— только с правом «Рекламодатели»"
LETTERS = "Письма рекламодателей"
THREADS = "Переписки рекламодателей"
REPLIES = "Ответы в переписках рекламодателей"
DRAFTS = "Черновики агента рекламодателей"
AGENT = "Настройки агента рекламодателей"
SUPPRESSIONS = "Записи стоп-листа рекламодателей"

#: Кому Этап 2 закрыт и кому открыт: роль с правами поверх неё. Отправка выдана: иначе маршрут
#: письма отказал бы правом отправки, а не этапа. Правило — о праве, а не о роли: и у админа.
WITHOUT = [
    pytest.param(UserRole.DONORS, {"send": True}, id="роль доноров"),
    pytest.param(UserRole.ADMIN, {"advertisers": False}, id="админ без права"),
]
WITH = [
    pytest.param(UserRole.OPERATOR, {"send": True}, id="оператор"),
    pytest.param(UserRole.ADMIN, {}, id="админ"),
]


def _walked(app: FastAPI) -> list[tuple[str, str]]:
    """Закрытые маршруты из схемы приложения: метод и путь с подстановками."""
    return sorted(
        (method.upper(), path)
        for path, methods in app.openapi()["paths"].items()
        for method in methods
        if path.startswith(SECTIONS) or (method.upper(), path) in SINGLE_ROUTES
    )


def _url(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "1", path)


async def _signed(
    make_user: MakeUser, sign_in: SignIn, email: str, role: UserRole, rights: dict[str, bool]
) -> dict[str, str]:
    await make_user(email, role=role, permissions=rights)
    return bearer(await sign_in(email))


@pytest.fixture
async def donors_only(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Роль «Только доноры» с отправкой, выданной поимённо."""
    return await _signed(
        make_user, sign_in, "donors@team.example.test", UserRole.DONORS, {"send": True}
    )


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    return await _signed(
        make_user, sign_in, "operator@team.example.test", UserRole.OPERATOR, {"send": True}
    )


# --- обход схемы: маршрут Этапа 2 без защиты роняет тест ----------------------------------


def test_the_walk_finds_the_routes(api_app: FastAPI) -> None:
    """Обход нашёл то, что должен: разделы и маршруты вне разделов целы."""
    walked = _walked(api_app)

    assert len(walked) >= AT_LEAST, walked
    assert set(walked) >= SINGLE_ROUTES
    for section in SECTIONS:
        assert any(path.startswith(section) for _, path in walked), section


@pytest.mark.parametrize(("role", "rights"), WITHOUT)
async def test_every_stage_two_route_is_refused_without_the_right(
    api_app: FastAPI,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    role: UserRole,
    rights: dict[str, bool],
) -> None:
    """Права нет — отказ словами и с признаком: по нему экран перечитывает «кто я»."""
    headers = await _signed(make_user, sign_in, "who@team.example.test", role, rights)

    for method, path in _walked(api_app):
        response = await client.request(method, _url(path), headers=headers)

        assert response.status_code == 403, f"{method} {path}: {response.text}"
        assert response.json()["detail"] == no_right(Permission.ADVERTISERS), f"{method} {path}"
        assert response.headers[ACCESS_REFUSAL] == "permission", f"{method} {path}"


@pytest.mark.parametrize(("role", "rights"), WITH)
async def test_with_the_right_no_route_refuses_it(
    api_app: FastAPI,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    role: UserRole,
    rights: dict[str, bool],
) -> None:
    """Оператор и админ не потеряли ни маршрута: дальше маршрут отвечает своим — данными,
    «не найдено», отказом разбора тела, — но не отказом в праве."""
    headers = await _signed(make_user, sign_in, "who@team.example.test", role, rights)

    for method, path in _walked(api_app):
        response = await client.request(method, _url(path), headers=headers)

        assert response.status_code != 403, f"{method} {path}: {response.text}"


# --- строки Этапа 2 на общих маршрутах ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class World:
    """Переписка Этапа 2 с письмом, ответом и черновиком, письмо в очереди — и донор рядом."""

    thread: int
    sent: int
    queued: int
    reply: int
    draft: int
    donor_thread: int
    donor_queued: int


async def _draft(session: AsyncSession, reply: ReplyModel) -> AgentDraftModel:
    settings = AgentSettingsModel(
        stage=Stage.ADVERTISERS,
        version=1,
        enabled=True,
        goal="Сделка",
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
        reason="спросил цену",
        model="test-model",
        prompt_version="test-v1",
    )
    session.add(draft)
    await session.flush()
    return draft


@pytest.fixture
async def world(session: AsyncSession) -> World:
    offers = await _campaign(session, Stage.ADVERTISERS)
    brand = await _domain(session, "brand-co.example.com")
    thread = await _thread(session, offers, brand)
    sent = await _letter(session, offers, brand, thread)
    reply = ReplyModel(
        thread_id=thread,
        message_id=sent.id,
        kind=ReplyKind.HUMAN,
        raw_body="Interesting offer, tell me more.",
        from_email="ceo@brand-co.example.com",
    )
    session.add(reply)
    await session.flush()
    queued = await _letter(session, offers, await _domain(session, "brand-two.example.com"))
    assert queued.status is MessageStatus.QUEUED
    draft = await _draft(session, reply)
    donors = await _campaign(session, Stage.DONORS)
    donor_thread = await _thread(session, donors, await _domain(session, "donor.example.com"))
    donor_queued = await _letter(session, donors, await _domain(session, "donor-two.example.com"))
    await session.commit()
    return World(
        thread=thread,
        sent=sent.id,
        queued=queued.id,
        reply=reply.id,
        draft=draft.id,
        donor_thread=donor_thread,
        donor_queued=donor_queued.id,
    )


@pytest.fixture(autouse=True)
def _null_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Письмо из теста не уходит наружу, даже если в `.env` разработчика настоящая почта."""
    monkeypatch.setattr(outreach_cfg, "TRANSPORT", "null")


STOP_ENTRY = {"target": "partner@brand-co.example.com", "reason": "manual", "stage": "advertisers"}
OFFER_BATCH = {"stage": "advertisers"}
NICHE_BATCH = {"stage": "advertisers", "audience": "niche"}

#: Метод, адрес, тело и что закрыто. `999999` — строк, которых нет: с правом маршрут отвечает
#: своим «не найдено», а без права до них не доходит.
ROW_ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/letters?stage=advertisers", None, LETTERS),
    ("GET", "/api/letters?stage=advertisers&audience=niche", None, LETTERS),
    ("GET", "/api/letters/unknown?stage=advertisers", None, LETTERS),
    ("GET", "/api/letters/unknown?stage=advertisers&audience=niche", None, LETTERS),
    ("GET", "/api/letters/forecast?stage=advertisers", None, LETTERS),
    ("GET", "/api/letters/deferred?stage=advertisers", None, LETTERS),
    ("GET", "/api/letters/deferred?stage=advertisers&audience=niche", None, LETTERS),
    ("POST", "/api/letters/build", {"campaign": "Оффер", "stage": "advertisers"}, LETTERS),
    ("POST", "/api/letters/send-queue", OFFER_BATCH, LETTERS),
    ("POST", "/api/letters/send-queue", NICHE_BATCH, LETTERS),
    ("POST", "/api/letters/send-queue/stop", OFFER_BATCH, LETTERS),
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
    ("POST", "/api/price-rechecks/replies/{reply}/same-price", None, REPLIES),
    ("GET", "/api/agent/drafts/{draft}", None, DRAFTS),
    ("POST", "/api/agent/drafts/{draft}/send", {}, DRAFTS),
    ("POST", "/api/agent/drafts/{draft}/reject", {"reason": "другое: не тот тон"}, DRAFTS),
    ("POST", "/api/agent/settings/advertisers", AGENT_BODY, AGENT),
    ("POST", "/api/suppressions", STOP_ENTRY, SUPPRESSIONS),
]


def _path(template: str, world: World) -> str:
    return template.format(
        queued=world.queued,
        sent=world.sent,
        thread=world.thread,
        reply=world.reply,
        draft=world.draft,
    )


@pytest.mark.parametrize(("method", "path", "body", "what"), ROW_ROUTES)
async def test_a_row_of_stage_two_is_refused_in_words(
    client: AsyncClient,
    world: World,
    donors_only: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
    what: str,
) -> None:
    response = await client.request(method, _path(path, world), json=body, headers=donors_only)

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == f"{what} {CLOSED}"


@pytest.mark.parametrize(("method", "path", "body", "what"), ROW_ROUTES)
async def test_with_the_right_the_row_answers_as_before(
    client: AsyncClient,
    world: World,
    operator: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
    what: str,
) -> None:
    response = await client.request(method, _path(path, world), json=body, headers=operator)

    assert response.status_code not in (401, 403), response.text
    assert CLOSED not in response.text


# --- Этап 1 у роли — целиком -------------------------------------------------------------

#: Маршруты, на которых держатся экраны доноров: роль видит всё про доноров.
STAGE_ONE_ROUTES = [
    "/api/donors",
    "/api/runs",
    "/api/runs/with-accepted",
    "/api/selection",
    "/api/review/accuracy",
    "/api/review/reserve",
    "/api/contacts/forms",
    "/api/letters",
    "/api/letters/unknown",
    "/api/letters/forecast",
    "/api/letters/deferred",
    "/api/threads",
    "/api/replies/unbound",
    "/api/replies/calibration",
    "/api/suppressions",
    "/api/settings/thresholds",
    "/api/agent/settings",
    "/api/agent/drafts",
    "/api/overview",
    "/api/overview/work",
    "/api/price-rechecks",
]


@pytest.mark.parametrize("path", STAGE_ONE_ROUTES)
async def test_stage_one_stays_open_to_the_role(
    client: AsyncClient, donors_only: dict[str, str], path: str
) -> None:
    response = await client.get(path, headers=donors_only)

    assert response.status_code == 200, response.text


async def test_a_donor_row_works_for_the_role(
    client: AsyncClient, world: World, donors_only: dict[str, str]
) -> None:
    """Письмо и переписка донора — как у оператора: снято только чужое."""
    thread = await client.get(f"/api/threads/{world.donor_thread}", headers=donors_only)
    skipped = await client.post(f"/api/letters/{world.donor_queued}/skip", headers=donors_only)
    stopped = await client.post(
        "/api/suppressions",
        json={"target": "owner@donor.example.com", "reason": "manual", "stage": "donors"},
        headers=donors_only,
    )

    assert thread.status_code == 200, thread.text
    assert skipped.status_code == 200, skipped.text
    assert stopped.status_code == 200, stopped.text
