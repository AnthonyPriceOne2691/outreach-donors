"""Что отдают маршруты доменов рассылки."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.features.core.domain import SenderStatus
from backend.features.core.models.outreach import SenderModel
from backend.features.outreach.senders import Warmup, warmup_state


class SenderCard(BaseModel):
    """Ящик на домене рассылки.

    Отдаётся и дневной расход, и потолок разгона, и общий кап: без всех
    трёх чисел «отправлено 5 из 20» врёт — на третьем дне разгона
    потолок не двадцать, а пятнадцать.
    """

    id: int
    domain: str
    email: str
    enabled: bool
    status: SenderStatus
    sent_today: int
    daily_cap: int
    warmup_day: int
    warmup_allowance: int
    warmup_finished: bool
    paused_at: datetime | None = None
    pause_reason: str | None = None

    @classmethod
    def of(cls, sender: SenderModel, warmup: Warmup | None = None) -> SenderCard:
        state = warmup or warmup_state(sender)
        return cls(
            id=sender.id,
            domain=sender.domain,
            email=sender.email,
            enabled=sender.enabled,
            status=sender.status,
            sent_today=sender.sent_today,
            daily_cap=sender.daily_cap,
            warmup_day=state.day,
            warmup_allowance=state.allowance,
            warmup_finished=state.finished,
            paused_at=sender.paused_at,
            pause_reason=sender.pause_reason,
        )


class DisableRequest(BaseModel):
    """Причина выключения. Не формальность: домен выключают руками редко,
    и через неделю никто не вспомнит, что именно с ним было."""

    reason: str = "выключено вручную"


class SendersView(BaseModel):
    """Список ящиков и ответ на единственный вопрос, который задаёт экран
    перед выключением: останется ли чем отправлять."""

    senders: list[SenderCard]
    enabled_domains: int
