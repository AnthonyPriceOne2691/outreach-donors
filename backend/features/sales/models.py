"""Лид и гипотеза продаж — свои таблицы модуля.

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

from enum import StrEnum

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


class LeadStatus(StrEnum):
    """Где лид на пути к первому письму. Причину отказа называет очистка."""

    NEW = "new"  # загружен, ещё не очищен
    READY = "ready"  # прошёл очистку: можно в очередь писем
    REJECTED = "rejected"  # отсеян очисткой


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

    __table_args__ = (
        Index("idx_sales_leads_hypothesis", "hypothesis_id"),
        Index("idx_sales_leads_domain", "domain_id"),
        # Без индекса обнуление ссылки читало бы всю таблицу лидов на каждом
        # удалении адреса у доноров — их поток не должен платить за наш.
        Index("idx_sales_leads_contact", "contact_id"),
    )
