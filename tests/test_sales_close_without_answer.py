"""«Закрыть без ответа» и ответ в переписке — ответ продаж перестаёт ждать человека.

Слово владельца 09.11.2026: подтверждение заявки службы поддержки, принятое ответом человека, и
пустой ответ снять с ожидания было нечем, кроме передачи телемаркетологу, — а она завела бы сделку в
Kommo на службу поддержки. Ответ человека в переписке ожидание тоже не снимал.

Условия хозяина общего кода (09.11): ответ из поля и черновик трогают только переписку продаж;
снимаются только ответы этой переписки, только ответы человека и только ждущие — не новее того, на
который ответили; сбой снятия — предупреждение в журнале, письмо отправлено, ответ API о снятии
молчит; ни закрытие, ни ответ не заводят сделку в Kommo и не пишут в Telegram.

Письмо здесь не уходит: `answer_reply` и `drafts.send_draft` подменены — их проверяют свои тесты
(`test_thread_answer.py`, `test_sales_draft_decisions.py`), здесь — что после них. Тексты ответов
выдуманные, на разных языках; домены — `*.example.test`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.agent import routes as agent_routes
from backend.api.sales import close as close_routes
from backend.api.sales import handoff as handoff_routes
from backend.api.threads import routes as threads_routes
from backend.features.access.permissions import no_right
from backend.features.agent import drafting, drafts
from backend.features.core.domain import Permission, ReplyKind, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel
from backend.features.letters.sending import SendOutcome
from backend.features.sales import close_manual
from backend.features.sales.close_manual import ANSWERED, CLOSED
from backend.features.sales.models import SalesHandoffModel
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_agent_situation import Plug, llm
from tests.test_sales_agent_stage import GOOD, INFORM, Writer, lead_replied, sales_on
from tests.test_sales_handoff_rows import SENT_AT, Dialog, answer, sales_dialog
from tests.test_sales_switch import sales_switched_on

__all__ = ["llm", "sales_on", "sales_switched_on"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
OPERATOR = "operator@ours.example.test"
OUTSIDER = "outsider@ours.example.test"
ADMIN = "admin@ours.example.test"

#: Ответ ждёт человека: вид назван неуверенно — ниже порога, путь «человек».
UNSURE = {
    "stage": "sales",
    "kind": "interested",
    "confidence": 0.0,
    "route": "manual",
    "waits": True,
    "reason": "модель: интересуется, уверенность 0% ниже порога 80% — решает человек",
}

#: Что закрывают без ответа: подтверждения служб поддержки, принятые до правила, и пустой ответ.
CLOSABLE = {
    "ru-заявка": (
        "zayavka.example.test",
        "support@zayavka.example.test",
        "Здравствуйте. Ваше обращение было передано в ответственный отдел. Пожалуйста, оцените "
        "качество обслуживания.",
    ),
    "en-ticket": (
        "desk.example.test",
        "help@desk.example.test",
        "Thank you for contacting us. Your request has been received.",
    ),
    "пустой": ("empty.example.test", "info@empty.example.test", "> Кто у вас отвечает за SEO?"),
}

ALLOW = {"claims": [], "promises": [], "tone": {"ok": True, "problem": ""}}


class Queue(list[int]):
    """Очередь передач — подставная: закрытие и ответ её не трогают."""

    def __call__(self, handoff_id: int) -> None:
        self.append(handoff_id)


@pytest.fixture
def queue(api_app: FastAPI) -> Queue:
    queued = Queue()
    api_app.dependency_overrides[handoff_routes.handoff_queue] = lambda: queued
    return queued


@pytest.fixture
def letter_goes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Ответ из поля «уходит» без почты: какие ответы в нём отвечены."""
    answered: list[int] = []

    async def sent(_session: AsyncSession, _sending: object, **kwargs: Any) -> SendOutcome:
        answered.append(kwargs["reply_id"])
        return SendOutcome(
            message_id=1,
            sender_email="anna@mail.example.test",
            provider_message_id="<1>",
            real=False,
        )

    monkeypatch.setattr(threads_routes, "answer_reply", sent)
    return answered


