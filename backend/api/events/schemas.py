"""Что приходит от платформы и что мы ей отвечаем."""

from __future__ import annotations

from pydantic import BaseModel


class Taken(BaseModel):
    """Ответ платформе. Она читает код, но человек читает это."""

    accepted: bool
    delivered: int = 0
    bounced: int = 0
    complained: int = 0
    #: События, к которым не нашлось письма. Не ошибка: платформа шлёт
    #: события и по письмам, удалённым из базы.
    unknown: int = 0
    #: Ящики, снятые с отправки по доле отказов.
    paused: list[str] = []
    reason: str | None = None
