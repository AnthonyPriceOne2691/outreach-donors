"""Насколько письмо отличается от шаблона.

Одно число, и оно должно быть честным. Цель из ТЗ — 15–25% изменённых
слов: ниже коридора письмо узнаётся фильтрами как рассылка, выше — теряет
смысл, потому что переписанным оказывается то, что переписывать не
просили.

**Отличие меряется против шаблона с теми же подстановками.** Это главное
решение файла. Если сравнивать с сырым шаблоном, то подстановка имени
сайта, отправителя и адреса отписки сама даёт несколько процентов
«отличия» в каждом письме — одинаковые письма выглядели бы
уникализированными, и число врало бы в нашу пользу ровно там, где по нему
принимают решение отправлять.

**Считается по словам, а не по символам.** Замена слова на синоним меняет
половину символов и одно слово; фильтры почтовых платформ смотрят на
текст, а не на длину строки.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from backend.config import outreach as cfg

#: Слово — последовательность букв или цифр. Знаки препинания не считаются:
#: переставленная запятая не делает письмо другим.
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> list[str]:
    """Слова текста в нижнем регистре, в порядке появления."""
    return _WORD_RE.findall(text.lower())


def difference(before: str, after: str) -> float:
    """Доля изменённых слов, 0–1.

    Сравнение позиционное (`SequenceMatcher`), а не по множествам:
    переставленные местами предложения — это другое письмо для читателя
    и то же самое для множества слов.
    """
    source, result = words(before), words(after)
    if not source and not result:
        return 0.0
    return 1.0 - SequenceMatcher(None, source, result).ratio()


def below_corridor(value: float) -> bool:
    return value < cfg.UNIQUENESS_TARGET_MIN


def above_corridor(value: float) -> bool:
    return value > cfg.UNIQUENESS_TARGET_MAX


def in_corridor(value: float) -> bool:
    """Попадает ли отличие в коридор из настроек."""
    return not below_corridor(value) and not above_corridor(value)


def corridor_verdict(value: float) -> str | None:
    """Что не так с числом. `None` — всё в порядке.

    Вердикт словами, а не флагом: он попадает и в лог, и на экран рядом
    с письмом, и «13% при коридоре 15–25%» объясняет себя само.
    """
    percent = round(value * 100)
    low = round(cfg.UNIQUENESS_TARGET_MIN * 100)
    high = round(cfg.UNIQUENESS_TARGET_MAX * 100)
    if below_corridor(value):
        return f"отличие {percent}% ниже коридора {low}–{high}%: письмо слишком похоже на шаблон"
    if above_corridor(value):
        return f"отличие {percent}% выше коридора {low}–{high}%: переписано больше, чем просили"
    return None
