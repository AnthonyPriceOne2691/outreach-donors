"""Рассылка: отправитель, кампания, письмо, тред, ответ."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DECIMAL,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
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

#: Длина номера события почтовой платформы в журнале здоровья ящика (`sg_event_id`).
EVENT_ID_LENGTH = 100


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


class SendingDomainModel(TimestampedMixin, Base):
    """Домен рассылки целиком: дневной лимит на все его ящики, выдержка, пауза.

    Репутация живёт у домена, а не у ящика: два ящика по двадцать писем на одном
    домене — это сорок писем с домена. Строка не обязательна: домен без неё пишет
    так, как пишут его ящики (у доноров строк нет — поведение прежнее).

    Состояния не хранится словом: «на паузе» — есть `paused_at`, «на выдержке» —
    `young_until` впереди. Слово, которое кто-то должен переписывать, однажды
    перестают переписывать (урок `SenderModel` о счётчике отправленного).
    """

    __tablename__ = "sending_domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Как в `senders.domain`: строка находит свои ящики по имени домена.
    domain: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)
    stage: Mapped[Stage] = mapped_column(_enum(Stage), nullable=False)
    #: Первых писем в сутки со всех ящиков домена (сутки и счёт — как у разгона).
    daily_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Пауза домена целиком — с причиной словами и временем.
    pause_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: До этой минуты домен не пишет: новый домен выдерживают неделю.
    young_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (CheckConstraint("daily_limit >= 0", name="ck_sending_domains_daily_limit"),)


class SenderHealthModel(Base):
    """Журнал здоровья ящика строкой; снижение лимита — следствие строк за сутки, не счётчик."""

    __tablename__ = "sender_health"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("senders.id", ondelete="CASCADE"))
    #: `deferred`, `blocked`, `complaint`, `limit_cut`, `paused`.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: Номер события у платформы — у строки, которую пишет само событие (сигнал, жалоба).
    #: Пусто — событие без номера и производные строки (`limit_cut`, `paused`).
    event_id: Mapped[str | None] = mapped_column(String(EVENT_ID_LENGTH), nullable=True)

    __table_args__ = (
        Index("idx_sender_health_sender_at", "sender_id", "at"),
        # Платформа доставляет пачку событий «хотя бы один раз»: повтор события — та же
        # строка, а не вторая. Гонку двух доставок одной пачки держит база, а не код.
        Index(
            "uq_sender_health_event",
            "event_id",
            unique=True,
            postgresql_where=text("event_id IS NOT NULL"),
        ),
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

    # Через сколько дней после первого письма уходят добивки: список
    # по шагу, например [7, 14]. Хранится в рассылке, а не в настройках
    # сервиса, потому что сроки подбирают по отклику — а настройка,
    # общая на всё, меняется вместе с историей уже идущих цепочек.
    followup_days: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)

    # Текст первого письма, как его утвердили при создании рассылки, —
    # в формате шаблона с зонами. Пусто — шаблон из кода. Хранится целиком,
    # а не правкой к умолчанию: шаблон в коде поменяют, а письма идущей
    # рассылки должны оставаться утверждённым текстом.
    letter_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Кому рассылка Этапа 2: `links` — рекламодателям, найденным по ссылке на
    #: нашем доноре, `niche` — бизнесам ниши из выдачи (`crawl/niche.py`). От
    #: аудитории зависят и оффер, и добивки: бизнесу ниши нельзя писать «ваше
    #: размещение, которое я видел» — размещения у него нет.
    audience: Mapped[str] = mapped_column(
        String(16), nullable=False, default="links", server_default="links"
    )

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
    #: Входящий ответ, на который это письмо отвечает. Пусто — первое письмо
    #: или добивка: они пишутся по цепочке, а не в ответ (`letters/answers.py`).
    #: Связь — отдельным ALTER (`use_alter`): ответ и сам ссылается на письмо
    #: (`replies.message_id`), и без этого таблицы не упорядочить для
    #: создания и удаления — SQLAlchemy видит цикл.
    answers_reply_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "replies.id",
            ondelete="SET NULL",
            name="fk_messages_answers_reply",
            use_alter=True,
        ),
        nullable=True,
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
    # Доставка — отдельное событие и отдельное время: «ушло» и «дошло»
    # разделяют минуты, а иногда и целый отказ.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Почему не дошло — словами платформы. Хранится, потому что читать
    # его будет человек, решающий судьбу домена.
    failure_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Номер письма у платформы (`X-Message-Id`) — для её журнала Activity
    # и сторожа тишины. Получатель его не видит, и якорем цепочки он не годится.
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Наш `Message-ID`, `<…@домен отправителя>` (`letters/identity.py`): его
    # видит получатель, на него ссылаются добивки, по нему находится письмо,
    # если ответ пришёл без метки. Пишется до вызова почты — вместе с
    # «отправляется». Пусто у писем, ушедших до него, и у неотправленных.
    internet_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

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
        Index("idx_messages_answers_reply_id", "answers_reply_id"),
        # Запасная привязка ответа ищет письмо по идентификатору из его
        # заголовков; уникальность — чтобы ответ не нашёл двух писем сразу.
        Index("uq_messages_internet_message_id", "internet_message_id", unique=True),
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
    # Такие ответы видны на вкладке «Не привязаны» (`replies/unbound.py`).
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
    #: На какие адреса письмо пришло: конверт, «кому», копия — как их собрал
    #: приём, наш адрес первым. В них видно, была ли в адресе метка и какая.
    #: Пусто у ответов, принятых до 28.09.2026: тогда адрес не хранился.
    to_addresses: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    #: Почему ответ не привязан ни к одному письму (`replies/binding.Unbound`).
    #: Решение приёма в ту минуту, а не пересчёт: к минуте, когда смотрит
    #: человек, секрет могли сменить, а письмо — удалить, и пересчёт объяснял бы
    #: не то, что случилось. Пусто у привязанного и у принятого до поля.
    unbound_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Что пришло файлами — в `reply_attachments` (`models/attachment.py`),
    # вместе с самими файлами. Прайс приходит вложением чаще, чем текстом,
    # и ответ, выглядящий пустым, — это ответ, из которого не видно главного.

    price_white: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    price_grey: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: Все цены, названные в ответе, словами донора (`replies/offers.py`):
    #: продукт, ниша, цена строкой, валюта, срок. `[]` — разобран, цен нет;
    #: пусто — разобран до 06.10.2026, когда списка ещё не было, или не разбирался.
    offers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    payment_methods: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    # Ниже порога — в ручную очередь, а не в базу. Приёмка требует не более
    # 5% ошибок извлечения, без этой ветки порог не держится.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Продаёт ли донор размещение по разбору ответа: `sells`, `declines`,
    #: `unclear`. Отдельно от цены: «не продаём» — ответ, а не пустая цена.
    placement: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Что предложила модель, нетронутым, с версией промпта. Решение человека
    #: ложится в поля выше, а этот снимок остаётся — по расхождению между ними
    #: и калибруется разбор (приём соседней системы: предложение модели против
    #: действия оператора, по версиям промпта).
    model_parse: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
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
