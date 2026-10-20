"""Что уходит и приходит по маршрутам прогона по ссылкам конкурентов (`competitors.py`).

Своим файлом, а не строками `schemas.py`: смета этого прогона из других частей — списки,
сайты списков, модель, — а бюджет у неё тот же, и его поля и слова берутся у сметы ключей
(`Forecast.of`), а не считаются второй раз.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from backend.api.runs.schemas import MAX_RUN_CEILING, Forecast
from backend.config import judge as judge_cfg
from backend.features.ahrefs.refdomains import list_units
from backend.features.runs.ahrefs_cap import KeyCap
from backend.features.runs.competitor_estimate import ModelCost
from backend.features.runs.competitor_hosts import MAX_COMPETITORS, competitor_hosts
from backend.features.runs.competitors import DEFAULT_DEPTH, MAX_DEPTH, MIN_DEPTH, Ask
from backend.features.runs.estimate import RunForecast
from backend.features.serp.dataforseo import COUNTRY_CODES
from backend.features.serp.modes import mode_of

#: Поля бюджета — те же, что у сметы ключей: экран показывает их одной плашкой.
BUDGET_FIELDS = frozenset(
    {
        "units_left",
        "units_cap",
        "units_spent_in_cycle",
        "cap_left",
        "cycle_start",
        "cycle_by_key",
        "cap_unknown",
        "next_reset",
        "run_ceiling",
        "budget",
        "affordable",
        "shortfall",
        "binding",
        "short_of",
    }
)


class CompetitorRunBody(BaseModel):
    """Чего хотим от прогона по ссылкам конкурентов. Отказы — своим текстом, как у прогона
    по ключам: экран показывает их целиком."""

    #: Домены конкурентов: схема сводит их к корневым и без повторов (`competitor_hosts`).
    competitors: list[str] = Field(min_length=1, max_length=MAX_COMPETITORS * 10)
    #: Страна прогона — из списка стран сервиса (`/runs/countries`): по ней регион донора.
    country: str
    #: Сколько ссылающихся доменов взять у каждого конкурента, сильнейшие первыми.
    depth: int = DEFAULT_DEPTH
    #: Потолок юнитов на этот прогон; пусто — остаток по капу цикла ключа.
    cap: int | None = Field(default=None, ge=1, le=MAX_RUN_CEILING)

    @field_validator("competitors")
    @classmethod
    def _domains(cls, typed: list[str]) -> list[str]:
        """Домены — как их спросит Ahrefs; отказ называет, что вписать."""
        try:
            return competitor_hosts(typed)
        except ValueError as exc:
            # Текст — значением, а не шаблоном: в нём набранное человеком, и фигурные
            # скобки оттуда шаблон разобрал бы как свои поля.
            raise PydanticCustomError("competitors", "{reason}", {"reason": str(exc)}) from None

    @field_validator("country")
    @classmethod
    def _country(cls, typed: str) -> str:
        """Страна — из того же списка, что у `/runs/countries`: у этого прогона нет выдачи,
        которая отказала бы чужой стране сама, и списки с метриками ушли бы на чужой рынок."""
        code = typed.strip().lower()
        if code not in COUNTRY_CODES:
            reason = f"Страны «{typed[:20]}» нет в списке стран сервиса — выберите страну из списка"
            raise PydanticCustomError("country", "{reason}", {"reason": reason})
        return code

    @field_validator("depth")
    @classmethod
    def _depth(cls, depth: int) -> int:
        if not MIN_DEPTH <= depth <= MAX_DEPTH:
            why = ": больше Ahrefs за один запрос не отдаёт" if depth > MAX_DEPTH else ""
            raise PydanticCustomError(
                "depth_out_of_range",
                f"Глубина списка — от {MIN_DEPTH} до {MAX_DEPTH} ссылающихся доменов на "
                f"конкурента, пришло {depth}{why}",
            )
        return depth

    @property
    def ask(self) -> Ask:
        return Ask(targets=tuple(self.competitors), depth=self.depth)


class CompetitorList(BaseModel):
    """Список одного конкурента в смете — до глубины строк и во что обойдётся."""

    target: str
    rows: int
    units: int


class CompetitorForecast(BaseModel):
    """Смета до запуска: списки, метрики и «Гео» сайтов списков, модель — и бюджет.

    Числа — худший случай: каждый список до глубины, все сайты новые и разные. Точная
    смета — по настоящим новым сайтам, после списков и до первой метрики.
    """

    lists: list[CompetitorList]
    depth: int
    #: Цена строки списка по модели (`ahrefs/refdomains.row_units`): при этих порогах.
    row_units: int
    units_lists: int
    #: Сайтов — сколько строк вернут списки в худшем случае.
    expected_domains: int
    units_screen: int
    units_metrics: int
    units_by_country: int
    #: Всё в юнитах Ahrefs: списки и три ступени воронки.
    units_total: int
    #: Токены модели на суд сайтов — другой счёт, не юниты; судья выключен — ноль.
    tokens: int
    judge_mode: str
    #: Тематика сайтов той же пачкой судьи (`topics/cost.py`) — худший случай и на сайт;
    #: ноль — темы прогон не определит. `topic_measured` — по замеру прошлых прогонов.
    topic_tokens: int = 0
    topic_tokens_per_site: int = 0
    topic_measured: bool = False
    #: Суд и тематика не помещаются в потолки модели — словами, и прогон не запускается
    #: (`competitor_estimate.model_cost`); помещаются — пусто.
    llm_refusal: str | None = None
    units_left: int
    units_cap: int | None
    units_spent_in_cycle: int
    cap_left: int
    cycle_start: datetime
    cycle_by_key: bool
    cap_unknown: str | None = None
    next_reset: datetime
    run_ceiling: int | None
    budget: int
    affordable: bool
    shortfall: int
    binding: str | None = None
    short_of: list[str] = Field(default_factory=list)

    @classmethod
    def of(
        cls, ask: Ask, made: RunForecast, *, cap: KeyCap, row_units: int, model: ModelCost
    ) -> CompetitorForecast:
        """Смета и бюджет: поля бюджета — теми же правилами упора, что у ключей."""
        budget = Forecast.of(made, serp_mode=mode_of(None), cap=cap).model_dump(
            include=set(BUDGET_FIELDS)
        )
        each = list_units(ask.depth, row_units)
        return cls(
            lists=[CompetitorList(target=t, rows=ask.depth, units=each) for t in ask.targets],
            depth=ask.depth,
            row_units=row_units,
            units_lists=made.lists_units,
            expected_domains=made.expected_domains,
            units_screen=made.estimate.screen,
            units_metrics=made.estimate.metrics,
            units_by_country=made.estimate.by_country,
            units_total=made.units_total,
            tokens=model.judge,
            judge_mode=judge_cfg.MODE.value,
            topic_tokens=model.topic_tokens,
            topic_tokens_per_site=model.topics.tokens if model.topics else 0,
            topic_measured=model.topics.measured if model.topics else False,
            llm_refusal=model.refusal,
            **budget,
        )
