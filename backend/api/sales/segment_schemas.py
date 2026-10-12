"""Схемы экрана «Прогон гипотезы»: форма, смета, прогон и его отчёт, решение человека."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.config import serp as serp_cfg
from backend.features.sales.segment_estimate import Estimate
from backend.features.sales.segment_models import SalesSegmentCandidateModel, SalesSegmentRunModel
from backend.features.sales.segment_report import RunView, summary

#: Длина набора ключей в теле: сто ключей по восьми словам — с большим запасом.
KEYWORDS_LENGTH = 20_000
#: Длина описания сегмента: абзац-другой, не документ.
SEGMENT_LENGTH = 4000


class SegmentAsk(BaseModel):
    """Запрос сметы и запуска: гипотеза, ключи строками, рынок, язык писем, глубина."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: int = Field(ge=1)
    keywords: str = Field(max_length=KEYWORDS_LENGTH, description="по ключу на строку")
    country: str = Field(min_length=2, max_length=8, description="рынок выдачи, ISO-2")
    language: str | None = Field(
        default=None, max_length=16, description="язык писем: ru или en; пусто — язык рынка"
    )
    depth_pages: int = Field(default=1, ge=1, le=100, description="страниц выдачи по 10")


class SegmentBody(BaseModel):
    """Описание сегмента гипотезы — его читает судья сегмента."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(max_length=SEGMENT_LENGTH)


class SegmentCard(BaseModel):
    hypothesis_id: int
    description: str | None


class EstimateView(BaseModel):
    """Смета словами и числами; `refusal` — запускать нельзя, и почему."""

    keywords: list[str]
    rejected: dict[str, str]
    country: str
    language: str
    depth_pages: int
    mode: str
    results: int
    sites: int
    serp_usd: float
    tokens: int
    tokens_left: int | None
    hunter_searches: int
    hunter_checks: int
    lines: list[str]
    refusal: str | None

    @classmethod
    def of(cls, found: Estimate) -> EstimateView:
        return cls(
            keywords=found.keywords,
            rejected=found.rejected,
            country=found.country,
            language=found.language,
            depth_pages=found.depth_pages,
            mode=found.mode,
            results=found.results,
            sites=found.sites,
            serp_usd=found.serp_usd,
            tokens=found.tokens,
            tokens_left=found.tokens_left,
            hunter_searches=found.hunter_searches,
            hunter_checks=found.hunter_checks,
            lines=found.lines,
            refusal=found.refusal,
        )


class RunRow(BaseModel):
    """Строка списка прогонов гипотезы."""

    id: int
    created_at: datetime
    finished_at: datetime | None
    created_by: str | None
    country: str
    language: str
    depth_pages: int
    keywords: int
    mode: str
    stage: str
    error: str | None
    job_id: str | None

    @classmethod
    def of(cls, run: SalesSegmentRunModel) -> RunRow:
        return cls(
            id=run.id,
            created_at=run.created_at,
            finished_at=run.finished_at,
            created_by=run.created_by,
            country=run.country,
            language=run.language,
            depth_pages=run.depth_pages,
            keywords=len(run.keywords),
            mode=run.mode,
            stage=run.stage,
            error=run.error,
            job_id=run.job_id,
        )


class SegmentHome(BaseModel):
    """Вкладка сбора по гипотезе: описание сегмента, границы формы и прогоны."""

    hypothesis_id: int
    segment: str | None
    mode: str
    countries: list[str]
    languages: list[str]
    max_keywords: int = serp_cfg.MAX_KEYWORDS_PER_RUN
    max_depth: int = serp_cfg.MAX_DEPTH_PAGES
    runs: list[RunRow]


class CandidateRow(BaseModel):
    """Сайт «на проверку»: что о нём сказала выдача, что — судья, и почему он ждёт человека."""

    id: int
    host: str
    page_url: str | None
    page_title: str | None
    keyword: str | None
    lane: str
    verdict: str | None
    reason: str | None
    quote: str | None
    unanswered: bool
    decided_by_user: str | None

    @classmethod
    def of(cls, candidate: SalesSegmentCandidateModel, host: str) -> CandidateRow:
        return cls(
            id=candidate.id,
            host=host,
            page_url=candidate.page_url,
            page_title=candidate.page_title,
            keyword=candidate.keyword,
            lane=candidate.lane,
            verdict=candidate.verdict,
            reason=candidate.reason,
            quote=candidate.quote,
            unanswered=candidate.unanswered,
            decided_by_user=candidate.decided_by_user,
        )


class Count(BaseModel):
    """Причина словами и сколько."""

    words: str
    count: int


class RunDetail(BaseModel):
    """Прогон и его отчёт: смета на запуске, этап, полосы, причины, адреса, лиды, «на проверку»."""

    run: RunRow
    hypothesis: str
    keywords: list[str]
    estimate_lines: list[str]
    #: Главные числа: сайтов, принято, на проверку, отсечено, без адреса, лидов.
    summary: dict[str, int]
    lanes: dict[str, int]
    rejected: list[Count]
    no_address: list[Count]
    waiting_address: int
    leads: dict[str, int]
    lead_reasons: dict[str, int]
    unanswered: int
    #: Итог этапов из строки прогона: выдача, судья, поиск адреса, лиды.
    report: dict[str, object]
    review: list[CandidateRow]

    @classmethod
    def of(cls, view: RunView) -> RunDetail:
        run = view.run
        return cls(
            run=RunRow.of(run),
            hypothesis=view.hypothesis,
            keywords=list(run.keywords),
            estimate_lines=list((run.estimate or {}).get("lines", [])),
            summary=summary(view),
            lanes=view.lanes,
            rejected=[Count(words=words, count=count) for words, count in view.rejected],
            no_address=[Count(words=words, count=count) for words, count in view.no_address],
            waiting_address=view.waiting_address,
            leads=view.leads,
            lead_reasons=view.lead_reasons,
            unanswered=view.unanswered,
            report=dict(run.report or {}),
            review=[CandidateRow.of(candidate, host) for candidate, host in view.review],
        )


class RunQueued(BaseModel):
    """Прогон поставлен задачей: ход — по строке задачи, отчёт — по прогону."""

    run_id: int
    job_id: str


class DecisionBody(BaseModel):
    """Решение человека по сайту «на проверку»."""

    model_config = ConfigDict(extra="forbid")

    accept: bool
