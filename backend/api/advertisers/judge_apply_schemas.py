"""«Применить советы судьи»: что уходит на экран и что с него приходит."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.features.crawl.judge_apply import MAX_PORTION, PORTION, Applied, Band, Standing
from backend.features.crawl.judge_batches import Batch
from backend.features.crawl.judge_mark import BATCH
from backend.features.crawl.judge_plan import Plan
from backend.features.crawl.judge_revert import Reverted


class StandingView(BaseModel):
    """Что судья сказал очереди спорных — числами одной строки: каждая ждущая пара ровно в одном
    из них. `write` — «пишем» с известным DR, `waiting_dr` — «пишем», которое ждёт DR и в полосу не
    идёт. `stale` — суд есть, но прежней подсказкой или моделью; `without` — суда нет."""

    waiting: int
    skip: int
    write: int
    waiting_dr: int
    unclear: int
    stale: int
    without: int

    @classmethod
    def of(cls, standing: Standing) -> StandingView:
        return cls(**asdict(standing))


class RuleCountView(BaseModel):
    """Правило судьи в полосе: слова и сколько пар оно решило."""

    rule: str
    words: str
    pairs: int


class ExampleView(BaseModel):
    """Пара полосы для показа: донор, домен, балл скоринга и слова судьи."""

    donor_host: str
    target_root: str
    points: int
    rule: str
    words: str


class LaunchView(BaseModel):
    """Что «пишем» даст переводу: доменов в полосе, из них новых (их заведёт «Перевести») и у
    скольких новых адрес уже есть. Стоп-листы не вычтены — их проверяет сам перевод."""

    domains: int
    fresh: int
    with_address: int


class PlanView(BaseModel):
    """План полосы — до единого решения. `takes_off` — у стольких доменов «не пишем» снимет
    рекламодателя (обычно ноль); `launch` — только у «пишем»."""

    band: Band
    pairs: int
    donors: int
    rules: list[RuleCountView]
    examples: list[ExampleView]
    standing: StandingView
    takes_off: int
    launch: LaunchView | None
    #: Пар за один запрос применения: экран повторяет его, пока не останется ноль.
    portion: int

    @classmethod
    def of(cls, plan: Plan) -> PlanView:
        return cls(
            band=plan.band,
            pairs=plan.pairs,
            donors=plan.donors,
            rules=[RuleCountView(**asdict(rule)) for rule in plan.rules],
            examples=[ExampleView(**asdict(example)) for example in plan.examples],
            standing=StandingView.of(plan.standing),
            takes_off=plan.takes_off,
            launch=LaunchView(**asdict(plan.launch)) if plan.launch is not None else None,
            portion=PORTION,
        )


class ApplyBody(BaseModel):
    """Порция применения. `batch` пуст — партию начинает сервер и называет её в ответе; следующие
    порции той же партии приходят с её именем. Лишнее поле — отказ: применение не угадывает."""

    model_config = ConfigDict(extra="forbid")

    band: Band
    batch: str | None = Field(default=None, pattern=BATCH.pattern, max_length=64)
    limit: int = Field(default=PORTION, ge=1, le=MAX_PORTION)


class AppliedView(BaseModel):
    """Итог порции. `decided` — пропущены: пока шла порция, пару решил человек; `gone` — строку
    заменил новый обход. `left` — пар полосы осталось: не ноль — экран шлёт следующую порцию.
    `removed` и `stopped` — что «не пишем» сняло: рекламодателей и писем из очереди. `answered` —
    домены, которые уже ответили (не больше двадцати поимённо), `answered_total` — сколько их всего:
    переписку «не пишем» не останавливает."""

    batch: str
    band: Band
    applied: int
    decided: int
    gone: int
    left: int
    removed: int
    stopped: int
    answered: list[str]
    answered_total: int

    @classmethod
    def of(cls, result: Applied, *, named: int) -> AppliedView:
        return cls(
            batch=result.batch,
            band=result.band,
            applied=result.applied,
            decided=result.decided,
            gone=result.gone,
            left=result.left,
            removed=result.removed,
            stopped=result.stopped,
            answered=result.answered[:named],
            answered_total=len(result.answered),
        )


class RevertedView(BaseModel):
    """Итог отката партии. `returned` — пар вернулось в очередь; `kept` — «пишем» осталось в силе:
    письмо домену уже ушло; `forgotten` — пары, которых последний обход не нашёл: решение снято из
    памяти обходов, вернутся нерешёнными. `removed` и `stopped` — у откатанного «пишем»: снято
    рекламодателей и писем из очереди."""

    batch: str
    returned: int
    kept: int
    forgotten: int
    removed: int
    stopped: int

    @classmethod
    def of(cls, result: Reverted) -> RevertedView:
        return cls(**asdict(result))


class BatchView(BaseModel):
    """Партия в списке: кто начал, когда, какой полосой и сколько применено (сумма порций). `left`
    не ноль — партию оборвали посреди полосы. `standing` — решений партии в силе сейчас: часть мог
    переиграть человек. `reverted_at` пусто — не откатана."""

    id: str
    band: str
    by: str | None
    started_at: datetime
    applied: int
    left: int
    standing: int
    reverted_at: datetime | None
    reverted_by: str | None
    returned: int

    @classmethod
    def of(cls, batch: Batch) -> BatchView:
        return cls(**asdict(batch))
