"""Политика почты для писем продаж — её спрашивает мост почты (`core/stages.SalesMail.policy`).

Здесь — окно отправки из настроек продаж (Ф4, 4.3), мягкие сигналы и сторож почты (4.5b;
порог жалоб — `SALES_COMPLAINT_PAUSE`) и пояса получателя по порядку: лида, его страны,
гипотезы (своего поля у гипотезы пока нет). Подключение — в ответах модуля мосту: `policy`
отдаёт `sales_policy()`, `recipient` кладёт `zones_of(lead)` в `Recipient.zones`.
"""

from __future__ import annotations

from datetime import timedelta

from backend.config import sales as cfg
from backend.features.core.stages import MailPolicy
from backend.features.core.window import SendWindow
from backend.features.outreach.health import SoftSignals
from backend.features.sales import geo
from backend.features.sales.models import SalesLeadModel


def sales_policy() -> MailPolicy:
    """Политика почты продаж: окно получателя, мягкие сигналы ящика, сторож почты."""
    return MailPolicy(
        window=SendWindow(
            days=cfg.SEND_DAYS,
            start=cfg.SEND_OPENS,
            end=cfg.SEND_CLOSES,
            spread=timedelta(minutes=cfg.SEND_SPREAD_MIN),
        ),
        soft=SoftSignals(complaints=cfg.COMPLAINT_PAUSE),
        watch=True,
    )


def zones_of(lead: SalesLeadModel, hypothesis_zone: str | None = None) -> tuple[str | None, ...]:
    """Пояса получателя по порядку: лида, его страны (единственный или столичный), гипотезы."""
    by_country = geo.timezone_for(lead.country) if lead.country else None
    return (lead.timezone, by_country, hypothesis_zone)
