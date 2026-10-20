"""Схемы строк ожидания в отчёте прогона и «Повторить сейчас» (`features/sales/segment_waits.py`).

Своим модулем: схемы экрана прогона (`segment_schemas.py`) стоят у предела длины файла.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.features.sales.segment_waits import Wait


class WaitRow(BaseModel):
    """Что в прогоне ждёт повтора: адрес сайта (`address`) или очистка лида (`cleaning`), почему
    словами, сколько, когда повтор пойдёт сам (UTC; пусто — срока нет) и можно ли «Повторить
    сейчас»."""

    kind: str
    words: str
    count: int
    at: datetime | None
    again: bool

    @classmethod
    def of(cls, wait: Wait) -> WaitRow:
        return cls(kind=wait.kind, words=wait.words, count=wait.count, at=wait.at, again=wait.again)


class AgainBody(BaseModel):
    """«Повторить сейчас»: поиск адреса сайтам, которые ждут повтора, или очистку ждущих лидов."""

    model_config = ConfigDict(extra="forbid")

    what: Literal["addresses", "cleaning"]
