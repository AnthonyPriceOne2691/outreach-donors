"""Схемы экрана «Прогон гипотезы»: форма, смета, прогон и его отчёт, решение человека."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.api.sales.segment_waits import WaitRow
from backend.config import serp as serp_cfg
from backend.features.sales import segment_progress
from backend.features.sales.segment_estimate import Estimate
from backend.features.sales.segment_estimate_keys import run_excluded
from backend.features.sales.segment_keywords import run_keywords
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_report import KeySites, RunView, summary
from backend.features.sales.segment_spend import spend_rows
from backend.features.sales.segment_watch import GOING, Watch
from backend.features.serp.modes import UnknownModeError, mode_of

logger = logging.getLogger(__name__)

#: Длина набора ключей в теле: сто ключей по восьми словам — с большим запасом.
KEYWORDS_LENGTH = 20_000
#: Длина описания сегмента: абзац-другой, не документ.
SEGMENT_LENGTH = 4000
#: Номер строки ключа: в наборе предельной длины строка с ключом не дальше знака с тем же номером.
LineNumber = Annotated[int, Field(ge=1, le=KEYWORDS_LENGTH)]


class SegmentAsk(BaseModel):
    """Запрос сметы и запуска: гипотеза, ключи строками, рынок, язык писем, глубина, режим
    покупки выдачи и снятые человеком ключи — номерами строк, как вписаны."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: int = Field(ge=1)
    keywords: str = Field(max_length=KEYWORDS_LENGTH, description="по ключу на строку")
    country: str = Field(min_length=2, max_length=8, description="рынок выдачи, ISO-2")
    language: str | None = Field(
        default=None, max_length=16, description="язык писем: ru или en; пусто — язык рынка"
    )
    depth_pages: int = Field(default=1, ge=1, le=100, description="страниц выдачи по 10")
    serp_mode: str | None = Field(
        default=None,
        max_length=16,
        description="режим покупки выдачи: live — быстрый, standard — дешевле и дольше; "
        "пусто — умолчание сервиса",
    )
    excluded: list[LineNumber] = Field(
        default_factory=list,
        max_length=KEYWORDS_LENGTH // 2,
        description="номера строк взятых ключей, как вписаны (пустые строки — тоже строки), "
        "снятые человеком: их выдача не покупается",
    )


class RunAsk(SegmentAsk):
    """Запуск — по смете, которую видел человек: её отпечаток (`EstimateView.digest`). Запуск
    считает смету заново и сверяет отпечаток — иначе «смета устарела»."""

    digest: str | None = Field(
        default=None,
        max_length=64,
        description="отпечаток сметы, которую видел человек; пусто — смету не считали",
    )


