"""Рассылка: отправитель, кампания, письмо, тред, ответ."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DECIMAL,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from backend.features.core.domain import (
    MessageStatus,
    ReplyKind,
    SenderStatus,
    Stage,
    ThreadStatus,
)
from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


def _enum(e: type) -> SQLEnum:
    return SQLEnum(e, values_callable=lambda x: [i.value for i in x])


class SenderModel(TimestampedMixin, Base):
    """Отправитель — адрес на одном из наших доменов рассылки.

    Раздача писем идёт тому, у кого больше остаток дневного лимита,
    при равенстве — меньший id. Не по кругу: круг раздаёт поровну только если
    все отправители одинаковы и заведены одновременно, а в жизни один добавлен
    вчера, другой стоял на паузе, у третьего кап правили руками.

    **Сколько ушло сегодня, здесь не хранится.** Такое поле было, и его
    никто не обнулял: к концу первых суток оно упиралось в кап и оставалось
    там навсегда, а ящик переставал получать письма молча. Считается
    по `messages.sent_at` — там же, где лежит сам факт отправки.
    """

    __tablename__ = "senders"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # У этапов домены отправки разные: Этап 2 конфликтнее, репутацию
    # доменов Этапа 1 он задевать не должен.
    stage: Mapped[Stage] = mapped_column(_enum(Stage), nullable=False)

    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[SenderStatus] = mapped_column(
        _enum(SenderStatus), nullable=False, default=SenderStatus.FREE
    )
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    warmup_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("idx_senders_stage_status", "stage", "status"),
        Index("idx_senders_domain", "domain"),
    )


class CampaignModel(TimestampedMixin, Base):
    """Рассылка, собранная из прогона."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[Stage] = mapped_column(_enum(Stage), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    messages: Mapped[list[MessageModel]] = relationship("MessageModel", back_populates="campaign")


class ThreadModel(TimestampedMixin, Base):
    """Переписка с одним человеком на стороне донора.

    Диалог привязан к адресу, а не только к домену: у сайта их несколько —
    `info@`, `editor@`, `advertising@`, — и за ними разные люди. Сведя их
    в один диалог, мы получили бы кашу там, где двое отвечают по-разному:
    отдел продаж называет одну цену, редактор другую.

    Наружу это всё равно один донор: список диалогов группируется
    по домену (docs/OUTREACH_THREADS.md).
    """

    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    # С кем именно разговор. Может смениться: ответ приходит с другого
    # адреса чаще, чем кажется, — на общий ящик смотрит секретарь.
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ThreadStatus] = mapped_column(
        _enum(ThreadStatus), nullable=False, default=ThreadStatus.OPEN
    )

    __table_args__ = (
        UniqueConstraint(
            "domain_id", "campaign_id", "contact_id", name="uq_threads_domain_campaign_contact"
        ),
        Index("idx_threads_status", "status"),
        Index("idx_threads_domain", "domain_id"),
    )

    replies: Mapped[list[ReplyModel]] = relationship("ReplyModel", back_populates="thread")


class MessageModel(TimestampedMixin, Base):
    """Одно письмо цепочки.

    Очередь общая, не нарезана по отправителям: `sender_id` проставляется
    в момент захвата задачи. Иначе упавший отправитель блокирует свою часть
    очереди вместо того, чтобы отдать работу остальным.
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), nullable=True
    )
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"), nullable=False)
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    sender_id: Mapped[int | None] = mapped_column(
        ForeignKey("senders.id", ondelete="SET NULL"), nullable=True
    )

    # 0 — первое письмо, дальше добивки на 7-й и 14-й день.
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[MessageStatus] = mapped_column(
        _enum(MessageStatus), nullable=False, default=MessageStatus.QUEUED
    )

    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Доля изменённых слов относительно шаблона. Цель 15–25%.
    uniqueness_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Домен + этап + шаг. Защита от повторной отправки при ретрае задачи:
    # второе письмо тому же донору — это жалоба на спам.
    idempotency_key: Mapped[str] = mapped_column(String(320), nullable=False)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_messages_idempotency"),
        # Выборка очереди: что готово к отправке.
        Index("idx_messages_status_next_action", "status", "next_action_at"),
        # «Сколько этот ящик отправил сегодня» — запрос на каждое письмо.
        Index("idx_messages_sender_sent_at", "sender_id", "sent_at"),
        Index("idx_messages_thread_id", "thread_id"),
    )

    campaign: Mapped[CampaignModel] = relationship("CampaignModel", back_populates="messages")


class ReplyModel(TimestampedMixin, Base):
    """Входящее письмо и то, что из него удалось извлечь.

    Исходный текст хранится всегда и рядом с разобранным: оператор в карточке
    донора должен видеть, из чего получена цена, — иначе спорный разбор нечем
    проверить.

    **Ответ может прийти с другого адреса, и это норма** (docs/OUTREACH_THREADS.md):
    на общий ящик смотрит секретарь и пересылает письмо редактору. Адрес
    отправителя хранится здесь, а не выводится из контакта, которому писали.
    """

    __tablename__ = "replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Диалога может не быть: ответ, который не удалось соотнести с нашим
    # письмом, всё равно сохраняется. Выброшенный ответ выглядит как
    # «донор не ответил», и причину будут искать в лестнице контактов.
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), nullable=True
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    # Цепочку останавливают только HUMAN и UNSUBSCRIBE (см. ReplyKind).
    kind: Mapped[ReplyKind] = mapped_column(_enum(ReplyKind), nullable=False)
    raw_body: Mapped[str] = mapped_column(Text, nullable=False)

    # Идентификатор письма у почты. По нему и только по нему отличается
    # повтор вебхука от второго ответа: провайдер доставляет события
    # «хотя бы один раз» и повторяет их при сбое, а без этой отметки
    # повтор давал бы второй ответ, второй разбор и второй вызов модели.
    inbound_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Что пришло файлами: имя, размер, тип. Сами файлы здесь не лежат.
    # Прайс приходит вложением чаще, чем текстом, и ответ, выглядящий
    # пустым, — это ответ, из которого не видно главного.
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    price_white: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    price_grey: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    payment_methods: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    # Ниже порога — в ручную очередь, а не в базу. Приёмка требует не более
    # 5% ошибок извлечения, без этой ветки порог не держится.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("inbound_message_id", name="uq_replies_inbound_message_id"),
        Index("idx_replies_thread_id", "thread_id"),
        Index("idx_replies_kind", "kind"),
        # Ручная очередь разбора: что ждёт человека. Считается, не хранится —
        # уверенность ниже порога и разбор ещё не подтверждён.
        Index("idx_replies_confidence_reviewed", "confidence", "reviewed_at"),
    )

    thread: Mapped[ThreadModel | None] = relationship("ThreadModel", back_populates="replies")
