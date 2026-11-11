"""Смета «Переобойти спорные» для экрана: кого и сколько — тем же планом, что у команды.

Кнопка берёт только спорных: «куплено» проверяет человек, и их проход — командой
(`outreach advertisers-judge`). План — `judge_pass.plan_pass`: уже разобранных он не берёт, поэтому
повтор продолжает с места, а новый запуск спрашивает только новых. Слова считает сервер, экран их
только называет, как у сметы пересуда продаж.

**Второй круг — своей строкой** (`judge_second.py`): задана вторая модель — смета называет, сколько
пар «не понять» уйдёт ей и во что это обойдётся; цена случая у кругов разная, поэтому токены тогда —
по кругам и итогом.

**Что не даст разбору дойти до конца — до нажатия.** Потолок уже выбран — отказ словами, вместо
кнопки. Хватит только на часть — сказано, на сколько: разбор встанет на потолке, и его продолжит
следующий запуск.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.usage import LlmCapExceededError, llm_tokens_spent
from backend.features.crawl.judge_pass import JudgePlan, judge_cap, plan_pass, within_caps
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

DISPUTED = (Verdict.PENDING,)


@dataclass(frozen=True, slots=True)
class Estimate:
    """Смета для окна подтверждения."""

    cases: int
    tokens: int
    lines: list[str]
    refusal: str | None


async def disputed_plan(
    session: AsyncSession, *, limit: int | None = None, model: str | None = None
) -> JudgePlan:
    """План разбора спорных: смета экрана и задача смотрят одним вызовом и не разойдутся."""
    return await plan_pass(session, verdicts=DISPUTED, limit=limit, model=model)


async def _cap_refusal(session: AsyncSession) -> str | None:
    """Потолок модели уже выбран — словами; заодно перечитан блок «Расхода» для `_room`."""
    try:
        await within_caps(session)
    except LlmCapExceededError as exc:
        logger.info("смета разбора спорных: потолок модели выбран", extra={"why": str(exc)})
        return str(exc)
    return None


async def _room(session: AsyncSession, now: datetime) -> tuple[int, int] | None:
    """Свой потолок судьи на сегодня: сколько осталось и из скольких; потолка нет — `None`."""
    cap = judge_cap()
    if not cap.tokens:
        return None
    day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
    spent = await llm_tokens_spent(session, since=day_start, operations=cap.operations)
    return max(0, cap.tokens - spent), cap.tokens


def _room_line(plan: JudgePlan, left: int, cap: int) -> str:
    line = f"Потолок судьи на сегодня: осталось {spaced(left)} из {spaced(cap)} токенов"
    if plan.tokens <= left:
        return line
    # По средней цене случая плана: со вторым кругом она не `per_case` первой модели.
    enough = spaced(left * len(plan.ids) // plan.tokens)
    return f"{line}: хватит примерно на {enough}, остальных разберёт следующий запуск"


def _token_lines(plan: JudgePlan) -> list[str]:
    """Токены сметы; со вторым кругом — по кругам и итогом."""
    source = "" if plan.from_history else " (запасное число, истории ещё нет)"
    per_case = f"по {spaced(plan.per_case)} на случай{source}"
    again = plan.second
    if again is None or not again.pairs:
        return [f"Токенов: до {spaced(plan.tokens)}, {per_case}"]
    first = plan.tokens - again.tokens
    lines = [f"Первый круг: до {spaced(first)} токенов, {per_case}"] if first else []
    return [*lines, again.line(), f"Всего токенов: до {spaced(plan.tokens)}"]


def _lines(plan: JudgePlan, room: tuple[int, int] | None) -> list[str]:
    lines = [f"Спорных к разбору: {spaced(len(plan.ids))}, доноров: {spaced(plan.donors)}"]
    if plan.fresh:
        lines.append(f"Уже разобраны, страницы не открываем: {spaced(plan.fresh)}")
    if plan.without_page:
        lines.append(f"Без страницы со ссылкой, разбирать нечего: {spaced(plan.without_page)}")
    if plan.ids:
        lines += _token_lines(plan)
    if room is not None:
        lines.append(_room_line(plan, *room))
    return lines


async def estimate(session: AsyncSession, *, now: datetime | None = None) -> Estimate:
    """Смета разбора спорных: только чтение, модель не зовётся, страницы не открываются."""
    plan = await disputed_plan(session)
    refusal = await _cap_refusal(session)
    room = await _room(session, now or datetime.now(UTC))
    return Estimate(len(plan.ids), plan.tokens, _lines(plan, room), refusal)
