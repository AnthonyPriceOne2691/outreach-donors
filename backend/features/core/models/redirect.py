"""Переадресация в диалоге рекламодателя: «пишите на partners@…» в ответе человека (16.11.2026).

Строка — на ответ, который приём поставил на разбор (`replies/lead_redirect.note`) или разовый
проход взял командой. В ней всё, что решено по ответу, и почему: без неё повторный проход звал бы
модель снова, а экран не знал бы, переслали ли лид и куда.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


class LeadRedirectModel(TimestampedMixin, Base):
    """Решение по одному ответу рекламодателя: просят ли писать на другой адрес — и что сделано.

    Состояние (`replies/lead_redirect.State`): `pending` — ждёт прохода (модель не спрошена или
    проверка адреса не кончилась); `none` — не просят; `refused` — просят, но не пересылаем
    (`reason` — почему словами); `letter` — письмо заведено (`message_id`), а ушло ли — его статус.
    """

    __tablename__ = "lead_redirects"

    id: Mapped[int] = mapped_column(primary_key=True)
    reply_id: Mapped[int] = mapped_column(
        ForeignKey("replies.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    #: Адрес, который выбрала модель меткой, — дословно из ответа. Пусто — не выбран.
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Почему не пересылаем или чего письмо ждёт сейчас — словами для экрана.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Пометка человеку: адрес на чужом домене (партнёрка на своём) — переслали и туда.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    #: Токены разбора моделью — тем же числом, что строка журнала расхода.
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Что ответила модель, нетронутым, с версией подсказки: по нему проверяют её выбор.
    model_answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: Когда проход в последний раз брался за строку: сбой модели или проверки ждёт паузу.
    tried_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Когда решение принято: нашли — не нашли — переслали.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_lead_redirects_thread", "thread_id"),
        Index("idx_lead_redirects_state", "state"),
    )
