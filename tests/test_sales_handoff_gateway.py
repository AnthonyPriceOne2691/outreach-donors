"""Передача лида целиком через шлюз агентства: одна сделка, примечание внутри, Telegram.

Мир передачи — из `tests/test_sales_handoff.py`: база настоящая, Telegram — `SalesBot`
поверх `httpx.MockTransport`, тревоги — список. Kommo — настоящий клиент шлюза
(`KommoGateway`) поверх своего подставного транспорта: видно каждый запрос шлюза.
Следующий ответ «хочет говорить» — без второй сделки: метода примечаний у шлюза нет,
телемаркетолог получает сообщение со ссылкой на ту же сделку, в журнале — слова.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import httpx
import pytest
from backend.config import sales as cfg
from backend.features.sales import handoff, handoff_console, handoff_jobs, handoff_kommo
from backend.features.sales import handoff_text as wording
from backend.features.sales.handoff import Deps
from backend.features.sales.kommo import KommoGateway, Pace
from backend.features.sales.models import HandoffKommo, HandoffTelegram
from backend.features.sales.telegram import SalesBot
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_handoff import (
    APP,
    HEAD,
    NOW,
    Alerts,
    alerts,
    api,
    dialog_line,
    hand_off,
    http,
    reread,
    sent,
)
from tests.test_sales_handoff_jobs import wired
from tests.test_sales_handoff_rows import answer, sales_dialog
from tests.test_sales_kommo import Script
from tests.test_sales_kommo_gateway import (
    ACCOUNT,
    DEAL,
    GATEWAY_FILLED,
    KEY,
    TAG,
    URL,
    _failed,
    _made,
)
from tests.test_sales_switch import sales_switched_on
from tests.test_sales_telegram import GROUP, PERSONAL, Recorder

__all__ = [  # оснастка передачи: тревоги, бот продаж, клиент httpx, задача
    "alerts",
    "api",
    "http",
    "sales_switched_on",  # продажи включены (SALES_ENABLED): передача идёт
    "wired",
]

MESSAGE = f"{HEAD}Ссылка на сделку в коммо: {DEAL}"


@pytest.fixture
async def gateway() -> AsyncIterator[tuple[Script, KommoGateway]]:
    """Подставной шлюз и клиент шлюза на нём; ответы тест задаёт сам (`script.replies`)."""
    script = Script()
    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
        pace = Pace(7, clock=script.clock.time, sleep=script.clock.sleep)
        yield script, KommoGateway(client, ACCOUNT, pace=pace)


def work(
    http: httpx.AsyncClient, alerts: Alerts, kommo: KommoGateway, *, at: datetime = NOW
) -> Deps:
    return Deps(kommo=kommo, bot=SalesBot(http), alert=alerts, now=lambda: at)


# --- первый ответ: одна сделка, примечание внутри, ссылка телемаркетологу ----------------------


async def test_one_deal_with_the_note_inside_and_the_link_to_telegram(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
) -> None:
    script, client = gateway
    script.replies = [_made(9341)]
    dialog = await sales_dialog(session)
    queued: list[int] = []

    row = await hand_off(session, dialog, work(http, alerts, client), queued)

    assert queued == [row.id]
    (request,) = script.requests
    assert (request.method, str(request.url)) == ("POST", URL)
    body = script.body(0)
    assert body["lead_name"] == "Email: Акме Тест"
    assert body["contact"] == {
        "email": "ivan@acme.example.test",
        "name": "Иван Примеров",
        "tag": TAG,
    }
    assert body["site"] == "https://acme.example.test"
    # Примечание — то же, что прямой путь кладёт отдельным запросом: письмо лида и сводка.
    assert body["note"] == wording.note(await handoff._card(session, row))
    assert "Давайте созвонимся во вторник после обеда." in body["note"]
    assert f"Диалог в сервисе: {APP}/threads/{dialog.thread.id}" in body["note"]
    assert sent(api) == [(PERSONAL, MESSAGE), (GROUP, MESSAGE)]
    assert (row.kommo, row.telegram, row.kommo_lead_id, row.noted_reply_id) == (
        HandoffKommo.DONE,
        HandoffTelegram.SENT,
        9341,
        dialog.reply.id,
    )
    assert (row.notified_link, row.last_error) == (DEAL, None)
    assert alerts == []


async def test_a_handoff_without_an_answer_makes_the_deal_without_a_note(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
) -> None:
    """Передачу позвали без ответа лида — примечания нет, как у прямого пути."""
    script, client = gateway
    script.replies = [_made(9341)]
    dialog = await sales_dialog(session)
    await session.delete(dialog.reply)
    await session.flush()

    row = await hand_off(session, dialog, work(http, alerts, client))

    assert "note" not in script.body(0)
    assert (row.kommo, row.kommo_lead_id, row.noted_reply_id) == (HandoffKommo.DONE, 9341, None)
    assert sent(api)[0] == (PERSONAL, MESSAGE)


# --- следующий ответ «хочет говорить»: без второй сделки, сообщение и слова -----------------------


async def test_the_next_answer_makes_no_second_deal_and_tells_the_telemarketer(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
    caplog: pytest.LogCaptureFixture,
) -> None:
    script, client = gateway
    script.replies = [_made(9341)]
    dialog = await sales_dialog(session)
    deps = work(http, alerts, client)
    await hand_off(session, dialog, deps)
    later = await answer(
        session,
        dialog.thread,
        dialog.message,
        "И ещё: нам интересен аудит ссылок.",
        at=NOW + timedelta(hours=3),
    )
    queued: list[int] = []

    with caplog.at_level(logging.WARNING, logger=handoff_kommo.__name__):
        row = await hand_off(session, dialog, deps, queued)

    assert queued == [row.id]
    assert len(script.requests) == 1, "вторая сделка или запрос примечания мимо шлюза"
    assert sent(api) == [(PERSONAL, MESSAGE), (GROUP, MESSAGE)] * 2, "ссылка на ту же сделку"
    assert (row.kommo, row.telegram, row.kommo_lead_id) == (
        HandoffKommo.DONE,
        HandoffTelegram.SENT,
        9341,
    )
    assert (row.noted_reply_id, row.notified_link) == (later.id, DEAL)
    assert row.last_error == handoff_kommo.NOT_NOTED
    assert row.last_error.startswith("примечание в Kommo не поддержано шлюзом")
    [record] = [r for r in caplog.records if "не поддержано шлюзом" in r.getMessage()]
    assert (record.handoff_id, record.kommo_lead_id, record.reply_id) == (  # type: ignore[attr-defined]
        row.id,
        9341,
        later.id,
    )
    assert alerts == [], "известное ограничение шлюза — не тревога владельцу"

    # Тот же ответ ещё раз — ни задачи, ни запроса, ни сообщения.
    again: list[int] = []
    await handoff.start(session, dialog.thread.id, enqueue=again.append, now=lambda: NOW)
    await handoff.process(session, row.id, deps)
    assert (again, len(script.requests), len(api.seen)) == ([], 1, 4)


# --- исходы шлюза в передаче --------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        httpx.ReadTimeout("timed out"),
        httpx.Response(200, json={"success": True}),
    ],
)
async def test_a_lost_answer_is_unconfirmed_and_never_makes_a_second_deal(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
    reply: httpx.Response | Exception,
) -> None:
    """Поиска у шлюза нет — потерянный ответ проверить нечем: решает человек консолью."""
    script, client = gateway
    script.replies = [reply]
    dialog = await sales_dialog(session)
    deps = work(http, alerts, client)

    row = await hand_off(session, dialog, deps)

    assert (row.kommo, row.kommo_lead_id, row.due_at) == (HandoffKommo.UNCONFIRMED, None, None)
    assert row.last_error is not None
    assert "поиском не проверить — контакты этот клиент не ищет" in row.last_error
    [alert] = alerts
    assert "проверить в Kommo руками, есть ли сделка: автоповтора нет" in alert
    line = dialog_line(dialog, " — сделка в Kommo не подтверждена, проверяем")
    assert sent(api) == [(PERSONAL, line), (GROUP, line)]

    # Ни задача, ни новый ответ, ни проход запись не открывают: вторая сделка не заводится.
    await handoff.process(session, row.id, deps)
    await answer(session, dialog.thread, dialog.message, "Ждём звонка.", at=NOW)
    queued: list[int] = []
    await handoff.start(session, dialog.thread.id, enqueue=queued.append, now=lambda: NOW)
    assert queued == []
    assert await handoff.due(session, now=NOW + timedelta(hours=2)) == []
    assert len(script.requests) == 1


async def test_a_busy_gateway_is_retried_by_the_pass_and_the_deal_is_made_once(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
) -> None:
    script, client = gateway
    script.replies = [httpx.Response(503, headers={"Retry-After": "41"}), _made(9341)]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, work(http, alerts, client))

    assert (row.kommo, row.kommo_lead_id, row.attempts) == (HandoffKommo.RETRY, None, 1)
    assert row.due_at == NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
    line = dialog_line(dialog, " — сделка в Kommo не создана, повторяем")
    assert sent(api) == [(PERSONAL, line), (GROUP, line)]
    [alert] = alerts
    assert "шлюз Kommo не принял запрос (HTTP 503, просит подождать 41 с)" in alert

    later = NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC + 17)
    assert await handoff.due(session, now=later) == [row.id]
    await handoff.process(session, row.id, work(http, alerts, client, at=later))
    row = await reread(session, row.id)

    assert (row.kommo, row.kommo_lead_id, row.noted_reply_id) == (
        HandoffKommo.DONE,
        9341,
        dialog.reply.id,
    )
    assert [request.method for request in script.requests] == ["POST", "POST"]
    assert "Давайте созвонимся во вторник после обеда." in script.body(1)["note"]
    assert sent(api)[2:] == [(PERSONAL, MESSAGE), (GROUP, MESSAGE)]
    assert "сделка в Kommo заведена после повтора" in alerts[1]


async def test_a_refused_key_fails_with_an_alert_and_the_dialog_link(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
) -> None:
    script, client = gateway
    script.replies = [_failed(401, "unauthorized")]
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, work(http, alerts, client))

    assert (row.kommo, row.due_at) == (HandoffKommo.FAILED, None)
    line = dialog_line(dialog, " — сделка в Kommo не создана, разбираемся")
    assert sent(api)[0] == (PERSONAL, line)
    [alert] = alerts
    assert "ключ шлюза Kommo отклонён (HTTP 401: unauthorized)" in alert
    assert KEY not in alert
    await answer(session, dialog.thread, dialog.message, "Алло?", at=NOW)
    queued: list[int] = []
    await handoff.start(session, dialog.thread.id, enqueue=queued.append, now=lambda: NOW)
    assert queued == [row.id], "новый ответ — новая попытка: ключ могли починить"


async def test_switched_off_sales_hold_the_gateway_path_too(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    gateway: tuple[Script, KommoGateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Выключатель продаж (`SALES_ENABLED`) держит и путь через шлюз: задача в шлюз не идёт."""
    script, client = gateway
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None, now=lambda: NOW)
    monkeypatch.setattr(cfg, "ENABLED", False)

    outcome = await handoff.process(session, row.id, work(http, alerts, client))

    assert outcome == {"handoff": row.id, "skipped": handoff.SWITCHED_OFF}
    assert (script.requests, api.seen, alerts) == ([], [], [])


