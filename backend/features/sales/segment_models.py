"""Сбор новой базы для гипотезы (срез 6.1): прогон, кандидаты сбора и вердикты судьи сегмента.

Свои таблицы модуля, отдельным файлом: `models.py` у предела длины. Реестр моделей
(`core/models/__init__.py`) импортирует их рядом с остальными моделями продаж.

**Кандидат — домен × гипотеза.** Один домен в гипотезе судится и ищет адрес один раз:
второй прогон той же гипотезы, нашедший тот же сайт, его не заводит и не платит за него
снова, а называет в отчёте «уже в сборе гипотезы». У кандидата — колонки поиска адреса
общей схемы (`ContactAttemptMixin`): правило «кому пора искать» и запись исхода — общие
(`contacts/attempts.py`), без копии (договорённость с соседней сессией 01.10).

**Вердикт судьи — своя таблица, а не колонки `domains.judge_*`:** те — у судьи площадки
доноров, и вопрос там другой («продаёт ли размещение»). Ключ вердикта — домен, гипотеза,
версия промпта и отпечаток описания сегмента: правка описания гипотезы — другой вопрос
модели, и прежний ответ на него не годится. Ответа модели нет (сбой, пустой ответ) —
строки нет: такой «на проверку» не кэшируется, и следующий проход судит домен снова.

**Что держит строки.** Домен уходит только чисткой прогонов доноров, когда его не держит
ничто (`runs/prune.py`): кандидат и вердикт уходят с ним каскадом — их снова даст выдача
и судья, а лида домен держит сам (`sales_leads.domain_id` — `RESTRICT`). Лид кандидата
уходит только чисткой пробы продаж — ссылка гаснет. Гипотезу с прогоном удалить нельзя.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.core.models._mixins import ContactAttemptMixin, TimestampedMixin
from backend.shared.database.base import Base

#: Ширина кодов полосы, решившего и режима: короткие слова, не абзацы.
CODE_LENGTH = 16
#: Ширина имени модели и версии промпта.
MODEL_LENGTH = 64
#: Кто запустил или решил: почта сотрудника.
AUTHOR_LENGTH = 255


class Lane(StrEnum):
    """Полоса сайта: три полосы судьи и «ещё не судили». Строкой, а не типом базы."""

    PENDING = "pending"  # не судили или модель не ответила — следующий проход судит снова
    ACCEPT = "accept"  # подходит: дальше поиск адреса и лид
    REVIEW = "review"  # решает человек
    REJECT = "reject"  # не подходит: отсечён


class Decider(StrEnum):
    """Кто поставил полосу. Точность меряется по каждому отдельно."""

    RULE = "rule"  # правило до модели: стоп-лист, «кому не пишем» по имени, пустой текст, отказ
    MODEL = "model"  # модель по тексту сайта
    HUMAN = "human"  # сотрудник на экране прогона


class Stage(StrEnum):
    """Где прогон сейчас: этап, в который он вошёл, — с начала работы этапа, а не после
    (тест прогона 14.10: выдача шла шесть минут, а экран стоял «в очереди»). Ход этапа —
    в `report.progress` (`segment_progress.py`). Строкой — новый этап не требует миграции."""

    QUEUED = "queued"  # поставлен, задача ещё не бралась
    SERP = "serp"  # покупаем выдачу (куплена ли — `report.serp`, `segment_serp.bought`)
    JUDGE = "judge"  # судит судья сегмента
    CONTACTS = "contacts"  # ищем адреса принятым
    LEADS = "leads"  # заводим лидов и чистим их
    DONE = "done"  # лиды заведены и очищены
    STOPPED = "stopped"  # остановлен с причиной (`error`): потолок модели, отказ провайдера


class SalesSegmentRunModel(TimestampedMixin, Base):
    """Прогон гипотезы: ключи, страна, глубина, смета на старте и итог по этапам."""

    __tablename__ = "sales_segment_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    hypothesis_id: Mapped[int] = mapped_column(
        ForeignKey("sales_hypotheses.id", ondelete="RESTRICT"), nullable=False
    )
    #: Ключи после гигиены и дедупа — ровно те, за которые платит выдача.
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    #: Рынок выдачи — ISO-2 нижним регистром, как `donors.geo`.
    country: Mapped[str] = mapped_column(String(8), nullable=False)
    #: Язык писем лидам сбора (`ru`, `en` — языки цепочки): у рынка выдачи он бывает другим.
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    depth_pages: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: Режим судьи на запуске (`JudgeMode` строкой): отчёт называет, резал ли он.
    mode: Mapped[str] = mapped_column(String(CODE_LENGTH), nullable=False)
    #: Смета на запуске — числа и слова, как их видел человек.
    estimate: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: Числа этапов: выдача, судья, поиск адреса, лиды, очистка.
    report: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    stage: Mapped[str] = mapped_column(String(CODE_LENGTH), nullable=False, default=Stage.QUEUED)
    job_id: Mapped[str | None] = mapped_column(String(MODEL_LENGTH), nullable=True)
    #: Почему прогон остановлен — словами человека. Пусто — не останавливался.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(AUTHOR_LENGTH), nullable=True)
    #: Когда кончился последний проход — готово или остановлен. Пусто — проход идёт или
    #: поставлен продолжением (`api/sales/segment.resume_run`).
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("idx_sales_segment_runs_hypothesis", "hypothesis_id"),)
    # `updated_at` — пульс прогона, его читает экран (`RunRow.alive_at`): значение базы
    # приходит в той же записи (RETURNING), а не просроченным полем, которое асинхронная
    # сессия догрузить на чтении не может.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 — так SQLAlchemy читает его


class SalesSegmentCandidateModel(TimestampedMixin, ContactAttemptMixin, Base):
    """Сайт из выдачи прогона: что о нём написала выдача, что сказал судья и человек,
    чем кончился поиск адреса и какой лид из него вышел."""

    __tablename__ = "sales_segment_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Прогон, который нашёл домен первым. Прогон удалён — его кандидаты с ним.
    run_id: Mapped[int] = mapped_column(
        ForeignKey("sales_segment_runs.id", ondelete="CASCADE"), nullable=False
    )
    hypothesis_id: Mapped[int] = mapped_column(
        ForeignKey("sales_hypotheses.id", ondelete="RESTRICT"), nullable=False
    )
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    #: Ключ, по которому домен нашёлся выше всего, и его страница в выдаче.
    keyword: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Текст, который видел судья: страница выдачи и главная. По нему сверяется цитата.
    site_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Полоса после режима и человека (`Lane`).
    lane: Mapped[str] = mapped_column(String(CODE_LENGTH), nullable=False, default=Lane.PENDING)
    #: Что сказал судья сам (`Lane` без `pending`). Пусто — вердикта нет: не судили,
    #: модель не ответила или судья выключен.
    verdict: Mapped[str | None] = mapped_column(String(CODE_LENGTH), nullable=True)
    #: Почему такая полоса — словами: причина судьи, правила, режима или человека.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Слова сайта, на которых стоит вердикт, — найденные в тексте дословно.
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Кто поставил полосу (`Decider`).
    decided_by: Mapped[str | None] = mapped_column(String(CODE_LENGTH), nullable=True)
    model: Mapped[str | None] = mapped_column(String(MODEL_LENGTH), nullable=True)
    #: Модель не ответила: вердикта нет, следующий проход судит снова.
    unanswered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Решение человека по «на проверку»: кто и когда.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_user: Mapped[str | None] = mapped_column(String(AUTHOR_LENGTH), nullable=True)
    #: Лид, заведённый из кандидата. Лид убран чисткой пробы — ссылка гаснет.
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_leads.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("domain_id", "hypothesis_id", name="uq_sales_segment_candidates_domain"),
        # Отчёт прогона и очередь судьи читают кандидатов прогона по полосе.
        Index("idx_sales_segment_candidates_run_lane", "run_id", "lane"),
        # Без индекса обнуление ссылки читало бы всю таблицу на каждом удалении лида.
        Index("idx_sales_segment_candidates_lead", "lead_id"),
        Index("idx_sales_segment_candidates_hypothesis", "hypothesis_id"),
    )


class SalesSegmentVerdictModel(TimestampedMixin, Base):
    """Вердикт судьи сегмента: домен, гипотеза, версия промпта, вердикт, цитата, причина,
    модель и дата (`created_at`). Кэш судьи и мерило его точности против человека."""

    __tablename__ = "sales_segment_verdicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    hypothesis_id: Mapped[int] = mapped_column(
        ForeignKey("sales_hypotheses.id", ondelete="RESTRICT"), nullable=False
    )
    #: Версия файла промпта (`segment_judge.PROMPT_VERSION`).
    prompt_version: Mapped[str] = mapped_column(String(MODEL_LENGTH), nullable=False)
    #: Отпечаток описания сегмента: правка описания — новый вопрос модели.
    segment_digest: Mapped[str] = mapped_column(String(CODE_LENGTH), nullable=False)
    #: `Lane` без `pending`: принять, на проверку, отказ.
    verdict: Mapped[str] = mapped_column(String(CODE_LENGTH), nullable=False)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: Модель; пусто — решило правило до модели.
    model: Mapped[str | None] = mapped_column(String(MODEL_LENGTH), nullable=True)
    decided_by: Mapped[str] = mapped_column(String(CODE_LENGTH), nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint(
            "domain_id",
            "hypothesis_id",
            "prompt_version",
            "segment_digest",
            name="uq_sales_segment_verdicts_key",
        ),
        Index("idx_sales_segment_verdicts_hypothesis", "hypothesis_id"),
    )


class SalesSegmentHelpModel(TimestampedMixin, Base):
    """Ответ помощника описания сегмента (`segment_helper.py`): черновик человека → описание для
    судьи, вопросы человеку и что в черновике собьёт судью. Кэш: тот же черновик под той же версией
    промпта и моделью второй раз не оплачивается; ответа модели нет (сбой, пустой или негодный
    ответ) — строки нет. Черновик хранится текстом — по нему видно, что пишут люди и что им
    предложено (докрутка промпта на накопленном). Гипотезы строка не держит: черновик из окна
    новой гипотезы ещё ничей."""

    __tablename__ = "sales_segment_helps"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Версия файла промпта (`segment_helper.PROMPT_VERSION`).
    prompt_version: Mapped[str] = mapped_column(String(MODEL_LENGTH), nullable=False)
    model: Mapped[str] = mapped_column(String(MODEL_LENGTH), nullable=False)
    #: Отпечаток черновика — тем же правилом, что у описания в вердикте (`segment_digest`).
    draft_digest: Mapped[str] = mapped_column(String(CODE_LENGTH), nullable=False)
    draft: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: Вопросы человеку — списком строк, пустой — открытого нет.
    unclear: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: Что в черновике собьёт судью; пустой — черновик не расплывчатый.
    problems: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint(
            "prompt_version", "model", "draft_digest", name="uq_sales_segment_helps_key"
        ),
    )
