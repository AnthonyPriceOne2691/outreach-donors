"""Агент переписки: его настройки, по этапу на каждую ветку разговора.

На этапе доноров мы покупаем размещение и торгуемся вниз, на этапе
рекламодателей — продаём его и держим цену. Цель, доводы и предел цены у них
противоположные, поэтому настройки свои у каждого этапа.

**Версионируются, как пороги** (`run_settings`): правка заводит новую версию,
а не переписывает старую. Черновик агента обязан оставаться объяснимым —
«почему он предложил $150» отвечается версией, по которой он писал, а не
нынешней.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.core.domain import Stage
from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


class AgentSettingsModel(TimestampedMixin, Base):
    """Одна версия настроек агента одного этапа. Текущая — последняя."""

    __tablename__ = "agent_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[Stage] = mapped_column(
        SQLEnum(Stage, values_callable=lambda x: [i.value for i in x]), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: Пишет ли агент черновики ответов на этом этапе. Выключен — переписку
    #: ведёт только человек, как до агента.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: Чего добиваемся разговором — словами человека.
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str] = mapped_column(Text, nullable=False)
    #: Доводы и вопросы, которые агент ведёт по порядку.
    points: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    #: Предел цены в долларах: у доноров — не дороже, у рекламодателей — не
    #: дешевле. Пусто — предела нет, и цену агент не обещает вовсе.
    price_limit_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    #: Темы, на которых агент не отвечает сам, а отдаёт разговор человеку.
    stop_topics: Mapped[list[str]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (UniqueConstraint("stage", "version", name="uq_agent_settings_stage_version"),)
