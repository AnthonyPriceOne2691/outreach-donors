"""Лид, гипотеза, стоп-лист, база знаний, отправитель и цепочка писем продаж — свои таблицы модуля.

**Лид — своя таблица, а не колонки `contacts`.** Имя, должность и компания —
сущность продаж; донорская таблица адресов о них не знает. Почтовые сущности
общие: строки `domains` и `contacts` заводятся как обычно, у лида — ссылки.

**Что станет с лидом, когда общую строку удалят.** Код доноров удаляет адрес
с карточки донора (`contacts/manual.py::remove`), а строка `contacts` одна на
домен и адрес — адрес лида может оказаться и адресом донора. Поэтому ссылка
на адрес обнуляется (`SET NULL`), а сам адрес лид хранит у себя: каскад стёр
бы лида молча, запрет уронил бы удаление у доноров. Домен компании и гипотезу,
пока на них есть лид, удалить нельзя (`RESTRICT`): боевые потоки доменов
не удаляют, а лида без компании у продаж не бывает.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


class LeadStatus(StrEnum):
    """Где лид на пути к первому письму. Причину отказа называет очистка."""

    NEW = "new"  # загружен, ещё не очищен
    READY = "ready"  # прошёл очистку: можно в очередь писем
    REJECTED = "rejected"  # отсеян очисткой


class RejectionReason(StrEnum):
    """Почему лид отсеян — перечисление данными, а не тип базы: по коду фильтрует
    экран (`?state=rejected&reason=duplicate`), новая причина — строка здесь, а не
    миграция. Словами причину называет `cleaning_note`."""

    DUPLICATE = "duplicate"  # адрес уже у лида продаж — в этом же файле или раньше
    STOPLIST = "stoplist"  # ручной стоп-лист продаж: клиенты и партнёры
    UNSUBSCRIBED = "unsubscribed"  # отписался или пожаловался где угодно — общий стоп-лист
    OTHER_DIRECTION = "other_direction"  # домен в работе у доноров или рекламодателей
    UNUSABLE = "unusable"  # адрес негодный по форме или назначению: чужой отдел, заглушка
    NO_MAIL = "no_mail"  # домен адреса не принимает почту: нет MX и A или нулевой MX
    UNDELIVERABLE = "undeliverable"  # проверяльщик: адреса не существует


class LeadSource(StrEnum):
    """Откуда лид. Новый источник — новое значение отдельной миграцией."""

    IMPORT = "import"  # файл или таблица
    REFERRAL = "referral"  # коллега, которого назвали в ответе


#: Длина имени гипотезы: имя короткое, по нему гипотезу выбирают.
NAME_LENGTH = 128


def _enum(e: type[StrEnum], name: str) -> SQLEnum:
    # Имя типа с приставкой модуля: в базе видно, чьё перечисление.
    return SQLEnum(e, name=name, values_callable=lambda x: [i.value for i in x])


class SalesHypothesisModel(TimestampedMixin, Base):
    """Кому и зачем пишем. Текст — данными в базе: репозиторий публичный."""

    __tablename__ = "sales_hypotheses"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Короткое имя: по нему гипотезу выбирают при загрузке базы и в отчётах.
    name: Mapped[str] = mapped_column(String(NAME_LENGTH), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class SalesLeadModel(TimestampedMixin, Base):
    """Человек, которому напишут продажи: кто он, где работает, откуда взялся."""

    __tablename__ = "sales_leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    hypothesis_id: Mapped[int] = mapped_column(
        ForeignKey("sales_hypotheses.id", ondelete="RESTRICT"), nullable=False
    )
    #: Домен компании обязателен: по нему стоп-лист, дубли с другими
    #: направлениями и сам сайт, о котором пишем.
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="RESTRICT"), nullable=False
    )
    #: Общая строка адреса. Пусто — строки ещё нет или её удалили у доноров:
    #: кому писать, лид знает по `email`.
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    position: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: ISO-2 нижним регистром, как `donors.geo`.
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: Пояс IANA (`Europe/Berlin`): по нему считаются окна отправки.
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Язык письма (`en`, `ru`).
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)

    source: Mapped[LeadSource] = mapped_column(
        _enum(LeadSource, "sales_lead_source"), nullable=False
    )
    status: Mapped[LeadStatus] = mapped_column(
        _enum(LeadStatus, "sales_lead_status"), nullable=False, default=LeadStatus.NEW
    )

    #: Код причины отказа — значение `RejectionReason`. Пусто у `new` и `ready`.
    rejection_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Что очистка сказала о лиде словами: причина отказа или почему лид остался
    #: `new` («проверка не выполнена: …»). У `ready` пусто.
    cleaning_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Вердикт проверяльщика с именем источника: `hunter:valid`, `fixture:valid`.
    #: Выдуманный вердикт не должен быть неотличим от живого — по умолчанию
    #: проверяльщик `fixture`, и отправка обязана это видеть.
    verification_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_sales_leads_hypothesis", "hypothesis_id"),
        Index("idx_sales_leads_domain", "domain_id"),
        # Без индекса обнуление ссылки читало бы всю таблицу лидов на каждом
        # удалении адреса у доноров — их поток не должен платить за наш.
        Index("idx_sales_leads_contact", "contact_id"),
        # Экран фильтрует лидов по состоянию и причине отказа.
        Index("idx_sales_leads_status_reason", "status", "rejection_reason"),
    )


class SalesStoplistModel(TimestampedMixin, Base):
    """Ручной стоп-лист продаж: домены и адреса клиентов и партнёров, которым не пишем.

    Своя таблица, а не общие `suppressions`: запись продаж там требует
    `stage=sales`, а значения `sales` в `Stage` ещё нет — срез 1.1b отложен
    владельцем до первого письма Этапа 2. Появится — строки переезжают в
    `suppressions` с причиной `manual`. Отписки общие: их очистка читает из
    `suppressions` со `stage NULL`.

    Строка — либо домен, либо адрес; причина у всех одна — решение человека,
    поэтому колонки причины нет: есть кто и когда внёс.
    """

    __tablename__ = "sales_stoplist"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Корневой домен, как `domains.host`: закрывает и адреса на нём, и компании с ним.
    host: Mapped[str | None] = mapped_column(String(253), nullable=True, unique=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    #: Откуда строка: имя файла или слова человека.
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint("(host IS NULL) <> (email IS NULL)", name="ck_sales_stoplist_one_key"),
    )


class KbKind(StrEnum):
    """Вид записи базы знаний. По виду агент берёт факты под ход (срез 3.2): вопрос
    о цене — `price_policy`, возражение — `objection`. Набор задан спекой; новый вид —
    значение здесь и `ADD VALUE` отдельной миграцией, как у `LeadSource`."""

    BRIEF = "brief"  # кто мы и что делаем — фон каждого письма
    SERVICE = "service"  # услуга: что входит и кому
    CASE = "case"  # кейс: что сделали и что вышло
    OBJECTION = "objection"  # возражение и ответ на него
    PRICE_POLICY = "price_policy"  # что можно говорить о цене и чего нельзя
    FORBIDDEN = "forbidden"  # чего не писать никогда
    CTA = "cta"  # чем закончить письмо: созвон, Telegram


#: Ширины колонок записи базы знаний. Язык — как у лида; тег — короткая метка выборки.
TITLE_LENGTH = 255
LANGUAGE_LENGTH = 16
TAG_LENGTH = 64
#: Кто правил: почта сотрудника или «консоль».
AUTHOR_LENGTH = 255


class SalesKbEntryModel(TimestampedMixin, Base):
    """Факт компании для агента продаж. Текст — данными в базе: репозиторий публичный.

    **Ключ — вид, язык и заголовок.** По нему повторная загрузка файла узнаёт
    запись, а не заводит вторую, и экран не даёт завести две одинаковые.
    **Запись не удаляется — выключается**: выключенную агент не видит, а её текст
    остаётся, и по журналу видно, когда и кем она выключена.
    """

    __tablename__ = "sales_kb_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[KbKind] = mapped_column(_enum(KbKind, "sales_kb_kind"), nullable=False)
    #: Код языка нижним регистром, как у лида: `en`, `ru`, `pt-br`.
    language: Mapped[str] = mapped_column(String(LANGUAGE_LENGTH), nullable=False)
    title: Mapped[str] = mapped_column(String(TITLE_LENGTH), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: Метки выборки: нижним регистром, без повторов, по алфавиту.
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(TAG_LENGTH)), nullable=False)
    #: Кто правил последним; когда — `updated_at`.
    updated_by: Mapped[str | None] = mapped_column(String(AUTHOR_LENGTH), nullable=True)

    __table_args__ = (
        UniqueConstraint("kind", "language", "title", name="uq_sales_kb_entries_key"),
    )


#: Ключ единственной строки настроек отправителя.
SETTINGS_ROW = 1


class SalesSettingsModel(TimestampedMixin, Base):
    """Отправитель продаж: от чьего имени письмо, чем подписано, куда звать. Одна строка.

    **Тексты — здесь, а не в окружении**: подпись и адрес правят на экране, и правка
    видна в журнале, а переменную окружения меняет только выкатка. **Секретов здесь
    нет**: ключи сервисов приходят из окружения через `config/`, а эту строку целиком
    показывает экран.

    Ключ всегда `SETTINGS_ROW`, вторую строку не пустит проверка базы. Пусто —
    «не задано»: без адреса, подписи или имени отправителя отправка продаж отказывает
    (`sender.check_ready`).
    """

    __tablename__ = "sales_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    sender_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sender_position: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Куда лиду писать в Telegram: `@имя` или ссылка `https://t.me/…`.
    telegram: Mapped[str | None] = mapped_column(String(255), nullable=True)
    physical_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(AUTHOR_LENGTH), nullable=True)

    __table_args__ = (CheckConstraint("id = 1", name="ck_sales_settings_one_row"),)


#: Ширина темы письма: тема — строка, а не абзац.
SUBJECT_LENGTH = 255


class SalesChainTemplateModel(TimestampedMixin, Base):
    """Шаблон шага цепочки писем продаж. Текст — данными в базе: репозиторий публичный.

    **Ключ — набор, шаг и язык.** Набор — гипотеза или общий (`hypothesis_id` пуст):
    общий действует для всех гипотез, своя цепочка гипотезы заменяет его на языке
    целиком (`sales/chain.py`). Пустой номер гипотезы — тоже значение ключа
    (`NULLS NOT DISTINCT`): второго общего шаблона того же шага и языка база не пустит.
    **Тема — только у первого письма**: добивки идут в той же переписке, и тему им
    даёт первое письмо — это держит и проверка базы. Шаблон не удаляется — выключается.
    """

    __tablename__ = "sales_chain_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    hypothesis_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_hypotheses.id", ondelete="RESTRICT"), nullable=True
    )
    #: 1 — первое письмо, 2 и 3 — добивки.
    step: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: Код языка письма нижним регистром: `ru`, `en`.
    language: Mapped[str] = mapped_column(String(LANGUAGE_LENGTH), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(SUBJECT_LENGTH), nullable=True)
    #: Тело в формате зон, как у шаблонов доноров: `[имя] rewrite` / `[имя] fixed`.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: Кто правил последним; когда — `updated_at`.
    updated_by: Mapped[str | None] = mapped_column(String(AUTHOR_LENGTH), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "hypothesis_id",
            "step",
            "language",
            name="uq_sales_chain_templates_key",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("step BETWEEN 1 AND 3", name="ck_sales_chain_templates_step"),
        CheckConstraint(
            "(step = 1) = (subject IS NOT NULL)", name="ck_sales_chain_templates_subject"
        ),
    )
