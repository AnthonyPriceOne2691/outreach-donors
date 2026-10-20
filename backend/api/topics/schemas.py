"""Тема сайта в ответах экранов: код и подпись, счётчики пунктов фильтра.

Своим модулем, а не в схемах доноров: тему отдают и «Доноры», и «Рассмотрение прогона»,
а подпись темы — одна на оба экрана и берётся из общего списка (`topics/taxonomy.py`):
своей копии подписей у экрана нет.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from backend.features.topics.taxonomy import NOT_DEFINED, label_of

#: Подпись пункта «тема не определена» — модель не спрашивали или она темы не назвала.
NOT_DEFINED_LABEL = "не определена"


class TopicMark(BaseModel):
    """Тема строки: код — для фильтра и адреса, подпись — для глаза."""

    code: str
    label: str

    @classmethod
    def of(cls, code: str | None) -> TopicMark | None:
        return None if not code else cls(code=code, label=label_of(code))


class TopicFields(BaseModel):
    """Тема строки экрана — основа строк «Доноров» и «Рассмотрения» (`topic_fields`)."""

    #: Тема сайта и вторая, подписью из общего списка; пусто — не определена.
    topic: TopicMark | None = None
    topic_secondary: TopicMark | None = None
    #: Почему такая тема — словами модели, одной фразой.
    topic_reason: str | None = None


def topic_fields(topic: str | None, secondary: str | None, reason: str | None) -> dict[str, Any]:
    """Поля темы строки из колонок домена (`domains.site_topic*`)."""
    return {
        "topic": TopicMark.of(topic),
        "topic_secondary": TopicMark.of(secondary),
        "topic_reason": reason,
    }


class TopicCount(BaseModel):
    """Пункт фильтра «Тематика»: тема и у скольких строк экрана она основная или вторая.
    `none` — сколько без темы."""

    code: str
    label: str
    count: int


def topic_counts(counts: Mapping[str, int]) -> list[TopicCount]:
    """Пункты фильтра: частые сверху, при равенстве — по подписи; «не определена» — последней."""
    named = [
        TopicCount(code=code, label=label_of(code), count=count)
        for code, count in counts.items()
        if code != NOT_DEFINED
    ]
    named.sort(key=lambda one: (-one.count, one.label))
    missing = counts.get(NOT_DEFINED, 0)
    if missing:
        named.append(TopicCount(code=NOT_DEFINED, label=NOT_DEFINED_LABEL, count=missing))
    return named


def with_chosen(items: list[TopicCount], code: str | None) -> list[TopicCount]:
    """Выбранная в фильтре тема — пунктом и тогда, когда у строк экрана её нет (счётчик ноль):
    иначе поле показало бы код, а пустой экран назвал бы тему кодом, а не подписью."""
    if code is None or any(item.code == code for item in items):
        return items
    label = NOT_DEFINED_LABEL if code == NOT_DEFINED else label_of(code)
    return [*items, TopicCount(code=code, label=label, count=0)]
