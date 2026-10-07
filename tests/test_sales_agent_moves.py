"""Ход агента продаж — таблица данными, срез 3.2a.

Проверяется то, чего не видно по зелёному прогону: таблица покрывает ровно
ситуации, где ответ нужен (молчание и сбой разбора решает код, а не строка
таблицы); ходы Spec 3.2 стоят как обещано; файл с ошибкой отказывает словами
при чтении, а не даёт ход по умолчанию в живой переписке.
"""

from __future__ import annotations

import copy
import re
import tomllib
from typing import Any

import pytest
from backend.features.sales.agent import moves
from backend.features.sales.agent.moves import ANSWERED, Cta, MovesTableError
from backend.features.sales.agent.situation import SILENT, Label
from backend.features.sales.models import KbKind


def _raw() -> dict[str, Any]:
    return tomllib.loads(moves.TABLE.read_text(encoding="utf-8"))


def test_table_covers_exactly_the_situations_that_need_an_answer() -> None:
    table = moves.table()

    assert set(table.moves) == ANSWERED
    assert set(Label) - SILENT - {Label.PARSE_FAILED} == ANSWERED
    assert table.version.startswith("sales-moves-")
    assert table.always == (KbKind.BRIEF, KbKind.FORBIDDEN)


def test_spec_moves_stand_as_promised() -> None:  # Spec 3.2, шаг 2
    table = moves.table()
    price, objection = table.move(Label.ASKS_PRICE), table.move(Label.OBJECTION)
    talk, later = table.move(Label.WANTS_TO_TALK), table.move(Label.NOT_NOW)

    assert KbKind.PRICE_POLICY in price.kinds
    assert price.cta[0] is Cta.CALL  # цен называть нельзя — созвон
    assert KbKind.OBJECTION in objection.kinds
    assert (talk.cta[0], talk.lead) == (Cta.TELEGRAM, True)
    assert (later.cta, later.lead) == ((), False)  # закрыть без давления
    assert all(move.does for move in table.moves.values())


def test_silent_situation_has_no_move() -> None:
    with pytest.raises(LookupError, match="«ack» хода нет"):
        moves.table().move(Label.ACK)


def _broken(path: tuple[str, ...], value: object) -> dict[str, Any]:
    raw = copy.deepcopy(_raw())
    *parents, last = path
    node = raw
    for name in parents:
        node = node[name]
    if value is None:
        del node[last]
    else:
        node[last] = value
    return raw


@pytest.mark.parametrize(
    ("path", "value", "words"),
    [
        (("version",), None, "нет версии таблицы"),
        (("always",), ["brief", "price_list"], "вида «price_list» в базе нет"),
        (("situations", "asks_price", "kinds"), "price_policy", "ждём список строк"),
        (("situations", "asks_price", "cta"), ["whatsapp"], "призыва «whatsapp» нет"),
        (("situations", "asks_info", "does"), "  ", "нет хода (move) или что он делает (does)"),
        (("situations", "asks_info", "move"), "Inform-Now", "имя хода «Inform-Now»"),
        (("situations", "objection"), None, "нет хода для ситуаций: objection"),
        (("situations", "ack"), {"move": "thanks", "does": "Ответить"}, "решает код"),
        (("situations", "terms"), {"move": "x", "does": "y"}, "ситуации «terms» нет среди меток"),
    ],
)
def test_broken_table_is_refused_in_words(path: tuple[str, ...], value: object, words: str) -> None:
    with pytest.raises(MovesTableError, match=re.escape(words)):
        moves.build(_broken(path, value))
