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
вид (`Problem`), слова экрана и когда. Деньги в словах — по правилу денег сервиса («48,10 $», доли
цента — «меньше 0,01 $», `said_usd`), числа — помощником слов сметы прогона (`segment_spend.number`).

**Встала именно на потолке** — для поднятого потолка (`plan_actions.py`): гипотеза — `stopped`, и
последняя из проблем остановки у неё «кончился бюджет» (`stopped_on_cap`: новая проблема ложится в
конец списка, `put`); план — на паузе словами своего потолка (`paused_on_cap`, начало слов —
`cap_lead`). Остановленные кнопкой, ключами, прогоном или сервисом встали не на потолке.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales.plan_card import DEPTH_CHOICES
from backend.features.sales.plan_estimate import TOP_WORDS
from backend.features.sales.plan_models import (
    PlanStage,
    PlanStatus,
    SalesPlanHypothesisModel,
    SalesPlanModel,
)
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_spend import number, spend_rows
from backend.features.sales.segment_watch import unfinished
from backend.features.serp.funds import dollars
from backend.features.serp.protocol import RESULTS_PER_PAGE

#: Доллары до сотой доли цента — точность колонок расхода гипотезы плана (`plan_models.SPEND`).
PRECISION = Decimal("0.0001")
NOTHING = Decimal(0)
#: Меньше полуцента в копейках — ноль: такая сумма пишется «меньше 0,01 $».
HALF_CENT = Decimal("0.005")
#: Цент — точность потолка (`plan_models.CAP`).
CENT = Decimal("0.01")
#: Предельная ступень книги: глубже не поднять, и слова «нужны ключи» глубину не предлагают.
DEEPEST = max(DEPTH_CHOICES.values())


def usd(value: float | Decimal | None) -> Decimal:
    """Доллары числом расхода — до сотой доли цента; числа нет — ноль."""
    return NOTHING if value is None else Decimal(str(value)).quantize(PRECISION)


def said_usd(value: Decimal) -> str:
    """Деньги словами экрана — по одному правилу сервиса (`frontend/src/format.ts`, `formatMoney`):
    «48,10 $», разряды и знак — через неразрывный пробел (`serp/funds.dollars`). Сумма больше нуля,
    которая в копейках вышла бы нулём, — «меньше 0,01 $»: «≈ 0,00 $» читалось бы как «даром»."""
    if NOTHING < value < HALF_CENT:
        return f"меньше {dollars(0.01)}"
    return dollars(float(value))


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
#: Где поднять потолок — словами экрана: потолок гипотезы — поле окна «Качество сайтов», плана — кнопка
#: «Потолок» у строки плана (стенд 20.11: слова «поднимите потолок» не говорили, где).
RAISE_AT = {Outcome.HYPOTHESIS_CAP: "в «Качестве сайтов»", Outcome.PLAN_CAP: "кнопкой «Потолок»"}


@dataclass(frozen=True, slots=True)
class Room:
    """Решение до прогона: исход и слова экрана; помещается — слов нет."""

    outcome: Outcome
    words: str | None = None


def cap_lead(outcome: Outcome) -> str:
    """Начало слов потолка (`cap_words`): «потолок плана ». По нему видно, что план стоит именно на
    своём потолке (`paused_on_cap`)."""
    return f"потолок {CAP_OF[outcome]} "


def cap_words(outcome: Outcome, cap: Decimal, held: Spent, next_usd: Decimal) -> str:
    """«потолок гипотезы 50,00 $: израсходовано 49,30 $, следующий прогон ≈ 0,90 $ — поднимите
    потолок в «Качестве сайтов» или остановите». Резерв идущих прогонов назван, когда он есть: без
    него числа слов не сходились бы с отказом. Где поднять — `RAISE_AT`."""
    spent = f"израсходовано {said_usd(held.spent)}"
    if held.reserved:
        runs = "идущего прогона" if held.going == 1 else "идущих прогонов"
        spent += f" и {said_usd(held.reserved)} в резерве {runs}"
    return (
        f"{cap_lead(outcome)}{said_usd(cap)}: {spent}, следующий прогон ≈ "
        f"{said_usd(next_usd)} — поднимите потолок {RAISE_AT[outcome]} или остановите"
    )


