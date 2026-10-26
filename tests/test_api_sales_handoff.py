"""«Передать телемаркетологу» через API — кнопка у ответа продаж, который ждёт человека.

Ответ лида ждёт человека: модель назвала вид неуверенно — ниже порога, путь «человек». Кнопка
заводит передачу тем же `handoff.start`, что и вид «хочет говорить», снимает ответы диалога с
ожидания словами решения и ставит задачу Kommo и Telegram — подставной очередью: Redis, Kommo и
Telegram тесты не трогают, задачу никто не исполняет. База настоящая.

Ответы лидов — выдуманные, на разных языках и письменностях, с номерами телефонов разных стран и
форм записи (номера заведомо выдуманные); домены — `*.example.test`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.sales import handoff as handoff_routes
from backend.config import sales as sales_cfg
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission, ReplyKind, Stage
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel
from backend.features.sales.handoff_manual import MANUAL
from backend.features.sales.models import LeadStatus, SalesHandoffModel
from fastapi import FastAPI
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_handoff_rows import Dialog, sales_dialog
from tests.test_sales_switch import sales_switched_on

__all__ = ["sales_switched_on"]  # продажи включены: передача лида идёт

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Учётка с правом отправки — у оператора его нет по роли, выдаётся поимённо.
SELLER = "seller@ours.example.test"
OPERATOR = "operator@ours.example.test"
OUTSIDER = "outsider@ours.example.test"

#: Ответ ждёт человека: вид назван неуверенно — ниже порога, путь «человек» (`reply_routes.decide`).
UNSURE = {
    "stage": "sales",
    "kind": "referral",
    "confidence": 0.61,
    "route": "manual",
    "waits": True,
    "reason": "модель: назвал другого человека, уверенность 61% ниже порога 80% — решает человек",
}

#: Ответ лида с телефоном — разные страны, формы записи номера и письменности.
ANSWERS = [
    (
        "kontor.example.test",
        "jonas@kontor.example.test",
        "Guten Tag, rufen Sie mich an: +49 30 000 000 00",
    ),
    ("souk.example.test", "layla@souk.example.test", "مرحبا، اتصلوا بي: ‎+٩٧١ ٥٠ ٠٠٠ ٠٠٠٠"),
    ("chaguan.example.test", "wei@chaguan.example.test", "您好，请给我打电话：+8613000000000"),
    (
        "biuro.example.test",
        "ola@biuro.example.test",
        "Dzień dobry, proszę dzwonić: (+48) 22-000-12-34",
    ),
]

#: Итог нажатия словами — задача встала, ответ один.
SAID = (
    "Лид передан телемаркетологу: сделка в Kommo и сообщение в Telegram уходят задачей очереди "
    "продаж; ответ больше не ждёт человека."
)


class Queue(list[int]):
    """Очередь продаж — подставная: какие передачи в неё поставлены."""

    def __call__(self, handoff_id: int) -> None:
        self.append(handoff_id)


@pytest.fixture
def queue(api_app: FastAPI) -> Queue:
    queued = Queue()
    api_app.dependency_overrides[handoff_routes.handoff_queue] = lambda: queued
    return queued


async def _seller(make_user: MakeUser, sign_in: SignIn) -> tuple[UserModel, dict[str, str]]:
    user = await make_user(SELLER, permissions={"send": True})
    return user, bearer(await sign_in(SELLER))


async def _waiting(session: AsyncSession, *, answer: tuple[str, str, str] = ANSWERS[0]) -> Dialog:
    """Диалог продаж, где ответ лида ждёт человека: вид назван ниже порога."""
    host, email, text = answer
    dialog = await sales_dialog(session, host=host, email=email, text=text)
    dialog.reply.model_parse = dict(UNSURE)
    await session.commit()
    return dialog


def _path(thread_id: int) -> str:
    return f"/api/sales/threads/{thread_id}/handoff"


async def _handoffs(session: AsyncSession, thread_id: int) -> int:
    found = await session.scalar(
        select(func.count()).where(SalesHandoffModel.thread_id == thread_id)
    )
    return int(found or 0)


async def _reply(session: AsyncSession, reply_id: int) -> ReplyModel:
    """Ответ, как он лежит в базе: сессия общая с приложением, строка — перечитанная."""
    found = await session.get(ReplyModel, reply_id)
    assert found is not None
    await session.refresh(found)
    return found


async def _thread(client: AsyncClient, thread_id: int, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.get(f"/api/threads/{thread_id}", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.parametrize("answer", ANSWERS, ids=["de", "ar", "zh", "pl"])
async def test_waiting_answer_is_handed_over_and_waits_no_more(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    queue: Queue,
    answer: tuple[str, str, str],
) -> None:
    dialog = await _waiting(session, answer=answer)
    user, headers = await _seller(make_user, sign_in)
    before = await _thread(client, dialog.thread.id, headers)
    assert before["card"]["state"] == "sales_pending"
    assert before["handoff"] is None

    response = await client.post(_path(dialog.thread.id), headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    row = await session.scalar(
        select(SalesHandoffModel).where(SalesHandoffModel.thread_id == dialog.thread.id)
    )
    assert row is not None
    assert row.lead_id == dialog.lead.id
    assert queue == [row.id]
    assert body["queued"] is True
    assert body["released"] == [dialog.reply.id]
    assert body["said"] == SAID
    assert body["handoff"] == {
        "id": row.id,
        "at": body["handoff"]["at"],
        "kommo": "pending",
        "kommo_words": "запись ждёт задачи",
        "telegram": "pending",
        "telegram_words": "ещё не отправлено",
    }
    reply = await _reply(session, dialog.reply.id)
    assert reply.model_parse is not None
    assert reply.model_parse["waits"] is False
    assert reply.model_parse["reason"] == f"{MANUAL}: {SELLER}"
    # Что сказала модель, остаётся в снимке: по нему меряют разбор.
    assert reply.model_parse["kind"] == "referral"
    assert reply.model_parse["confidence"] == UNSURE["confidence"]
    assert (reply.reviewed_by, reply.reviewed_at is not None) == (SELLER, True)
    after = await _thread(client, dialog.thread.id, headers)
    assert after["card"]["state"] == "replied"
    assert after["incoming"][0]["needs_review"] is False
    assert after["handoff"]["id"] == row.id
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.target == f"sales_handoff:{row.id}")
    )
    assert journal is not None
    assert (journal.action, journal.user_id) == (AuditAction.LEAD_TAKEN, user.id)


async def test_waiting_work_counters_see_the_handover(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    """Числа «Ждут человека» — у пункта меню и на главной — то же правило, что у списка."""
    dialog = await _waiting(session, answer=ANSWERS[3])
    _, headers = await _seller(make_user, sign_in)
    before = await client.get("/api/overview/work", headers=headers)
    assert before.json()["sales_threads"] == 1

    assert (await client.post(_path(dialog.thread.id), headers=headers)).status_code == 200

    after = await client.get("/api/overview/work", headers=headers)
    assert after.json()["sales_threads"] == 0
    listed = await client.get("/api/threads", headers=headers)
    [card] = [row for row in listed.json() if row["id"] == dialog.thread.id]
    assert card["state"] == "replied"


async def test_every_waiting_answer_of_the_dialog_is_released_others_keep_their_words(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    """Передача — по диалогу: снимаются все ответы, которые ждут человека; разобранный ответ
    (диалог закрыт по виду) и автоответ остаются как были."""
    dialog = await _waiting(session)
    settled = {
        "stage": "sales",
        "kind": "not_now",
        "route": "closed",
        "waits": False,
        "reason": "не сейчас: диалог закрыт",
    }
    earlier = ReplyModel(
        thread_id=dialog.thread.id,
        message_id=dialog.message.id,
        kind=ReplyKind.HUMAN,
        raw_body="Not now, maybe in spring.",
        from_email=dialog.lead.email,
        model_parse=settled,
        created_at=dialog.reply.created_at.replace(hour=1),
    )
    pending = ReplyModel(
        thread_id=dialog.thread.id,
        message_id=dialog.message.id,
        kind=ReplyKind.HUMAN,
        raw_body="Ou par téléphone : +33 1 00 00 00 00",
        from_email=dialog.lead.email,
        created_at=dialog.reply.created_at.replace(hour=23),
    )
    session.add_all([earlier, pending])
    await session.commit()
    _, headers = await _seller(make_user, sign_in)

    response = await client.post(_path(dialog.thread.id), headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["released"] == [dialog.reply.id, pending.id]
    assert response.json()["said"].endswith("; с ожидания человека сняты ответы: 2.")
    assert (await _reply(session, earlier.id)).model_parse == settled
    assert (await _reply(session, earlier.id)).reviewed_at is None
    fresh = await _reply(session, pending.id)
    assert fresh.model_parse == {
        "stage": "sales",
        "kind": None,
        "waits": False,
        "reason": f"{MANUAL}: {SELLER}",
    }


async def test_repeat_is_refused_in_words_and_makes_no_second_handoff(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    dialog = await _waiting(session, answer=ANSWERS[1])
    _, headers = await _seller(make_user, sign_in)
    first = await client.post(_path(dialog.thread.id), headers=headers)
    assert first.status_code == 200, first.text

    again = await client.post(_path(dialog.thread.id), headers=headers)

    assert again.status_code == 409
    assert again.json()["detail"] == (
        "лид уже передан телемаркетологу: Kommo — запись ждёт задачи, Telegram — ещё не "
        "отправлено; повтор ничего не меняет"
    )
    assert await _handoffs(session, dialog.thread.id) == 1
    assert queue == [first.json()["handoff"]["id"]]


async def test_queue_down_is_not_a_refusal_the_pass_takes_the_handoff(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    api_app: FastAPI,
) -> None:
    """Очередь не приняла задачу — передача заведена и ждёт прохода по расписанию: срок стоит."""

    def down(_handoff_id: int) -> None:
        raise RedisConnectionError("Error 61 connecting to redis.example.test:6379")

    api_app.dependency_overrides[handoff_routes.handoff_queue] = lambda: down
    dialog = await _waiting(session, answer=ANSWERS[2])
    _, headers = await _seller(make_user, sign_in)

    response = await client.post(_path(dialog.thread.id), headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["queued"] is False
    every = sales_cfg.HANDOFF_RETRY_SEC // 60
    assert response.json()["said"] == (
        "Лид передан телемаркетологу: задача не встала в очередь — передачу возьмёт проход по "
        f"расписанию в пределах {every} мин; ответ больше не ждёт человека."
    )
    row = await session.scalar(
        select(SalesHandoffModel).where(SalesHandoffModel.thread_id == dialog.thread.id)
    )
    assert row is not None
    assert row.due_at is not None


# --- отказы словами: ничего не записано -----------------------------------------------------


async def _untouched(session: AsyncSession, dialog: Dialog, queue: Queue) -> None:
    """Отказ ничего не записал: передачи нет, задача не ставилась, ответ ждёт, как ждал."""
    assert await _handoffs(session, dialog.thread.id) == 0
    assert queue == []
    reply = await _reply(session, dialog.reply.id)
    assert (reply.model_parse, reply.reviewed_at) == (UNSURE, None)


async def test_unknown_dialog_is_404_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    _, headers = await _seller(make_user, sign_in)
    for number in (987_654, 99_999_999_999):
        response = await client.post(_path(number), headers=headers)
        assert response.status_code == 404
        assert response.json()["detail"] == f"диалога №{number} нет — передавать некого"
    assert queue == []


async def test_not_a_sales_dialog_is_refused_in_words(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    dialog = await _waiting(session)
    await session.execute(
        update(CampaignModel)
        .where(CampaignModel.id == dialog.thread.campaign_id)
        .values(stage=Stage.DONORS)
    )
    await session.commit()
    _, headers = await _seller(make_user, sign_in)

    response = await client.post(_path(dialog.thread.id), headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"диалог №{dialog.thread.id} — не продаж: телемаркетологу передают только лида продаж"
    )
    await _untouched(session, dialog, queue)
    # У переписки не продаж передачи в выдаче нет вовсе.
    assert (await _thread(client, dialog.thread.id, headers))["handoff"] is None


async def test_sales_switched_off_is_refused_before_anything_is_written(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = await _waiting(session)
    _, headers = await _seller(make_user, sign_in)
    monkeypatch.setattr(sales_cfg, "ENABLED", False)

    response = await client.post(_path(dialog.thread.id), headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"передача диалога №{dialog.thread.id} не начата: модуль продаж выключен — "
        "передача ждёт включения"
    )
    await _untouched(session, dialog, queue)


async def test_dialog_without_a_lead_is_refused_in_words(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    """Лида отсеяли, а явной связи диалога с лидом нет — передавать некого (`handoff.lead_of`)."""
    dialog = await _waiting(session)
    dialog.lead.status = LeadStatus.REJECTED
    await session.commit()
    _, headers = await _seller(make_user, sign_in)

    response = await client.post(_path(dialog.thread.id), headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"у диалога №{dialog.thread.id} нет лида продаж: ни ссылкой на адрес, ни почтой на его "
        "домене"
    )
    await _untouched(session, dialog, queue)


async def test_dialog_without_an_answer_of_a_person_is_refused_in_words(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, queue: Queue
) -> None:
    """Лид не отвечал — пришёл только автоответ: телемаркетологу передают ответившего."""
    dialog = await _waiting(session)
    dialog.reply.kind = ReplyKind.AUTO_REPLY
    await session.commit()
    _, headers = await _seller(make_user, sign_in)

    response = await client.post(_path(dialog.thread.id), headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"в диалоге №{dialog.thread.id} нет ответа человека — телемаркетологу передают лида, "
        "который ответил"
    )
    assert await _handoffs(session, dialog.thread.id) == 0
    assert queue == []


@pytest.mark.parametrize(
    ("email", "permissions", "missing"),
    [
        # Оператор без права отправки — по роли его нет.
        (OPERATOR, None, Permission.SEND),
        # Право отправки есть, «Продажи» сняты.
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
    dialog = await _waiting(session)
    await make_user(email, permissions=permissions)

    response = await client.post(_path(dialog.thread.id), headers=bearer(await sign_in(email)))

    assert response.status_code == 403
    assert response.json()["detail"] == no_right(missing)
    await _untouched(session, dialog, queue)
