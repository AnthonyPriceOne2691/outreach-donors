"""Повтор сообщения о лиде в Telegram по расписанию — решение владельца по ревью стыков (5.3).

Сообщение телемаркетологу или копия в группу, не ушедшие из-за сети, 5xx или 429, повторяет
проход: пауза растёт вдвое (5, 10, 20, 40 мин), у 429 — не меньше паузы, названной Telegram;
попыток — пять, после последней — тревога словами; постоянный отказ (чат не найден, нет
токена, непечатный знак в токене) — без повтора и с тревогой сразу. Повтор только сообщения —
своя задача: запись в Kommo она не открывает.

Оснастка — `test_sales_handoff.py`: Kommo — подставной, Telegram — `SalesBot` поверх
`httpx.MockTransport`, тревоги — список. Номера, токен и тексты — выдуманные.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest
from backend.config import sales as cfg
from backend.features.sales import handoff, handoff_jobs, telegram_series
from backend.features.sales.kommo import CreatedLead, KommoAuthError, KommoFixture, NewLead
from backend.features.sales.models import HandoffKommo, HandoffTelegram, SalesHandoffModel
from backend.shared.queue import SALES_QUEUE_NAME
from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_handoff import (
    NOW,
    Alerts,
    DownKommo,
    alerts,
    api,
    deps,
    hand_off,
    http,
    reread,
    sent,
)
from tests.test_sales_handoff_jobs import wired
from tests.test_sales_handoff_rows import sales_dialog
from tests.test_sales_switch import sales_switched_on
from tests.test_sales_telegram import GROUP, PERSONAL, TOKEN, Recorder, ok, refused

__all__ = [  # оснастка передачи и задачи
    "alerts",
    "api",
    "http",
    "sales_switched_on",  # продажи включены (SALES_ENABLED): передача и повтор сообщения
    "wired",
]

DOWN = refused(503, "Service Unavailable")
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)


def flood(seconds: int) -> httpx.Response:
    """429 с паузой, которую назвал Telegram."""
    return httpx.Response(
        429,
        json={
            "ok": False,
            "description": "Too Many Requests",
            "parameters": {"retry_after": seconds},
        },
    )


class CountingKommo(KommoFixture):
    """Kommo, который считает записи сделок: повтор сообщения его звать не должен."""

    writes = 0

    async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
        self.writes += 1
        return await super().create_complex_lead(lead)


async def resend(
    session: AsyncSession,
    handoff_id: int,
    http: httpx.AsyncClient,
    alerts: Alerts,
    at: datetime,
    kommo: KommoFixture | None = None,
) -> SalesHandoffModel:
    """Задача повтора сообщения в минуту `at` — та, что ставит проход (`MESSAGE_JOB`)."""
    work = deps(http, alerts, kommo or KommoFixture(), at=at)
    await handoff.process(session, handoff_id, work, kommo=False)
    return await reread(session, handoff_id)


async def _undelivered(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> SalesHandoffModel:
    """Передача, чьё сообщение не ушло на первой задаче: сделка есть, Telegram отвечает 503."""
    api.answers = [DOWN]
    dialog = await sales_dialog(session)
    return await hand_off(session, dialog, deps(http, alerts, KommoFixture()))


# --- пауза растёт, проход берёт передачу, когда пора ------------------------------------------


async def test_message_that_did_not_go_is_retried_by_the_pass_with_a_growing_pause(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    row = await _undelivered(session, http, api, alerts)
    pauses = [row.telegram_due_at - NOW] if row.telegram_due_at else []

    for _ in range(3):
        at = row.telegram_due_at
        assert at is not None
        assert await handoff.message_due(session, now=at - MINUTE) == [], "раньше срока"
        assert await handoff.message_due(session, now=at) == [row.id]
        row = await resend(session, row.id, http, alerts, at)
        assert row.telegram_due_at is not None
        pauses.append(row.telegram_due_at - at)

    assert pauses == [timedelta(minutes=m) for m in (5, 10, 20, 40)]
    assert (row.telegram, row.telegram_tries) == (HandoffTelegram.UNDELIVERED, 4)
    assert row.last_error is not None
    assert "попытка 4 из 5" in row.last_error
    assert alerts == [], "до последней попытки тревоги нет"
    assert len(api.seen) == 4 * 3, "каждая задача — три попытки бота, не больше"


async def test_last_try_is_an_alert_in_words_and_the_retries_end(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    row = await _undelivered(session, http, api, alerts)
    while row.telegram_tries:
        at = row.telegram_due_at
        assert at is not None
        row = await resend(session, row.id, http, alerts, at)

    assert len(api.seen) == telegram_series.MESSAGE_TRIES * 3
    assert (row.telegram, row.telegram_due_at) == (HandoffTelegram.UNDELIVERED, None)
    [alert] = alerts
    assert "сообщение телемаркетологу не доставлено за 5 попыток" in alert
    assert "повторов больше нет" in alert
    assert "HTTP 503" in alert
    assert f"диалог №{row.thread_id}" in alert
    assert "ivan@" not in alert, "адрес лида не уходит в чат эксплуатации"
    assert row.last_error is not None
    assert "повторов больше нет" in row.last_error
    assert await handoff.message_due(session, now=NOW + timedelta(days=3)) == []


async def test_after_the_last_try_another_job_tries_quietly_and_starts_no_series(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Итог сказан: задача по другому поводу (повтор Kommo, новый ответ) пробует сообщение
    ещё раз, но при отказе тревоги не повторяет и новой серии повторов не заводит."""
    row = await _undelivered(session, http, api, alerts)
    while row.telegram_tries:
        at = row.telegram_due_at
        assert at is not None
        row = await resend(session, row.id, http, alerts, at)
    assert len(alerts) == 1
    before = len(api.seen)

    await handoff.process(session, row.id, deps(http, alerts, KommoFixture(), at=NOW + 3 * HOUR))
    row = await reread(session, row.id)

    assert len(api.seen) == before + 3, "задача по другому поводу пробует, как и раньше"
    assert (row.telegram, row.telegram_tries, row.telegram_due_at) == (
        HandoffTelegram.UNDELIVERED,
        0,
        None,
    )
    assert len(alerts) == 1, "тревога о том же сообщении — второй раз"
    assert await handoff.message_due(session, now=NOW + timedelta(days=3)) == []


