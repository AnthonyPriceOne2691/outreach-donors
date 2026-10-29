"""Смета прогона по ссылкам конкурентов — до запуска и до каждого списка.

**Списки платятся юнитами того же ключа Ahrefs, что метрики**, — в отличие от выдачи, за
которую берут доллары. Поэтому смета у этого прогона из трёх частей, и худший случай
сверяется с бюджетом ДО первого списка (`runs/competitor_run.py`):

    списки   = Σ по конкурентам max(50, глубина × цена строки)       — `ahrefs/refdomains`
    воронка  = estimate_run(конкурентов × глубина, доля стран = 1)   — `ahrefs/units`
    модель   = токены судьи и тематики на сайт × конкурентов × глубина — токены, не юниты

* сайтов — столько, сколько строк могут вернуть списки: худший случай, все новые и разные,
  как у выдачи «все домены новые»;
* запрос по странам — каждому: два порога из четырёх списки проверили на стороне Ahrefs,
  и пороги пакета такие сайты проходят чаще, чем сайты выдачи, — доля из истории прогонов
  по ключам её занизила бы. Та же доля — у точной сметы после списков
  (`competitor_run.country_share_of`);
* токены — худшая доля на домен из недавних прогонов (`judge_tokens_from`), как у остальной
  сметы, и тематика сайтов той же пачкой судьи (`topics/cost.py`); судья выключен — ноль.
  Потолки модели, за прогон и за день, сверяются тогда же (`model_cost`): судья сверяет их
  перед каждой пачкой, и прогон, упершийся в них посреди суда, встал бы с оплаченными
  списками на руках.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import judge as judge_cfg
from backend.config import llm as llm_cfg
from backend.features.ahrefs import refdomains as links
from backend.features.ahrefs.units import estimate_run
from backend.features.core import llm_caps, usage
from backend.features.core.domain import RunStatus
from backend.features.core.models.run import RunModel
from backend.features.runs.competitors import SOURCE_KIND, Ask
from backend.features.runs.estimate import RunForecast
from backend.features.runs.planning import SOURCE_TRIMS
from backend.features.topics.cost import PerSite, per_site, runs_classify
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

#: Доля новых сайтов с запросом по странам — все: см. описание модуля.
COUNTRY_SHARE = 1.0
#: Токенов судьи на домен, пока своей истории нет: замер трёх прогонов 20.10.2026 —
#: 1,3–1,7 тыс. с арбитром спора выдачи и главной; сверху запас.
JUDGE_TOKENS_PER_DOMAIN = 1_800
#: Меньше судимых в прогоне — доля по нему шум.
MIN_JUDGED = 20
#: Сколько недавних прогонов смотрит доля токенов.
JUDGE_HISTORY = 10
#: Что делать, когда смета не помещается: прогон по конкурентам режут конкуренты и глубина —
#: те же слова, что у отказа точной сметы (`planning.Candidates.trim`).
TRIM = SOURCE_TRIMS[SOURCE_KIND]
#: Потолок модели на прогон — настройка сервера, сброса у него нет.
RUN_CAP_ASK = "попросите администратора поднять потолок прогона"


def lists_units(ask: Ask, per_row: int, *, skip: Sequence[str] = ()) -> int:
    """Списки в худшем случае — каждый до глубины; `skip` — уже полученные."""
    return sum(links.list_units(ask.depth, per_row) for t in ask.targets if t not in skip)


def forecast(
    ask: Ask,
    *,
    per_row: int,
    units_left: int,
    units_cap: int | None,
    spent_in_cycle: int = 0,
    ceiling: int | None = None,
) -> RunForecast:
    """Смета до запуска — тем же расчётом бюджета, что у ключей (`runs/estimate.RunForecast`):
    списки идут в `lists_units`, сайты списков — в три ступени воронки."""
    rows = ask.rows_max
    return RunForecast(
        keywords=0,
        depth_pages=1,
        expected_results=rows,
        expected_domains=rows,
        estimate=estimate_run(rows, country_share=COUNTRY_SHARE),
        units_left=units_left,
        units_cap=units_cap,
        units_spent_in_cycle=spent_in_cycle,
        run_ceiling=ceiling,
        unique_share=1.0,
        lists_units=lists_units(ask, per_row),
    )


def judge_tokens_from(history: Sequence[Mapping[str, Any]], default: int) -> int:
    """Токенов судьи на домен — худшая доля из недавних прогонов (на входе — от новых
    к старым); истории нет — `default`."""
    shares: list[float] = []
    for stats in history:
        judge = stats.get("judge") or {}
        judged, tokens = judge.get("judged"), judge.get("tokens")
        if isinstance(judged, int) and isinstance(tokens, int) and judged >= MIN_JUDGED:
            shares.append(tokens / judged)
        if len(shares) >= JUDGE_HISTORY:
            break
    return math.ceil(max(shares)) if shares else default


async def judge_tokens(session: AsyncSession, domains: int) -> int:
    """Токены модели на суд сайтов списков — до запуска. Судья выключен — ноль."""
    if judge_cfg.MODE is judge_cfg.JudgeMode.OFF or domains <= 0:
        return 0
    rows = await session.scalars(
        select(RunModel.stats)
        .where(RunModel.status == RunStatus.DONE)
        .order_by(RunModel.id.desc())
        .limit(JUDGE_HISTORY * 4)
    )
    per_domain = judge_tokens_from([stats or {} for stats in rows.all()], JUDGE_TOKENS_PER_DOMAIN)
    return per_domain * domains


async def llm_refusal(
    session: AsyncSession, tokens: int, *, run_id: int | None = None
) -> str | None:
    """Поместятся ли суд и тематика сайтов списков в потолки модели
    (`core/usage.ensure_llm_within_cap`): за прогон и в остаток дня UTC. Не помещается — слова
    отказа; помещается, судья выключен или потолков нет — `None`. Имя настройки — журналу,
    человеку — слова, как у отказа потолка. Дневной — действующий (`core/llm_caps.py`): блок на
    «Расходе» сохранён — лимит части «Аутрич», к которой относится суд прогона, не сохраняли —
    общий из `.env`; отказ называет, где поднимают и когда сброс."""
    if tokens <= 0:
        return None
    now = datetime.now(UTC)
    limits: list[tuple[str, int, str, str]] = []
    if llm_cfg.RUN_TOKEN_CAP:
        spent = 0 if run_id is None else await usage.llm_tokens_spent(session, run_id=run_id)
        left = llm_cfg.RUN_TOKEN_CAP - spent
        limits.append(("потолок модели на прогон", left, "LLM_RUN_TOKEN_CAP", RUN_CAP_ASK))
    await llm_caps.refresh(session)
    day = datetime.combine(now.date(), time.min, tzinfo=UTC)
    saved = llm_caps.block()
    if saved is not None and saved.general is not None:
        # Блок на «Расходе» сохранён: суд и тема прогона — часть «Аутрич», и её лимит жёсткий.
        part = llm_caps.PART_OUTREACH
        spent_parts = await usage.spent_by_part(session, since=day)
        left = llm_caps.available(saved, part, spent_parts)
        title = f"остаток лимита раздела «{llm_caps.TITLES[part]}»"
        limits.append((title, left, part, llm_caps.after_part(now)))
    elif saved is None and (general := llm_caps.daily_cap()):
        spent = await usage.llm_tokens_spent(session, since=day)
        left = general - spent
        limits.append(
            ("остаток дневного потолка модели", left, llm_caps.GENERAL, llm_caps.after_cap(now))
        )
    for what, left, setting, ask in limits:
        if tokens > left:
            logger.warning(
                "сайты списков не помещаются в потолок модели: %s",
                setting,
                extra={"setting": setting, "tokens": tokens, "left": left},
            )
            return (
                f"Сайты списков обойдутся модели до {spaced(tokens)} токенов, а {what} — "
                f"{spaced(max(0, left))}: прогон встал бы посреди суда, когда списки уже оплачены. "
                f"{TRIM} — или {ask}"
            )
    return None


@dataclass(frozen=True, slots=True)
class ModelCost:
    """Модель на сайты списков — до запуска: токены суда, тематика (`None` — темы прогон не
    определит) и отказ её потолков словами (`None` — помещается)."""

    judge: int
    topics: PerSite | None
    domains: int
    refusal: str | None

    @property
    def topic_tokens(self) -> int:
        """Тематика — тем же худшим случаем, что суд: все сайты новые и без темы."""
        return self.topics.tokens * self.domains if self.topics else 0


async def model_cost(
    session: AsyncSession, domains: int, *, run_id: int | None = None
) -> ModelCost:
    """Суд и тематика сайтов списков — та же пачка судьи (`runs/judge_stage.py`), и потолки
    модели сверяются по обоим: тема, как и суд, тратит токены прогона и дня."""
    judge = await judge_tokens(session, domains)
    topics = await per_site(session) if runs_classify() and domains > 0 else None
    tokens = judge + (topics.tokens * domains if topics else 0)
    refusal = await llm_refusal(session, tokens, run_id=run_id)
    return ModelCost(judge, topics, domains, refusal)
