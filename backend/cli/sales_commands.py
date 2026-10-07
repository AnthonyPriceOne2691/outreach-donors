"""Команды консоли продаж одним перечнем для `cli/main.py`.

Каждая новая команда продаж дописывала строки в общий `main.py` — в импорты, разбор
аргументов, таблицу команд и подписи прерывания — и упирала его в потолок длины,
а соседние срезы спотыкались о те же строки. Здесь — всё, что `main.py` знает о
продажах: он берёт перечни целиком и сам ни одной команды продаж не называет.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Coroutine
from typing import Any

from backend.cli import sales

#: Команда → что выполнить; вливается в таблицу команд `main.py`.
COMMANDS: dict[str, Callable[[argparse.Namespace], Coroutine[Any, Any, int]]] = {
    "sales-hypothesis-add": sales.cmd_sales_hypothesis_add,
    "sales-import": sales.cmd_sales_import,
    "sales-stoplist-add": sales.cmd_sales_stoplist_add,
    "sales-clean": sales.cmd_sales_clean,
    "sales-kb-load": sales.cmd_sales_kb_load,
    "sales-chain-load": sales.cmd_sales_chain_load,
}

#: Что осталось в базе после прерывания — словами, на команду.
KEPT_ON_INTERRUPT: dict[str, str] = {
    "sales-import": "Загрузка одной транзакцией: в базе ничего не осталось.",
    "sales-stoplist-add": "Стоп-лист пишется одной транзакцией: в базе ничего не осталось.",
    "sales-clean": "Записанные партии остались в базе; повторный проход продолжит с лидов `new`.",
    "sales-kb-load": "База знаний пишется одной транзакцией: в базе ничего не осталось.",
    "sales-chain-load": "Цепочка писем пишется одной транзакцией: в базе ничего не осталось.",
}


def add_parsers(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Разбор аргументов всех команд продаж."""
    sales.add_parser(sub)
