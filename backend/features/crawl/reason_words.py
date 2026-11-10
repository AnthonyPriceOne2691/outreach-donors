"""Причины скоринга на экране: в строке таблицы — коротко, пояснение — в «i» у причины.

Причина хранится строкой целиком (`scoring.py`): её читают в файле, в консоли и на экране. В ячейке
таблицы длинное пояснение растит строку: «зона .org — чаще фонд или сообщество, чем рекламодатель:
решает человек» у каждого домена .org вставала в три-четыре строки ячейки (разведка экранов
10.11.2026). Экрану такая причина уходит короткой — «зона .org», — а пояснение рядом, по её словам
(`ShownReasons.notes`). Файл и консоль берут причину как записана.

Узнаётся причина по своему тексту отсюда же (`nonprofit_reason`): строки кандидатов, записанные до
этого модуля, — тот же текст, и короткими они уходят так же.

Число держится за свой знак неразрывным пробелом (`_glued`): «кому не пишем: DR 82 > 80» в узкой ячейке
рвалась на «DR 82 >» и «80» отдельной строкой (QA прода 10.11.2026, правило «≥ N» в `docs/UI_RULES.md`).
Склейка — на выдаче, а не при записи: причины, записанные раньше, склеиваются так же.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

#: Почему домену зоны некоммерческих «куплено» ставит только человек: платную статью о себе фонды и
#: сообщества заказывают редко.
NONPROFIT_WHY = "чаще фонд или сообщество, чем рекламодатель: решает человек"
#: Пояснение у короткой причины — фразой с заглавной: в «i» оно стоит одно.
NONPROFIT_NOTE = NONPROFIT_WHY[:1].upper() + NONPROFIT_WHY[1:]
_NONPROFIT = re.compile(rf"(зона \S+) — {re.escape(NONPROFIT_WHY)}")


def nonprofit_reason(zone: str) -> str:
    """Причина зоны некоммерческих — целиком, как её хранит кандидат: «зона .org — чаще фонд…»."""
    return f"зона {zone} — {NONPROFIT_WHY}"


#: Что склеивается с числом: «DR» — со своим числом, знак сравнения — с обеих сторон, очки причины — с
#: текстом перед ними («+2» не уходит строкой ниже).
_GLUE = (
    (re.compile(r"\bDR (?=\d)"), "DR\u00a0"),
    (re.compile(r" ([<>≤≥=]) (?=\d)"), "\u00a0\\1\u00a0"),
    (re.compile(r" (?=[+−]\d)"), "\u00a0"),
)


def _glued(text: str) -> str:
    """Причина с неразрывными пробелами у чисел: «DR 82 > 80» не рвётся переносом строки."""
    for pattern, replacement in _GLUE:
        text = pattern.sub(replacement, text)
    return text


@dataclass(frozen=True, slots=True)
class ShownReasons:
    """Причины для экрана: слова строк — по порядку, пояснения — по словам строки."""

    words: list[str] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)


def shown_reasons(reasons: Iterable[object] | None) -> ShownReasons:
    """Причины кандидата для экрана: длинное пояснение — в `notes` у короткой причины."""
    shown = ShownReasons()
    for reason in reasons or ():
        text = str(reason)
        zone = _NONPROFIT.fullmatch(text)
        if zone is None:
            shown.words.append(_glued(text))
            continue
        short = zone.group(1)
        shown.words.append(short)
        shown.notes[short] = NONPROFIT_NOTE
    return shown
