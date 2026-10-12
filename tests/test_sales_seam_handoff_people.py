"""Ревью стыков, круг 2: передача лида в бою — кто написал, ссылка на сделку после «закрыть руками»,
копия в группу, когда личное сообщение не ушло.

- **Ответил не лид.** Первое письмо спрашивает, кто у компании отвечает за маркетинг, и ответить
  может коллега со своего адреса («давайте созвонимся» от руководителя маркетинга). Примечание к
  сделке говорило «От: <адрес лида>» — телемаркетолог искал бы не того человека. Теперь «От:» —
  адрес, с которого письмо пришло, и пометка, что это не адрес лида (`handoff_text.note`).
- **«Закрыть руками» с номером сделки.** Ответ шлюза потерян (`unconfirmed`), телемаркетолог
  получил ссылку на диалог с пометкой «сделка не подтверждена, проверяем»; человек нашёл сделку
  и закрыл передачу с её номером (`sales-handoff-close --deal`). Ссылку на сделку телемаркетолог
  получал только со следующим письмом лида — а лид, который ждёт звонка, может больше не писать.
  Правило передачи — «ссылка на сделку после ссылки на диалог — тоже» (`handoff.py`): теперь её
  везёт проход повторов сообщения (`handoff_console.close` ставит срок сообщения).
- **Решение владельца:** личное сообщение телемаркетологу не ушло (чат не задан, бот
  заблокирован) — копии в группу нет вовсе: группа узнаёт о лиде только из чата эксплуатации
  владельца. `xfail(strict=True)` ниже — вариант «группа получает лида и тогда».
- **Решение владельца:** через шлюз агентства новое письмо уже переданного лида не ложится в
  Kommo (примечаний у шлюза нет), а сообщение «Лид написал снова» несёт ссылку на сделку, где
  этого письма нет: текст письма — только в переписке сервиса, и ссылки на неё у телемаркетолога
  нет. `xfail(strict=True)` ниже — вариант «в сообщении и ссылка на диалог, где письмо».
- **Решение владельца:** ответ в переписке (и черновик агента) уходит на адрес лида, даже если
  написал его коллега со своего адреса (`sales/mail.recipient`: «адрес — лида»): агент пишет
  «Здравствуйте, Ольга», а письмо получает общий ящик компании. `xfail(strict=True)` ниже —
  вариант «ответ — тому, кто написал, если адрес на домене компании лида».

База настоящая; Kommo — подставной; Telegram — `httpx.MockTransport`; очередь — список.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from backend.features.sales import handoff
from backend.features.sales.handoff_console import close
from backend.features.sales.kommo import KommoAuthError, KommoFixture, KommoGateway
from backend.features.sales.models import HandoffKommo, HandoffTelegram
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_handoff import (
    DEAL,
    HEAD,
    NOW,
    Alerts,
    LostAnswerKommo,
    alerts,
    api,
    deps,
    dialog_line,
    hand_off,
    http,
    reread,
    sent,
)
from tests.test_sales_handoff_gateway import gateway, work
from tests.test_sales_handoff_rows import answer, sales_dialog
from tests.test_sales_kommo import Script
from tests.test_sales_kommo_gateway import _made
from tests.test_sales_send import _seen
from tests.test_sales_switch import sales_switched_on
from tests.test_sales_telegram import GROUP, PERSONAL, Recorder, refused
from tests.test_sales_thread_answer import Answered, answered

__all__ = ["alerts", "answered", "api", "gateway", "http", "sales_switched_on"]

LEAD = "ivan@acme.example.test"
COLLEAGUE = "olga@acme.example.test"


# --- ответил не лид: «От:» — тот, кто написал ---------------------------------------------------


async def test_note_names_the_colleague_who_answered_and_says_it_is_not_the_lead(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Коллега лида ответил со своего адреса «давайте созвонимся» — примечание называет его
    адрес и что это не адрес лида: телемаркетолог звонит тому, кто хочет говорить."""
    dialog = await sales_dialog(session, text="Я отвечаю за маркетинг. Давайте созвонимся.")
    dialog.reply.from_email = COLLEAGUE
    kommo = KommoFixture()

    await hand_off(session, dialog, deps(http, alerts, kommo))

    [note] = kommo.leads[9301].notes.values()
    assert f"От: {COLLEAGUE} (не адрес лида {LEAD})" in note.splitlines()
    assert "Я отвечаю за маркетинг. Давайте созвонимся." in note


