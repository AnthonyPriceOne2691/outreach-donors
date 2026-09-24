"""DomainModel — каноничная запись о сайте.

Якорь всей схемы. Отдельная сущность, а не поле донора, по одной причине:
один и тот же сайт бывает донором в Этапе 1 и рекламодателем в Этапе 2.
Сквозной стоп-лист адресатов и дедупликация держатся на этом уровне.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base

if TYPE_CHECKING:
    from backend.features.core.models.donor import ContactModel, DonorModel


class DomainModel(TimestampedMixin, Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Корневой домен без протокола, www и поддоменов — ключ дедупликации.
    host: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)

    # --- судья площадки -----------------------------------------------------
    #
    # ⚠ Здесь, а не у донора. Способ заработка — свойство САЙТА, и второму
    # этапу он нужен с обратным знаком: `sells_own` для донора отказ, а для
    # поиска рекламодателей — лучший кандидат. Отметка времени даёт кэш
    # даром: повторный прогон домен не пересуживает.
    site_intent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    judge_recommendation: Mapped[str | None] = mapped_column(String(8), nullable=True)
    judge_quote: Mapped[str | None] = mapped_column(String(512), nullable=True)
    judge_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    judge_source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    judge_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Версия промпта судьи. Точность против человека считается по версии:
    # пересуд меняет не всех, и без отметки старое смешается с новым.
    judge_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Кто решил: правило, модель или арбитр. Без этого точность судьи —
    # одно число на всех, и не видно, какой слой ошибается.
    judge_decided_by: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Что сказала главная: открылась ли и какие признаки магазина нашлись.
    judge_home: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Где сайт сам зовёт авторов или рекламодателей: страница «write for us»
    # из выдачи, её заголовок, пункт меню главной «Advertise» — словами,
    # как увидит человек (`donors.author_door`). Для гест-постинга это
    # признак «продаёт размещение у себя», и очередь рассмотрения ставит
    # таких первыми. NULL — не смотрели; пустая строка — смотрели, двери нет.
    site_door: Mapped[str | None] = mapped_column(String(256), nullable=True)
    judged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- ответ самого донора ------------------------------------------------
    #
    # Продаёт ли сайт размещение — по его собственному ответу на письмо:
    # `sells` (назвал цену или сказал, что продаёт), `declines` («не продаём»).
    # Для гест-постинга это правда первого сорта, сильнее и судьи, и
    # человека: сайт сам сказал. Отдельно от обоих — по расхождению с ними
    # и считается, как часто отбор ошибается в главном.
    seller_answer: Mapped[str | None] = mapped_column(String(16), nullable=True)
    seller_answer_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    seller_answer_reply_id: Mapped[int | None] = mapped_column(nullable=True)

    # --- решение человека ---------------------------------------------------
    #
    # Сильнее модели, но её вердикт НЕ переписывает: расхождение между ними
    # и есть измеритель того, как часто она ошибается.
    human_intent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    human_verdict_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    human_note: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        Index("idx_domains_host", "host"),
        Index("idx_domains_judge_recommendation", "judge_recommendation"),
    )

    donor: Mapped[DonorModel | None] = relationship(
        "DonorModel", back_populates="domain", uselist=False
    )
    contacts: Mapped[list[ContactModel]] = relationship(
        "ContactModel", back_populates="domain", cascade="all, delete-orphan"
    )
