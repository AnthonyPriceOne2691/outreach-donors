"""Агент переписки: настройки по этапам и черновики ответов.

На этапе доноров мы покупаем размещение и торгуемся вниз, на этапе
рекламодателей — продаём его и держим цену. Цель, доводы и предел цены у них
противоположные, поэтому настройки свои у каждого этапа.

**Версионируются, как пороги** (`run_settings`): правка заводит новую версию,
а не переписывает старую. Черновик агента обязан оставаться объяснимым —
«почему он предложил $150» отвечается версией, по которой он писал, а не
нынешней.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DECIMAL,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.core.domain import DraftStatus, Stage
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
    #: Режим: `drafts` — агент пишет черновик, отправляет человек; `autopilot` —
    #: ответ в границах уходит сам (`agent/autopilot.py`), если этапу автопилот
    #: разрешён в коде и выключатель сервера включён.
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="drafts", server_default="drafts"
    )
    #: Сколько ответов автопилот шлёт в одну переписку, прежде чем отдать её
    #: человеку: разговор, который не сходится за пару писем, ведёт человек.
    max_turns: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=2, server_default="2"
    )

    __table_args__ = (UniqueConstraint("stage", "version", name="uq_agent_settings_stage_version"),)


class AgentDraftModel(TimestampedMixin, Base):
    """Черновик агента — ответ на один входящий ответ человека, и решение по нему.

    Один на ответ: «написать заново» переписывает его, пока решения нет, —
    объяснимость держит ссылка на версию настроек, а попытки петли правки
    лежат в `meta`. Черновик — не письмо: в `messages` его нет, и ни очередь,
    ни счётчики писем его не видят. Письмом он становится, когда ответ
    отправят (`agent/drafts.py`): рядом ложатся ушедший текст и была ли
    правка — по ним видно, как часто агента правят (датасет калибровки).
    """

    __tablename__ = "agent_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    reply_id: Mapped[int] = mapped_column(
        ForeignKey("replies.id", ondelete="CASCADE"), nullable=False
    )
    #: По какой версии настроек написан.
    settings_id: Mapped[int] = mapped_column(ForeignKey("agent_settings.id"), nullable=False)
    status: Mapped[DraftStatus] = mapped_column(
        SQLEnum(DraftStatus, values_callable=lambda x: [i.value for i in x]), nullable=False
    )
    #: Текст агента. Пусто — бриф этапа решил не писать (`skipped`) или сразу
    #: отдал ответ человеку (`escalated`), либо модель не вернула годного.
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Почему пропущен или отдан человеку — словами.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Что этап знал и решил до письма (бриф) и что было в петле правки —
    #: для калибровки по версиям, а не для отправки.
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Решение: что ушло, правили ли текст агента, кто и когда решил
    #: (`autopilot` — без человека), почему отклонён (словами и видом),
    #: каким письмом ушёл.
    final_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Вид причины — пункт списка этапа (`AgentStage.reject_reasons`) или «другое»:
    #: по нему калибровка считает отклонения, не читая слов. Пусто — причина
    #: своими словами у этапа без строгого списка.
    reject_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("reply_id", name="uq_agent_drafts_reply"),
        Index("idx_agent_drafts_status", "status"),
    )
