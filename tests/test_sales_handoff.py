"""Передача лида продаж — срез 5.3, T3: Kommo → Telegram, запасной путь, повторы.

Kommo — `KommoFixture` (помнит созданное) и её подклассы со сбоями; Telegram —
настоящий `SalesBot` поверх `httpx.MockTransport`; тревоги — список. Сети нет.
База настоящая: передача, её состояния и захват задачи живут в ней.
Номера чатов, токен, адреса и тексты — выдуманные.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from backend.config import sales as cfg
from backend.features.sales import handoff, telegram
from backend.features.sales.handoff import Deps, HandoffBusyError, HandoffError
from backend.features.sales.kommo import (
    CreatedLead,
    KommoAuthError,
    KommoContact,
    KommoFixture,
    KommoUnavailableError,
    KommoUnconfirmedError,
    NewLead,
)
from backend.features.sales.models import (
    HandoffKommo,
    HandoffTelegram,
    LeadSource,
    LeadStatus,
    SalesHandoffModel,
    SalesLeadModel,
)
from backend.features.sales.telegram import SalesBot
from backend.shared.queue import QUEUE_NAME, SALES_QUEUE_NAME
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Queue
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_handoff_rows import Dialog, answer, sales_dialog
from tests.test_sales_telegram import GROUP, PERSONAL, TOKEN, Recorder, ok, refused

NOW = datetime(2026, 10, 15, 11, 23, 17, tzinfo=UTC)
APP = "https://app.example.test"
DEAL = "https://fixture.kommo.com/leads/detail/9301"
HEAD = "Новый лид\nЭмейл Рассылка\n"


class Alerts(list[str]):
    """Тревоги владельцу: что ушло бы в чат эксплуатации."""

    async def __call__(self, text: str) -> bool:
        self.append(text)
        return True


class DownKommo(KommoFixture):
    """Kommo, который не отвечает на запись, пока `down`."""

    def __init__(self) -> None:
        super().__init__()
        self.down = True

    async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
        if self.down:
            raise KommoUnavailableError("Kommo не принял запрос (HTTP 503) — повторим позже")
        return await super().create_complex_lead(lead)

    async def add_note(self, lead_id: int, text: str) -> int:
        if self.down:
            raise KommoUnavailableError("Kommo не ответил: связь оборвалась (ReadError)")
        return await super().add_note(lead_id, text)


class LostAnswerKommo(KommoFixture):
    """Запись ушла (`lands`) или не ушла, а ответ потерян в обоих случаях."""

    def __init__(self, *, lands: bool) -> None:
        super().__init__()
        self.lands = lands
        self.writes = 0

    async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
        self.writes += 1
        if self.lands:
            await super().create_complex_lead(lead)
        raise KommoUnconfirmedError("ответ Kommo потерян после отправки (ReadTimeout)")


class RefusingKommo(KommoFixture):
    async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
        raise KommoAuthError("ключ Kommo отклонён (HTTP 401) — выпустить новый")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Бот продаж настроен; Telegram принимает всё, пока тест не скажет иначе."""
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(cfg, "TELEGRAM_CHAT_ID", PERSONAL)
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_CHAT_ID", GROUP)
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_COPY", True)
    monkeypatch.setattr(cfg, "APP_URL", APP)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(telegram, "_sleep", no_sleep)
    return Recorder(ok())


@pytest.fixture
async def http(api: Recorder) -> AsyncIterator[httpx.AsyncClient]:
    async with api.client() as client:
        yield client


@pytest.fixture
def alerts() -> Alerts:
    return Alerts()


def deps(
    http: httpx.AsyncClient,
    alerts: Alerts,
    kommo: KommoFixture | None,
    *,
    at: datetime = NOW,
) -> Deps:
    return Deps(kommo=kommo, bot=SalesBot(http), alert=alerts, now=lambda: at)


def sent(api: Recorder) -> list[tuple[str, str]]:
    """Что ушло в Telegram: (чат, текст) по порядку."""
    bodies = [json.loads(request.content) for request in api.seen]
    return [(body["chat_id"], body["text"]) for body in bodies]