async def test_success_on_a_retry_sends_the_group_copy_and_ends_the_retries(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    row = await _undelivered(session, http, api, alerts)
    api.answers = [ok()]
    at = row.telegram_due_at
    assert at is not None

    row = await resend(session, row.id, http, alerts, at)

    assert [chat for chat, _ in sent(api)[3:]] == [PERSONAL, GROUP]
    assert (row.telegram, row.telegram_tries, row.telegram_due_at) == (
        HandoffTelegram.SENT,
        0,
        None,
    )
    assert (row.notified_at, row.due_at) == (at, None)
    assert alerts == []


# --- 429: пауза Telegram — нижняя граница ----------------------------------------------------


async def test_pause_asked_by_telegram_is_kept_when_longer_than_ours(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    api.answers = [flood(317)]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert len(api.seen) == 1, "долгую паузу бот не спит — отказ сразу"
    assert row.telegram_due_at == NOW + timedelta(seconds=317)
    assert row.last_error is not None
    assert "317" in row.last_error


async def test_our_pause_is_kept_when_telegram_asks_less(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    api.answers = [flood(3)]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert len(api.seen) == 3
    assert row.telegram_due_at == NOW + timedelta(minutes=5)


def test_pause_grows_and_never_undercuts_telegram() -> None:
    assert [telegram_series.pause_after(n, None) for n in (1, 2, 3, 4)] == [300, 600, 1200, 2400]
    assert telegram_series.pause_after(2, 4000.0) == 4000
    assert telegram_series.pause_after(2, 7.0) == 600


# --- постоянный отказ — без повтора, тревога сразу --------------------------------------------


@pytest.mark.parametrize("cause", ["chat not found", "no token", "control character"])
async def test_permanent_refusal_is_not_retried_and_is_told_at_once(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    monkeypatch: pytest.MonkeyPatch,
    cause: str,
) -> None:
    if cause == "chat not found":
        api.answers = [refused(400, "Bad Request: chat not found")]
    elif cause == "no token":
        monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", "")
    else:
        monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", f"{TOKEN[:10]}\t{TOKEN[10:]}")
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert (row.telegram, row.telegram_tries, row.telegram_due_at) == (
        HandoffTelegram.UNDELIVERED,
        0,
        None,
    )
    assert len(api.seen) <= 1, "постоянный отказ бот не повторяет"
    [alert] = alerts
    assert "не доставлено" in alert
    assert await handoff.message_due(session, now=NOW + timedelta(days=3)) == []


async def test_group_copy_refused_for_good_is_told_and_not_retried(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    api.answers = [ok(), refused(403, "Forbidden: bot was kicked from the group chat")]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert (row.telegram, row.telegram_due_at) == (HandoffTelegram.SENT, None)
    [alert] = alerts
    assert "копия в группу продаж не доставлена" in alert


# --- копия в группу: повтор без второго личного сообщения --------------------------------------


async def test_group_copy_that_did_not_go_is_retried_without_a_second_personal_message(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    api.answers = [ok(), DOWN]
    dialog = await sales_dialog(session)
    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))
    assert (row.telegram, row.telegram_tries) == (HandoffTelegram.SENT, 1)
    assert alerts == [], "копия не ушла временно — тревоги ещё нет"
    at = row.telegram_due_at
    assert at == NOW + timedelta(minutes=5)
    api.answers = [ok()]
    before = len(api.seen)

    row = await resend(session, row.id, http, alerts, at)

    assert [chat for chat, _ in sent(api)[before:]] == [GROUP]
    assert (row.telegram, row.telegram_tries, row.telegram_due_at) == (
        HandoffTelegram.SENT,
        0,
        None,
    )


async def test_new_personal_message_starts_its_own_series(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Копия в группу ждала повтора, а тем временем появилась сделка: новое личное сообщение
    со ссылкой на неё не ушло — у него своя серия с первой попытки, а не остаток копии."""
    api.answers = [ok(), DOWN]
    dialog = await sales_dialog(session)
    kommo = DownKommo()
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    assert (row.kommo, row.telegram, row.telegram_tries) == (
        HandoffKommo.RETRY,
        HandoffTelegram.SENT,
        1,
    )
    at = row.telegram_due_at
    assert at is not None
    assert row.due_at == at
    kommo.down = False
    api.answers = [DOWN]

    await handoff.process(session, row.id, deps(http, alerts, kommo, at=at))
    row = await reread(session, row.id)

    assert row.kommo_lead_id == 9301
    assert (row.telegram, row.telegram_tries) == (HandoffTelegram.UNDELIVERED, 1)
    assert row.telegram_due_at == at + timedelta(minutes=5)


# --- раньше срока не шлём, ближайшее дело решает срок прохода -----------------------------------


async def test_job_that_came_early_leaves_the_message_until_its_time(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Задачу привёл не повтор сообщения (повтор Kommo, новый ответ) — Telegram ждёт своего
    срока: пауза растёт, а у 429 её назвал Telegram."""
    row = await _undelivered(session, http, api, alerts)
    before = len(api.seen)

    row = await resend(session, row.id, http, alerts, NOW + 2 * MINUTE)

    assert len(api.seen) == before, "сообщение ушло раньше срока"
    assert (row.telegram_tries, row.telegram_due_at) == (1, NOW + timedelta(minutes=5))
    assert row.due_at == NOW + timedelta(minutes=5)


async def test_kommo_retry_and_a_message_retry_take_the_nearest_time(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    api.answers = [DOWN]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, DownKommo()))

    assert row.kommo is HandoffKommo.RETRY
    assert row.telegram_due_at == NOW + timedelta(minutes=5)
    assert row.due_at == NOW + timedelta(minutes=5), "сообщение ждало бы повтора Kommo"
    at = NOW + timedelta(minutes=5)
    assert await handoff.message_due(session, now=at) == [], (
        "где ждёт и Kommo, задача одна — передача целиком"
    )
    assert await handoff.due(session, now=at) == [row.id]


# --- повтор сообщения не трогает Kommo -------------------------------------------------------


async def test_message_retry_leaves_a_refused_kommo_alone(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Kommo отказал (`failed`), сообщение не ушло: повтор сообщения не повторяет запись —
    её повтор — следующий ответ лида, а тревога об отказе — одна."""

    class Refusing(CountingKommo):
        async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
            self.writes += 1
            raise KommoAuthError("ключ Kommo отклонён (HTTP 401) — выпустить новый")

    kommo = Refusing()
    api.answers = [DOWN]
    dialog = await sales_dialog(session)
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    assert (row.kommo, row.telegram, kommo.writes) == (
        HandoffKommo.FAILED,
        HandoffTelegram.UNDELIVERED,
        1,
    )
    api.answers = [ok()]
    at = row.telegram_due_at
    assert at is not None

    row = await resend(session, row.id, http, alerts, at, kommo=kommo)

    assert kommo.writes == 1, "повтор сообщения повторил запись в Kommo"
    assert (row.kommo, row.telegram) == (HandoffKommo.FAILED, HandoffTelegram.SENT)
    assert len(alerts) == 1, "тревога об отказе Kommo — одна"
    assert sent(api)[-2][1].endswith("сделка в Kommo не создана, разбираемся")


# --- проход и задача --------------------------------------------------------------------------


async def test_pass_queues_a_message_retry_where_only_telegram_waits(
    session: AsyncSession, wired: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = {}
    for name in ("message", "both"):
        dialog = await sales_dialog(
            session, host=f"{name}.example.test", email=f"a@{name}.example.test"
        )
        rows[name] = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    past = datetime.now(NOW.tzinfo) - MINUTE
    message, both = rows["message"], rows["both"]
    message.kommo, message.telegram = HandoffKommo.DONE, HandoffTelegram.UNDELIVERED
    both.kommo, both.telegram = HandoffKommo.RETRY, HandoffTelegram.UNDELIVERED
    for row in (message, both):
        row.telegram_tries, row.telegram_due_at, row.due_at = 1, past, past
    await session.commit()
    handoffs: list[int] = []
    messages: list[int] = []
    monkeypatch.setattr(handoff_jobs, "enqueue_handoff", handoffs.append)
    monkeypatch.setattr(handoff_jobs, "enqueue_message", messages.append)

    await handoff_jobs.retry_pass()

    assert (handoffs, messages) == ([both.id], [message.id])
    pushed = await reread(session, message.id)
    assert pushed.due_at is not None
    assert pushed.due_at > past + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
    assert pushed.telegram_due_at == past, "срок сообщения не сдвигается: по нему задача шлёт"
    await handoff_jobs.retry_pass()
    assert messages == [message.id], "взятое проходом не берётся вторым кругом"


async def test_message_job_goes_to_the_sales_queue_with_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    made = handoff.sales_queue

    def sales_queue() -> Queue:
        found = made()

        def enqueue(job: str, *args: object, **kwargs: object) -> None:
            calls.append((found.name, job, *args, sorted(kwargs)))

        monkeypatch.setattr(found, "enqueue", enqueue)
        return found

    monkeypatch.setattr(handoff, "sales_queue", sales_queue)

    handoff.enqueue_message(4127)

    assert calls == [(SALES_QUEUE_NAME, handoff.MESSAGE_JOB, 4127, ["result_ttl", "retry"])]
    module, _, name = handoff.MESSAGE_JOB.rpartition(".")
    assert module == handoff_jobs.__name__
    assert getattr(handoff_jobs, name) is handoff_jobs.resend_lead_message


def test_message_job_runs_the_core_without_kommo(
    monkeypatch: pytest.MonkeyPatch, wired: Recorder
) -> None:
    monkeypatch.setattr(handoff_jobs, "setup_logging", lambda: None)
    seen: list[tuple[int, bool]] = []

    async def run(handoff_id: int, *, kommo: bool = True) -> dict[str, object]:
        seen.append((handoff_id, kommo))
        return {"handoff": handoff_id}

    monkeypatch.setattr(handoff_jobs, "run_hand_off", run)

    assert handoff_jobs.resend_lead_message(31) == {"handoff": 31}
    assert handoff_jobs.hand_off_lead(32) == {"handoff": 32}
    assert seen == [(31, False), (32, True)]
