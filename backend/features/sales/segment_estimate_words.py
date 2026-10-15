"""Смета прогона сбора словами — строкой на статью: выдача, сайты, судья, Hunter, деньги.

Вынесено из `segment_estimate.py` целиком (он у предела длины): там смета считает, здесь — как она
говорит. Что нужно из сметы, кроме самих чисел, — параметрами `lines_of` (токенов на сайт,
непрочитанный потолок модели), и цикла импорта нет.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.config.judge import JudgeMode
from backend.features.sales.segment_spend import money, number
from backend.features.serp.modes import SerpMode

if TYPE_CHECKING:
    from backend.features.sales.segment_estimate import Estimate

#: Режим покупки выдачи словами — как в форме: быстрый — секунды, стандарт — дешевле и дольше.
SERP_MODE_WORDS = {
    SerpMode.LIVE: "быстрая (Live)",
    SerpMode.PRIORITY: "приоритетная",
    SerpMode.STANDARD: "стандарт",
}


def _wait(seconds: int) -> str:
    """Сколько ждать выдачу — словами: секунды или минуты."""
    return f"{seconds} с" if seconds < 60 else f"{round(seconds / 60)} мин"


def _serp_line(found: Estimate) -> str:
    mode = SERP_MODE_WORDS[SerpMode(found.serp_mode)]
    return (
        f"Выдача — {mode}: ключей {number(len(found.keywords))} × страниц {found.depth_pages} — "
        f"результатов до {number(found.results)}, по {money(found.serp_page_usd)} за страницу "
        f"≈ {money(found.serp_usd)}; ждать ≈ {_wait(found.serp_wait_s)}."
    )


def _sites_line(found: Estimate) -> str:
    """Сколько сайтов даст выдача и откуда доля; сколько из них уже в сборе гипотезы."""
    share = f"{number(found.hosts)} — {round(found.share * 100)}% результатов"
    line = (
        f"Сайтов ≈ {share}, как в прошлых прогонах сбора (с выдачей — {found.share_runs}): "
        "выдача отдаёт не все результаты и повторяет сайты."
        if found.share_runs
        else f"Сайтов ≈ {share}: прогонов сбора с выдачей ещё нет, доля — худшая из замеренных у "
        "выдачи, смета скорее завышена."
    )
    if not found.known:
        return line
    return (
        f"{line} Уже в сборе гипотезы ≈ {number(found.known)} — их не судят и адрес им не ищут: "
        f"новых ≈ {number(found.sites)}."
    )


def _judge_line(found: Estimate, *, per_site: int, cap_unread: bool) -> str:
    if found.mode == JudgeMode.OFF:
        return (
            f"Судья сегмента выключен: модель не зовётся, сайтов ≈ {number(found.sites)} — "
            "решает человек."
        )
    left = (
        ""
        if found.tokens_left is None
        else ", дневной потолок модели продаж не прочитан из настроек — токенов ноль"
        if cap_unread
        else f", остаток дневного потолка — {number(found.tokens_left)}"
    )
    share = f"{round(found.output_share * 100)}% токенов"
    shared = (
        f"доля вывода — {share}, как в прошлых прогонах с раздельным счётом "
        f"({found.output_share_runs})"
        if found.output_share_runs
        else f"доля вывода — запасная, замеров ещё нет: {share}"
    )
    return (
        f"Судья сегмента: сайтов ≈ {number(found.sites)}, токенов модели ≈ {number(found.tokens)} "
        f"(по {number(per_site)} на сайт) — ≈ {money(found.tokens_usd)}{left}; {shared}."
    )


def _hunter_line(found: Estimate) -> str:
    searches = (
        f"поисков адреса до {number(found.hunter_searches)} — только тем, кому бесплатные "
        "ступени адреса не нашли"
        if found.hunter_searches
        else "платный поиск адреса у сбора выключен или не настроен — только бесплатные ступени"
    )
    month = "" if found.month is None else f", {found.month.words}"
    checks = (
        f"проверок адреса до {number(found.hunter_checks)}{month}"
        if found.hunter_checks
        else "проверка адреса выдуманная"
    )
    return f"Hunter: {searches}; {checks}."


def _money_line(found: Estimate) -> str:
    """Деньги вместе — выдача и модель. Hunter — запросами тарифа: в сумму не входит."""
    what = "выдача" if found.mode == JudgeMode.OFF else "выдача и модель"
    hunter = (
        "Hunter платится запросами своего тарифа и в сумму не входит"
        if found.hunter_searches or found.hunter_checks
        else "Hunter не тратится"
    )
    return f"Деньгами: ≈ {money(found.serp_usd + found.tokens_usd)} — {what}; {hunter}."


def lines_of(found: Estimate, *, per_site: int, cap_unread: bool) -> list[str]:
    """Смета словами — строкой на статью. `per_site` — токенов судьи на сайт, `cap_unread` —
    свой потолок модели продаж не прочитан из настроек (`segment_estimate`)."""
    lines = [
        _serp_line(found),
        _sites_line(found),
        _judge_line(found, per_site=per_site, cap_unread=cap_unread),
    ]
    if found.mode == JudgeMode.SHADOW:
        lines.append("Судья в наблюдении: его «отказ» не отсекает сайт, а отдаёт человеку.")
    return [*lines, _hunter_line(found), _money_line(found)]
