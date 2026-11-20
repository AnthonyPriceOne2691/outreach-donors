"""Таблицы «Плана работ»: план, его гипотезы и реестр ключей — что загружено и докуда куплено.

Доработка 14.11.2026: одна книга задаёт работу на неделю или месяц (`plan_file.py`), а конвейер
собирает гипотезы плана по очереди с нарастающей глубиной выдачи. Здесь только хранение:

- **план** (`sales_plans`) — режим, общая цель, потолок плана в долларах «выдача + модель», смета
  книги, как её видел человек, и её отпечаток: запуск — только по нему (`plan_store.plan_digest`);
- **гипотеза плана** (`sales_plan_hypotheses`) — лист книги. Лист — это своя гипотеза продаж
  (ключ уникален: гипотеза в одном плане), рынки, язык писем, цель, предельная глубина и потолок
  гипотезы. Этап конвейера и числа прохода (найдено, израсходовано, резерв) пишет конвейер;
- **реестр ключей** (`sales_plan_keywords`) — ключ листа на каждом рынке: докуда куплена выдача
  (`pages`) и какой прогон её покупает. По нему углубление не платит второй раз за купленное.

Прогон сбора знает, чей он (`sales_segment_runs.plan_hypothesis_id`) и с какой страницы покупает
(`from_page`), — колонки прогона в `segment_models.py`. Потолки обязательны (решение владельца 16.11):
без них план не заводится. Деньги — `numeric`, а не `float`: их складывают и сравнивают с потолком.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.core.models._mixins import TimestampedMixin
from backend.features.sales.segment_models import AUTHOR_LENGTH, CODE_LENGTH
from backend.shared.database.base import Base

#: Имя плана и листа: имя файла или своё; лист Excel — до 31 знака, с запасом.
NAME_LENGTH = 255
#: Рынок выдачи — ISO-2 строчными, как у прогона сбора.
MARKET_LENGTH = 8
#: Отпечаток превью — шестнадцать шестнадцатеричных, как у сметы прогона.
DIGEST_LENGTH = 16
#: Потолок — доллары и центы, как их вписал человек.
CAP = Numeric(10, 2)
#: Расход — до сотой доли цента: страница выдачи стоит доли цента.
SPEND = Numeric(12, 4)


class PlanMode(StrEnum):
    """Режим плана — выбирается в окне загрузки, в книге его нет."""

    ECONOMY = "economy"  # выдача «стандарт», судья подешевле: медленнее, дешевле
    FAST = "fast"  # выдача Live: сразу, дороже


class PlanStatus(StrEnum):
    """Состояние плана: идёт, на паузе (потолок плана, счёт выдачи, модель, «Остановить»), кончен."""

    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"


class PlanStage(StrEnum):
    """Этап гипотезы плана; переходы — у конвейера."""

    WAITING = "waiting"  # ждёт своей очереди
    COLLECTING = "collecting"  # идут прогоны сбора
    SENDING = "sending"  # цель набрана, лиды уходят в письма
    DONE = "done"  # первые письма ушли
    STOPPED = "stopped"  # новых прогонов нет, причина — в проблемах


class SalesPlanModel(TimestampedMixin, Base):
    """План работ: книга, загруженная одной кнопкой, и как её запускать."""

    __tablename__ = "sales_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME_LENGTH), nullable=False)
    #: `PlanMode` строкой.
    mode: Mapped[str] = mapped_column(String(CODE_LENGTH), nullable=False)
    #: Общая цель «Найти сайтов» — для листов без своей; пусто — у каждого листа своя.
    target_default: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Потолок плана в долларах «выдача + модель»: дошёл — план на паузу.
    cap_usd: Mapped[Decimal] = mapped_column(CAP, nullable=False)
    #: `PlanStatus` строкой.
    status: Mapped[str] = mapped_column(
        String(CODE_LENGTH), nullable=False, default=PlanStatus.RUNNING
    )
    #: Почему план стоит — словами человека; пусто — не стоит.
    problem: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Смета книги на запуске — числа, как их видел человек (`plan_estimate.PlanEstimate`).
    estimate: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    digest: Mapped[str] = mapped_column(String(DIGEST_LENGTH), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(AUTHOR_LENGTH), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SalesPlanHypothesisModel(TimestampedMixin, Base):
    """Лист плана: своя гипотеза продаж, что и как собирать, и где конвейер."""

    __tablename__ = "sales_plan_hypotheses"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("sales_plans.id", ondelete="RESTRICT"), nullable=False
    )
    #: Лист — это гипотеза продаж; гипотеза — не больше чем в одном плане.
    hypothesis_id: Mapped[int] = mapped_column(
        ForeignKey("sales_hypotheses.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    #: Порядок листа в книге, с 1: конвейер идёт слева направо.
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sheet: Mapped[str] = mapped_column(String(NAME_LENGTH), nullable=False)
    #: «Где ищем» — рынки выдачи, ISO-2 строчными; прогон — на каждый рынок.
    markets: Mapped[list[str]] = mapped_column(ARRAY(String(MARKET_LENGTH)), nullable=False)
    #: «Язык писем» — код цепочки.
    language: Mapped[str] = mapped_column(String(CODE_LENGTH), nullable=False)
    #: Сколько сайтов, прошедших судью, набрать: своя цель листа или общая.
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    #: «Глубина поиска до» — страниц выдачи по 10: 1, 2, 3 или 5.
    max_pages: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: Потолок гипотезы в долларах «выдача + модель»: дошла — `stopped`, «кончился бюджет».
    cap_usd: Mapped[Decimal] = mapped_column(CAP, nullable=False)
    #: `PlanStage` строкой.
    stage: Mapped[str] = mapped_column(
        String(CODE_LENGTH), nullable=False, default=PlanStage.WAITING
    )
    #: Страница выдачи, которую покупает волна: 1, 2, 3 или 4 (страницы 4–5 — одна волна).
    wave_page: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    #: Проблемы словами — список `{code, words, at}`; этап не меняют.
    problems: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: Найдено сайтов, прошедших судью, — счёт конвейера; решение человека по сайту прогона плана
    #: пересчитывает его сразу (`plan_found.recount`).
    found: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    spent_usd: Mapped[Decimal] = mapped_column(
        SPEND, nullable=False, default=Decimal(0), server_default="0"
    )
    #: Резерв на идущий прогон — его смета, пока факт меньше.
    reserved_usd: Mapped[Decimal] = mapped_column(
        SPEND, nullable=False, default=Decimal(0), server_default="0"
    )
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("plan_id", "position", name="uq_sales_plan_hypotheses_position"),
    )


class SalesPlanKeywordModel(Base):
    """Ключ листа на рынке: докуда куплена выдача. Строк — до 300 тысяч на план, без времени
    строки: когда куплено, знает прогон (`run_id`)."""

    __tablename__ = "sales_plan_keywords"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plan_hypothesis_id: Mapped[int] = mapped_column(
        ForeignKey("sales_plan_hypotheses.id", ondelete="CASCADE"), nullable=False
    )
    market: Mapped[str] = mapped_column(String(MARKET_LENGTH), nullable=False)
    #: Ключ, как его купит выдача (`plan_file.cleaned`): нормализованный.
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    #: Чем ключ является для выдачи (`serp/protocol.keyword_key`): по нему ключ уникален.
    keyword_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: Порядок ключа на листе после чистки, с 1: волна берёт ключи по порядку.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Последняя купленная страница выдачи; 0 — ещё не покупали.
    pages: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    #: Прогон, который покупает следующую страницу; прогон удалён — пусто.
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_segment_runs.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "plan_hypothesis_id", "market", "keyword_key", name="uq_sales_plan_keywords_key"
        ),
        Index("idx_sales_plan_keywords_pages", "plan_hypothesis_id", "market", "pages"),
        Index("idx_sales_plan_keywords_run", "run_id"),
    )