# --- задача очереди: фабрика по настройкам собирает шлюз ----------------------------------------


async def test_the_queue_job_hands_off_through_the_gateway_by_the_settings(
    session: AsyncSession, wired: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`live` и адрес шлюза в настройках — задача собирает клиент шлюза и идёт через него."""
    script = Script(_made(9341))

    def route(request: httpx.Request) -> httpx.Response:
        return script(request) if request.url.host == "gateway.example.test" else wired(request)

    monkeypatch.setattr(
        handoff_jobs, "_http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(route))
    )
    monkeypatch.setattr(cfg, "KOMMO_PROVIDER", "live")
    for name, value in GATEWAY_FILLED.items():
        monkeypatch.setattr(cfg, name, value)
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)

    outcome = await handoff_jobs.run_hand_off(row.id)

    assert outcome == {
        "handoff": row.id,
        "kommo": "done",
        "telegram": "sent",
        "kommo_lead_id": 9341,
    }
    (request,) = script.requests
    assert (str(request.url), request.headers["Authorization"]) == (URL, f"Bearer {KEY}")
    assert "Давайте созвонимся во вторник после обеда." in script.body(0)["note"]
    [message] = wired.seen
    assert json.loads(message.content)["text"] == MESSAGE


# --- консоль передач ----------------------------------------------------------------------------


def test_the_console_hides_the_gateway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "KOMMO_GATEWAY_KEY", KEY)

    assert (
        handoff_console.clean(f"шлюз ответил: Bearer {KEY}") == "шлюз ответил: Bearer <ключ Kommo>"
    )
