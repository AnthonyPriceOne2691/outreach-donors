"""Поиск по набранному человеком: подстрока, а не шаблон.

Образец `LIKE` / `ILIKE` читает «%» как «что угодно», «_» как «любой знак», а «\\» —
как экранирование. Набранное в поле поиска — текст, а не шаблон: на «Донорах» и
«Отборе» поиск «_» или «%» находил всех (проверка прода 10.10.2026). Поэтому все
поиски по вводу человека строят условие здесь: знаки шаблона в набранном экранируются,
и найдётся ровно то, что набрано, в любом месте колонки, без учёта регистра.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute

#: Знак экранирования в образце — назван в запросе явно (`ESCAPE`), а не умолчанием
#: базы: умолчание у баз разное, а правило должно быть одно.
ESCAPE = "\\"

#: Знаки шаблона `LIKE`. Сам знак экранирования — первым: иначе он удвоил бы
#: экранирование, поставленное перед «%» и «_».
_PATTERN_SIGNS = (ESCAPE, "%", "_")


def literal(typed: str) -> str:
    """Набранное как часть образца: каждый знак шаблона значит сам себя."""
    for sign in _PATTERN_SIGNS:
        typed = typed.replace(sign, ESCAPE + sign)
    return typed


def contains(
    column: InstrumentedAttribute[str] | InstrumentedAttribute[str | None] | ColumnElement[str],
    typed: str,
) -> ColumnElement[bool]:
    """Колонка содержит набранное — без пробелов по краям и без учёта регистра."""
    return column.ilike(f"%{literal(typed.strip())}%", escape=ESCAPE)
