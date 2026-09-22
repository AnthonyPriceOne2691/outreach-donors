"""AdvertiserModel и SupplierDonorModel — кому пишем на втором этапе и кого не трогаем.

**Одна строка на домен — это и есть дедупликация.** Требование говорит
«один рекламодатель — одно письмо, сколько бы страниц он ни занимал»;
здесь оно выражено ограничением базы, а не бережностью кода. Один
и тот же домен встречается у нескольких доноров и на десятках страниц —
и остаётся одной строкой.

**Рекламодатель привязан к домену, а не заведён рядом с ним.** Схему
под это заложили в самом начале: адрес принадлежит домену, а не донору,
потому что тот же сайт бывает и донором, и рекламодателем. Заводить
вторую ветку данных не пришлось.

**Стоп-лист поставщиков — про доноров, а не про получателей.** Это
площадки, где агентство само размещалось за последний год, и его
партнёры: искать их рекламодателей значит писать тем, с кем уже
работают. Список приходит со стороны задачи; пока его нет, таблица
пуста, и это видно числом, а не молчанием.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, or_
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.elements import ColumnElement

from backend.features.core.domain import ContactStatus
from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


def _enum(e: type) -> SQLEnum:
    return SQLEnum(e, values_callable=lambda x: [i.value for i in x])


class AdvertiserModel(TimestampedMixin, Base):
    """Домен, которому мы собираемся написать оффер.

    Поля контакта повторяют донорские намеренно: лестница поиска адреса
    одна на оба этапа, и её исход хранится одинаково — иначе у двух
    очередей появятся два разных правила «кому нужен контакт»,
    и разойдутся они молча.
    """

    __tablename__ = "advertisers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Уникальность домена и есть правило «одно письмо на рекламодателя».
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    #: Лучший балл среди ссылок, приведших сюда.
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Сколько РАЗНЫХ наших доноров на него ссылается. Усилитель требования
    #: живёт в скоринге, а здесь это просто наблюдаемое число.
    donors: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    links: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Ссылка, под которую пишется письмо: требование просит
    #: персонализацию «под конкретную найденную ссылку — страницу и анкор».
    best_donor_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    best_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    best_anchor: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Прошёл ли через руки человека. Не то же самое, что высокий балл:
    #: по расхождению этих двух и считается, как часто ошибается скоринг.
    confirmed_by_human: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    contact_status: Mapped[ContactStatus | None] = mapped_column(
        _enum(ContactStatus), nullable=True
    )
    contact_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_advertisers_points", "points"),
        Index("idx_advertisers_contact_status", "contact_status"),
    )

    domain: Mapped[object] = relationship("DomainModel")


class SupplierDonorModel(TimestampedMixin, Base):
    """Донор, чьих рекламодателей мы не трогаем.

    Площадки, где агентство размещалось за последние двенадцать месяцев,
    и текущие партнёры. Их рекламодатели — это чужие клиенты и свои же
    размещения; письмо им портит отношения, а не приносит лид.

    **Отсев идёт при переводе кандидата в рекламодатели**, а не при
    отправке: чем раньше, тем меньше работы уходит впустую — контакт
    такого домена искать незачем, а платная ступень стоит денег.
    """

    __tablename__ = "supplier_donors"

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    #: Почему в списке: «размещались в марте», «текущий партнёр».
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Докуда действует. Пусто — навсегда: так выглядит текущий партнёр.
    #: Срок стоит у тех, кто попал в список размещением: требование
    #: считает их «за последние 12 месяцев», то есть окно съезжает.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("idx_supplier_donors_host", "host"),)

    @classmethod
    def in_force(cls, moment: datetime) -> ColumnElement[bool]:
        """Условие «запись ещё держит». То же правило, что у стоп-листа."""
        return or_(cls.expires_at.is_(None), cls.expires_at > moment)
