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
- **Решение владельца 13.10: личное не дошло — группа всё равно узнаёт о лиде.** Когда итог
  сказан — отказ постоянный (чат не задан, бот заблокирован) или попытки кончились, — копия в
  группу уходит и без личного, тем же текстом со строкой «Личное сообщение телемаркетологу не
  доставлено: <причина>» (`handoff_telegram._heads_up`). Сбой внутри серии повторов группе не
  виден. Копия выключена (`SALES_TELEGRAM_GROUP_COPY`) — в группу не пишем: это было бы новое
  сообщение наружу; тревога владельцу говорит словами, что группа о лиде не знает.
- **Решение владельца 13.10: письмо переданного лида через шлюз — где его читать.** Через шлюз
  агентства новое письмо уже переданного лида не ложится в Kommo (примечаний у шлюза нет):
  «Лид написал снова» несёт к ссылке на сделку и ссылку на диалог, где письмо. Отдельного
  сообщения нет; копия в группу — тот же текст.
- **Решение владельца 13.10: ответ — тому, кто написал** (коллеге лида с домена компании) —
  `test_sales_seam_answer_colleague.py`.

База настоящая; Kommo — подставной; Telegram — `httpx.MockTransport`; очередь — список.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from backend.config import sales as cfg
from backend.features.sales import handoff, handoff_telegram, telegram, telegram_series
from backend.features.sales import handoff_text as wording
from backend.features.sales.handoff_console import close
from backend.features.sales.kommo import KommoAuthError, KommoFixture, KommoGateway
from backend.features.sales.models import HandoffKommo, HandoffTelegram
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_handoff import (
    AGAIN,
    APP,
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
from tests.test_sales_handoff_message_retry import DOWN, resend
from tests.test_sales_handoff_rows import Dialog, answer, sales_dialog
from tests.test_sales_kommo import Script
from tests.test_sales_kommo_gateway import DEAL as GATEWAY_DEAL
from tests.test_sales_kommo_gateway import _made
from tests.test_sales_switch import sales_switched_on
from tests.test_sales_telegram import GROUP, PERSONAL, Recorder, ok, refused

__all__ = ["alerts", "api", "gateway", "http", "sales_switched_on"]

LEAD = "ivan@acme.example.test"
COLLEAGUE = "olga@acme.example.test"
#: Как Telegram отвечает, когда телемаркетолог заблокировал бота и когда бота убрали из группы.
BLOCKED = "Forbidden: bot was blocked by the user"
KICKED = "Forbidden: bot was kicked from the group chat"
#: Пометка тревоги, когда телемаркетолог лида не получил.
LOST = handoff_telegram.CONCERNS[handoff_telegram.PERSONAL]


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


# --- решение владельца 13.10: личное не дошло — группа узнаёт о лиде с пометкой ------------------


async def test_group_hears_of_the_lead_when_the_personal_message_did_not_go(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
) -> None:
    """Бот заблокирован телемаркетологом — отказ постоянный, итог сказан сразу: группа получает то
    же сообщение о лиде со строкой, кому не доставлено и почему; тревога владельцу говорит, что
    группа знает. Телемаркетолог сообщения не получил — ссылка отправленной не отмечена."""
    api.answers = [refused(403, BLOCKED), *([api.answers[0]] * 3)]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    message = f"{HEAD}Ссылка на сделку в коммо: {DEAL}"
    [personal, (chat, told)] = sent(api)
    assert personal == (PERSONAL, message)
    assert chat == GROUP
    assert told.startswith(
        f"{message}\n{wording.UNDELIVERED}Telegram отказал (HTTP 403: {BLOCKED})"
    )
    assert told.count("\n") == 3, "то же сообщение и одна строка пометки"
    assert (row.telegram, row.telegram_due_at, row.notified_link) == (
        HandoffTelegram.UNDELIVERED,
        None,
        None,
    )
    [alert] = alerts
    assert "сообщение телемаркетологу не доставлено — Telegram отказал (HTTP 403" in alert
    assert alert.endswith(f"; {handoff_telegram.GROUP_TOLD}\n{LOST.words}")


async def test_group_hears_once_when_the_retries_end_and_not_on_each_failure(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
) -> None:
    """Telegram лежит для личного: сбой внутри серии повторов группе не виден — мигнувшую сеть
    лечит повтор через 5 минут; попытки кончились — одна копия с пометкой, сколько их было."""
    tries = telegram_series.MESSAGE_TRIES * telegram.ATTEMPTS
    api.answers = [*([DOWN] * tries), api.answers[0]]
    dialog = await sales_dialog(session)
    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))
    while row.telegram_tries:
        assert [chat for chat, _ in sent(api)].count(GROUP) == 0, "сбой в серии — не итог"
        at = row.telegram_due_at
        assert at is not None
        row = await resend(session, row.id, http, alerts, at)

    assert [chat for chat, _ in sent(api)] == [PERSONAL] * tries + [GROUP]
    told = sent(api)[-1][1]
    assert f"\n{wording.UNDELIVERED}не доставлено за {telegram.ATTEMPTS} попытки" in told
    assert told.endswith(f"попыток — {telegram_series.MESSAGE_TRIES}, повторов больше нет")
    [alert] = alerts
    assert f"не доставлено за {telegram_series.MESSAGE_TRIES} попыток" in alert
    assert alert.endswith(f"; {handoff_telegram.GROUP_TOLD}\n{LOST.words}")


