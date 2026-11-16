"""Схемы ответов плана работ: превью книги и планы для вкладки «Гипотезы».

Ключи листа в ответе — счётом и примерами, а не списком: книга в 300 тысяч ключей дала бы ответ в
десятки мегабайт. Деньги — числами (доллары), как у сметы прогона: экран складывает и сравнивает их с
потолком.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.features.sales.plan_estimate import HypothesisSpend, PlanEstimate, Spend
from backend.features.sales.plan_file import PlanHypothesis
from backend.features.sales.plan_store import PlanRow, PlanView

#: Примеров ключей листа в превью — взятых и отсеянных.
EXAMPLES = 5


class SpendView(BaseModel):
    """Проход сметы: запросы выдачи, результаты, сайты, выдача и модель в долларах, Hunter."""

    queries: int
    results: int
    sites: int
    serp_usd: float
    tokens: int
    tokens_usd: float
    hunter_searches: int
    hunter_checks: int
    #: Деньги вместе — выдача и модель.
    usd: float

    @classmethod
    def of(cls, spend: Spend) -> SpendView:
        return cls(
            queries=spend.queries,
            results=spend.results,
            sites=spend.sites,
            serp_usd=spend.serp_usd,
            tokens=spend.tokens,
            tokens_usd=spend.tokens_usd,
            hunter_searches=spend.hunter_searches,
            hunter_checks=spend.hunter_checks,
            usd=spend.usd,
        )


class SheetKeys(BaseModel):
    """Ключи листа: взято, убрано и отсеяно — счётом, и примеры."""

    taken: int
    blank: int
    repeats: int
    rejected: int
    examples: list[str]
    #: Отсеянный ключ → причина словами.
    rejected_examples: dict[str, str]


class SheetIssueView(BaseModel):
    """Ошибка листа: поле карточки (`null` — лист целиком) и слова."""

    field: str | None
    words: str


class SheetView(BaseModel):
    """Лист книги в превью: поля карточки, ключи, ошибки, предупреждения и смета диапазоном."""

    sheet: str
    name: str
    segment: str
    markets: list[str]
    language: str
    other_countries: str
    excluded_countries: list[str]
    target: int | None
    depth_pages: int
    keys: SheetKeys
    issues: list[SheetIssueView]
    warnings: list[str]
    #: Смета — у листа без ошибок: от топ-10 до предельной глубины; цель, общая или своя.
    low: SpendView | None = None
    high: SpendView | None = None
    goal: int | None = None

    @classmethod
    def of(
        cls, found: PlanHypothesis, spend: HypothesisSpend | None, chain: str | None
    ) -> SheetView:
        """Лист превью; `chain` — письма не соберутся (`plan_store.chain_gaps`), словами."""
        card, keys = found.card, found.keys
        warnings = [*found.warnings, *([chain] if chain else [])]
        if spend is not None and spend.warning:
            warnings.append(spend.warning)
        return cls(
            sheet=found.sheet,
            name=card.name,
            segment=card.segment,
            markets=list(card.markets),
            language=card.language,
            other_countries=card.other_countries.value,
            excluded_countries=list(card.excluded_countries),
            target=card.target,
            depth_pages=card.depth_pages,
            keys=SheetKeys(
                taken=len(keys.taken),
                blank=keys.blank,
                repeats=keys.repeats,
                rejected=len(keys.rejected),
                examples=list(keys.taken[:EXAMPLES]),
                rejected_examples=dict(list(keys.rejected.items())[:EXAMPLES]),
            ),
            issues=[
                SheetIssueView(
                    field=None if issue.field is None else issue.field.value, words=issue.words
                )
                for issue in found.issues
            ],
            warnings=warnings,
            low=None if spend is None else SpendView.of(spend.low),
            high=None if spend is None else SpendView.of(spend.high),
            goal=None if spend is None else spend.target,
        )


class PlanPreview(BaseModel):
    """Превью книги: листы, пропущенные листы, смета на план, почему не сохранить и отпечаток."""

    file: str
    sheets: list[SheetView]
    #: Лист → почему пропущен: инструкция, скрытый, пустой.
    skipped: dict[str, str]
    serp_mode: str
    low: SpendView
    high: SpendView
    #: Сайтов на результат выдачи и токенов судьи на сайт — из истории; 0 прогонов — запасные.
    share: float
    share_runs: int
    tokens_per_site: int
    tokens_per_site_runs: int
    #: Отказы настроек сметы словами.
    notes: list[str]
    #: Почему план не сохранить — словами; пусто — «Сохранить план» открыт.
    refusals: list[str]
    #: Отпечаток того, что человек видит: сохранение — только по нему.
    digest: str

    @classmethod
    def of(
        cls,
        name: str,
        sheets: list[SheetView],
        skipped: dict[str, str],
        estimate: PlanEstimate,
        refusals: list[str],
        digest: str,
    ) -> PlanPreview:
        return cls(
            file=name,
            sheets=sheets,
            skipped=skipped,
            serp_mode=estimate.serp_mode,
            low=SpendView.of(estimate.low),
            high=SpendView.of(estimate.high),
            share=estimate.share,
            share_runs=estimate.share_runs,
            tokens_per_site=estimate.tokens_per_site,
            tokens_per_site_runs=estimate.tokens_per_site_runs,
            notes=list(estimate.notes),
            refusals=refusals,
            digest=digest,
        )


class PlanHypothesisView(BaseModel):
    """Гипотеза плана на вкладке: лист, цель, глубина, потолок, этап и числа конвейера."""

    id: int
    hypothesis_id: int
    name: str
    position: int
    sheet: str
    markets: list[str]
    language: str
    target: int
    max_pages: int
    cap_usd: float
    stage: str
    found: int
    spent_usd: float
    #: Ключей в реестре (ключ × рынок) и из них купленных хотя бы до первой страницы.
    keywords: int
    bought: int

    @classmethod
    def of(cls, found: PlanRow) -> PlanHypothesisView:
        row = found.row
        return cls(
            id=row.id,
            hypothesis_id=row.hypothesis_id,
            name=found.name,
            position=row.position,
            sheet=row.sheet,
            markets=list(row.markets),
            language=row.language,
            target=row.target,
            max_pages=row.max_pages,
            cap_usd=float(row.cap_usd),
            stage=row.stage,
            found=row.found,
            spent_usd=float(row.spent_usd),
            keywords=found.keywords,
            bought=found.bought,
        )


class PlanCard(BaseModel):
    """План на вкладке: режим, цель, потолок, состояние и гипотезы по порядку листов."""

    id: int
    name: str
    mode: str
    target_default: int | None
    cap_usd: float
    status: str
    problem: str | None
    created_by: str | None
    created_at: datetime
    hypotheses: list[PlanHypothesisView]

    @classmethod
    def of(cls, view: PlanView) -> PlanCard:
        plan = view.plan
        return cls(
            id=plan.id,
            name=plan.name,
            mode=plan.mode,
            target_default=plan.target_default,
            cap_usd=float(plan.cap_usd),
            status=plan.status,
            problem=plan.problem,
            created_by=plan.created_by,
            created_at=plan.created_at,
            hypotheses=[PlanHypothesisView.of(row) for row in view.hypotheses],
        )


class PlansView(BaseModel):
    """Планы — новые сверху."""

    plans: list[PlanCard]
