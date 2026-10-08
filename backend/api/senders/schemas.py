"""Что отдают маршруты доменов рассылки."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.features.core.domain import SenderStatus, Stage
from backend.features.core.models.outreach import SenderModel
from backend.features.outreach.senders import Warmup, warmup_state


class SenderCard(BaseModel):
    """Ящик на домене рассылки.

    Отдаётся и дневной расход, и потолок разгона, и общий кап: без всех
    трёх чисел «отправлено 5 из 20» врёт — на третьем дне разгона
    потолок не двадцать, а пятнадцать.

    Дневной расход приходит извне, а не из строки отправителя: он
    считается по письмам — первым, тем же счётом, что кап и разгон
    (`OutreachRepository.sent_today(first_only=True)`). Добивки в него
    не входят: у них свой часовой потолок, и «25 из 20» не бывает.
    """

    id: int
    domain: str
    email: str
    #: Направление ящика: у этапов домены отправки свои.
    stage: Stage
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
    def of(
        cls, sender: SenderModel, *, sent_today: int = 0, warmup: Warmup | None = None
    ) -> SenderCard:
        state = warmup or warmup_state(sender)
        return cls(
            id=sender.id,
            domain=sender.domain,
            email=sender.email,
            stage=sender.stage,
            enabled=sender.enabled,
            status=sender.status,
            sent_today=sent_today,
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


class DomainLimit(BaseModel):
    """Домен рассылки строкой `sending_domains`. `sent_today` — первые письма со всех
    ящиков домена: тот же счёт, что у лимита и разгона (`outreach/limits.py`)."""

    model_config = ConfigDict(from_attributes=True)

    domain: str
    stage: Stage
    daily_limit: int
    sent_today: int = 0
    young_until: datetime | None = None
    paused_at: datetime | None = None
    pause_reason: str | None = None


class DirectionLimit(BaseModel):
    """Направление целиком: дневной лимит (пусто — своего нет) и первые письма за сутки."""

    stage: Stage
    daily_limit: int | None
    sent_today: int


class SendersView(BaseModel):
    """Список ящиков и ответ на единственный вопрос, который задаёт экран
    перед выключением: останется ли чем отправлять. Домены и направления —
    лимиты сверх ящика (`outreach/limits.py`)."""

    senders: list[SenderCard]
    enabled_domains: int
    domains: list[DomainLimit] = []
    directions: list[DirectionLimit] = []
