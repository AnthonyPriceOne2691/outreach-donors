"""Команда бота продаж: `outreach sales-telegram-chat-id`.

Бот не может написать человеку первым. Телемаркетолог один раз жмёт Start
у бота продаж (бота добавляют и в группу продаж), а команда читает
`getUpdates` один раз и печатает номера чатов — их записывают в
`SALES_TELEGRAM_CHAT_ID` и `SALES_TELEGRAM_GROUP_CHAT_ID`. Ничего не пишет:
ни в базу, ни в Telegram. Логика чтения — `features/sales/telegram.py`.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from backend.config import sales as cfg
from backend.features.sales.telegram import SalesBot, TelegramError

EXIT_OK = 0
#: Обновлений нет: Start ещё никто не нажал — или их уже забрал другой читатель.
EXIT_NOBODY = 1
#: Токена бота нет — чинится настройкой, не повтором.
EXIT_NOT_CONFIGURED = 2
#: Telegram отказал или не ответил — слова отказа напечатаны.
EXIT_REFUSED = 8


def _client() -> httpx.AsyncClient:
    """Клиент на один запрос. Отдельной функцией — её подменяет тест: сети в тестах нет."""
    return httpx.AsyncClient()


async def cmd_sales_telegram_chat_id(_args: argparse.Namespace | None) -> int:
    """Напечатать номера чатов, где боту продаж нажали Start или куда его добавили."""
    if not cfg.TELEGRAM_BOT_TOKEN:
        print(
            "Не задан SALES_TELEGRAM_BOT_TOKEN — токен бота продаж от @BotFather, в .env",
            file=sys.stderr,
        )
        return EXIT_NOT_CONFIGURED
    async with _client() as http:
        try:
            chats = await SalesBot(http).chats()
        except TelegramError as exc:
            print(f"Telegram: {exc}", file=sys.stderr)
            return EXIT_REFUSED
    if not chats:
        print(
            "Обновлений нет: телемаркетологу открыть бота продаж и нажать Start, "
            "бота добавить в группу продаж — и повторить команду в течение суток"
        )
        return EXIT_NOBODY
    print("chat id — тип — имя или название:")
    for chat in chats:
        print(f"  {chat.id}\t{chat.kind}\t{chat.title}")
    print(
        "Личный чат телемаркетолога → SALES_TELEGRAM_CHAT_ID; "
        "группа продаж → SALES_TELEGRAM_GROUP_CHAT_ID"
    )
    return EXIT_OK


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    sub.add_parser(
        "sales-telegram-chat-id",
        help="напечатать chat id тех, кто нажал Start у бота продаж (getUpdates один раз)",
    )
