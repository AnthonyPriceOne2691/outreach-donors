"""Расход прогона сбора рядом со сметой: что обещала смета на запуске и что вышло на деле.

Человек решает «запускать ли» по смете, а «сколько стоило» — по отчёту прогона: оба говорят одними
статьями и единицами, рядом (`okf/unit-economy.md`). **Статья — своей единицей:** счета у
провайдеров разные, и складывать их нельзя; деньгами — там, где платят деньгами:

- **выдача** — доллары, как их назвал провайдер (`report.serp.cost_usd`);
- **судья сегмента** — токены за все проходы (`report.judge.tokens`) и доллары по цене токена,
  с которой считалась смета (`estimate.usd_per_1m_tokens`; у прогонов до неё — по настройке);
- **поиск адреса Hunter** — запросы, которые провайдер принял: вошло в платную ступень минус
  отказы (`report.contacts.counters` — тем же счётом, что строка расхода `contacts_search`);
- **проверка адреса Hunter** — проверки (`report.leads.cleaning.paid_units`).

Деньги вместе — только выдача и модель: Hunter платится запросами тарифа и в доллары не
переводится. **Этап, до которого прогон не дошёл, — «ещё не было», а не ноль:** ноль сказал бы
«обошлось бесплатно». Не записанная цена — «не записана», а не ноль (`CONSTITUTION.md`,
«Неизвестный расход не равен нулевому»). Продолжение прогона тратит снова — отчёт копит
расход по проходам (`segment_run`), панель читает итог.

**Цена токена — смешанная.** Журнал и отчёт хранят токены суммой, а вход и вывод модели стоят
по-разному: доля вывода (`OUTPUT_SHARE`) — предложение до замера разбивки живым прогоном.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.verifier import FIXTURE

#: Доля вывода в токенах судьи сегмента — для цены токена. Вход — промпт, описание сегмента и
#: текст сайта (сотни токенов), вывод — рассуждение на `low` и короткий JSON. Предложение до
#: замера: разбивку `prompt_tokens`/`completion_tokens` назовёт живой прогон.
OUTPUT_SHARE = 0.2
#: Токенов в миллионе: цены модели — за миллион.
MILLION = 1_000_000

#: Чего ещё не было — словами по статье: этап не начинался, а не «ноль».
NOT_YET = {
    "serp": "выдачу ещё не покупали",
    "judge": "судья ещё не судил",
    "search": "адреса ещё не искали",
    "check": "адреса ещё не проверяли",
}


def number(value: int) -> str:
    """12345 → «12 345»: узкие группы, как пишут числа на экранах."""
    return f"{value:,}".replace(",", " ")


def money(value: float) -> str:
    """Доллары до десятой доли цента: выдача стоит доли цента за страницу."""
    return f"${value:.4f}".replace(".", ",")


def usd_per_million() -> float:
    """Цена миллиона токенов судьи сегмента — вход и вывод модели, смешанные долей вывода."""
    return (1 - OUTPUT_SHARE) * sales_cfg.SEGMENT_INPUT_USD_PER_1M_TOKENS + (
        OUTPUT_SHARE * sales_cfg.SEGMENT_OUTPUT_USD_PER_1M_TOKENS
    )


def tokens_usd(tokens: int, per_million: float) -> float:
    return round(tokens * per_million / MILLION, 4)


@dataclass(frozen=True, slots=True)
class SpendRow:
    """Статья расхода прогона: факт и смета на запуске — словами и числами. Пустое число —
    в этой единице не платили или этапа ещё не было (тогда слова говорят, что именно)."""

    item: str
    title: str
    fact: str
    promised: str
    fact_units: int | None = None
    fact_usd: float | None = None
    promised_units: int | None = None
    promised_usd: float | None = None


def _section(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    found = report.get(name)
    return found if isinstance(found, dict) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _serp(report: dict[str, Any], estimate: dict[str, Any]) -> SpendRow:
    promised_usd = _float(estimate.get("serp_usd"))
    promised = "смета не записала" if promised_usd is None else f"≈ {money(promised_usd)}"
    row = SpendRow("serp", "Выдача", NOT_YET["serp"], promised, promised_usd=promised_usd)
    serp = _section(report, "serp")
    if serp is None:
        return row
    usd = _float(serp.get("cost_usd"))
    if usd is None:
        return SpendRow("serp", row.title, "цена не записана", promised, promised_usd=promised_usd)
    usd = round(usd, 4)
    return SpendRow(
        "serp", row.title, money(usd), promised, fact_usd=usd, promised_usd=promised_usd
    )


def _judge(
    report: dict[str, Any], estimate: dict[str, Any], per_million: float, mode: str
) -> SpendRow:
    title = "Судья сегмента"
    if mode == JudgeMode.OFF:
        return SpendRow("judge", title, "модель не звалась", "судья выключен", 0, None, 0, None)
    promised_tokens = _int(estimate.get("tokens"))
    promised_usd = _float(estimate.get("tokens_usd"))
    promised = (
        "смета не записала"
        if promised_tokens is None
        else f"токенов ≈ {number(promised_tokens)}"
        + ("" if promised_usd is None else f", ≈ {money(promised_usd)}")
    )
    judge = _section(report, "judge")
    tokens = None if judge is None else _int(judge.get("tokens"))
    if tokens is None:
        fact = NOT_YET["judge"] if judge is None else "токены не записаны"
        return SpendRow("judge", title, fact, promised, None, None, promised_tokens, promised_usd)
    usd = tokens_usd(tokens, per_million)
    fact = f"токенов {number(tokens)}, ≈ {money(usd)}"
    return SpendRow("judge", title, fact, promised, tokens, usd, promised_tokens, promised_usd)


def _search(report: dict[str, Any], estimate: dict[str, Any]) -> SpendRow:
    title = "Hunter: поиск адреса"
    promised_units = _int(estimate.get("hunter_searches")) or 0
    promised = f"до {number(promised_units)}" if promised_units else "платный поиск выключен"
    contacts = _section(report, "contacts")
    if contacts is None:
        return SpendRow("search", title, NOT_YET["search"], promised, None, None, promised_units)
    counters = contacts.get("counters")
    counters = counters if isinstance(counters, dict) else {}
    refused = _int(counters.get("provider_refused")) or 0
    paid = max(0, (_int(counters.get("provider_entered")) or 0) - refused)
    fact = f"поисков {number(paid)}" if paid else "платных поисков не было"
    if refused:
        fact += f"; отказов провайдера {number(refused)} — их он не засчитывает"
    return SpendRow("search", title, fact, promised, paid, None, promised_units)


def _check(report: dict[str, Any], estimate: dict[str, Any]) -> SpendRow:
    title = "Hunter: проверка адреса"
    promised_units = _int(estimate.get("hunter_checks")) or 0
    promised = f"до {number(promised_units)}" if promised_units else "проверка выдуманная"
    leads = _section(report, "leads")
    if leads is None:
        return SpendRow("check", title, NOT_YET["check"], promised, None, None, promised_units)
    cleaning = _section(leads, "cleaning")
    if cleaning is None:
        fact = "лидов не заводили — проверок не было"
        return SpendRow("check", title, fact, promised, 0, None, promised_units)
    units = _int(cleaning.get("paid_units")) or 0
    fact = (
        f"проверок {number(units)}"
        if units
        else "проверка выдуманная — не платили"
        if cleaning.get("verifier") == FIXTURE
        else "платных проверок не было"
    )
    return SpendRow("check", title, fact, promised, units, None, promised_units)


def _total(rows: list[SpendRow]) -> SpendRow:
    """Деньгами — выдача и модель, факт и смета. Hunter в сумму не входит: он не в долларах."""
    paid = [row for row in rows if row.item in ("serp", "judge")]
    facts = [row.fact_usd for row in paid if row.fact_usd is not None]
    promises = [row.promised_usd for row in paid if row.promised_usd is not None]
    fact_usd = round(sum(facts), 4) if facts else None
    promised_usd = round(sum(promises), 4) if promises else None
    return SpendRow(
        "money",
        "Деньгами: выдача и модель",
        "трат ещё не было" if fact_usd is None else money(fact_usd),
        "смета не записала" if promised_usd is None else f"≈ {money(promised_usd)}",
        fact_usd=fact_usd,
        promised_usd=promised_usd,
    )


def spend_rows(run: SalesSegmentRunModel) -> list[SpendRow]:
    """Статьи расхода прогона с его сметой — и деньги вместе последней строкой."""
    report, estimate = run.report or {}, run.estimate or {}
    per_million = _float(estimate.get("usd_per_1m_tokens")) or usd_per_million()
    rows = [
        _serp(report, estimate),
        _judge(report, estimate, per_million, run.mode),
        _search(report, estimate),
        _check(report, estimate),
    ]
    return [*rows, _total(rows)]
