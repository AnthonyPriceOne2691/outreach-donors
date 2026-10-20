"""Прогон по ссылкам конкурентов: смета и запуск (`runs/competitors.py`).

Тот же экран и те же правила денег, что у прогона по ключам (`routes.py`): смета ничего
не тратит, запуск кладёт задачу в очередь и до строки прогона сверяет бюджет той же сметой,
что видел человек. Отличаются вход и смета: вместо ключей — домены конкурентов и глубина,
вместо выдачи за доллары — списки за юниты того же ключа Ahrefs, что метрики.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.runs.competitor_schemas import CompetitorForecast, CompetitorRunBody
from backend.api.runs.routes import enqueue_run, refuse_over_budget
from backend.api.runs.schemas import RunQueued
from backend.config.startup_checks import check_collect
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.donors.geo import assert_settings_allow_limited_fetch
from backend.features.donors.verdict import Thresholds
from backend.features.runs.ahrefs_cap import KeyCap, key_cap
from backend.features.runs.ahrefs_key import keeper
from backend.features.runs.budget import units_left
from backend.features.runs.competitor_estimate import TRIM, forecast, model_cost
from backend.features.runs.competitors import row_price
from backend.features.runs.duplicates import refuse_duplicate
from backend.features.runs.estimate import RunForecast
from backend.features.runs.thresholds import rules_in_force

router = APIRouter(prefix="/runs/competitors", tags=["прогоны"])

_runner = Depends(needs(Permission.RUN))

#: Что сказать после запуска: первыми идут списки, смета проверяется второй раз.
QUEUED_NOTE = (
    "Прогон встал в очередь. Сначала — списки сайтов, ссылающихся на конкурентов; потом смета "
    "проверяется ещё раз по настоящим новым сайтам и останавливает прогон до первой метрики, "
    "если не помещается."
)


def _made(
    body: CompetitorRunBody, thresholds: Thresholds, *, left: int, cap: KeyCap
) -> RunForecast:
    """Смета тела запроса — одна для экрана и для запуска: остаток Ahrefs `left`, кап цикла
    ключа, потрачено в цикле, потолок человека и цена строки при порогах этой версии."""
    return forecast(
        body.ask,
        per_row=row_price(thresholds),
        units_left=left,
        units_cap=cap.units_cap,
        spent_in_cycle=cap.spent,
        ceiling=body.cap,
    )


@router.post(
    "/estimate", response_model=CompetitorForecast, summary="Смета прогона по ссылкам конкурентов"
)
async def estimate(
    body: CompetitorRunBody,
    _: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> CompetitorForecast:
    """Сколько будет стоить — худшим случаем. Не тратит ничего: остаток у Ahrefs — бесплатный
    запрос (его ответ ложится снимком ключа), списки и сайты — арифметика."""
    ahrefs = AhrefsClient()
    try:
        # Остаток сырой, без капа: арифметику потолков делает смета в одном месте.
        left = await units_left(ahrefs, keep=keeper(session))
    finally:
        await ahrefs.aclose()
    rules = await rules_in_force(session)
    cap = await key_cap(session)
    made = _made(body, rules.thresholds, left=left, cap=cap)
    view = CompetitorForecast.of(
        body.ask,
        made,
        cap=cap,
        row_units=row_price(rules.thresholds),
        model=await model_cost(session, made.expected_domains),
    )
    # Снимок ключа из ответа — в базу: смета больше ничего не пишет.
    await session.commit()
    return view


@router.post("", response_model=RunQueued, summary="Запустить прогон по ссылкам конкурентов")
async def start(
    body: CompetitorRunBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> RunQueued:
    """Поставить прогон в очередь. Отказы — до строки прогона и словами: настройки, бюджет
    по смете, потолки модели, правило региона, такой же прогон, который ещё не закончен."""
    check_collect()
    rules = await rules_in_force(session)

    async def made(left: int, cap: KeyCap) -> RunForecast:
        return _made(body, rules.thresholds, left=left, cap=cap)

    await refuse_over_budget(session, made, trim=f"{TRIM}.")
    # Потолки модели — до строки прогона, как бюджет: суд, упершийся в них посреди прогона,
    # остановил бы его с оплаченными списками на руках.
    refusal = (await model_cost(session, body.ask.rows_max)).refusal
    if refusal is not None:
        raise LlmCapExceededError(refusal)
    assert_settings_allow_limited_fetch(rules.geo.top_n, rules.geo.min_share)
    asked = body.ask.as_dict()
    await refuse_duplicate(
        session, keywords=[], country=body.country, depth_pages=1, competitors=asked
    )
    queued = await enqueue_run(
        session,
        author=author,
        rules=rules,
        asked_cap=body.cap,
        details={
            "конкуренты": list(body.competitors),
            "глубина": body.depth,
            "страна": body.country,
        },
        keywords=[],
        country=body.country,
        competitors=asked,
    )
    return queued.model_copy(update={"note": QUEUED_NOTE})