async def test_note_of_the_lead_himself_names_his_address_as_before(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Граница правила: написал сам лид (и адрес — в другом регистре) — строка прежняя."""
    dialog = await sales_dialog(session)
    dialog.reply.from_email = LEAD.upper()
    kommo = KommoFixture()

    await hand_off(session, dialog, deps(http, alerts, kommo))

    [note] = kommo.leads[9301].notes.values()
    assert f"От: {LEAD}" in note.splitlines()


async def test_note_without_a_sender_address_falls_back_to_the_lead(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    dialog.reply.from_email = None
    kommo = KommoFixture()

    await hand_off(session, dialog, deps(http, alerts, kommo))

    [note] = kommo.leads[9301].notes.values()
    assert f"От: {LEAD}" in note.splitlines()


# --- «закрыть руками» с номером сделки — ссылка телемаркетологу ---------------------------------


async def test_closing_with_the_deal_found_sends_its_link_to_the_telemarketer_once(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Ответ Kommo потерян — телемаркетолог получил ссылку на диалог «проверяем»; человек нашёл
    сделку и закрыл передачу с её номером. Проход повторов сообщения везёт ссылку на сделку —
    без нового письма лида; второй круг прохода её не повторяет."""
    dialog = await sales_dialog(session)
    kommo = LostAnswerKommo(lands=True)
    kommo.seed_contact(dialog.lead.email)
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    assert row.kommo is HandoffKommo.UNCONFIRMED
    told = dialog_line(dialog, " — сделка в Kommo не подтверждена, проверяем")
    assert sent(api) == [(PERSONAL, told), (GROUP, told)]

    await close(session, row.id, note="нашёл сделку по почте", deal=9301, author="тест", now=NOW)
    later = NOW + timedelta(minutes=1)
    taken = await handoff.message_due(session, now=later)
    for handoff_id in taken:
        await handoff.process(session, handoff_id, deps(http, alerts, kommo, at=later), kommo=False)
    again = await handoff.message_due(session, now=later + timedelta(minutes=20))

    deal = f"{HEAD}Ссылка на сделку в коммо: {DEAL}"
    assert taken == [row.id]
    assert sent(api)[2:] == [(PERSONAL, deal), (GROUP, deal)]
    assert again == []
    row = await reread(session, row.id)
    assert (row.telegram, row.notified_link, row.telegram_due_at) == (
        HandoffTelegram.SENT,
        DEAL,
        None,
    )


async def test_closing_without_a_deal_sends_nothing(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Граница правила: без номера сделки ссылки нет — проход сообщений строку не берёт."""
    dialog = await sales_dialog(session)
    kommo = LostAnswerKommo(lands=False)
    kommo.seed_contact(dialog.lead.email)
    row = await hand_off(session, dialog, deps(http, alerts, kommo))

    await close(session, row.id, note="лид уже на связи", deal=None, author="тест", now=NOW)

    assert await handoff.message_due(session, now=NOW + timedelta(minutes=1)) == []
    assert len(api.seen) == 2


async def test_closing_with_the_deal_the_telemarketer_already_has_sends_nothing(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Сделка была заведена до отказа (номер записан) — ссылка на неё уже у телемаркетолога:
    копия в группу и личное не повторяются."""

    class NoteRefused(KommoFixture):
        async def add_note(self, lead_id: int, text: str) -> int:
            raise KommoAuthError("ключ Kommo отклонён (HTTP 401) — выпустить новый")

    dialog = await sales_dialog(session)
    kommo = NoteRefused()
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    assert (row.kommo, row.kommo_lead_id) == (HandoffKommo.FAILED, 9301)
    seen = len(api.seen)

    await close(session, row.id, note="сделка есть", deal=9301, author="тест", now=NOW)

    assert await handoff.message_due(session, now=NOW + timedelta(minutes=1)) == []
    assert len(api.seen) == seen


# --- решение владельца: личное не ушло — группа всё равно узнаёт о лиде ------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "решение владельца: личное сообщение телемаркетологу не ушло (чат не задан, бот "
        "заблокирован, Telegram лежит) — копия в группу не уходит вовсе, и о лиде знает только "
        "чат эксплуатации владельца. Вариант: группа получает сообщение о лиде и тогда — с "
        "пометкой, что телемаркетологу не доставлено (handoff_telegram.step)"
    ),
)
async def test_group_hears_of_the_lead_when_the_personal_message_did_not_go(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
) -> None:
    api.answers = [refused(403, "Forbidden: bot was blocked by the user"), *([api.answers[0]] * 3)]
    dialog = await sales_dialog(session)

    await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert GROUP in [chat for chat, _ in sent(api)]


# --- решение владельца: письмо переданного лида через шлюз — куда его читать -------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "решение владельца: через шлюз новое письмо переданного лида в Kommo не ложится "
        "(примечаний у шлюза нет), а «Лид написал снова» несёт только ссылку на сделку, где "
        "этого письма нет. Вариант: в сообщении и ссылка на диалог в сервисе, где письмо "
        "(handoff_text.message, again и клиент без add_notes)"
    ),
)
async def test_a_new_letter_through_the_gateway_tells_where_to_read_it(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
) -> None:
    script, client = gateway
    script.replies = [_made(9341)]
    dialog = await sales_dialog(session)
    await hand_off(session, dialog, work(http, alerts, client))
    await answer(
        session,
        dialog.thread,
        dialog.message,
        "Перезвоните, пожалуйста, завтра после 15:00 по номеру из подписи.",
        at=NOW + timedelta(hours=3),
    )

    await hand_off(session, dialog, work(http, alerts, client))

    [(_, told), _] = sent(api)[2:]
    assert told.startswith("Лид написал снова")
    assert f"/threads/{dialog.thread.id}" in told, "письма нет ни в Kommo, ни по ссылке"


# --- решение владельца: ответ — тому, кто написал ------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "решение владельца: коллега лида ответил со своего адреса на домене компании — ответ "
        "в переписке (и черновик агента) уходит на адрес лида (sales/mail.recipient: «адрес — "
        "лида»), а не тому, кто написал. Вариант: адрес ответившего, если он на домене компании "
        "лида; чужой домен — адрес лида, как сейчас"
    ),
)
async def test_the_answer_goes_to_the_colleague_who_wrote(
    session: AsyncSession, answered: Answered
) -> None:
    answered.reply.from_email = "olga@acme.example.test"
    await session.flush()

    await answered.answer(session)

    [_, out] = _seen(answered.source)
    assert out.to == "olga@acme.example.test"
