"""Слова правила «компания — в одной гипотезе» как данные: шаблон и разбор — в одном месте.

Номер держателя компании живёт в словах очистки (`cleaning_note`), а не в колонке (решение
соседней сессии 15.10, вариант (б), без миграции): ждущего держателя проход очистки будит по
словам ожидания (`company_hold.after`), а отклонённого из-за держателя возвращает по номеру
держателя в словах отказа (`company_hold.freed`). Достать номер из слов — хрупко: правка
формулировки молча выключила бы и пробуждение, и возврат. Поэтому каждое такое слово — один
шаблон (`Words`): по нему слова строятся (`say`) и разбираются — в Python (`lead`) и в SQL
(`match`, `lead_of`), регулярка одна, из того же шаблона. Слова возврата и свободной компании
кончаются одинаково — «адрес проверит очистка гипотезы «…»»: очистка гипотезы платит только за
свои лиды, лида чужой гипотезы проверит его очистка. Петля «построили → разобрали → тот
же номер», в том числе в настоящей базе, — в тестах (`tests/test_sales_company_wake.py`).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import BigInteger, ColumnElement, cast, func, literal
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.types import Text

#: Поле шаблона с номером лида — его разбор достаёт.
LEAD = "{lead}"
#: Номер лида в регулярке: до 18 цифр — `bigint` в SQL не переполнится ни на каких словах.
NUMBER = "([0-9]{1,18})"

#: Колонка слов очистки — или выражение над ней.
Note = ColumnElement[str | None] | InstrumentedAttribute[str | None]


class Words:
    """Шаблон слов очистки с полями `{lead}` (номер лида — один раз), `{hypothesis}`, `{host}`.
    Регулярка — литералы шаблона как есть, номер — группой, прочие поля — любым текстом; она же
    годится Postgres (`~`, `substring`): экранированный не буквенный знак там — сам знак."""

    def __init__(self, template: str) -> None:
        self.template = template
        pieces = re.split(r"(\{\w+\})", template)
        self.pattern = "^" + "".join(_piece(piece) for piece in pieces) + "$"

    def say(self, **fields: Any) -> str:
        """Слова по шаблону."""
        return self.template.format(**fields)

    def lead(self, note: str | None) -> int | None:
        """Номер лида из этих слов; другие слова — `None`."""
        found = re.match(self.pattern, note or "", flags=re.DOTALL)
        return None if found is None else int(found.group(1))

    def match(self, note: Note) -> ColumnElement[bool]:
        """Это эти слова — условием SQL (`~`)."""
        return note.regexp_match(self.pattern)

    def lead_of(self, note: Note) -> ColumnElement[int]:
        """Номер лида из этих слов — выражением SQL; другие слова — NULL."""
        return cast(func.substring(note, literal(self.pattern, Text)), BigInteger)


def _piece(piece: str) -> str:
    if piece == LEAD:
        return NUMBER
    if piece.startswith("{") and piece.endswith("}"):
        return ".*"
    return re.escape(piece)


#: Отказ из-за держателя (`company_hold.Holder.words`): по номеру — возврат, когда держателя
#: отклонят.
HELD = Words(
    "компания уже в работе гипотезы «{hypothesis}»: лид №{lead} на домене {host} — "
    "одной компании пишет одна гипотеза"
)
#: Ожидание держателя (`company_hold.Holder.waits`): по этим словам проход будит ждущих.
WAITS = Words(
    "ждёт: компанию держит лид №{lead} гипотезы «{hypothesis}», его адрес ещё не проверен — "
    "решит очистка гипотезы «{hypothesis}»"
)
#: Вернувшийся: компания освободилась (`company_hold.freed`), номер — отклонённого держателя,
#: `{own}` — гипотеза самого лида: её очистка его и проверит.
BACK = Words(
    "вернулся: компания освободилась — лид №{lead} гипотезы «{hypothesis}» отклонён — "
    "адрес проверит очистка гипотезы «{own}»"
)
#: Разбуженный лид чужой гипотезы, чья компания свободна (`company_hold.split`): очистка
#: другой гипотезы его не проверяет — платит она только за свою область.
FREED = Words(
    "компания свободна: лид №{lead} гипотезы «{hypothesis}» отклонён — "
    "адрес проверит очистка гипотезы «{own}»"
)
