"""CrawlRunModel и OutLinkModel — обход донора и то, что он нашёл.

**Сырой HTML не хранится** — так требует требование. Страницы живут
в памяти ровно столько, сколько нужно снять с них ссылки; в базу едут
адрес, анкор, пометки `rel` и домен-получатель.

**Запись обхода нужна не для истории, а для честности.** Без неё нельзя
отличить «у донора нет исходящих ссылок» от «мы не смогли его обойти»:
в обоих случаях строк со ссылками ноль. Исход, причина остановки и то,
какой уровень каскада не поднялся, лежат здесь — и это то, чего срез
обхода был должен и не отдал.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason
from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


def _enum(e: type) -> SQLEnum:
    return SQLEnum(e, values_callable=lambda x: [i.value for i in x])


class CrawlRunModel(TimestampedMixin, Base):
    """Один обход одного донора: чем кончился и что успел."""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)

    # Обход идёт до часа, поэтому у него есть жизнь между нажатием и исходом:
    # строка заводится постановкой в очередь, а исход и причина остановки
    # появляются только у законченного (до того — `NULL`, а не выдуманный исход).
    status: Mapped[CrawlStatus] = mapped_column(
        _enum(CrawlStatus), nullable=False, default=CrawlStatus.DONE
    )
    outcome: Mapped[CrawlOutcome | None] = mapped_column(_enum(CrawlOutcome), nullable=True)
    stop_reason: Mapped[StopReason | None] = mapped_column(_enum(StopReason), nullable=True)

    # Задача очереди, которая ведёт обход: по ней разбор мёртвых узнаёт,
    # жив ли воркер. Кто поставил — для журнала и экрана.
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Сколько раз обход продолжали после смерти воркера. Остановка выкаткой
    # сюда не считается — она штатная и сама ставит продолжение.
    resumes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # С чего продолжить: открытые страницы, очередь адресов, счётчики
    # (`crawl/progress.py`). Пишется пачками по ходу вместе со ссылками.
    # `none_as_null`: стёртый чекпоинт — пустое поле, а не JSON `null` (иначе
    # `checkpoint IS NULL` врёт про законченный обход; замечено живым обходом).
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )

    pages_opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    articles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Числа прохода: запросы, отказы, доли, время, источник списка страниц.
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Чего каскаду не хватило. В записи, а не в логе: иначе обход,
    # у которого не поднялся браузер, выглядит зелёным и врёт, что
    # закрытые страницы проверены.
    degradation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("idx_crawl_runs_host", "host"),
        Index("idx_crawl_runs_created_at", "created_at"),
        # Донор — один обход за раз. Замок держит база, а не код: две кнопки,
        # нажатые разом, или кнопка поверх идущего обхода иначе завели бы
        # два обхода одного сайта с двойной нагрузкой на него и двойными ссылками.
        Index(
            "uq_crawl_runs_active_host",
            "host",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    links: Mapped[list[OutLinkModel]] = relationship(
        "OutLinkModel", back_populates="run", cascade="all, delete-orphan"
    )


class OutLinkModel(TimestampedMixin, Base):
    """Исходящая ссылка донора: откуда, куда, каким анкором и с какими пометками."""

    __tablename__ = "outlinks"

    id: Mapped[int] = mapped_column(primary_key=True)
    crawl_run_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=False
    )

    # Адреса — `Text`: путь со списком параметров в 255 символов
    # не укладывается, а обрезанный адрес нельзя ни открыть, ни сверить.
    page_url: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    target_host: Mapped[str] = mapped_column(String(255), nullable=False)
    target_root: Mapped[str] = mapped_column(String(255), nullable=False)

    # Две формы анкора: показываемая человеку и ключ сравнения без
    # регистра и диакритики. Одной не хватает — см. okf/crawl-access.md
    # и грабли в `features/crawl/links.py`.
    anchor: Mapped[str] = mapped_column(Text, nullable=False, default="")
    anchor_key: Mapped[str] = mapped_column(Text, nullable=False, default="")

    nofollow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sponsored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ugc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Ссылка стояла в теле статьи или рядом с ним. Не фильтр, а признак:
    # размещения боевой ниши живут в витринах офферов, и выбросить
    # их значит не найти никого.
    in_body: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Корень домена угадан: суффикс неизвестен вшитому снимку списка.
    # Ноль — норма, рост — повод обновить список, а не тихая потеря.
    root_guessed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Про статью, а не про ссылку: чем она помечена сама («рубрика
    # sponsored-post», «This post is sponsored by…») и когда вышла.
    # У обходов до 06.10.2026 — `NULL`: не смотрели, а не «пометки нет».
    page_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_published: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        Index("idx_outlinks_run", "crawl_run_id"),
        Index("idx_outlinks_target_root", "target_root"),
    )

    run: Mapped[CrawlRunModel] = relationship("CrawlRunModel", back_populates="links")
