"""Тревога сторожа — одна на все правила: общего сторожа тишины (`silence.py`) и сторожа
почты этапов (`mail_watch.py`). Своим модулем: правила почты не тянут за собой общие."""

from __future__ import annotations

from dataclasses import dataclass

from backend.features.core.domain import Stage


@dataclass(frozen=True, slots=True)
class Alarm:
    """Одна тревога: что молчит, с каких пор и что это значит."""

    code: str
    title: str
    detail: str
    #: Этап, о почте которого тревога (сторож почты этапов): тревога о почте продаж — только
    #: с правом «Продажи» (решение Anthony 10.10.2026, П2б). Общая тревога — без этапа.
    stage: Stage | None = None
