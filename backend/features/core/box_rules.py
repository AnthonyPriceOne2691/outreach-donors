"""Пороги, которыми этап судит ящик. Своим модулем: их значение держит мост почты
(`core/stages.CURRENT`), а судит ящик `outreach/health.py`, и тот стоит на мосту."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SoftSignals:
    """Мягкие сигналы и окно последних писем ящика: их задаёт политика этапа."""

    events: int = 3  # мягких отказов за сутки — и лимит ящика снижен
    cut: float = 0.5  # во сколько раз
    window: int = 50  # последних писем ящика в окне
    bounces: float = 0.05
    complaints: float = 0.001  # `SALES_COMPLAINT_PAUSE`
