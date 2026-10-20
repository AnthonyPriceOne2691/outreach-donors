"""Ступень суда прогона: судья площадки до первой траты у Ahrefs и тема сайта той же пачкой.

Отдельно от исполнения прогона (`pipeline.py`) с 20.10.2026: исполнение упёрлось в потолок
длины файла, а суд — ступень со своими правилами (пачки, потолок модели, журнал токенов,
тематика), и её поведение здесь то же, что было там.

**Суд стоит ДО Ahrefs, а не после**: за домен, который мы всё равно отбросим, платить
метриками незачем. В наблюдении судья не режет, но считает сэкономленное
(`donors/judging.py`).

**Пачками, с записью после каждой** (`JUDGE_CHUNK`): прогон, убитый посреди суда, продолжает
с места — вердикты прошлых пачек уже в базе, и `fresh_judged` отдаёт их из кэша.

**Тема сайта — в той же пачке и тем же текстом** (`topics/step.py`): страница из выдачи
и главная, которую судья уже открыл, — ни одного лишнего захода на сайт. Тематика
выключена (`TOPIC_MODE=off`) — шага нет, и в отчёте прогона ветки тем нет вовсе.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from backend.config import judge as judge_cfg
from backend.config import llm as llm_cfg
from backend.config import topics as topics_cfg
from backend.features.core.models.run import RunModel
from backend.features.donors.home_signals import home_timeout
from backend.features.donors.judging import JudgePass, JudgeSummary, judge_candidates
from backend.features.donors.repository import JudgeRecord
from backend.features.runs.planning import Candidates, RunPlan
from backend.features.runs.report import RunReport
from backend.features.topics.step import TopicStep
from backend.shared.net.url_guard import guarded_client

if TYPE_CHECKING:
    from backend.features.runs.pipeline import RunDeps

logger = logging.getLogger(__name__)

#: Как называется расход на выдачу в журнале. Единица — доллар:
#: основной источник берёт деньгами, а не юнитами.
SERP_OPERATION = "serp_search"
#: Токены судьи площадки — строка журнала, как у сборки ключей.
JUDGE_OPERATION = "site_judge"

#: Судья сохраняет вердикты пачками по столько доменов. Прогон, убитый посреди суда
#: (выкатка, смерть воркера, потолок задачи), продолжает с места: вердикты прошлых
#: пачек уже в базе, и `fresh_judged` отдаёт их из кэша, а не судит снова. До
#: 14.10.2026 вердикты писались одним разом после всего прохода — полчаса токенов
#: на прогоне в пятьсот доменов терялись целиком.
JUDGE_CHUNK = 50


def _topic_step(deps: RunDeps, run: RunModel, report: RunReport) -> TopicStep | None:
    """Тема сайта — шагом пачки, только когда тематика включена: у выключенной в отчёте
    прогона нет ни ветки, ни нулей (`report._topic_stats`)."""
    if topics_cfg.MODE is not topics_cfg.TopicMode.ON:
        return None
    step = TopicStep(deps.donors.topics(), deps.runs, run.id)
    report.topics = step.summary
    return step


async def judge_stage(
    deps: RunDeps, run: RunModel, plan: RunPlan, candidates: Candidates, report: RunReport
) -> JudgePass | None:
    """Суд до первой траты у Ahrefs. `None` — судья выключен.

    Место вызова выбрано ценой, а не удобством: за домен, который мы всё
    равно отбросим, платить метриками незачем. В наблюдении он не режет,
    но считает сэкономленное — см. `donors/judging.py`.
    """
    # Свежие домены тоже судятся, если вердикта у них нет: иначе у базы,
    # собранной до судьи, его не появится никогда (замер 23.09 — 28 из 43
    # доменов ниши ставок). Платят за это токенами, не юнитами, и экономию
    # такие домены не дают: их метрики уже куплены.
    hosts = [*plan.new, *plan.fresh]
    if judge_cfg.MODE is judge_cfg.JudgeMode.OFF or not hosts:
        return None

    fresh = await deps.donors.fresh_judged(hosts)
    topics = _topic_step(deps, run, report)
    passes: list[JudgePass] = []
    async with (
        httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S) as http,
        guarded_client(timeout=home_timeout()) as home,
    ):
        for start in range(0, len(hosts), JUDGE_CHUNK):
            # Потолок модели — до каждой пачки: прогон останавливается причиной, как при
            # нехватке юнитов Ahrefs, а не тратит сверх (`runs/failures.REFUSALS`).
            await deps.runs.ensure_llm_within_cap(run_id=run.id)
            chunk = hosts[start : start + JUDGE_CHUNK]
            part = await judge_candidates(
                http,
                chunk,
                candidates.texts,
                already_judged=fresh,
                paid=plan.new,
                home_client=home if judge_cfg.HOME_CHECK else None,
                # Закрытую главную смотрим глазами индекса: `site:` у того же
                # источника выдачи, что и сам прогон.
                index=deps.provider if judge_cfg.HOME_CHECK else None,
            )
            await _save_judged(deps, run, part)
            if topics is not None:
                await topics.classify(http, chunk, candidates.texts, part.homes)
            passes.append(part)

    outcome = _joined(passes)
    _log_judged(run, outcome)
    return outcome


async def _save_judged(deps: RunDeps, run: RunModel, part: JudgePass) -> None:
    """Вердикты пачки — СРАЗУ, до метрик и до следующей пачки: прогон, упавший на
    Ahrefs или посреди суда, не должен стоить уже оплаченных токенов. Токены и
    деньги `site:` — в журнал рядом."""
    await deps.donors.save_judgements(part.verdicts)
    if part.summary.tokens:
        await deps.runs.record_tokens(
            run_id=run.id, operation=JUDGE_OPERATION, tokens=part.summary.tokens
        )
    if part.summary.index_usd:
        await deps.runs.record_money(
            run_id=run.id, operation=SERP_OPERATION, amount_usd=part.summary.index_usd
        )
    await deps.runs.session_commit()


def _joined(passes: list[JudgePass]) -> JudgePass:
    """Пачки суда — одним итогом для отчёта и для отбора к Ahrefs."""
    summary = JudgeSummary()
    verdicts: dict[str, JudgeRecord] = {}
    rejected: set[str] = set()
    for part in passes:
        summary.merge(part.summary)
        verdicts.update(part.verdicts)
        rejected |= part.rejected
    return JudgePass(verdicts, summary, rejected)


def _log_judged(run: RunModel, outcome: JudgePass) -> None:
    logger.info(
        "Судья площадки (%s): судили %s, из кэша %s, отрезал бы %s (сэкономил бы %s юнитов), "
        "к человеку %s, токенов %s",
        judge_cfg.MODE.value,
        outcome.summary.judged,
        outcome.summary.from_cache,
        outcome.summary.would_cut,
        outcome.summary.units_saved,
        outcome.summary.to_review,
        outcome.summary.tokens,
    )
    if outcome.summary.unanswered:
        # Громко: домены ушли человеку без совета, а вердикт у них будет
        # только при следующем суде. Причина — что чинить.
        logger.error(
            "Прогон %s: модель не ответила судье по %s доменам — %s",
            run.id,
            outcome.summary.unanswered,
            outcome.summary.unanswered_reason,
        )
