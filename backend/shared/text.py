"""Фраза к единому виду: одна нормализация для ключей поиска и для цитат судей сайта.

До 03.11.2026 она жила в отсеве ключей (`keywords/hygiene`), и сверка цитаты брала её оттуда. Цитату
теперь сверяют судьи разных этапов (`shared/quote`), а `shared` — лист и не импортирует `features`:
нормализация переехала сюда без правок поведения, `keywords/hygiene.normalize` — то же имя.
"""

from __future__ import annotations

import unicodedata

#: Виды тире и апострофов, которые модель ставит вперемешку. Для поиска
#: это разные символы, а для человека один и тот же.
_DASHES = "‐‑‒–—―−"
_APOSTROPHES = "‘’‛`"
_TRANSLATION = {ord(c): "-" for c in _DASHES} | {ord(c): "'" for c in _APOSTROPHES}


def normalize(phrase: str) -> str:
    """Привести фразу к единому виду.

    Диакритика сохраняется: `mañana` и `piñol` — законные слова, а не
    мусор. Складывать их в латиницу значит ломать язык рынка.
    """
    text = unicodedata.normalize("NFKC", phrase or "").translate(_TRANSLATION)
    return " ".join(text.split()).strip().lower()
