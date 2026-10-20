"""Прогон по ссылкам конкурентов: шаг задачи до воронки — списки, удержание и кандидаты.

Смета — в `runs/competitor_estimate.py`: списки, воронка и модель, худшим случаем; здесь —
что задача делает с ней до первого списка и после каждого.

**На время списков прогон держит худший случай** (`HOLD_KEY`). Удержание идущего прогона —
его смета минус потраченное (`RunRepository.claimed_units`), а точной сметы до списков нет:
без удержания соседний прогон планировался бы под те же юниты. Точная смета — по настоящим
новым сайтам, после списков и до первой метрики (`plan_run`), как у ключей, — ложится поверх
той же фиксацией (`release_hold`). Не помещается — прогон остановлен с причиной, а списки
уже в журнале расхода и в факте прогона (`RunRepository.stop_run`).

**Списки — по одному, и каждый с фиксацией** (`runs.competitors`): умерший воркер оставляет
то, за что уже заплачено, и продолжение не покупает списки второй раз. Отказ Ahrefs по самому
запросу (`SKIPPED_STATUSES`) после хотя бы одного полученного списка — пропуск конкурента с
причиной в журнале; ключ и доступ останавливают прогон. Ahrefs назвал строку дороже модели —
бюджет сверяется заново до следующего списка.

**Потолок продолжения — без двойного вычета.** Остаток капа цикла на старте задачи уже учёл
списки прошлых попыток (он считается по журналу), обещанное человеком — нет: потолок задачи
считается от обещанного за их вычетом (`lists_paid`), а воронке достаётся он же за вычетом
списков этой попытки (`Prepared.cap`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.ahrefs import refdomains as links
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import UsageCollector, estimate_run
from backend.features.core.models.run import RunModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.donors.verdict import Thresholds
from backend.features.runs.budget import CapExceededError, budget_of
from backend.features.runs.competitor_estimate import COUNTRY_SHARE, TRIM, model_cost
from backend.features.runs.competitors import (
    SKIPPED_STATUSES,
    Ask,
    CompetitorLinksError,
    Fetched,
    candidates_of,
    collapse,
    fetch_list,
    known_donors,
    links_filter,
    row_price,
)
from backend.features.runs.planning import Candidates
from backend.features.runs.repository import RunRepository
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

#: Ключ строки `competitors`: смета прогона — удержание худшего случая на время списков,
#: а не точная (см. описание модуля).
HOLD_KEY = "hold"


@dataclass(slots=True)
class Progress:
    """Что списки дали прогону за все попытки — строка прогона `competitors`: полученные,
    пропущенные (почему — словами) и юниты всех запросов списков, отказанных тоже: Ahrefs
    списывает и за отказ (`AhrefsClient._request`), и смета прогона его видит."""

    done: dict[str, Fetched] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    units: int = 0

    @classmethod
    def of(cls, stored: Mapping[str, Any] | None) -> Progress:
        stored = stored or {}
        fetched = stored.get("fetched") or {}
        done = {target: Fetched.restored(target, fetched[target]) for target in fetched}
        units = stored.get("units")
        return cls(
            done=done,
            skipped=dict(stored.get("skipped") or {}),
            units=sum(f.units for f in done.values()) if units is None else int(units),
        )

    def left(self, ask: Ask) -> list[str]:
        """Конкуренты без списка: не получен и не пропущен."""
        return [t for t in ask.targets if t not in self.done and t not in self.skipped]

    def lists(self, ask: Ask) -> list[Fetched]:
        """Полученные списки — в порядке ввода конкурентов."""
        return [self.done[t] for t in ask.targets if t in self.done]

    def worst_rows(self, ask: Ask) -> int:
        """Сайтов в худшем случае: у полученных списков — сколько пришло, у оставшихся — до
        глубины, у пропущенных — ни одного."""
        return sum(len(f.rows) for f in self.done.values()) + ask.depth * len(self.left(ask))

    def price(self, model: int) -> int:
        """Цена строки оставшихся списков: по модели — или дороже, если Ahrefs назвал дороже."""
        return max([model, *(f.per_row for f in self.done.values() if f.per_row)])

    def as_dict(self) -> dict[str, Any]:
        return {
            "fetched": {target: fetched.as_dict() for target, fetched in self.done.items()},
            "skipped": dict(self.skipped),
            "units": self.units,
        }


def lists_paid(run: RunModel) -> int:
    """Юниты запросов списков прошлых попыток. Потолок задачи — от обещанного за их вычетом
    (`workers/run_job`): остаток капа цикла их уже учёл, а обещанное — нет. У прогона по ключам
    — ноль."""
    return Progress.of(run.competitors).units


def held(run: RunModel) -> bool:
    """Смета прогона — удержание на время списков, а не точная."""
    return bool((run.competitors or {}).get(HOLD_KEY))


def release_hold(run: RunModel) -> None:
    """Снять удержание: точная смета ляжет поверх той же фиксацией (`pipeline.execute_run`), и
    окна, когда прогон ничего не держит, нет. Не удержание — как есть: смета ставится один раз
    (`RunRepository.set_estimate`)."""
    if held(run):
        run.competitors = {k: v for k, v in (run.competitors or {}).items() if k != HOLD_KEY}
        run.estimated_units = None


async def country_share_of(runs: RunRepository, run: RunModel, country: str) -> float:
    """Доля новых сайтов с запросом по странам для точной сметы (`planning.plan_run`): у прогона
    по конкурентам — как у сметы до запуска, у прогона по ключам — из истории его прогонов."""
    if Ask.of(run.competitors) is None:
        return await runs.country_call_share(country)
    return COUNTRY_SHARE


@dataclass(frozen=True, slots=True)
class Prepared:
    """С чем задача идёт в воронку: кандидаты прогона по конкурентам (у прогона по ключам и у
    продолжения с сохранёнными кандидатами — `None`), потолок воронки — потолок задачи за
    вычетом списков этой попытки — и юниты всех списков прогона: смета их включает."""

    candidates: Candidates | None
    cap: int
    spent: int = 0


async def _hold(
    session: AsyncSession,
    client: AhrefsClient,
    run: RunModel,
    ask: Ask,
    progress: Progress,
    *,
    per_row: int,
    model: int,
    cap: int,
) -> None:
    """Худший случай оставшегося — до следующего списка: не помещается в бюджет или потолки
    модели — отказ словами, и больше ни один юнит не потрачен. Помещается — удержание и
    фиксация: соседний прогон увидит его раньше, чем уйдёт список. Остаток — живым ответом
    Ahrefs, за вычетом обещанного идущими."""
    runs = RunRepository(session)
    claimed = await runs.claimed_units(exclude_run_id=run.id)
    budget = await budget_of(client, cap=cap, claimed=claimed, keep=runs.keep_key)
    rows = progress.worst_rows(ask)
    lists = len(progress.left(ask)) * links.list_units(ask.depth, per_row)
    worst = lists + estimate_run(rows, country_share=COUNTRY_SHARE).total
    if worst > budget.units:
        dearer = (
            f" Строка списка у Ahrefs дороже сметы: {per_row} юнитов вместо {model}."
            if per_row > model
            else ""
        )
        raise CapExceededError(
            f"Списки конкурентов и метрики их сайтов обойдутся до {spaced(worst)} юнитов, "
            f"доступно {spaced(budget.units)}; {budget.shortage(worst)}.{dearer} {TRIM}."
        )
    refusal = (await model_cost(session, rows, run_id=run.id)).refusal
    if refusal is not None:
        raise LlmCapExceededError(refusal)
    if run.estimated_units is None or held(run):
        run.estimated_units = progress.units + worst
        run.competitors = {**(run.competitors or {}), HOLD_KEY: True}
    await runs.session_commit()


async def _checkpoint(
    runs: RunRepository, run: RunModel, progress: Progress, sink: UsageCollector
) -> None:
    """Списки — в строку прогона, расход — в журнал и в счёт списков, и фиксация: за них
    заплачено."""
    for operation, cost in sink.drain():
        progress.units += cost.billable
        await runs.record_usage(run_id=run.id, operation=operation, cost=cost, stage=run.stage)
    run.competitors = {**(run.competitors or {}), **progress.as_dict()}
    await runs.session_commit()


async def _one(
    client: AhrefsClient, target: str, ask: Ask, where: links.RefdomainsFilter, progress: Progress
) -> None:
    """Список одного конкурента — в `progress`. Отказ Ahrefs по самому запросу
    (`SKIPPED_STATUSES`) после хотя бы одного полученного списка — пропуск с причиной: запрос
    Ahrefs принимает, не подошла цель, и оплаченные списки остальных не пропадают. Такой отказ
    на первом же списке, отказ ключа и доступа — остановка: они про все списки сразу."""
    try:
        progress.done[target] = await fetch_list(client, target, depth=ask.depth, where=where)
    except CompetitorLinksError as exc:
        if not progress.done or exc.status not in SKIPPED_STATUSES:
            raise
        logger.warning("Ссылки конкурента %s пропущены: %s", target, exc)
        # Причина — про домен, а не про запрос: списки других конкурентов тот же запрос дал.
        said = exc.__cause__ or exc
        progress.skipped[target] = (
            f"Ahrefs не принял этот домен (код {exc.status}), хотя списки других конкурентов "
            f"отдал. Ahrefs: {said}"
        )[:500]


async def _fetch(
    session: AsyncSession,
    client: AhrefsClient,
    run: RunModel,
    ask: Ask,
    progress: Progress,
    thresholds: Thresholds,
    *,
    cap: int,
) -> None:
    """Списки, которых ещё нет, — по одному и каждый с фиксацией. Перед первым — худший случай
    и удержание (`_hold`); Ahrefs назвал строку дороже — заново, до следующего списка."""
    left = progress.left(ask)
    if not left:
        return
    runs = RunRepository(session)
    model = row_price(thresholds)
    per_row = progress.price(model)
    start = progress.units
    await _hold(session, client, run, ask, progress, per_row=per_row, model=model, cap=cap)
    where = links_filter(thresholds)
    sink = UsageCollector()
    previous, client.on_usage = client.on_usage, sink
    try:
        for target in left:
            await _one(client, target, ask, where, progress)
            await _checkpoint(runs, run, progress, sink)
            if progress.price(model) > per_row and progress.left(ask):
                logger.warning(
                    "Прогон %s: строка списка у Ahrefs — %s юнитов, смета считала %s; "
                    "бюджет — заново до следующего списка",
                    run.id,
                    progress.price(model),
                    per_row,
                )
                per_row = progress.price(model)
                left_cap = max(0, cap - (progress.units - start))
                await _hold(
                    session, client, run, ask, progress, per_row=per_row, model=model, cap=left_cap
                )
    finally:
        # Отказ посреди списков тоже списал: строка расхода пишется и тогда.
        await _checkpoint(runs, run, progress, sink)
        client.on_usage = previous


async def prepare(
    session: AsyncSession, client: AhrefsClient, run: RunModel, thresholds: Thresholds, *, cap: int
) -> Prepared:
    """Шаг задачи до воронки. Прогон по ключам — как есть. Прогон по конкурентам — списки (уже
    полученные не покупаются), повторы, уже доноры — и кандидаты с журналом. `cap` — потолок
    прогона на старте задачи, уже за вычетом списков прошлых попыток (`lists_paid`); воронке —
    он же за вычетом списков этой."""
    ask = Ask.of(run.competitors)
    if ask is None:
        return Prepared(None, cap)
    progress = Progress.of(run.competitors)
    if run.candidates:
        # Кандидатов сохранила прошлая попытка: списки не покупаются, но их цена — в смете
        # прогона, иначе точная смета вышла бы без них (проба ревью 20.10.2026).
        return Prepared(None, cap, progress.units)
    runs = RunRepository(session)
    # «Идёт» — до первого списка: списки платные, и экран не показывает их ждущими в очереди.
    await runs.mark_running(run)
    await runs.session_commit()
    start = progress.units
    await _fetch(session, client, run, ask, progress, thresholds, cap=cap)

    lists = progress.lists(ask)
    journal = collapse(ask, lists, thresholds)
    journal.without(await known_donors(session, journal.hosts))
    logger.info(
        "Прогон %s: списки %s конкурентов (пропущено %s) — строк %s, кандидатов %s, "
        "выброшено %s, юнитов %s",
        run.id,
        len(lists),
        len(progress.skipped),
        journal.rows,
        len(journal.hosts),
        journal.dropped,
        progress.units,
    )
    candidates = candidates_of(
        ask, lists, journal, row_units=row_price(thresholds), skipped=progress.skipped
    )
    return Prepared(candidates, cap=max(0, cap - (progress.units - start)), spent=progress.units)
