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

**Решение и спрошенный DR переживают и повтор обхода** (`CandidateMemoryModel`,
решение владельца 28.10.2026): строки кандидатов — по последнему обходу донора,
а память — по паре «донор + домен», пока обход не найдёт домен снова.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.features.core.domain import Verdict
from backend.features.core.models._mixins import TimestampedMixin
from backend.features.core.models._mixins import sql_enum as _enum
from backend.shared.database.base import Base


class CarriedMixin:
    """Решение человека и спрошенный DR — то, что переносится через пересчёт и повтор обхода.

    Колонки одни у кандидата и у памяти донора: перенос копирует их поимённо
    (`crawl/gate.py`), и разойдись они — перенос молча терял бы поле.
    """

    # Решение человека: подтверждено, отклонено или ещё не смотрели.
    # Оно сильнее вердикта скоринга и хранится отдельно от него.
    confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # DR домена и когда его спросили (`crawl/big_sites.py`, «DR > 80 — не
    # пишем»). Переносится при пересчёте, как решение человека: пересчёт
    # весов не должен стоить юнитов. `dr_checked_at` пусто — не спрашивали;
    # заполнено при пустом `dr` — спросили, а провайдер домена не знает.
    dr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dr_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


#: Колонки `CarriedMixin` поимённо: их переносит пересчёт и пишет память (`crawl/gate.py`).
CARRIED = ("confirmed", "decided_by", "decided_at", "dr", "dr_checked_at")


class CandidateModel(TimestampedMixin, CarriedMixin, Base):
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

    __table_args__ = (
        Index("idx_candidates_run", "crawl_run_id"),
        Index("idx_candidates_verdict", "verdict"),
        Index("idx_candidates_target_root", "target_root"),
    )

    run: Mapped[Any] = relationship("CrawlRunModel", backref="candidates")


class CandidateMemoryModel(TimestampedMixin, CarriedMixin, Base):
    """Память донора о домене: решение человека и спрошенный DR — сквозь его обходы.

    Решение владельца 28.10.2026: «Переобойти» — у последнего обхода каждого донора,
    и повтор стал обычным делом. Кандидаты — по последнему обходу (`crawl/gate.py`),
    и повтор, не нашедший домена, стирал вместе с его строкой решение человека
    и оплаченный DR. Здесь они ждут, пока обход не найдёт домен снова.

    **Не кандидат.** Списки, перевод, приёмка и проверки офферов её не читают —
    в них только то, что нашёл последний обход. Читает и пишет её один пересчёт.

    **Без внешних ключей**, как `donor_host` кандидата: память — о паре имён, а не
    об обходе. Обход уносит своих кандидатов каскадом, память остаётся — ради этого
    она и заведена. Чистки доменов (`runs/prune.py`, `prune --probes`) кандидатов
    не трогают — и память тоже: оба держат домен именем, а не строкой `domains`.
    """

    __tablename__ = "candidate_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    donor_host: Mapped[str] = mapped_column(String(255), nullable=False)
    target_root: Mapped[str] = mapped_column(String(255), nullable=False)

    # Пара уникальна: два пересчёта одного донора разом сходятся на одной записи.
    __table_args__ = (
        UniqueConstraint("donor_host", "target_root", name="uq_candidate_memory_pair"),
    )