async def hand_off(
    session: AsyncSession, dialog: Dialog, work: Deps, queued: list[int] | None = None
) -> SalesHandoffModel:
    """Триггер и задача подряд: `start`, затем `process` того, что встало в очередь."""
    queue = [] if queued is None else queued
    row = await handoff.start(session, dialog.thread.id, enqueue=queue.append, now=lambda: NOW)
    await handoff.process(session, row.id, work)
    return await reread(session, row.id)


async def reread(session: AsyncSession, handoff_id: int) -> SalesHandoffModel:
    row = await session.get(SalesHandoffModel, handoff_id, populate_existing=True)
    assert row is not None
    return row


def dialog_line(dialog: Dialog, tail: str = "") -> str:
    return f"{HEAD}Ссылка на диалог: {APP}/threads/{dialog.thread.id}{tail}"


# --- A1: сделка → три строки со ссылкой → копия в группу → цепочка держится ---------------


async def test_a1_deal_then_three_lines_then_group_copy_and_chain_held(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    kommo = KommoFixture()
    queued: list[int] = []

    row = await hand_off(session, dialog, deps(http, alerts, kommo), queued)

    assert queued == [row.id]
    assert list(kommo.leads) == [9301]
    deal = kommo.leads[9301]
    assert (deal.draft.email, deal.draft.site, deal.draft.title) == (
        "ivan@acme.example.test",
        "acme.example.test",
        "Email: Акме Тест",
    )
    assert deal.draft.hypothesis == "гипотеза acme.example.test"
    assert deal.draft.name == "Иван Примеров"
    [note] = deal.notes.values()
    assert "Давайте созвонимся во вторник после обеда." in note
    assert f"{APP}/threads/{dialog.thread.id}" in note
    assert "ГЕО: de" in note
    message = f"{HEAD}Ссылка на сделку в коммо: {DEAL}"
    assert message.count("\n") == 2
    assert sent(api) == [(PERSONAL, message), (GROUP, message)]
    assert (row.kommo, row.telegram, row.kommo_lead_id) == (
        HandoffKommo.DONE,
        HandoffTelegram.SENT,
        9301,
    )
    assert (row.notified_link, row.notified_at, row.due_at, row.claimed_at) == (
        DEAL,
        NOW,
        None,
        None,
    )
    assert row.noted_reply_id == dialog.reply.id
    assert alerts == []
    assert await handoff.handed_off(session, dialog.lead.id) is True


async def test_lead_without_handoff_is_not_held(session: AsyncSession) -> None:
    dialog = await sales_dialog(session)
    assert await handoff.handed_off(session, dialog.lead.id) is False


# --- A2: следующий ответ — примечание к той же сделке -------------------------------------


async def test_a2_next_answer_is_a_note_to_the_same_deal(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    kommo = KommoFixture()
    work = deps(http, alerts, kommo)
    await hand_off(session, dialog, work)
    later = await answer(
        session,
        dialog.thread,
        dialog.message,
        "И ещё: нам интересен аудит ссылок.",
        at=NOW + timedelta(hours=3),
    )
    queued: list[int] = []

    row = await hand_off(session, dialog, work, queued)

    assert queued == [row.id]
    assert list(kommo.leads) == [9301], "вторая сделка на повторе"
    notes = list(kommo.leads[9301].notes.values())
    assert len(notes) == 2
    assert "аудит ссылок" in notes[1]
    assert len(api.seen) == 2, "новый лид не новый: телемаркетологу второй раз не пишем"
    assert (row.kommo, row.noted_reply_id) == (HandoffKommo.DONE, later.id)


async def test_a2_answer_that_came_while_a_job_ran_is_noted_by_the_next_job(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Ответ пришёл, пока шла задача: её итог перезаписал «есть что записать», поставленное
    триггером. Следующая задача сама видит неотмеченный ответ — примечание не теряется."""
    dialog = await sales_dialog(session)
    kommo = KommoFixture()
    work = deps(http, alerts, kommo)
    row = await hand_off(session, dialog, work)
    await answer(session, dialog.thread, dialog.message, "Созвон во вторник?", at=NOW)
    assert row.kommo is HandoffKommo.DONE, "итог прошлой задачи — «записано»"

    await handoff.process(session, row.id, work)

    assert len(kommo.leads[9301].notes) == 2
    assert len(kommo.leads) == 1


async def test_a2_same_trigger_twice_is_one_handoff(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    kommo = KommoFixture()
    work = deps(http, alerts, kommo)
    row = await hand_off(session, dialog, work)
    queued: list[int] = []

    again = await handoff.start(session, dialog.thread.id, enqueue=queued.append)
    await handoff.process(session, row.id, work)

    assert again.id == row.id
    assert queued == []
    assert len(kommo.leads[9301].notes) == 1
    assert len(api.seen) == 2
    assert (
        await session.scalar(select(SalesHandoffModel.id).where(SalesHandoffModel.id != row.id))
        is None
    )


# --- A3: Kommo не ответил — ссылка на диалог, тревога, повтор ------------------------------


async def test_a3_kommo_down_sends_dialog_link_alerts_and_waits_for_retry(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    kommo = DownKommo()

    row = await hand_off(session, dialog, deps(http, alerts, kommo))

    message = dialog_line(dialog, " — сделка в Kommo не создана, повторяем")
    assert sent(api) == [(PERSONAL, message), (GROUP, message)], "тишина при отказе Kommo"
    assert (row.kommo, row.telegram, row.kommo_lead_id, row.attempts) == (
        HandoffKommo.RETRY,
        HandoffTelegram.SENT,
        None,
        1,
    )
    assert row.due_at == NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
    assert row.last_error is not None
    assert "HTTP 503" in row.last_error
    [alert] = alerts
    assert "Kommo не ответил" in alert
    assert f"диалог №{dialog.thread.id}" in alert
    assert "ivan@" not in alert, "адрес лида не уходит в чат эксплуатации"


async def test_a3_retry_makes_the_deal_and_sends_its_link_once(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    kommo = DownKommo()
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    await handoff.process(
        session, row.id, deps(http, alerts, kommo, at=NOW + timedelta(minutes=17))
    )
    kommo.down = False

    await handoff.process(
        session, row.id, deps(http, alerts, kommo, at=NOW + timedelta(minutes=33))
    )
    row = await reread(session, row.id)

    assert list(kommo.leads) == [9301]
    deal_message = f"{HEAD}Ссылка на сделку в коммо: {DEAL}"
    assert sent(api)[2:] == [(PERSONAL, deal_message), (GROUP, deal_message)]
    assert len(api.seen) == 4, "повтор без сделки не шлёт ту же ссылку второй раз"
    assert (row.kommo, row.attempts, row.due_at) == (HandoffKommo.DONE, 3, None)
    assert len(alerts) == 2, "тревога — на входе в повтор и на выходе, не на каждом круге"
    assert "заведена" in alerts[1]


async def test_a3_note_that_failed_is_written_later_once(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    kommo = DownKommo()
    kommo.down = False
    work = deps(http, alerts, kommo)
    await hand_off(session, dialog, work)
    await answer(session, dialog.thread, dialog.message, "Вторник подходит.", at=NOW)
    kommo.down = True

    row = await hand_off(session, dialog, work)
    assert (row.kommo, row.kommo_lead_id) == (HandoffKommo.RETRY, 9301)
    assert len(api.seen) == 2, "сделка есть — ссылка на неё уже у телемаркетолога"
    kommo.down = False
    await handoff.process(session, row.id, work)

    assert len(kommo.leads[9301].notes) == 2
    assert (await reread(session, row.id)).kommo is HandoffKommo.DONE


# --- запись ушла, ответ потерян: поиск, а не повтор вслепую --------------------------------


async def test_unconfirmed_write_with_contact_found_is_not_written_again(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    kommo = LostAnswerKommo(lands=True)

    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    await handoff.process(session, row.id, deps(http, alerts, kommo))
    queued: list[int] = []
    await answer(session, dialog.thread, dialog.message, "Ждём звонка.", at=NOW)
    await handoff.start(session, dialog.thread.id, enqueue=queued.append)
    row = await reread(session, row.id)

    assert kommo.writes == 1, "повтор записи после KommoUnconfirmedError"
    assert len(kommo.leads) == 1
    assert (row.kommo, row.kommo_lead_id, row.due_at) == (HandoffKommo.UNCONFIRMED, None, None)
    assert queued == [], "неподтверждённую запись не трогает и новый ответ"
    message = dialog_line(dialog, " — сделка в Kommo не подтверждена, проверяем")
    assert sent(api) == [(PERSONAL, message), (GROUP, message)]
    [alert] = alerts
    assert "проверить в Kommo руками" in alert


async def test_unconfirmed_write_without_contact_before_and_after_is_retried(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    kommo = LostAnswerKommo(lands=False)

    row = await hand_off(session, dialog, deps(http, alerts, kommo))

    assert kommo.writes == 1
    assert row.kommo is HandoffKommo.RETRY
    assert row.last_error is not None
    assert "запись не дошла" in row.last_error


async def test_unconfirmed_write_when_contact_existed_is_left_to_a_human(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    kommo = LostAnswerKommo(lands=False)
    kommo.seed_contact("ivan@acme.example.test")

    row = await hand_off(session, dialog, deps(http, alerts, kommo))

    assert kommo.writes == 1
    assert row.kommo is HandoffKommo.UNCONFIRMED


async def test_contact_that_was_there_before_the_write_decides_even_if_gone_after(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Контакт был до записи — по поиску не узнать, создалась ли сделка, даже если
    после записи поиск его не видит: повтор мог бы завести вторую."""

    class Vanishing(LostAnswerKommo):
        searches = 0

        async def find_contact(self, email: str) -> KommoContact | None:
            self.searches += 1
            return KommoContact(5711, "") if self.searches == 1 else None

    dialog = await sales_dialog(session)
    kommo = Vanishing(lands=False)

    row = await hand_off(session, dialog, deps(http, alerts, kommo))

    assert (kommo.searches, kommo.writes, row.kommo) == (2, 1, HandoffKommo.UNCONFIRMED)


async def test_note_whose_answer_was_lost_is_not_repeated(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    class LostNote(KommoFixture):
        notes_tried = 0

        async def add_note(self, lead_id: int, text: str) -> int:
            self.notes_tried += 1
            raise KommoUnconfirmedError("ответ Kommo потерян после отправки (ReadError)")

    dialog = await sales_dialog(session)
    kommo = LostNote()
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    await handoff.process(session, row.id, deps(http, alerts, kommo))

    assert kommo.notes_tried == 1
    assert (row.kommo, row.noted_reply_id) == (HandoffKommo.DONE, dialog.reply.id)
    assert row.last_error is not None
    assert "примечание" in row.last_error
    assert len(alerts) == 1


# --- Kommo отказал: ключ, права — человек ------------------------------------------------


async def test_refused_kommo_is_failed_with_alert_and_dialog_link(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, RefusingKommo()))

    assert (row.kommo, row.due_at) == (HandoffKommo.FAILED, None)
    message = dialog_line(dialog, " — сделка в Kommo не создана, разбираемся")
    assert sent(api)[0] == (PERSONAL, message)
    [alert] = alerts
    assert "ключ Kommo отклонён" in alert
    queued: list[int] = []
    await answer(session, dialog.thread, dialog.message, "Алло?", at=NOW)
    await handoff.start(session, dialog.thread.id, enqueue=queued.append)
    assert queued == [row.id], "новый ответ — новая попытка: ключ могли починить"


# --- A4: Telegram недоступен — три попытки, тревога, «не доставлено» -----------------------


async def test_a4_telegram_down_three_attempts_ops_alert_undelivered(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    api.answers = [refused(503, "Service Unavailable")]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert [chat for chat, _ in sent(api)] == [PERSONAL, PERSONAL, PERSONAL]
    assert (row.kommo, row.telegram, row.notified_link) == (
        HandoffKommo.DONE,
        HandoffTelegram.UNDELIVERED,
        None,
    )
    [alert] = alerts
    assert "не доставлено" in alert
    assert "HTTP 503" in alert


async def test_a4_token_from_network_error_stays_out_of_error_and_alert(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    api.answers = [httpx.ConnectError(f"no route to {TOKEN}")]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert row.telegram is HandoffTelegram.UNDELIVERED
    assert row.last_error is not None
    assert TOKEN not in row.last_error
    assert all(TOKEN not in alert for alert in alerts)


# --- A5: копия в группу выключена ---------------------------------------------------------


async def test_a5_group_copy_off_sends_only_personal(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_COPY", False)
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert [chat for chat, _ in sent(api)] == [PERSONAL]
    assert row.telegram is HandoffTelegram.SENT


async def test_group_copy_on_without_group_chat_is_named(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_CHAT_ID", "")
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert [chat for chat, _ in sent(api)] == [PERSONAL]
    assert row.last_error is not None
    assert "SALES_TELEGRAM_GROUP_CHAT_ID" in row.last_error


async def test_group_copy_refused_is_an_alert_not_undelivered(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    api.answers = [ok(), refused(403, "Forbidden: bot was kicked from the group chat")]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert row.telegram is HandoffTelegram.SENT
    [alert] = alerts
    assert "копия в группу" in alert


# --- A6: Kommo не подключён — ссылка на диалог, не тишина ----------------------------------


async def test_a6_kommo_not_connected_sends_dialog_link(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, None))

    assert sent(api) == [(PERSONAL, dialog_line(dialog)), (GROUP, dialog_line(dialog))]
    assert (row.kommo, row.telegram, row.kommo_lead_id, row.attempts) == (
        HandoffKommo.OFF,
        HandoffTelegram.SENT,
        None,
        0,
    )
    assert alerts == []
    queued: list[int] = []
    await handoff.start(session, dialog.thread.id, enqueue=queued.append)
    assert queued == []


async def test_a6_later_answer_after_kommo_connected_makes_the_deal(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    await hand_off(session, dialog, deps(http, alerts, None))
    await answer(session, dialog.thread, dialog.message, "Так что, созвон?", at=NOW)
    kommo = KommoFixture()

    row = await hand_off(session, dialog, deps(http, alerts, kommo))

    assert (row.kommo, row.kommo_lead_id) == (HandoffKommo.DONE, 9301)
    assert sent(api)[-1] == (GROUP, f"{HEAD}Ссылка на сделку в коммо: {DEAL}")


async def test_dialog_link_without_app_url_names_the_setting(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "APP_URL", "")
    dialog = await sales_dialog(session)

    await hand_off(session, dialog, deps(http, alerts, None))

    assert sent(api)[0][1].endswith(f"диалог №{dialog.thread.id} (SALES_APP_URL не задан)")


# --- какой лид у диалога: правило, а не догадка -------------------------------------------


async def test_unknown_dialog_is_refused(session: AsyncSession) -> None:
    with pytest.raises(HandoffError, match="диалога №987651 нет"):
        await handoff.start(session, 987651, enqueue=lambda _id: None)


async def test_dialog_without_sales_lead_is_refused_loudly(session: AsyncSession) -> None:
    dialog = await sales_dialog(session)
    await session.execute(
        update(SalesLeadModel).values(email="other@acme.example.test", contact_id=None)
    )
    with pytest.raises(HandoffError, match="нет лида продаж") as caught:
        await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    assert caught.value.permanent is True


async def test_two_leads_for_one_dialog_are_not_guessed(session: AsyncSession) -> None:
    dialog = await sales_dialog(session)
    twin = _twin(dialog, LeadStatus.READY)
    session.add(twin)
    await session.flush()
    with pytest.raises(HandoffError, match=f"{dialog.lead.id}, {twin.id}"):
        await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)


async def test_rejected_duplicate_is_not_a_candidate(session: AsyncSession) -> None:
    dialog = await sales_dialog(session)
    session.add(_twin(dialog, LeadStatus.REJECTED))
    await session.flush()
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    assert row.lead_id == dialog.lead.id


async def test_lead_is_found_by_email_when_its_address_row_is_gone(session: AsyncSession) -> None:
    dialog = await sales_dialog(session)
    await session.execute(
        update(SalesLeadModel).values(contact_id=None, email="Ivan@Acme.Example.Test")
    )
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    assert row.lead_id == dialog.lead.id


def _twin(dialog: Dialog, status: LeadStatus) -> SalesLeadModel:
    return SalesLeadModel(
        hypothesis_id=dialog.lead.hypothesis_id,
        domain_id=dialog.lead.domain_id,
        email=dialog.lead.email,
        source=LeadSource.IMPORT,
        status=status,
    )


# --- задача: захват, сбой внутри, очередь недоступна --------------------------------------


async def test_handoff_held_by_another_job_is_busy(
    session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    row.claimed_at = NOW - timedelta(minutes=3)
    await session.commit()

    with pytest.raises(HandoffBusyError) as caught:
        await handoff.process(session, row.id, deps(http, alerts, KommoFixture()))

    assert getattr(caught.value, "permanent", False) is False


async def test_stale_claim_of_a_dead_job_is_taken_over(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    row.claimed_at = NOW - timedelta(seconds=cfg.HANDOFF_CLAIM_SEC + 1)
    await session.commit()

    await handoff.process(session, row.id, deps(http, alerts, KommoFixture()))

    assert (await reread(session, row.id)).telegram is HandoffTelegram.SENT


async def test_unexpected_failure_releases_the_claim_and_is_raised(
    session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts
) -> None:
    class Broken(KommoFixture):
        async def find_contact(self, email: str) -> None:
            raise RuntimeError("сломалось внутри клиента")

    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)

    with pytest.raises(RuntimeError, match="сломалось"):
        await handoff.process(session, row.id, deps(http, alerts, Broken()))

    assert (await reread(session, row.id)).claimed_at is None


async def test_missing_handoff_is_a_permanent_refusal(
    session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts
) -> None:
    with pytest.raises(HandoffError, match="передачи №987653 нет"):
        await handoff.process(session, 987653, deps(http, alerts, KommoFixture()))


async def test_queue_down_leaves_the_handoff_to_the_pass(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    dialog = await sales_dialog(session)

    def refuse(_handoff_id: int) -> None:
        raise RedisConnectionError("очередь не отвечает")

    with caplog.at_level(logging.ERROR):
        row = await handoff.start(session, dialog.thread.id, enqueue=refuse, now=lambda: NOW)

    assert row.due_at == NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
    assert "не поставлена" in caplog.text
    later = NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC + 7)
    assert await handoff.due(session, now=later) == [row.id]


# --- проход по расписанию: кого брать ------------------------------------------------------


async def test_pass_takes_retries_and_forgotten_ones_and_skips_the_rest(
    session: AsyncSession,
) -> None:
    rows = {}
    for name in ("retry", "forgotten", "early", "done", "claimed", "undelivered"):
        dialog = await sales_dialog(
            session, host=f"{name}.example.test", email=f"a@{name}.example.test"
        )
        rows[name] = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    past = NOW - timedelta(minutes=1)
    states = {
        "retry": (HandoffKommo.RETRY, HandoffTelegram.SENT, past, None),
        "forgotten": (HandoffKommo.PENDING, HandoffTelegram.PENDING, past, None),
        "early": (HandoffKommo.RETRY, HandoffTelegram.SENT, NOW + timedelta(minutes=4), None),
        "done": (HandoffKommo.DONE, HandoffTelegram.SENT, None, None),
        "claimed": (HandoffKommo.RETRY, HandoffTelegram.SENT, past, NOW - timedelta(minutes=2)),
        "undelivered": (HandoffKommo.DONE, HandoffTelegram.UNDELIVERED, past, None),
    }
    for name, (kommo, telegram_state, due_at, claimed_at) in states.items():
        row = rows[name]
        row.kommo, row.telegram, row.due_at, row.claimed_at = (
            kommo,
            telegram_state,
            due_at,
            claimed_at,
        )
    await session.commit()

    taken = await handoff.due(session, now=NOW)

    assert sorted(taken) == sorted([rows["retry"].id, rows["forgotten"].id])
    pushed = await reread(session, rows["retry"].id)
    assert pushed.due_at == NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
    assert await handoff.due(session, now=NOW) == [], "взятое проходом не берётся вторым кругом"


async def test_dialog_without_counterpart_address_is_refused(session: AsyncSession) -> None:
    dialog = await sales_dialog(session)
    dialog.thread.contact_id = None
    await session.flush()
    with pytest.raises(HandoffError, match="нет адреса собеседника"):
        await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)


async def test_dialog_without_an_answer_still_makes_the_deal_without_note(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    await session.delete(dialog.reply)
    await session.flush()
    kommo = KommoFixture()

    row = await hand_off(session, dialog, deps(http, alerts, kommo))

    assert (row.kommo, row.kommo_lead_id, row.noted_reply_id) == (HandoffKommo.DONE, 9301, None)
    assert kommo.leads[9301].notes == {}
    assert sent(api)[0] == (PERSONAL, f"{HEAD}Ссылка на сделку в коммо: {DEAL}")


async def test_search_after_lost_answer_that_fails_is_left_to_a_human(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    class SearchDiesToo(LostAnswerKommo):
        searches = 0

        async def find_contact(self, email: str) -> None:
            self.searches += 1
            if self.searches > 1:
                raise KommoUnavailableError("Kommo не ответил: связь оборвалась (ConnectError)")

    dialog = await sales_dialog(session)
    kommo = SearchDiesToo(lands=False)

    row = await hand_off(session, dialog, deps(http, alerts, kommo))

    assert (kommo.writes, row.kommo) == (1, HandoffKommo.UNCONFIRMED)
    assert row.last_error is not None
    assert "поиск после записи не ответил" in row.last_error


async def test_handoff_job_goes_to_the_sales_queue_with_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Передача — в очередь продаж (`worker-sales`), а не в общую: общий воркер держит прогон
    доноров до часа, а лид, который хочет говорить, ждать за ним не должен. Очередь —
    настоящая `rq.Queue` из `shared/queue.py`, Redis не трогается: перехвачена только постановка."""
    calls: list[tuple[object, ...]] = []
    made = handoff.sales_queue

    def sales_queue() -> Queue:
        found = made()

        def enqueue(job: str, *args: object, **kwargs: object) -> None:
            calls.append((found.name, job, *args, sorted(kwargs)))

        monkeypatch.setattr(found, "enqueue", enqueue)
        return found

    monkeypatch.setattr(handoff, "sales_queue", sales_queue)

    handoff.enqueue_handoff(4127)

    assert calls == [(SALES_QUEUE_NAME, handoff.HANDOFF_JOB, 4127, ["result_ttl", "retry"])]
    assert SALES_QUEUE_NAME != QUEUE_NAME
    assert handoff.HANDOFF_JOB == "backend.features.sales.handoff_jobs.hand_off_lead"


class _BrokenTransaction:
    """Сессия после сбоя базы: транзакция мертва, а сама база отвечает или нет."""

    is_active = False

    def __init__(self, *, database_up: bool) -> None:
        self.database_up = database_up
        self.calls: list[str] = []

    async def rollback(self) -> None:
        self.calls.append("rollback")

    async def execute(self, _statement: object) -> None:
        if not self.database_up:
            raise OperationalError("UPDATE sales_handoffs", {}, Exception("база недоступна"))
        self.calls.append("execute")

    async def commit(self) -> None:
        self.calls.append("commit")


async def test_claim_after_database_failure_is_released_after_rollback() -> None:
    broken = _BrokenTransaction(database_up=True)
    await handoff._release_after_failure(broken, 7)  # type: ignore[arg-type]
    assert broken.calls == ["rollback", "execute", "commit"]


async def test_claim_that_cannot_be_released_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken = _BrokenTransaction(database_up=False)
    with caplog.at_level(logging.ERROR):
        await handoff._release_after_failure(broken, 7)  # type: ignore[arg-type]
    assert "захват передачи не снят" in caplog.text