async def _seller(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER, permissions={"send": True})
    return bearer(await sign_in(SELLER))


async def _admin(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(ADMIN, role=UserRole.ADMIN)
    return bearer(await sign_in(ADMIN))


async def _waiting(
    session: AsyncSession, answer_of: tuple[str, str, str] = CLOSABLE["ru-заявка"]
) -> Dialog:
    host, email, text = answer_of
    dialog = await sales_dialog(session, host=host, email=email, text=text)
    dialog.reply.model_parse = dict(UNSURE)
    await session.commit()
    return dialog


async def _later(
    session: AsyncSession, dialog: Dialog, text: str = "Ещё вопрос: сколько стоит?"
) -> ReplyModel:
    """Новый ответ лида в тот же диалог — пришёл позже и ждёт человека."""
    reply = await answer(
        session,
        dialog.thread,
        dialog.message,
        text,
        at=SENT_AT.replace(day=20),
        sender=dialog.lead.email,
    )
    await session.commit()
    return reply


async def _reply(session: AsyncSession, reply_id: int) -> ReplyModel:
    """Ответ, как он лежит в базе: сессия общая с приложением, строка — перечитанная."""
    found = await session.get(ReplyModel, reply_id)
    assert found is not None
    await session.refresh(found)
    return found


async def _state(client: AsyncClient, thread_id: int, headers: dict[str, str]) -> str:
    response = await client.get(f"/api/threads/{thread_id}", headers=headers)
    assert response.status_code == 200, response.text
    state: str = response.json()["card"]["state"]
    return state


async def _no_handoff(session: AsyncSession, thread_id: int, queue: Queue) -> None:
    """Ни сделки в Kommo, ни сообщения в Telegram: строки передачи нет, задача не ставилась."""
    count = await session.scalar(
        select(func.count()).where(SalesHandoffModel.thread_id == thread_id)
    )
    assert count == 0
    assert queue == []


def _close(thread_id: int) -> str:
    return f"/api/sales/threads/{thread_id}/close"


# --- «Закрыть без ответа» ---------------------------------------------------------------------


@pytest.mark.parametrize("answer_of", CLOSABLE.values(), ids=CLOSABLE.keys())
async def test_waiting_answer_is_closed_and_waits_no_more(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    queue: Queue,
    answer_of: tuple[str, str, str],
) -> None:
    dialog = await _waiting(session, answer_of)
    headers = await _seller(make_user, sign_in)
    assert await _state(client, dialog.thread.id, headers) == "sales_pending"

    response = await client.post(
        _close(dialog.thread.id), json={"reply_id": dialog.reply.id}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "released": [dialog.reply.id],
        "later": 0,
        "said": "Закрыто без ответа: переписка больше не ждёт человека.",
    }
    reply = await _reply(session, dialog.reply.id)
    assert reply.model_parse is not None
    assert (reply.model_parse["waits"], reply.model_parse["reason"]) == (
        False,
        f"{CLOSED}: {SELLER}",
    )
    # Что сказала модель, остаётся в снимке: по нему меряют разбор. Вид ответа — прежний.
    assert (reply.model_parse["kind"], reply.model_parse["confidence"]) == ("interested", 0.0)
    assert (reply.kind, reply.reviewed_by, reply.reviewed_at is not None) == (
        ReplyKind.HUMAN,
        SELLER,
        True,
    )
    assert await _state(client, dialog.thread.id, headers) == "replied"
    work = await client.get("/api/overview/work", headers=headers)
    assert work.json()["sales_threads"] == 0
    await _no_handoff(session, dialog.thread.id, queue)


async def test_answer_that_came_later_keeps_waiting(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    """Закрыли то, что видели: ответ, пришедший позже, человек не читал — он ждёт дальше."""
    dialog = await _waiting(session)
    later = await _later(session, dialog)
    headers = await _seller(make_user, sign_in)

    response = await client.post(
        _close(dialog.thread.id), json={"reply_id": dialog.reply.id}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "released": [dialog.reply.id],
        "later": 1,
        "said": "Закрыто без ответа: новый ответ лида пришёл позже и ждёт человека.",
    }
    fresh = await _reply(session, later.id)
    assert (fresh.model_parse, fresh.reviewed_at) == (None, None)
    assert await _state(client, dialog.thread.id, headers) == "sales_pending"
    await _no_handoff(session, dialog.thread.id, queue)


async def test_only_waiting_answers_of_a_person_in_this_dialog_are_closed(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    """Закрыли по последнему ответу — снят только ждущий ответ человека; разобранный ответ (диалог
    закрыт по виду), автоответ и ответ соседнего диалога — как были."""
    dialog = await _waiting(session)
    settled = {
        "stage": "sales",
        "kind": "not_now",
        "route": "closed",
        "waits": False,
        "reason": "не сейчас: диалог закрыт",
    }
    decided = await answer(session, dialog.thread, dialog.message, "Nicht jetzt.", at=SENT_AT)
    decided.model_parse = dict(settled)
    robot = await answer(session, dialog.thread, dialog.message, "Je suis absent.", at=SENT_AT)
    robot.kind = ReplyKind.AUTO_REPLY
    other = await _waiting(session, CLOSABLE["en-ticket"])
    await session.commit()
    headers = await _seller(make_user, sign_in)

    response = await client.post(
        _close(dialog.thread.id), json={"reply_id": robot.id}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert (response.json()["released"], response.json()["later"]) == ([dialog.reply.id], 0)
    kept = await _reply(session, decided.id)
    assert (kept.model_parse, kept.reviewed_at) == (settled, None)
    auto = await _reply(session, robot.id)
    assert (auto.model_parse, auto.reviewed_at) == (None, None)
    assert (await _reply(session, other.reply.id)).model_parse == UNSURE
    assert await _state(client, other.thread.id, headers) == "sales_pending"


async def test_repeat_is_refused_in_words_and_changes_nothing(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    dialog = await _waiting(session)
    headers = await _seller(make_user, sign_in)
    body = {"reply_id": dialog.reply.id}
    assert (
        await client.post(_close(dialog.thread.id), json=body, headers=headers)
    ).status_code == 200
    closed_at = (await _reply(session, dialog.reply.id)).reviewed_at

    again = await client.post(_close(dialog.thread.id), json=body, headers=headers)

    assert again.status_code == 409
    assert again.json()["detail"] == (
        f"в диалоге №{dialog.thread.id} ответ №{dialog.reply.id} и ответы до него уже не ждут "
        "человека — закрывать нечего"
    )
    assert (await _reply(session, dialog.reply.id)).reviewed_at == closed_at
    await _no_handoff(session, dialog.thread.id, queue)


async def test_unknown_dialog_is_404_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    headers = await _seller(make_user, sign_in)
    for number in (987_654, 99_999_999_999):
        response = await client.post(_close(number), json={"reply_id": 1}, headers=headers)

        assert response.status_code == 404
        assert response.json()["detail"] == f"диалога №{number} нет — закрывать нечего"


async def test_not_a_sales_dialog_and_a_foreign_answer_are_refused_in_words(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    dialog = await _waiting(session)
    other = await _waiting(session, CLOSABLE["пустой"])
    headers = await _seller(make_user, sign_in)

    foreign = await client.post(
        _close(dialog.thread.id), json={"reply_id": other.reply.id}, headers=headers
    )
    await session.execute(
        update(CampaignModel)
        .where(CampaignModel.id == dialog.thread.campaign_id)
        .values(stage=Stage.DONORS)
    )
    await session.commit()
    donors = await client.post(
        _close(dialog.thread.id), json={"reply_id": dialog.reply.id}, headers=headers
    )

    assert foreign.status_code == 409
    assert foreign.json()["detail"] == (
        f"ответа №{other.reply.id} в диалоге №{dialog.thread.id} нет — откройте переписку заново"
    )
    assert donors.status_code == 409
    assert donors.json()["detail"] == (
        f"диалог №{dialog.thread.id} — не продаж: без ответа закрывают только ответ лида продаж"
    )
    for untouched in (dialog.reply.id, other.reply.id):
        reply = await _reply(session, untouched)
        assert (reply.model_parse, reply.reviewed_at) == (UNSURE, None)


@pytest.mark.parametrize(
    ("email", "permissions", "missing"),
    [
        (OPERATOR, None, Permission.SEND),
        (OUTSIDER, {"send": True, "sales": False}, Permission.SALES),
    ],
    ids=["no-send", "no-sales"],
)
async def test_someone_elses_right_is_403_in_words(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    queue: Queue,
    email: str,
    permissions: dict[str, bool] | None,
    missing: Permission,
) -> None:
    """Закрывает тот, кто может ответить в переписке продаж, — как у передачи."""
    dialog = await _waiting(session)
    await make_user(email, permissions=permissions)

    response = await client.post(
        _close(dialog.thread.id),
        json={"reply_id": dialog.reply.id},
        headers=bearer(await sign_in(email)),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == no_right(missing)
    reply = await _reply(session, dialog.reply.id)
    assert (reply.model_parse, reply.reviewed_at) == (UNSURE, None)


@pytest.mark.parametrize(
    ("released", "later", "words"),
    [
        ((1,), 0, "Закрыто без ответа: переписка больше не ждёт человека."),
        ((1, 2), 0, "Закрыто без ответа (ответов — 2): переписка больше не ждёт человека."),
        (
            (1, 2),
            1,
            "Закрыто без ответа (ответов — 2): новый ответ лида пришёл позже и ждёт человека.",
        ),
        ((1,), 3, "Закрыто без ответа: пришедшие позже ответы лида ждут человека — 3."),
    ],
)
def test_summary_has_one_colon_whatever_the_counts(
    released: tuple[int, ...], later: int, words: str
) -> None:
    """Стенд 09.11.2026: при двух снятых ответах строка была «…, ответов: 2: …» — два двоеточия
    подряд под кнопками и в уведомлении."""
    assert close_manual.said(close_manual.Closed(released, later)) == words


# --- ответ в переписке --------------------------------------------------------------------------


async def test_answer_in_the_dialog_releases_what_it_answered(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    queue: Queue,
    letter_goes: list[int],
) -> None:
    """Ответили на первый ответ — он больше не ждёт, второй (пришёл позже) ждёт; ответили и на него —
    переписка «ответил»."""
    dialog = await _waiting(session)
    later = await _later(session, dialog, "¿Y cuánto cuesta?")
    headers = await _seller(make_user, sign_in)

    first = await client.post(
        f"/api/threads/{dialog.thread.id}/answer",
        json={"reply_id": dialog.reply.id, "body": "Пришлём предложение сегодня."},
        headers=headers,
    )

    assert first.status_code == 200, first.text
    assert set(first.json()) == {"id", "sender_email", "real"}  # о снятии ответ API молчит
    answered = await _reply(session, dialog.reply.id)
    assert answered.model_parse is not None
    assert (answered.model_parse["waits"], answered.model_parse["reason"]) == (
        False,
        f"{ANSWERED}: {SELLER}",
    )
    assert answered.reviewed_by == SELLER
    assert (await _reply(session, later.id)).reviewed_at is None
    assert await _state(client, dialog.thread.id, headers) == "sales_pending"

    second = await client.post(
        f"/api/threads/{dialog.thread.id}/answer",
        json={"reply_id": later.id, "body": "Цена — в предложении."},
        headers=headers,
    )

    assert second.status_code == 200, second.text
    assert (await _reply(session, later.id)).reviewed_by == SELLER
    assert await _state(client, dialog.thread.id, headers) == "replied"
    assert letter_goes == [dialog.reply.id, later.id]
    await _no_handoff(session, dialog.thread.id, queue)


@pytest.mark.parametrize("stage", [Stage.DONORS, Stage.ADVERTISERS])
async def test_answer_outside_sales_touches_nothing(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    letter_goes: list[int],
    stage: Stage,
) -> None:
    dialog = await _waiting(session)
    await session.execute(
        update(CampaignModel)
        .where(CampaignModel.id == dialog.thread.campaign_id)
        .values(stage=stage)
    )
    await session.commit()
    headers = await _admin(make_user, sign_in)
    before = await _state(client, dialog.thread.id, headers)

    response = await client.post(
        f"/api/threads/{dialog.thread.id}/answer",
        json={"reply_id": dialog.reply.id, "body": "Thanks!"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert letter_goes == [dialog.reply.id]
    reply = await _reply(session, dialog.reply.id)
    assert (reply.model_parse, reply.reviewed_by, reply.reviewed_at) == (UNSURE, None, None)
    assert await _state(client, dialog.thread.id, headers) == before


async def test_failed_release_is_a_warning_and_the_letter_stays_sent(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    letter_goes: list[int],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dialog = await _waiting(session)
    headers = await _seller(make_user, sign_in)

    async def broken(*_args: object, **_kwargs: object) -> tuple[int, ...]:
        raise RuntimeError(f"сбой базы на диалоге №{dialog.thread.id}")

    monkeypatch.setattr(close_manual, "answered", broken)

    with caplog.at_level(logging.WARNING, logger=close_routes.__name__):
        response = await client.post(
            f"/api/threads/{dialog.thread.id}/answer",
            json={"reply_id": dialog.reply.id, "body": "Пришлём предложение."},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert letter_goes == [dialog.reply.id]
    [record] = [r for r in caplog.records if r.name == close_routes.__name__]
    assert record.levelno == logging.WARNING
    assert (
        record.getMessage()
        == "продажи: ответ в переписке ушёл, а ответы с ожидания человека не сняты"
    )
    assert (record.__dict__["thread_id"], record.__dict__["reply_id"]) == (
        dialog.thread.id,
        dialog.reply.id,
    )
    reply = await _reply(session, dialog.reply.id)
    assert (reply.model_parse, reply.reviewed_at) == (UNSURE, None)
    assert await _state(client, dialog.thread.id, headers) == "sales_pending"


async def test_release_after_the_letter_commits_by_itself(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Маршрут черновика коммитит отправку раньше — снятие идёт новой транзакцией и коммитит её
    само; в базе тестов одна внешняя транзакция, поэтому коммит виден только подменой."""
    dialog = await _waiting(session)
    at_commit: list[str | None] = []
    real = session.commit

    async def counted() -> None:
        at_commit.append(dialog.reply.reviewed_by)
        await real()

    monkeypatch.setattr(session, "commit", counted)

    await close_routes.release_answered(
        session, dialog.thread.id, dialog.reply.id, UserModel(email=SELLER)
    )

    assert at_commit == [SELLER]


# --- ответ по черновику агента ------------------------------------------------------------------


@pytest.mark.usefixtures("sales_on")
async def test_sent_draft_releases_its_answer(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    llm: Plug,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«Подходит · отправить» уходит мимо ответа из поля — ответ продаж всё равно больше не ждёт."""
    reply_id = await lead_replied(session)
    llm(situation=[INFORM], judge=[ALLOW])
    outcome = await drafting.draft_answer(session, Writer(GOOD), reply_id)
    await session.commit()
    assert outcome.draft_id is not None
    assert (await _reply(session, reply_id)).reviewed_at is None

    async def sent(*_args: object, **_kwargs: object) -> SendOutcome:
        return SendOutcome(
            message_id=1,
            sender_email="anna@mail.example.test",
            provider_message_id="<1>",
            real=False,
        )

    monkeypatch.setattr(drafts, "send_draft", sent)
    headers = await _admin(make_user, sign_in)

    response = await client.post(
        f"/api/agent/drafts/{outcome.draft_id}/send", json={}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert agent_routes.release_answered is close_routes.release_answered
    reply = await _reply(session, reply_id)
    assert reply.model_parse is not None
    assert (reply.model_parse["waits"], reply.model_parse["reason"]) == (
        False,
        f"{ANSWERED}: {ADMIN}",
    )
    assert reply.reviewed_by == ADMIN
    assert reply.thread_id is not None
    await _no_handoff(session, reply.thread_id, queue)
