"""Во что обходится тема сайта: токены на сайт и деньги — до траты.

**Токены на сайт — худшие из недавних прогонов** (`per_site_from`), как доля уникальных
доменов у сметы (`runs/repository.unique_share_from`): смета — верхняя граница, и занизить
здесь значит, что потолок модели кончится посреди прогона. В прогоне берётся итог ветки
`topics` отчёта: токены на один ответ модели (тема названа, «не ясна» или не по форме).

**До первого замера — оценка по размеру промпта**, и она так и подписана. Ввод — по худшему
письму, а не по среднему: список тем и правила — 1030 токенов (словарь `o200k_base`), текст
сайта в потолке `TOPIC_MAX_TEXT_CHARS` (1500 знаков) — до 590 у кириллицы (болгарский;
английский — 270, русский — 400, тайский — 560), рамка с метками — ещё около сорока: 1700.
Вывод вместе с рассуждением на `low` — около трёхсот (у судьи площадки на `low` ~720 на домен
всего, при промпте вдвое короче). Первый прогон с темами заменяет оценку замером.

**Деньги — по прайсу модели** (`TOPIC_INPUT_USD_PER_1M_TOKENS`, `…_OUTPUT_…`): ввод
и вывод отдельно, рассуждение — в выводе. Скидка провайдера на повторный промпт не
вычитается: смета — верхняя граница, а скидка не обещана.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import judge as judge_cfg
from backend.config import topics as cfg
from backend.features.core.domain import RunStatus
from backend.features.core.models.run import RunModel

#: Оценка до первого замера: ввод (список тем, правила и текст сайта худшего письма) и вывод
#: с рассуждением. Ввод пересчитывается, когда растёт промпт или потолок текста.
ESTIMATED_PROMPT_TOKENS = 1700
ESTIMATED_COMPLETION_TOKENS = 300

#: Меньше ответов модели в прогоне — его счёт шум, а не число.
MIN_ANSWERS = 10
#: Сколько последних прогонов с темами смотрит смета.
HISTORY_WINDOW = 20


@dataclass(frozen=True, slots=True)
class PerSite:
    """Токены одного ответа модели о теме: ввод и вывод, и замер ли это."""

    prompt: int
    completion: int
    #: Из истории прогонов (`True`) или оценка по размеру промпта (`False`).
    measured: bool

    @property
    def tokens(self) -> int:
        return self.prompt + self.completion

    def usd(self, sites: int) -> float:
        """Деньги за `sites` ответов по прайсу модели — без скидки кэша провайдера."""
        per_million = (
            self.prompt * cfg.INPUT_USD_PER_1M_TOKENS
            + self.completion * cfg.OUTPUT_USD_PER_1M_TOKENS
        )
        return max(0, sites) * per_million / 1_000_000


ESTIMATE = PerSite(ESTIMATED_PROMPT_TOKENS, ESTIMATED_COMPLETION_TOKENS, measured=False)


def _number(value: object) -> int:
    """Число из отчёта — JSON прошлых версий: чего нет — ноль, а не падение."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, int(value))


def _of_run(topics: Mapping[str, Any]) -> PerSite | None:
    """Токены на ответ в одном прогоне; мало ответов или нет токенов — `None`. Ответ не по
    форме оплачен так же, как названная тема, — он в счёте ответов."""
    answers = sum(_number(topics.get(key)) for key in ("classified", "unclear", "unreadable"))
    tokens = _number(topics.get("tokens"))
    if answers < MIN_ANSWERS or not tokens:
        return None
    prompt, completion = (
        _number(topics.get("prompt_tokens")),
        _number(topics.get("completion_tokens")),
    )
    if prompt + completion != tokens:
        # Раздельно провайдер не назвал — делим итог в доле оценки.
        share = ESTIMATED_COMPLETION_TOKENS / ESTIMATE.tokens
        completion = round(tokens * share)
        prompt = tokens - completion
    return PerSite(round(prompt / answers), round(completion / answers), measured=True)


def per_site_from(history: Sequence[Mapping[str, Any]]) -> PerSite:
    """Токены на сайт — худшие из недавних прогонов; на вход — отчёты от новых к старым."""
    measured: list[PerSite] = []
    for stats in history:
        topics = stats.get("topics")
        found = _of_run(topics) if isinstance(topics, Mapping) else None
        if found is not None:
            measured.append(found)
        if len(measured) >= HISTORY_WINDOW:
            break
    return max(measured, key=lambda one: one.tokens) if measured else ESTIMATE


def runs_classify() -> bool:
    """Определит ли прогон темы: тематика включена и судья не выключен (тема — в его пачке)."""
    return cfg.MODE is cfg.TopicMode.ON and judge_cfg.MODE is not judge_cfg.JudgeMode.OFF


async def per_site(session: AsyncSession) -> PerSite:
    """Токены на сайт по истории своих прогонов — для сметы прогона и досуда."""
    rows = await session.execute(
        select(RunModel.stats)
        .where(RunModel.status == RunStatus.DONE)
        .order_by(RunModel.id.desc())
        .limit(HISTORY_WINDOW * 2)
    )
    return per_site_from([stats or {} for stats in rows.scalars().all()])
