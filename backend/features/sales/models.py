"""Лид, гипотеза, стоп-лист, база знаний, отправитель, цепочка писем, диалог, передача лида и
журнал сообщений о черновиках агента продаж — свои таблицы модуля.

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
    BigInteger,
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
    #: Диалог, в котором этого лида назвали («пишите …», источник `referral`).
    #: Удаление диалога лида не уносит: ссылка обнуляется.
    referred_from_thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index("idx_sales_leads_hypothesis", "hypothesis_id"),
        Index("idx_sales_leads_domain", "domain_id"),
        # Без индекса обнуление ссылки читало бы всю таблицу лидов на каждом
        # удалении адреса у доноров — их поток не должен платить за наш.
        Index("idx_sales_leads_contact", "contact_id"),
        # Экран фильтрует лидов по состоянию и причине отказа.
        Index("idx_sales_leads_status_reason", "status", "rejection_reason"),
        Index("idx_sales_leads_referred_from_thread", "referred_from_thread_id"),
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


#: Ширина версии цепочки: `chain-` и двенадцать знаков отпечатка (`chain.version_of`).
VERSION_LENGTH = 32


class SalesThreadModel(TimestampedMixin, Base):
    """Диалог продаж и его лид: кому письмо, каким набором цепочки и какой её версией.

    **Связь явная, а не поиском по домену и адресу.** Строки `contacts` у диалога продаж
    нет — адрес лида живёт у лида (решение 01.10), и два лида одной компании — два
    диалога одного домена: по домену их не различить. Заводит связь сборка очереди
    продаж (`sales/queue.py`); по ней отправка находит адрес лида, добивка — набор и
    язык цепочки первого письма (шаги наборов не смешиваются), передача — лида.

    **Один диалог на лида** (ключ `lead_id`): второе первое письмо тому же человеку —
    жалоба на спам. Диалог уходит только липовым (чистка аутрича) — связь с ним
    каскадом; лида и гипотезу с диалогом удалить нельзя.
    """

    __tablename__ = "sales_threads"

    thread_id: Mapped[int] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("sales_leads.id", ondelete="RESTRICT"), nullable=False
    )
    #: Набор цепочки первого письма: гипотеза или общий (пусто) — добивки берут его же.
    chain_hypothesis_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_hypotheses.id", ondelete="RESTRICT"), nullable=True
    )
    #: Язык цепочки — код, как у шаблонов: `ru`, `en`.
    language: Mapped[str] = mapped_column(String(LANGUAGE_LENGTH), nullable=False)
    #: Версия цепочки, которой написано первое письмо: по ней калибровка узнаёт текст.
    chain_version: Mapped[str] = mapped_column(String(VERSION_LENGTH), nullable=False)

    __table_args__ = (
        UniqueConstraint("lead_id", name="uq_sales_threads_lead"),
        # Без индекса проверка запрета на удаление гипотезы читала бы всю таблицу.
        Index("idx_sales_threads_chain_hypothesis", "chain_hypothesis_id"),
    )


class HandoffKommo(StrEnum):
    """Что с записью в Kommo по последнему письму диалога (срез 5.3).

    Состояние Kommo и состояние Telegram — две колонки, а не одна: «Kommo
    повторяем, телемаркетологу уже ушла ссылка на диалог» и «сделка есть,
    сообщение не доставлено» — обычные исходы, и одно значение их не вместит.
    """

    PENDING = "pending"  # есть что записать: сделка или примечание с новым письмом
    RETRY = "retry"  # Kommo не ответил после повторов — проход по расписанию повторит
    UNCONFIRMED = "unconfirmed"  # запись ушла, ответ потерян, поиск не решил — смотрит человек
    FAILED = "failed"  # Kommo отказал: ключ, права, форма ответа — повтор не поможет
    DONE = "done"  # сделка есть, последнее письмо легло примечанием, — или закрыл человек
    OFF = "off"  # Kommo не подключён (fixture): телемаркетологу — ссылка на диалог


class HandoffTelegram(StrEnum):
    """Дошло ли сообщение телемаркетологу. Повтор по расписанию — `telegram_due_at`."""

    PENDING = "pending"  # ещё не отправляли
    SENT = "sent"  # Telegram принял; ссылка — в `notified_link`
    UNDELIVERED = "undelivered"  # не ушло: ждёт повтора прохода или повторов больше нет


class SalesHandoffModel(TimestampedMixin, Base):
    """Передача лида продаж телемаркетологу: сделка в Kommo и сообщение в Telegram.

    **Ключ — диалог.** Следующий ответ того же человека — не вторая передача,
    а примечание к той же сделке; номер сделки живёт здесь.

    **Что держит строку.** Диалог уходит только липовым (чистка аутрича
    удаляет пробную переписку) — передача уходит с ним каскадом. Лида с
    передачей удалить нельзя: номер сделки в чужой CRM терять нельзя.
    """

    __tablename__ = "sales_handoffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("sales_leads.id", ondelete="RESTRICT"), nullable=False
    )
    #: Номер сделки в Kommo. Пусто — сделки нет: Kommo не подключён или не ответил.
    kommo_lead_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    kommo: Mapped[HandoffKommo] = mapped_column(
        _enum(HandoffKommo, "sales_handoff_kommo"), nullable=False, default=HandoffKommo.PENDING
    )
    telegram: Mapped[HandoffTelegram] = mapped_column(
        _enum(HandoffTelegram, "sales_handoff_telegram"),
        nullable=False,
        default=HandoffTelegram.PENDING,
    )
    #: Сколько раз задача бралась за запись в Kommo.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Последний сбой словами — Kommo, Telegram или копия в группу; без адресов и ключей.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Ответ, который уже лёг примечанием в Kommo. Номер, а не ссылка: ответы
    #: удаляет чистка пробных писем, а история передачи должна остаться.
    noted_reply_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Ссылка из последнего доставленного сообщения: другая ссылка — новое сообщение.
    notified_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Когда проходу по расписанию пора взяться за передачу. Пусто — делать нечего.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Задача взялась за передачу: вторая ждёт. Старше срока — задача умерла.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Сколько раз подряд сообщение о лиде — личное или копия в группу — не ушло из-за сети,
    #: 5xx или 429 (`handoff_telegram.py`). Ушло, отказ постоянный или попытки кончились — ноль.
    telegram_tries: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    #: Не раньше чего проход повторит сообщение, которое не ушло. Пусто — повторять нечего.
    #: `undelivered` со сроком — ждёт личное сообщение, `sent` со сроком — копия в группу.
    telegram_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("thread_id", name="uq_sales_handoffs_thread"),
        # Без индекса проверка запрета на удаление лида читала бы всю таблицу.
        Index("idx_sales_handoffs_lead", "lead_id"),
    )


class NoticeStatus(StrEnum):
    """Чем кончилось сообщение о черновике в группе продаж. Перечисление данными, как
    `RejectionReason`: новый исход — строка здесь, а не миграция типа."""

    SENT = "sent"  # Telegram принял сообщение
    UNDELIVERED = "undelivered"  # не доставлено за попытки бота — ушла тревога эксплуатации


class SalesDraftNoticeModel(TimestampedMixin, Base):
    """Журнал отправки: сообщение о черновике агента продаж в группу продаж (срез 3.5).

    **Строка — версия черновика** (`written_at` — когда он написан): «написать заново»
    переписывает черновик, и о новой версии группа узнаёт новым сообщением, а повтор
    задачи ту же версию второй раз не объявляет. Сам черновик здесь не хранится и от
    исхода сообщения не зависит: не доставлено — черновик цел и виден в переписке.
    Черновик удалён (чистка пробного ответа) — его строки уходят с ним.
    """

    __tablename__ = "sales_draft_notices"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("agent_drafts.id", ondelete="CASCADE"), nullable=False
    )
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: `NoticeStatus` строкой.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Что ушло в группу — так, как прочёл его человек.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Почему не доставлено — словами бота, без токена.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("draft_id", "written_at", name="uq_sales_draft_notices_version"),
    )
