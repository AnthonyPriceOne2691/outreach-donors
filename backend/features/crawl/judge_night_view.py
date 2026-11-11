"""Что судья сделал сам — для экрана «Проверка»: полосы автоприменения и итог последней ночи.

**На виду — одна строка чисел** (`docs/UI_RULES.md`, 10.11.2026): сколько пар ночь разобрала, сколько
решила сама по каждой полосе и сколько из разобранных осталось человеку («не понять»). Всё
остальное — строками для «i»: итог разбора как его отдала задача (исходы, советы, токены), почему
разбор встал, сколько советов пересчитано и что с каждой полосой — что оставлено человеку и что не
вошло в потолок ночи. Слова считает сервер: экран их только называет.

**«Применено» — решения этой ночи, а не советы прохода.** Обычно числа совпадают; расходятся они,
когда полоса выключена, сработал предохранитель или ночь добирала вчерашний остаток — и тогда
разницу объясняют строки «i».

**Что остаётся человеку после «пишем»** — рядом числом: сколько доменов ждут перевода в
рекламодатели (`promote.standing`). Ночь их не переводит: «Перевести» ставит поиск адресов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.advertiser import JudgeNightModel
from backend.features.crawl import judge_bands, promote
from backend.features.crawl.judge_apply import Band
from backend.features.crawl.judge_bands import BandState
from backend.features.crawl.judge_night import RELABELLED, BandNight, Phase
from backend.features.crawl.judge_rules import ADVICE_WORDS, Advice
from backend.features.crawl.night import MOSCOW
from backend.shared.numbers import spaced

#: Что шаг делает сейчас — словами строки, пока он не кончился.
GOING = {
    Phase.JUDGING.value: "идёт разбор спорных",
    Phase.APPLYING.value: "применяются полосы",
}


@dataclass(frozen=True, slots=True)
class NightSummary:
    """Итог ночи для строки над очередью и её «i»."""

    night: date
    status: str
    #: Пар разобрано этой ночью.
    judged: int = 0
    #: Решено самой ночью по полосам: `skip`, `write`.
    applied: dict[str, int] = field(default_factory=dict)
    #: «Не понять» среди разобранных — осталось человеку.
    unclear: int = 0
    #: Шаг ещё идёт — что он делает; кончился — пусто.
    going: str | None = None
    #: Почему шаг не дошёл до конца или разбор встал — словами, на виду.
    why: str | None = None
    lines: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NightView:
    """Автоприменение на экране: как стоят полосы, что было последней ночью и что ждёт человека."""

    bands: dict[Band, BandState]
    last: NightSummary | None
    #: Шаг этой даты уже начинался: полоса, включённая сейчас, пойдёт следующей ночью, а не сегодня.
    ran_today: bool
    #: Доменов ждут перевода в рекламодатели — после «пишем» это делает человек.
    awaiting_promotion: int


def _number(body: dict[str, Any], key: str) -> int:
    value = body.get(key)
    return value if isinstance(value, int) else 0


def _band_line(band: Band, state: BandNight) -> str:
    """Полоса за ночь одной строкой: применено и что осталось — нули не называются."""
    parts = [f"«{ADVICE_WORDS[Advice(band.value)]}»: применено {spaced(state.applied)}"]
    extra = (
        ("оставлено человеку — сняло бы рекламодателя", state.spared),
        ("сверх потолка ночи, пойдут следующей", state.over_cap),
        ("пропущено — решил человек или строку заменил обход", state.passed),
        ("снято писем из очереди", state.stopped),
    )
    parts.extend(f"{words}: {spaced(count)}" for words, count in extra if count)
    return "; ".join(parts)


def _lines(row: JudgeNightModel, report: dict[str, Any], applied: dict[str, Any]) -> list[str]:
    """Строки «i»: итог разбора как есть, почему встал, пересчёт советов и полосы."""
    lines = [str(line) for line in report.get("lines", []) if isinstance(line, str)]
    if report.get("stopped"):
        lines.append(f"разбор встал: {report['stopped']}")
    relabelled = _number(applied, RELABELLED)
    if relabelled:
        lines.append(f"советов пересчитано нынешней таблицей: {spaced(relabelled)}")
    lines.extend(
        _band_line(band, BandNight.of(applied[band.value]))
        for band in Band
        if band.value in applied
    )
    if row.note:
        lines.append(row.note)
    return lines


def summary_of(row: JudgeNightModel) -> NightSummary:
    """Строка ночи → итог для экрана."""
    report, applied = dict(row.report or {}), dict(row.applied or {})
    advice = report.get("advice")
    stopped = report.get("stopped")
    return NightSummary(
        night=row.night,
        status=row.status,
        judged=_number(report, "done"),
        applied={
            band.value: BandNight.of(applied[band.value]).applied
            for band in Band
            if band.value in applied
        },
        unclear=_number(advice, Advice.UNCLEAR.value) if isinstance(advice, dict) else 0,
        going=GOING.get(row.status),
        why=row.note or (f"разбор встал: {stopped}" if stopped else None),
        lines=_lines(row, report, applied),
    )


async def view(session: AsyncSession, *, now: datetime) -> NightView:
    """Полосы, последняя ночь и что ждёт человека — одним ответом для экрана."""
    last = await session.scalar(
        select(JudgeNightModel).order_by(JudgeNightModel.night.desc()).limit(1)
    )
    today = now.astimezone(MOSCOW).date()
    return NightView(
        bands=await judge_bands.states(session),
        last=None if last is None else summary_of(last),
        ran_today=last is not None and last.night >= today,
        awaiting_promotion=(await promote.standing(session)).fresh,
    )