class SegmentBody(BaseModel):
    """Правка гипотезы: описание сегмента (его читает судья сегмента) и имя. Нет поля — оно не
    меняется: «Сбор базы» шлёт одно описание, окно гипотезы — то, что в нём поправили."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, description="новое имя; правило — как у заведения")
    description: str | None = Field(
        default=None, max_length=SEGMENT_LENGTH, description="пустое — описания нет"
    )


class SegmentCard(BaseModel):
    hypothesis_id: int
    name: str
    description: str | None


class EstimateView(BaseModel):
    """Смета словами и числами; `refusal` — запускать нельзя, и почему. Ключи — взятые (за них
    заплатит выдача), снятые человеком (`excluded`, по порядку строк) — отдельно от отсеянных с
    причиной, пометки взятых и номер строки, как вписана, у каждого взятого и снятого
    (`keyword_lines`: по нему экран снимает и возвращает галочку); сайтов — в выдаче (`hosts`),
    из них уже в сборе гипотезы (`known`) и новых (`sites`) — по доле `share` из истории сбора
    (`share_runs` прогонов; 0 — запасная доля). `segment` — описание сегмента, по которому
    посчитано; `digest` — отпечаток сметы: запуск идёт только по нему."""

    keywords: list[str]
    excluded: list[str]
    keyword_lines: dict[str, int]
    rejected: dict[str, str]
    notes: dict[str, str]
    segment: str
    digest: str
    country: str
    language: str
    depth_pages: int
    mode: str
    serp_mode: str
    serp_page_usd: float
    serp_wait_s: int
    results: int
    share: float
    share_runs: int
    hosts: int
    known: int
    sites: int
    serp_usd: float
    tokens: int
    tokens_usd: float
    tokens_left: int | None
    hunter_searches: int
    hunter_checks: int
    lines: list[str]
    refusal: str | None

    @classmethod
    def of(cls, found: Estimate) -> EstimateView:
        return cls(
            keywords=found.keywords,
            excluded=list(found.excluded),
            keyword_lines=found.keyword_lines,
            rejected=found.rejected,
            notes=found.notes,
            segment=found.segment,
            digest=found.digest,
            country=found.country,
            language=found.language,
            depth_pages=found.depth_pages,
            mode=found.mode,
            serp_mode=found.serp_mode,
            serp_page_usd=found.serp_page_usd,
            serp_wait_s=found.serp_wait_s,
            results=found.results,
            share=found.share,
            share_runs=found.share_runs,
            hosts=found.hosts,
            known=found.known,
            sites=found.sites,
            serp_usd=found.serp_usd,
            tokens=found.tokens,
            tokens_usd=found.tokens_usd,
            tokens_left=found.tokens_left,
            hunter_searches=found.hunter_searches,
            hunter_checks=found.hunter_checks,
            lines=found.lines,
            refusal=found.refusal,
        )


class SpendRow(BaseModel):
    """Статья расхода прогона: факт и смета на запуске — словами и числами (`segment_spend`).
    Пустое число — в этой единице не платили или этапа ещё не было: слова говорят, что именно."""

    item: str
    title: str
    fact: str
    promised: str
    fact_units: int | None
    fact_usd: float | None
    promised_units: int | None
    promised_usd: float | None


class RunKeywordsView(BaseModel):
    """Ключи прогона: взятые с пометками, снятые человеком и отсеянные с причиной — как на
    запуске; пропавшие (заплачено — не пришло) и пустые — что вышло с покупкой выдачи. Для
    отчёта — почти повторы группами (`twins`), прочие пометки без «похож на «…»» (`remarks`) и
    сколько новых для гипотезы сайтов принёс каждый ключ и сколько из них принято."""

    taken: list[str]
    notes: dict[str, str]
    rejected: dict[str, str]
    lost: list[str]
    empty: list[str]
    #: Сняты человеком на запуске — выдача их не покупала; прогон старше снятия — пусто.
    excluded: list[str] = Field(default_factory=list)
    #: Почти повтор → ключ, на который он похож.
    twins: dict[str, str] = Field(default_factory=dict)
    #: Прочие пометки взятых — без «похож на «…»».
    remarks: dict[str, str] = Field(default_factory=dict)
    #: Ключ → новых для гипотезы сайтов; сайт нескольких ключей — у первого по списку.
    sites: dict[str, int] = Field(default_factory=dict)
    #: Ключ → сколько из его сайтов принято.
    accepted: dict[str, int] = Field(default_factory=dict)


class RunSpend(BaseModel):
    """Что купил прогон и во что обошёлся — рядом со сметой на запуске: ключи и расход статьями."""

    keywords: RunKeywordsView
    rows: list[SpendRow]

    @classmethod
    def of(
        cls, run: SalesSegmentRunModel, *, by_keyword: Mapping[str, KeySites] | None = None
    ) -> RunSpend:
        found = run_keywords(run)
        counts = by_keyword or {}
        return cls(
            keywords=RunKeywordsView(
                taken=found.taken,
                notes=found.notes,
                rejected=found.rejected,
                lost=found.lost,
                empty=found.empty,
                excluded=run_excluded(run),
                twins=found.twins,
                remarks=found.remarks,
                sites={key: count.sites for key, count in counts.items()},
                accepted={key: count.accepted for key, count in counts.items()},
            ),
            rows=[SpendRow(**asdict(row)) for row in spend_rows(run)],
        )


class RunProgress(BaseModel):
    """Ход прохода (`report.progress`, форма — `segment_progress.py`): этап, сделано из
    всего, когда начат этап и проход, когда менялась запись хода. «Сделано» судьи и адресов
    — живое, из кандидатов (`segment_progress.live`)."""

    stage: str
    done: int | None = None
    total: int | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    pass_started_at: datetime | None = None
    #: Режим покупки выдачи (`SerpMode`) — у этапа выдачи.
    mode: str | None = None
    #: Что знать о сбое этапа до следующего прохода: «следующий проход купит её заново».
    note: str | None = None

    @classmethod
    def of(cls, raw: object) -> RunProgress | None:
        """Запись хода из отчёта; её нет (прогон старше хода) или она не разобрана — пусто:
        отчёт читает экран, и чужое поле не должно ронять его целиком."""
        if not isinstance(raw, Mapping) or not isinstance(raw.get("stage"), str):
            return None
        try:
            return cls.model_validate(dict(raw))
        except ValidationError as exc:
            logger.warning("сбор продаж: запись хода не разобрана", extra={"error": str(exc)})
            return None


class RunWatch(BaseModel):
    """Что с задачей прогона в очереди (`segment_watch.py`) — у идущего прогона и у прохода,
    поставленного продолжением."""

    #: Задача жива: в очереди, идёт или ждёт повтора.
    going: bool
    #: Строка «идёт», а задачи нет — словами; экран: «остановлен» и «Продолжить».
    stalled: str | None
    #: Живых воркеров долгих задач продаж; ноль — «Задачу некому взять»; пусто — не спросили.
    workers: int | None
    #: Очередь не ответила: жив ли прогон, сейчас не узнать.
    silent: bool
    #: Состояние живой задачи: `queued`, `deferred`, `scheduled` (ждёт повтора), `started`.
    job: str | None

    @classmethod
    def of(cls, found: Watch) -> RunWatch:
        return cls(
            going=found.going,
            stalled=found.stalled,
            workers=found.workers,
            silent=found.silent,
            job=found.job,
        )


def _active(run: SalesSegmentRunModel, watch: Watch | None) -> bool:
    """Идёт ли прогон: по очереди задач, а без неё — по стадии. Задачи нет — не идёт, сколько
    бы стадия ни говорила «судья»; очередь не ответила — идёт, пока не скажет обратного."""
    if watch is None:
        return run.stage in GOING
    if watch.stalled is not None:
        return False
    return watch.silent or watch.going


class RunRow(BaseModel):
    """Строка списка прогонов гипотезы: что задано, где прогон, его ход и жив ли он."""

    id: int
    hypothesis_id: int
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
    #: Пульс: когда строка прогона менялась последний раз (идущий проход отмечается сам).
    alive_at: datetime
    #: Прогон идёт — экран опрашивает его и список, пока это так.
    active: bool
    progress: RunProgress | None
    watch: RunWatch | None

    @classmethod
    def of(
        cls,
        run: SalesSegmentRunModel,
        *,
        progress: Mapping[str, Any] | None = None,
        watch: Watch | None = None,
    ) -> RunRow:
        """`progress` — живой ход (`segment_progress.live`); не дан — запись прохода как есть.
        `watch` — ответ очереди; не дан — прогон о ней не спрашивали (он кончился)."""
        return cls(
            id=run.id,
            hypothesis_id=run.hypothesis_id,
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
            alive_at=run.updated_at,
            active=_active(run, watch),
            progress=RunProgress.of(
                progress if progress is not None else run.report.get(segment_progress.KEY)
            ),
            watch=None if watch is None else RunWatch.of(watch),
        )


def default_serp_mode() -> str | None:
    """Режим покупки выдачи по умолчанию — настройкой сервиса (`SERP_MODE`, `serp/modes.py`):
    форма сбора ставит его сама, а не своё «быстрый». Настройка негодная — пусто, а не отказ
    всей вкладки: запуск её всё равно не пустит, и скажет почему (`segment_jobs._mode`)."""
    try:
        return mode_of(None).value
    except UnknownModeError as exc:
        logger.warning(
            "сбор продаж: режим выдачи по умолчанию не прочитан", extra={"error": str(exc)}
        )
        return None


class SegmentHome(BaseModel):
    """Вкладка сбора по гипотезе: описание сегмента, границы формы, режим выдачи по
    умолчанию и прогоны."""

    hypothesis_id: int
    segment: str | None
    mode: str
    countries: list[str]
    languages: list[str]
    max_keywords: int = serp_cfg.MAX_KEYWORDS_PER_RUN
    max_depth: int = serp_cfg.MAX_DEPTH_PAGES
    #: Режим покупки выдачи по умолчанию (`SerpMode`: standard, priority, live); пусто — не
    #: прочитан из настроек.
    serp_mode: str | None = None
    runs: list[RunRow]


class Count(BaseModel):
    """Причина словами и сколько."""

    words: str
    count: int


class RunDetail(BaseModel):
    """Прогон и его отчёт: смета на запуске, этап, полосы, причины, адреса, лиды — числами. Сайтов
    по одному в нём нет: отчёт опрашивается, пока прогон идёт, а сайты («на проверку» тоже) отдаёт
    список сайтов прогона страницами (`segment_sites.py`)."""

    run: RunRow
    hypothesis: str
    keywords: list[str]
    estimate_lines: list[str]
    #: Ключи и расход прогона рядом со сметой на запуске (`segment_spend`).
    spend: RunSpend
    #: Главные числа: сайтов, принято, на проверку, отсечено, ещё не судили, без адреса, лидов.
    summary: dict[str, int]
    lanes: dict[str, int]
    rejected: list[Count]
    no_address: list[Count]
    waiting_address: int
    #: Что ждёт повтора: адреса и очистка — словами, сроком и можно ли повторить сейчас.
    waits: list[WaitRow]
    leads: dict[str, int]
    lead_reasons: dict[str, int]
    unanswered: int
    #: Итог этапов из строки прогона: выдача, судья, поиск адреса, лиды.
    report: dict[str, object]

    @classmethod
    def of(cls, view: RunView, *, watch: Watch | None = None) -> RunDetail:
        run = view.run
        return cls(
            run=RunRow.of(run, progress=view.progress, watch=watch),
            hypothesis=view.hypothesis,
            keywords=list(run.keywords),
            estimate_lines=list((run.estimate or {}).get("lines", [])),
            spend=RunSpend.of(run, by_keyword=view.keyword_sites),
            summary=summary(view),
            lanes=view.lanes,
            rejected=[Count(words=words, count=count) for words, count in view.rejected],
            no_address=[Count(words=words, count=count) for words, count in view.no_address],
            waiting_address=view.waiting_address,
            waits=[WaitRow.of(wait) for wait in view.waits],
            leads=view.leads,
            lead_reasons=view.lead_reasons,
            unanswered=view.unanswered,
            report=dict(run.report or {}),
        )


class RunQueued(BaseModel):
    """Прогон поставлен задачей: ход — по строке задачи, отчёт — по прогону."""

    run_id: int
    job_id: str


class DecisionBody(BaseModel):
    """Решение человека по сайту «на проверку»."""

    model_config = ConfigDict(extra="forbid")

    accept: bool
