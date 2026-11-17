"""Потолки расхода «Плана работ»: сколько израсходовано, что в резерве и поместится ли прогон.

Потолки гипотезы плана и плана — в долларах «выдача + модель» (решение владельца 16.11): Hunter
платится запросами своего тарифа, держится своими потолками и сюда не входит (`segment_spend`).
Здесь — чистые функции на числах и строках и одно тонкое чтение базы. Этап гипотезы и паузу плана
ставит проход конвейера (следующий срез); он же хранит числа в строке гипотезы плана (`spent_usd`,
`reserved_usd`), чтобы экран не пересчитывал сотни отчётов на каждом опросе.

- **Израсходовано** — сумма факта прогонов «деньгами» (`segment_spend.spend_rows`, последняя строка:
  выдача и модель) — тем же счётом, что панель расхода прогона. Трат ещё не было — ноль.
- **Резерв** — у прогона, который ещё идёт (`segment_watch.unfinished`): смета сверх факта. Идущий
  прогон весит `max(факт, смета)`: факт — в израсходованном, остаток сметы — в резерве.
- **Перед прогоном** (`room`): `израсходовано + резерв + смета прогона ≤ потолок` — сперва у
  гипотезы, потом у плана. Не помещается гипотеза — проход ставит ей `stopped` и проблему «кончился
  бюджет» и идёт к следующей гипотезе; не помещается план — план на паузу. Впритык — помещается.
  Потолок мягкий на один прогон: идущий прогон посреди не прерывается.
- **Деньги — `Decimal`** до сотой доли цента, как колонки плана: доллары складывают и сравнивают с
  потолком, а двоичная дробь сказала бы «через край» там, где впритык.

**Проблемы гипотезы** (`SalesPlanHypothesisModel.problems`) — список словарей `{code, words, at}`:
вид (`Problem`), слова экрана и когда. Деньги и числа в словах — помощниками слов сметы прогона
(`segment_spend.money`, `number`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales.plan_card import DEPTH_CHOICES
from backend.features.sales.plan_estimate import TOP_WORDS
from backend.features.sales.plan_models import SalesPlanHypothesisModel
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_spend import money, number, spend_rows
from backend.features.sales.segment_watch import unfinished
from backend.features.serp.protocol import RESULTS_PER_PAGE

#: Доллары до сотой доли цента — точность колонок расхода гипотезы плана (`plan_models.SPEND`).
PRECISION = Decimal("0.0001")
NOTHING = Decimal(0)
#: Предельная ступень книги: глубже не поднять, и слова «нужны ключи» глубину не предлагают.
DEEPEST = max(DEPTH_CHOICES.values())


def usd(value: float | Decimal | None) -> Decimal:
    """Доллары числом расхода — до сотой доли цента; числа нет — ноль."""
    return NOTHING if value is None else Decimal(str(value)).quantize(PRECISION)


@dataclass(frozen=True, slots=True)
class Spent:
    """Расход гипотезы плана или плана: израсходовано, резерв идущих прогонов и сколько их идёт."""

    spent: Decimal = NOTHING
    reserved: Decimal = NOTHING
    going: int = 0

    @property
    def held(self) -> Decimal:
        """Занято под потолком: израсходовано и резерв."""
        return self.spent + self.reserved

    def __add__(self, other: Spent) -> Spent:
        return Spent(
            self.spent + other.spent, self.reserved + other.reserved, self.going + other.going
        )


def run_spent(run: SalesSegmentRunModel) -> Spent:
    """Расход прогона: факт «деньгами» отчёта (`spend_rows`, последняя строка); у идущего — и резерв,
    смета сверх факта. Сметы нет — резерва нет."""
    total = spend_rows(run)[-1]
    fact = usd(total.fact_usd)
    if not unfinished(run):
        return Spent(fact)
    return Spent(fact, max(fact, usd(total.promised_usd)) - fact, 1)


async def spending(session: AsyncSession, plan_id: int) -> dict[int, Spent]:
    """Расход гипотез плана по их прогонам: гипотеза плана → `Spent`. Гипотезы без прогонов здесь нет —
    у неё ноль (`Spent()`); план целиком — суммой (`sum(…, Spent())`). Одно чтение на весь план:
    проход берёт отсюда и гипотезу, и план."""
    rows = await session.execute(
        select(SalesPlanHypothesisModel.id, SalesSegmentRunModel)
        .join(
            SalesSegmentRunModel,
            SalesSegmentRunModel.plan_hypothesis_id == SalesPlanHypothesisModel.id,
        )
        .where(SalesPlanHypothesisModel.plan_id == plan_id)
    )
    found: dict[int, Spent] = {}
    for row_id, run in rows.tuples():
        found[row_id] = found.get(row_id, Spent()) + run_spent(run)
    return found


class Outcome(StrEnum):
    """Поместится ли следующий прогон в потолки."""

    GO = "go"  # помещается: прогон можно ставить
    HYPOTHESIS_CAP = "hypothesis_cap"  # гипотеза — `stopped` и «кончился бюджет»
    PLAN_CAP = "plan_cap"  # план — на паузу


#: Чей потолок — словами экрана.
CAP_OF = {Outcome.HYPOTHESIS_CAP: "гипотезы", Outcome.PLAN_CAP: "плана"}


@dataclass(frozen=True, slots=True)
class Room:
    """Решение до прогона: исход и слова экрана; помещается — слов нет."""

    outcome: Outcome
    words: str | None = None


def cap_words(outcome: Outcome, cap: Decimal, held: Spent, next_usd: Decimal) -> str:
    """«потолок гипотезы $50,0000: израсходовано $49,3000, следующий прогон ≈ $0,9000 — поднимите
    потолок или остановите». Резерв идущих прогонов назван, когда он есть: без него числа слов не
    сходились бы с отказом."""
    spent = f"израсходовано {money(float(held.spent))}"
    if held.reserved:
        runs = "идущего прогона" if held.going == 1 else "идущих прогонов"
        spent += f" и {money(float(held.reserved))} в резерве {runs}"
    return (
        f"потолок {CAP_OF[outcome]} {money(float(cap))}: {spent}, следующий прогон ≈ "
        f"{money(float(next_usd))} — поднимите потолок или остановите"
    )


def room(
    *,
    hypothesis: Spent,
    hypothesis_cap: Decimal,
    plan: Spent,
    plan_cap: Decimal,
    next_usd: Decimal | float,
) -> Room:
    """Поместится ли прогон со сметой `next_usd` (выдача и модель): `израсходовано + резерв + смета ≤
    потолок` — сперва у гипотезы, потом у плана (шапка модуля)."""
    cost = usd(next_usd)
    for outcome, held, cap in (
        (Outcome.HYPOTHESIS_CAP, hypothesis, hypothesis_cap),
        (Outcome.PLAN_CAP, plan, plan_cap),
    ):
        if held.held + cost > cap:
            return Room(outcome, cap_words(outcome, cap, held, cost))
    return Room(Outcome.GO)


def keys_words(max_pages: int, found: int, target: int) -> str:
    """«ключи и глубина до топ-30 кончились: найдено 3 120 из 5 000 — добавьте ключи или поднимите
    глубину». На предельной ступени книги глубже не поднять — только «добавьте ключи»."""
    top = TOP_WORDS.get(max_pages, f"топ-{max_pages * RESULTS_PER_PAGE}")
    deeper = " или поднимите глубину" if max_pages < DEEPEST else ""
    return (
        f"ключи и глубина до {top} кончились: найдено {number(found)} из {number(target)} — "
        f"добавьте ключи{deeper}"
    )


class Problem(StrEnum):
    """Вид проблемы гипотезы плана; этап она не меняет — его ставит проход."""

    BUDGET = "budget"  # кончился бюджет — `cap_words`
    KEYS = "keys"  # нужны ключи — `keys_words`
    RUN_FAILED = "run_failed"  # прогон остановлен — слова `run.error`
    CHAIN = "chain"  # нет цепочки на языке писем — письма не соберутся


def problem(code: Problem, words: str, at: datetime) -> dict[str, Any]:
    """Проблема — строкой списка `problems`: вид, слова экрана и когда."""
    return {"code": code.value, "words": words, "at": at.isoformat()}


def put(problems: Sequence[dict[str, Any]], found: dict[str, Any]) -> list[dict[str, Any]]:
    """Список проблем с новой: прежняя того же вида уходит — у вида одни слова, последние. Новый
    список, а не правка на месте: колонку JSONB база видит изменённой только по присваиванию."""
    return [*(old for old in problems if old.get("code") != found["code"]), found]
