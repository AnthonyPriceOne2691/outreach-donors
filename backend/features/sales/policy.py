"""Политика почты для писем продаж — её спрашивает мост почты (`core/stages.SalesMail.policy`).

Здесь — окно отправки из настроек продаж (Ф4, 4.3), мягкие сигналы и сторож почты (4.5b;
порог жалоб — `SALES_COMPLAINT_PAUSE`) и пояса получателя по порядку: лида, его страны,
гипотезы (своего поля у гипотезы пока нет). Подключение — в ответах модуля мосту: `policy`
отдаёт `sales_policy()`, `recipient` кладёт `zones_of(lead)` в `Recipient.zones`. Тем же
правилом загрузка базы называет лидов, чьих часов окно не знает (`lead_values.warnings`).
Дневной лимит первых писем направления судит общий выбор ящика; его опечатку здесь называют
словами, что исправить (`unread_limit`): автоотправка тогда стоит не по делу.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from backend.config import outreach as outreach_cfg
from backend.config import sales as cfg
from backend.features.core.domain import Stage
from backend.features.core.stages import MailPolicy
from backend.features.core.window import SendWindow
from backend.features.outreach.health import SoftSignals
from backend.features.sales import geo


class Located(Protocol):
    """Где получатель: записанный лид или загружаемый (`intake.Lead`) — страна и пояс."""

    @property
    def country(self) -> str | None: ...
    @property
    def timezone(self) -> str | None: ...


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


def zones_of(lead: Located, hypothesis_zone: str | None = None) -> tuple[str | None, ...]:
    """Пояса получателя по порядку: лида, его страны (единственный или столичный), гипотезы."""
    by_country = geo.timezone_for(lead.country) if lead.country else None
    return (lead.timezone, by_country, hypothesis_zone)


#: Лимит первых писем продаж с опечаткой (`config/outreach.direction_unread`) — что исправить.
UNREAD_LIMIT = (
    "настройка лимита первых писем продаж не прочитана ({}) — первых писем ноль, пока её не "
    "поправят в .env: нужно целое число не меньше нуля или пусто"
)


def unread_limit() -> str | None:
    """Лимит первых писем продаж не прочитан из настроек — опечатка, и продажам ноль первых писем
    в сутки (`config/_base.spared`): что исправить, словами; `None` — прочитан или не задан."""
    name = outreach_cfg.direction_unread(Stage.SALES.value)
    return None if name is None else UNREAD_LIMIT.format(name)