def below_spent(cap: Decimal, spent: Decimal) -> str | None:
    """Потолок ниже израсходованного — слова отказа без начала «потолок гипотезы …»: «не может быть
    ниже израсходованного: израсходовано 12,40 $ — задайте от 12,40 $». «От» — израсходованное,
    поднятое до цента: потолок — в центах, а расход — до сотой доли цента. Расход с долей цента —
    «израсходовано больше 1,73 $ — задайте от 1,74 $»: округлённое «израсходовано 1,73 $» рядом с
    отказом потолку 1,73 читалось противоречием (стенд 20.11). Не ниже — `None`."""
    if cap >= spent:
        return None
    least = spent.quantize(CENT, rounding=ROUND_CEILING)
    whole = spent.quantize(CENT, rounding=ROUND_FLOOR)
    # Меньше цента — «меньше 0,01 $» правила денег, а не «больше 0,00 $».
    said = said_usd(spent) if least == whole or not whole else f"больше {said_usd(whole)}"
    return (
        f"не может быть ниже израсходованного: израсходовано {said} — задайте от {said_usd(least)}"
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
    STOPPED = "stopped"  # остановлена кнопкой — `plan_actions.py`


def problem(code: Problem, words: str, at: datetime) -> dict[str, Any]:
    """Проблема — строкой списка `problems`: вид, слова экрана и когда."""
    return {"code": code.value, "words": words, "at": at.isoformat()}


def put(problems: Sequence[dict[str, Any]], found: dict[str, Any]) -> list[dict[str, Any]]:
    """Список проблем с новой: прежняя того же вида уходит — у вида одни слова, последние. Новый
    список, а не правка на месте: колонку JSONB база видит изменённой только по присваиванию."""
    return [*(old for old in problems if old.get("code") != found["code"]), found]


def reworded(problems: Sequence[dict[str, Any]], code: Problem, words: str) -> list[dict[str, Any]]:
    """Список проблем, где у вида `code` новые слова — на том же месте и с тем же временем: та же
    проблема, пересказанная заново, а не новая, как у `put` (`plan_moves.restate_keys`)."""
    return [{**old, "words": words} if old.get("code") == code else old for old in problems]


#: Проблемы, с которыми гипотеза встаёт в `stopped`: «кончился бюджет», «нужны ключи», «прогон
#: встал» и «остановлена кнопкой».
STOPS = frozenset({Problem.BUDGET, Problem.KEYS, Problem.RUN_FAILED, Problem.STOPPED})


def stopped_on_cap(row: SalesPlanHypothesisModel) -> bool:
    """Гипотеза стоит именно на своём потолке: `stopped`, и последняя из проблем остановки —
    «кончился бюджет». Новая проблема ложится в конец списка (`put`), поэтому последняя — та, с
    которой гипотеза встала: остановленная кнопкой после «кончился бюджет» стоит кнопкой, а слова
    «прогон не поставлен», сказанные до потолка, гипотезу не держат."""
    if row.stage != PlanStage.STOPPED:
        return False
    codes = [item.get("code") for item in row.problems if item.get("code") in STOPS]
    return bool(codes) and codes[-1] == Problem.BUDGET


def paused_on_cap(plan: SalesPlanModel) -> bool:
    """План стоит именно на своём потолке: на паузе словами потолка плана (`cap_words`). На паузе
    кнопкой «Остановить план», счётом выдачи или моделью — слова другие."""
    words = plan.problem or ""
    return plan.status == PlanStatus.PAUSED and words.startswith(cap_lead(Outcome.PLAN_CAP))
