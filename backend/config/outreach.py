"""Рассылка: отправка, добивки, лимиты на отправителя."""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Outreach(DomainSettings):
    sendgrid_api_key: str = Field(default="", validation_alias="OUTREACH_SENDGRID_API_KEY")
    inbound_secret: str = Field(default="", validation_alias="OUTREACH_INBOUND_SECRET")
    # Писем на отправителя в день. Держим низким: схема на 20 доменах
    # работает именно за счёт малого объёма на ящик (см. TZ.md).
    daily_cap_per_sender: int = Field(default=20, validation_alias="OUTREACH_DAILY_CAP_PER_SENDER")
    # Разгон нового отправителя: ступени дневного капа по дням.
    warmup_daily_caps: str = Field(default="5,10,15,20", validation_alias="OUTREACH_WARMUP_CAPS")
    warmup_step_days: int = Field(default=2, validation_alias="OUTREACH_WARMUP_STEP_DAYS")
    # Добивки: дни от первого письма. Стоп при любом ответе или отписке.
    followup_days: str = Field(default="7,14", validation_alias="OUTREACH_FOLLOWUP_DAYS")
    # Доля отказов, после которой отправитель уходит на паузу, и минимум
    # отправок, до которого доля не считается (иначе 2 отказа из 3 роняют домен).
    bounce_pause_threshold: float = Field(
        default=0.05, validation_alias="OUTREACH_BOUNCE_PAUSE_THRESHOLD"
    )
    bounce_pause_min_sent: int = Field(
        default=50, validation_alias="OUTREACH_BOUNCE_PAUSE_MIN_SENT"
    )
    # Уникализация письма: целевая доля изменённых слов.
    uniqueness_target_min: float = Field(default=0.15, validation_alias="OUTREACH_UNIQ_MIN")
    uniqueness_target_max: float = Field(default=0.25, validation_alias="OUTREACH_UNIQ_MAX")
    # Уверенность разбора ответа ниже порога — в ручную очередь, не в базу.
    price_confidence_threshold: float = Field(
        default=0.80, validation_alias="OUTREACH_PRICE_CONFIDENCE"
    )


_s = _Outreach()

SENDGRID_API_KEY: str = _s.sendgrid_api_key
INBOUND_SECRET: str = _s.inbound_secret
DAILY_CAP_PER_SENDER: int = _s.daily_cap_per_sender
WARMUP_DAILY_CAPS: tuple[int, ...] = tuple(int(x) for x in _s.warmup_daily_caps.split(","))
WARMUP_STEP_DAYS: int = _s.warmup_step_days
FOLLOWUP_DAYS: tuple[int, ...] = tuple(int(x) for x in _s.followup_days.split(","))
BOUNCE_PAUSE_THRESHOLD: float = _s.bounce_pause_threshold
BOUNCE_PAUSE_MIN_SENT: int = _s.bounce_pause_min_sent
UNIQUENESS_TARGET_MIN: float = _s.uniqueness_target_min
UNIQUENESS_TARGET_MAX: float = _s.uniqueness_target_max
PRICE_CONFIDENCE_THRESHOLD: float = _s.price_confidence_threshold
