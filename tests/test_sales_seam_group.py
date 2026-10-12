"""Ревью стыков, круг 2: группа продаж в бою — недельный отчёт за сторожем бота, группа стала
супергруппой.

- **Сбой сторожа бота не держит недельный отчёт.** Проход продаж процесса разбора
  (`handoff_jobs.retry_pass`): повторы передач и сообщений, затем сторож бота «без токена»
  (`notify.watch_token`), последним — недельный отчёт. Шапка обещала, что сбой отчёта не держит
  ни повторов, ни сторожа, — а сбой сторожа (база на его запросе) ронял весь круг до отчёта:
  отчёт ждал, пока сторож не перестанет падать. Теперь сбой сторожа — строка в журнал, отчёт
  идёт своим кругом.
- **Группа стала супергруппой.** Telegram меняет номер чата группы, когда она становится
  супергруппой (настройка истории для новых участников, рост группы), и отвечает 400 с новым
  номером (`parameters.migrate_to_chat_id`). Отказ говорил «проверить chat id… номер печатает
  `outreach sales-telegram-chat-id`» — а новый номер Telegram уже назвал: теперь он в словах
  отказа и тревоги, вместе с настройкой, которую заменить.

Бот — поверх подставного Bot API; проход — в транзакции теста.
"""

from __future__ import annotations

import logging
from datetime import time

import httpx
import pytest
from backend.config import sales as cfg
from backend.features.sales import handoff_jobs, telegram, weekly_jobs
from backend.features.sales.telegram import SalesBot, TelegramError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_sales_draft_notify import APP, GROUP, TOKEN, BotApi, _Closable

#: Новый номер группы, ставшей супергруппой, — выдуманный.
SUPERGROUP = -1002233445566


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    """Продажи включены, отчёт — по понедельникам в полночь (при любых часах прошлая неделя ждёт
    отчёта), проход продаж — в транзакции теста."""
    monkeypatch.setattr(cfg, "ENABLED", True)
    monkeypatch.setattr(cfg, "WEEKLY_REPORT_AT", (0, time(0, 0)))
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_CHAT_ID", GROUP)
    monkeypatch.setattr(cfg, "APP_URL", APP)
    monkeypatch.setattr(handoff_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        handoff_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )


@pytest.mark.usefixtures("wired")
async def test_a_failing_bot_watch_does_not_hold_the_weekly_report(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Сторож бота упал на базе — круг доходит до недельного отчёта, сбой — строкой журнала."""

    async def broken(*_: object, **__: object) -> None:
        raise OperationalError("SELECT count(*)", {}, Exception("база не ответила"))

    monkeypatch.setattr(handoff_jobs, "watch_token", broken)
    queued: list[tuple[int, int]] = []
    monkeypatch.setattr(weekly_jobs, "queue_send", lambda *args: queued.append(args))

    with caplog.at_level(logging.ERROR, logger=handoff_jobs.__name__):
        await handoff_jobs.retry_pass()

    assert len(queued) == 1, "сбой сторожа бота задержал недельный отчёт"
    assert "сторож бота продаж не прошёл" in caplog.text


@pytest.mark.usefixtures("wired")
async def test_a_failing_weekly_pass_is_still_raised_for_the_loop_to_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Граница правки: сбой самого прохода отчёта — по-прежнему исключение круга: его пишет
    цикл процесса разбора (`every`), а не глотает проход."""

    async def broken(*_: object, **__: object) -> list[tuple[int, int]]:
        raise OperationalError("INSERT", {}, Exception("база не ответила"))

    monkeypatch.setattr(weekly_jobs, "due", broken)

    with pytest.raises(OperationalError):
        await handoff_jobs.retry_pass()


async def test_group_upgraded_to_a_supergroup_names_its_new_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 «group chat was upgraded to a supergroup chat» с новым номером — постоянный отказ, и
    в его словах новый номер и настройка, которую заменить; токена в словах нет."""
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)
    api = BotApi(
        httpx.Response(
            400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: group chat was upgraded to a supergroup chat",
                "parameters": {"migrate_to_chat_id": SUPERGROUP},
            },
        )
    )

    async with api.client() as http:
        with pytest.raises(TelegramError) as caught:
            await SalesBot(http).send(GROUP, "Продажи — недельный отчёт")

    words = str(caught.value)
    assert caught.value.permanent is True
    assert len(api.seen) == 1, "постоянный отказ не повторяется"
    assert f"новый номер чата — {SUPERGROUP}" in words
    assert "SALES_TELEGRAM_GROUP_CHAT_ID" in words
    assert TOKEN not in words


async def test_other_bad_requests_keep_their_advice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Граница правила: 400 без нового номера — прежний совет про chat id."""
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)
    api = BotApi(
        httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        )
    )

    async with api.client() as http:
        with pytest.raises(TelegramError) as caught:
            await SalesBot(http).send(GROUP, "Продажи — недельный отчёт")

    assert "новый номер" not in str(caught.value)
    assert "outreach sales-telegram-chat-id" in str(caught.value)


def test_new_chat_number_of_any_shape_but_a_number_is_not_trusted() -> None:
    """Номер — только целое: строка, дробь или логическое значение нового номера не дают."""
    for value in ("-100223", 1.5, True, None):
        answer = {"parameters": {"migrate_to_chat_id": value}}
        assert telegram._moved_to(answer) is None
    assert telegram._moved_to({"parameters": {"migrate_to_chat_id": SUPERGROUP}}) == SUPERGROUP