@pytest.mark.parametrize(
    ("copy", "chat", "said"),
    [(False, GROUP, handoff_telegram.GROUP_OFF), (True, "", handoff_telegram.GROUP_UNSET)],
    ids=["group-copy-switched-off", "group-chat-not-set"],
)
async def test_without_the_group_copy_the_owner_hears_that_the_group_does_not_know(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    monkeypatch: pytest.MonkeyPatch,
    copy: bool,
    chat: str,
    said: str,
) -> None:
    """Копия в группу выключена — в группу не пишем и тогда: это было бы новое сообщение наружу.
    Тревога владельцу говорит словами, что группа о лиде не знает; чат группы не задан — так же."""
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_COPY", copy)
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_CHAT_ID", chat)
    api.answers = [refused(403, BLOCKED), *([api.answers[0]] * 3)]
    dialog = await sales_dialog(session)

    await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert [to for to, _ in sent(api)] == [PERSONAL]
    [alert] = alerts
    assert "сообщение телемаркетологу не доставлено — Telegram отказал (HTTP 403" in alert
    assert alert.endswith(f"; {said}\n{LOST.words}")


async def test_a_group_copy_that_did_not_go_either_is_told_and_leaves_the_series_alone(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
) -> None:
    """Не ушла и копия с пометкой — тревога говорит, что группа о лиде не знает и почему; строка
    передачи — итог личного: повторов нет, ошибка — его, копия свою серию не заводит."""
    api.answers = [refused(403, BLOCKED), refused(403, KICKED)]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert [to for to, _ in sent(api)] == [PERSONAL, GROUP]
    [alert] = alerts
    assert (
        f"копия в группу тоже не ушла — группа о лиде не знает: Telegram отказал (HTTP 403: {KICKED})"
        in alert
    )
    assert (row.telegram, row.telegram_tries, row.telegram_due_at) == (
        HandoffTelegram.UNDELIVERED,
        0,
        None,
    )
    assert row.last_error is not None
    assert row.last_error.startswith(f"Telegram: Telegram отказал (HTTP 403: {BLOCKED})")


# --- решение владельца 13.10: письмо переданного лида через шлюз — где его читать ----------------


async def _again_through_the_gateway(
    session: AsyncSession,
    http: httpx.AsyncClient,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
) -> tuple[Dialog, KommoGateway]:
    """Лид передан через шлюз (сделка 9341), затем написал снова."""
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
    return dialog, client


def _again(dialog: Dialog) -> str:
    """«Лид написал снова» через шлюз: ссылка на сделку и ссылка на диалог, где письмо."""
    where = f"{wording.LETTER_IN_SERVICE}{APP}/threads/{dialog.thread.id}"
    return f"{AGAIN}Ссылка на сделку в коммо: {GATEWAY_DEAL}\n{where}"


async def test_a_new_letter_through_the_gateway_tells_where_to_read_it(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
) -> None:
    """В сделку письмо не легло (примечаний у шлюза нет) — «Лид написал снова» несёт и ссылку на
    диалог, где письмо; копия в группу — тот же текст. Отдельного сообщения нет."""
    dialog, client = await _again_through_the_gateway(session, http, alerts, gateway)

    await hand_off(session, dialog, work(http, alerts, client))

    assert sent(api)[2:] == [(PERSONAL, _again(dialog)), (GROUP, _again(dialog))]


async def test_the_group_copy_repeated_by_the_pass_keeps_the_link_to_the_letter(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
) -> None:
    """Копия в группу о письме через шлюз не ушла сразу (Telegram 503) — проход повторяет её тем
    же текстом, что получил телемаркетолог: со ссылкой на диалог."""
    dialog, client = await _again_through_the_gateway(session, http, alerts, gateway)
    api.answers = [ok(), DOWN]
    row = await hand_off(session, dialog, work(http, alerts, client))
    at = row.telegram_due_at
    assert at is not None, "копия ждёт повтора"
    api.answers = [ok()]

    await handoff.process(session, row.id, work(http, alerts, client, at=at), kommo=False)

    assert sent(api)[2] == (PERSONAL, _again(dialog))
    assert sent(api)[-1] == (GROUP, _again(dialog))
