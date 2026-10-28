"""UsageRecordModel, AhrefsCapModel, AhrefsKeySnapshotModel и SuppressionModel — расход,
кап Ahrefs, снимок ключа Ahrefs и стоп-лист."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    and_,
    func,
    or_,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from backend.features.core.domain import Stage, SuppressionReason, UsageProvider
from backend.features.core.models._mixins import TimestampedMixin
from backend.features.core.models._mixins import sql_enum as _enum
from backend.shared.database.base import Base


class UsageRecordModel(TimestampedMixin, Base):
    """Построчный расход. Остаток лимита НЕ хранится полем: два независимых
    счётчика неизбежно разойдутся. Остаток = месячный лимит минус сумма строк.

    Поле `system` существует потому, что ключ Ahrefs общий с сервисом
    соседней системой: обе системы пишут сюда, и каждая перед прогоном видит
    общий остаток, а не свой.
    """

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[UsageProvider] = mapped_column(_enum(UsageProvider), nullable=False)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 4), nullable=True)
    #: На какой этап ушла трата — расход Этапа 2 считается отдельно от Этапа 1 (требование
    #: к расходу Этапа 2). Пусто — общая на все этапы (отправка письма) или этап не назван:
    #: операцию делят этапы, а вызов его не сказал, либо строка старше 12.10.2026.
    stage: Mapped[Stage | None] = mapped_column(_enum(Stage), nullable=True)

    __table_args__ = (
        # Основной запрос — «сколько потрачено за месяц по провайдеру».
        Index("idx_usage_provider_created", "provider", "created_at"),
        Index("idx_usage_run_id", "run_id"),
    )


class AhrefsCapModel(Base):
    """Кап юнитов Ahrefs, заданный на экране «Расход»: строка на каждую смену.

    Действующая настройка — последняя строка; строк нет — «весь лимит ключа»
    (`runs/ahrefs_cap.py`). Строка не правится и не удаляется: по строкам видно, кто
    и когда менял кап, — как по версиям порогов (требование: «видно, кто и когда
    менял»). Остаток капа полем не хранится — он считается по журналу расхода
    за цикл ключа (`runs/budget.cap_left`).
    """

    __tablename__ = "ahrefs_caps"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: «Не больше стольких юнитов за цикл ключа»; пусто — весь лимит ключа: кап следует
    #: за ключом, когда его лимит двигают в Ahrefs (16.10.2026). Не больше лимита ключа
    #: на минуту смены — проверяет смена (`set_ahrefs_cap`).
    units_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Кто поставил. Пусто — учётку удалили: строка истории остаётся и без неё.
    set_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("units_cap > 0", name="ck_ahrefs_caps_positive"),)


class AhrefsKeySnapshotModel(Base):
    """Снимок ключа Ahrefs — последний удачный ответ `limits-and-usage`: одна строка.

    Сервис владеет ситуацией по ключу, а не зашитыми числами (решение владельца
    16.10.2026): от размера ключа считается кап, от даты обнуления — цикл капа
    (`runs/ahrefs_key.py`). Строка переписывается каждым ответом, но не старшим: две
    записи разом не возвращают прошлого (`asked_at`). Пустое число — Ahrefs его не
    назвал: у ключа без своего лимита действует лимит пространства.
    """

    __tablename__ = "ahrefs_key_snapshot"

    #: Всегда 1: снимок один, новый ответ ложится на его место.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    key_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    key_used: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    workspace_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    workspace_used: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Следующее обнуление на минуту ответа; пусто — Ahrefs даты не назвал.
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Когда спросили — по нему экран пишет «проверено в», а запись не затирает новое старым.
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (CheckConstraint("id = 1", name="ck_ahrefs_key_snapshot_single"),)


class SuppressionModel(TimestampedMixin, Base):
    """Кому не пишем. Общий на оба этапа: один и тот же адресат не должен
    получить письмо и как донор, и как рекламодатель.

    Проверяется перед каждой отправкой, без исключений.
    """

    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int | None] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=True
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[SuppressionReason] = mapped_column(_enum(SuppressionReason), nullable=False)
    # Пусто = действует на обоих этапах.
    stage: Mapped[Stage | None] = mapped_column(_enum(Stage), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: До какого момента запись держит. Пусто — навсегда, и это умолчание:
    #: требование даёт срок только тем, кого мы внесли сами (размещались
    #: за последние 12 месяцев), а отписка и жалоба бессрочны.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_suppressions_domain_id", "domain_id"),
        Index("idx_suppressions_email", "email"),
    )

    @classmethod
    def in_force(cls, moment: datetime) -> ColumnElement[bool]:
        """Условие «запись ещё держит». Живёт рядом с полем намеренно.

        Читателей у стоп-листа пять — отбор прогона, очередь писем,
        отправка, перевод в рекламодатели и экран, — и забытый срок
        у любого из них означает письмо тому, кому писать нельзя,
        либо молчание тому, кому уже можно.
        """
        return or_(cls.expires_at.is_(None), cls.expires_at > moment)

    @classmethod
    def holds(
        cls, stage: Stage, moment: datetime, rule: ColumnElement[bool] | None = None
    ) -> ColumnElement[bool]:
        """Условие «запись держит этап `stage` в момент `moment`»: без этапа — все этапы,
        с этапом — только свой, истёкшая — никого (`in_force`). `rule` заменяет условие этапа
        своим (у пробы продаж — правило продаж, `cli/mail_test.py`); срок судится всегда.

        Рядом с полем по той же причине, что срок: правило одно у проверки перед отправкой
        (`letters/stoplist.holding`) и у очереди «Форм» (`runs/exclusions.stoplisted`, 28.10.2026).
        Форма — тоже письмо, и сайт, которому письмо не ушло бы, рук на «Формах» не ждёт.
        """
        staged = or_(cls.stage.is_(None), cls.stage == stage) if rule is None else rule
        return and_(staged, cls.in_force(moment))
