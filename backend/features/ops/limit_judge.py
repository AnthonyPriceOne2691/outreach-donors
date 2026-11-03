"""Строка панели лимитов о судье рекламодателей (`crawl/judge_pass.py`): свой потолок суток.

Только чтение: потолок — настройка сервера `ADVERTISER_JUDGE_DAILY_TOKEN_CAP`, внутри общего потолка
модели и лимита части «Аутрич» (строки `limit_model`). Выбрано — тем же журналом, что считает
потолок: операция `advertiser_judge` с полуночи UTC и с первого числа. Ключ — этапа рекламодателей:
без права «Рекламодатели» строки нет (`api/settings/limit_panel.stage_of`).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from backend.config import llm as llm_cfg
from backend.features.core import llm_caps
from backend.features.core.limit_rows import LimitRow
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.usage import SYSTEM
from backend.features.crawl.judge_pass import JUDGE_OPERATION
from backend.features.ops.limit_fields import BLANK, OUTREACH, day_start, spent
from backend.features.runs.spending import month_start
from backend.shared.numbers import spaced

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

KEY = "outreach.advertisers.judge"
SETTING = "ADVERTISER_JUDGE_DAILY_TOKEN_CAP"


def _now(cap: int) -> str:
    # Опечатка в настройке: без сохранённого блока проход отказывает (`usage._readable`), с ним —
    # держит лимит части. Строка говорит то же, что проход, — как строки модели (`limit_model`).
    if SETTING in llm_cfg.UNREAD_CAPS:
        if llm_caps.block() is None:
            return "Настройка потолка не прочитана: проход отказывает, пока её не поправят"
        return "Настройка потолка не прочитана: своего потолка нет, проход держит лимит «Аутрич»"
    if not cap:
        return "Своего потолка нет: проход держат лимит части «Аутрич» и общий потолок модели"
    return (
        f"Свой потолок судьи — {spaced(cap)} токенов в сутки, внутри лимита «Аутрич»: выбран — проход "
        "встаёт словами, сделанное остаётся"
    )


async def rows(session: AsyncSession) -> list[LimitRow]:
    """Строка судьи на эту минуту. Менять нечего: настройка сервера."""
    # Блок «Расхода» — свежим чтением, а не тем, что оставила в памяти строка модели перед этой.
    await llm_caps.refresh(session)
    moment = datetime.now(UTC)
    rule = (UsageRecordModel.system == SYSTEM, UsageRecordModel.operation == JUDGE_OPERATION)
    day, _ = await spent(session, day_start(moment), *rule)
    month, _ = await spent(session, month_start(moment), *rule)
    cap = llm_cfg.ADVERTISER_JUDGE_DAILY_TOKEN_CAP
    return [
        replace(
            BLANK,
            key=KEY,
            task=OUTREACH,
            service="Модель",
            action="Судья рекламодателей",
            scope="проход в тени: спорные и «куплено»",
            unit="токенов",
            per_day=cap or None,
            used_day=day,
            used_month=month,
            now=_now(cap),
            source=f".env {SETTING}",
        )
    ]
