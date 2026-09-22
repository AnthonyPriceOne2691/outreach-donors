"""CandidateModel — домен-получатель с баллом, вердиктом и причинами.

Единица решения здесь **домен, а не ссылка**: письмо уходит владельцу
один раз, сколько бы страниц донора он ни занимал. Ссылки остаются
в `outlinks` и объясняют балл; сюда едет итог.

**Причины хранятся текстом, а не пересчитываются.** Веса скоринга будут
меняться — они выведены из замера на пяти донорах и заведомо не
окончательные. Вердикт, вынесенный старыми весами, должен остаться
объяснимым после их правки: иначе разбор спорного случая через месяц
упирается в «балл 3, почему — неизвестно».

**Решение человека хранится отдельно от вердикта модели.** Перезаписав
балл подтверждением, мы потеряли бы то, по чему видно, часто ли скоринг
ошибается, — а допуск по ложным рекламодателям десять процентов,
и мерить их придётся.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.features.core.domain import Verdict
from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


def _enum(e: type) -> SQLEnum:
    return SQLEnum(e, values_callable=lambda x: [i.value for i in x])


class CandidateModel(TimestampedMixin, Base):
    """Кандидат в рекламодатели по итогам одного обхода."""

    __tablename__ = "advertiser_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    crawl_run_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=False
    )

    donor_host: Mapped[str] = mapped_column(String(255), nullable=False)
    target_root: Mapped[str] = mapped_column(String(255), nullable=False)

    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verdict: Mapped[Verdict] = mapped_column(_enum(Verdict), nullable=False)
    reasons: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)

    links: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Лучшая ссылка — та, по которой письмо будет персонализировано:
    # требование просит писать «под конкретную найденную ссылку —
    # страницу и анкор».
    best_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    best_anchor: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Решение человека: подтверждено, отклонено или ещё не смотрели.
    # Оно сильнее вердикта скоринга и хранится отдельно от него.
    confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_candidates_run", "crawl_run_id"),
        Index("idx_candidates_verdict", "verdict"),
        Index("idx_candidates_target_root", "target_root"),
    )

    run: Mapped[Any] = relationship("CrawlRunModel", backref="candidates")
