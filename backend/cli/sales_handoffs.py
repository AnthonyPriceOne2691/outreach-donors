"""Команды передач продаж, где Kommo решает человек: список, «повторить», «закрыть руками».

    outreach sales-handoffs                               # unconfirmed и failed
    outreach sales-handoff-retry 17                       # сделки в Kommo нет — записать заново
    outreach sales-handoff-close 17 --note "…" [--deal 9301]

Логика — `features/sales/handoff_console.py`; здесь — доводы и печать. Отказ — словами
и код 2; решение пишется одной транзакцией вместе со строкой журнала действий. Ключ
Kommo и токен бота продаж не печатаются: тексты проходят `handoff_console.clean`.
"""

from __future__ import annotations

import argparse
import getpass
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.cli.prune import in_session
from backend.config import sales as cfg
from backend.features.sales import handoff_console as console
from backend.features.sales.handoff import enqueue_handoff
from backend.features.sales.handoff_console import ConsoleRefusalError, Waiting

EXIT_OK = 0
#: Решение не принято: передачи нет, она не ждёт человека или её держит задача.
EXIT_REFUSED = 2


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Разбор аргументов трёх команд."""
    sub.add_parser(
        "sales-handoffs",
        help="передачи лидов продаж, где Kommo решает человек: не подтверждена или отказ",
    )
    retry = sub.add_parser(
        "sales-handoff-retry",
        help="повторить запись передачи в Kommo — человек проверил: сделки нет",
    )
    retry.add_argument("handoff", type=int, help="номер передачи из `outreach sales-handoffs`")
    close = sub.add_parser(
        "sales-handoff-close",
        help="закрыть передачу руками: сделка в Kommo есть или ничего не нужно",
    )
    close.add_argument("handoff", type=int, help="номер передачи из `outreach sales-handoffs`")
    close.add_argument(
        "--note", required=True, help="записка: что проверено в Kommo и почему закрыто"
    )
    close.add_argument(
        "--deal",
        type=int,
        default=None,
        help="номер сделки в Kommo, если она есть: следующий ответ лида ляжет к ней",
    )


def _author() -> str:
    return f"консоль: {getpass.getuser()}"


def _line(item: Waiting) -> list[str]:
    company = f" ({item.company})" if item.company else ""
    deal = f"сделка №{item.deal}" if item.deal else "сделки нет"
    return [
        f"  №{item.id} · лид №{item.lead_id} {item.email}{company} · диалог №{item.thread_id}",
        f"      {console.KOMMO_WORDS[item.kommo]} · {deal} · попыток записи: {item.attempts} · "
        f"Telegram: {console.TELEGRAM_WORDS[item.telegram]}",
        f"      причина: {item.reason or 'не записана'}",
    ]


async def run_list(session: AsyncSession) -> int:
    """Список передач в `unconfirmed` и `failed`. Только читает."""
    found = await console.waiting(session)
    if not found:
        print("Передач, где Kommo решает человек, нет.")
        return EXIT_OK
    print(f"Передачи, где Kommo решает человек: {len(found)}")
    for item in found:
        print("\n".join(_line(item)))
    print(
        "Сделки в Kommo нет — повторить запись: outreach sales-handoff-retry <номер>\n"
        "Сделка есть или ничего не нужно — закрыть: "
        'outreach sales-handoff-close <номер> --note "…" [--deal <номер сделки>]'
    )
    return EXIT_OK


async def run_retry(
    session: AsyncSession,
    handoff_id: int,
    *,
    enqueue: Callable[[int], object] = enqueue_handoff,
    now: datetime | None = None,
) -> int:
    """«Повторить» на готовой сессии; очередь и время подменяет тест."""
    moment = now or datetime.now(UTC)
    try:
        queued = await console.retry(
            session, handoff_id, author=_author(), now=moment, enqueue=enqueue
        )
    except ConsoleRefusalError as exc:
        print(f"Не повторено: {console.clean(str(exc))}")
        return EXIT_REFUSED
    then = (
        "задача поставлена в очередь продаж"
        if queued
        else "очередь не ответила — задачу возьмёт проход по расписанию "
        f"(через {cfg.HANDOFF_RETRY_SEC // 60} мин)"
    )
    print(
        f"Передача №{handoff_id}: запись в Kommo снова ждёт задачи — {then}. "
        "Заведённая сделка уйдёт телемаркетологу ссылкой. Запись в журнале действий."
    )
    return EXIT_OK


async def run_close(
    session: AsyncSession,
    handoff_id: int,
    note: str,
    deal: int | None,
    *,
    now: datetime | None = None,
) -> int:
    """«Закрыть руками» на готовой сессии."""
    try:
        row = await console.close(
            session,
            handoff_id,
            note=note,
            deal=deal,
            author=_author(),
            now=now or datetime.now(UTC),
        )
    except ConsoleRefusalError as exc:
        print(f"Не закрыто: {console.clean(str(exc))}")
        return EXIT_REFUSED
    then = (
        f"сделка №{row.kommo_lead_id} записана — следующий ответ лида ляжет к ней примечанием"
        if row.kommo_lead_id
        else "номера сделки нет — следующий ответ лида заведёт сделку в Kommo заново; если "
        "сделка есть, закройте с --deal <номер>"
    )
    print(f"Передача №{handoff_id} закрыта руками: {then}. Запись в журнале действий.")
    return EXIT_OK


async def cmd_sales_handoffs(_args: argparse.Namespace | None) -> int:
    """Напечатать передачи, где Kommo решает человек."""
    return await in_session(run_list)


async def cmd_sales_handoff_retry(args: argparse.Namespace) -> int:
    """Повторить запись передачи в Kommo."""
    return await in_session(lambda session: run_retry(session, args.handoff))


async def cmd_sales_handoff_close(args: argparse.Namespace) -> int:
    """Закрыть передачу руками с запиской."""
    return await in_session(lambda session: run_close(session, args.handoff, args.note, args.deal))
