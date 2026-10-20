"""Расход прогона сбора рядом со сметой: что обещала смета на запуске и что вышло на деле.

Человек решает «запускать ли» по смете, а «сколько стоило» — по отчёту прогона: оба говорят одними
статьями и единицами, рядом (`okf/unit-economy.md`). **Статья — своей единицей:** счета у
провайдеров разные, и складывать их нельзя; деньгами — там, где платят деньгами:

- **выдача** — доллары, как их назвал провайдер (`report.serp.cost_usd`);
- **судья сегмента** — токены за все проходы (`report.judge.tokens`) и доллары по ценам токена,
  с которыми считалась смета (у прогонов до них — по настройкам);
- **пересуд** — сумма всех пересудов прогона (`report.rejudges`, `segment_rejudge_report`):
  токены и доллары теми же ценами; пересудов не было — строки нет;
- **поиск адреса Hunter** — запросы, которые провайдер принял: вошло в платную ступень минус
  отказы (`report.contacts.counters` — тем же счётом, что строка расхода `contacts_search`);
- **проверка адреса Hunter** — проверки (`report.leads.cleaning.paid_units`).

Деньги вместе — только выдача и модель (судья и пересуд): Hunter платится запросами тарифа и в доллары не
переводится. **Этап, до которого прогон не дошёл, — «ещё не было», а не ноль:** ноль сказал бы
«обошлось бесплатно». Не записанная цена — «не записана», а не ноль (`CONSTITUTION.md`,
«Неизвестный расход не равен нулевому»). **Выдача, которую покупают или чья покупка прервана**
(`segment_serp.buying`: номера задач в отчёте до опроса, итога нет), — «покупка не закончена»:
«цена не записана» стояло бы все минуты очереди провайдера, а задачи уже оплачены, и «трат
ещё не было» в деньгах вместе — тоже неправда. Продолжение прогона тратит снова — отчёт копит
расход по проходам (`segment_run`), панель читает итог.

**Цена токена — раздельная.** Ввод и вывод модели стоят по-разному, и рассуждение идёт в счёт
вывода: отчёт копит токены раздельно (`segment_window.SPLIT_TOKENS`), и доллары — ввод по цене
ввода, вывод — по цене вывода. Токены без разбивки (провайдер не назвал, прогон судился до неё:
`tokens` без ввода и вывода) — смешанной ценой с запасной долей вывода (`OUTPUT_SHARE`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_rejudge_report import totals_of
from backend.features.sales.segment_serp import buying
from backend.features.sales.verifier import FIXTURE

#: Запасная доля вывода в токенах судьи сегмента — для смешанной цены, когда разбивки нет: в
#: смете без истории раздельного счёта (`segment_history.output_share`) и у токенов без разбивки.
#: Живой замер 15.10 (eval на синтетике, 23 сайта, gpt-5-mini): при рассуждении `low` вывод
#: ≈ 190 токенов на сайт (из них рассуждение ≈ 140), ввод ≈ 440; на настоящих сайтах (~1 100
#: токенов на сайт, замер прогона) это ≈ 17 % — 0,2 с запасом вверх. При `minimal` вывод ≈ 48 на
#: сайт при тех же воротах синтетики; переход — только после размеченного набора (golden).
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
#: Выдачу покупают или покупка прервана: цены в отчёте ещё нет, её назовёт итог покупки.
BUYING = "покупка не закончена — цену назовёт её итог"
#: Деньги вместе, пока выдача не куплена: задачи отложенного режима уже оплачены у провайдера.
AFTER_SERP = "сумма — после покупки выдачи"


def number(value: int) -> str:
    """12345 → «12 345»: разряды — неразрывным пробелом, как у экрана: число не рвётся посреди."""
    return f"{value:,}".replace(",", "\u00a0")


def money(value: float) -> str:
    """Доллары до десятой доли цента: выдача стоит доли цента за страницу."""
    return f"${value:.4f}".replace(".", ",")


def usd_per_million(
    share: float = OUTPUT_SHARE, *, input_usd: float | None = None, output_usd: float | None = None
) -> float:
    """Цена миллиона токенов судьи сегмента — ввод и вывод модели, смешанные долей вывода; цены —
    названные или настроек."""
    entered = sales_cfg.SEGMENT_INPUT_USD_PER_1M_TOKENS if input_usd is None else input_usd
    produced = sales_cfg.SEGMENT_OUTPUT_USD_PER_1M_TOKENS if output_usd is None else output_usd
    return (1 - share) * entered + share * produced


def tokens_usd(tokens: int, per_million: float) -> float:
    return round(tokens * per_million / MILLION, 4)


@dataclass(frozen=True, slots=True)
class Prices:
    """Цены миллиона токенов модели для расхода прогона: ввод, вывод и токены без разбивки."""

    input: float
    output: float
    unsplit: float


def prices_of(estimate: dict[str, Any]) -> Prices:
    """Цены на запуске — из сметы прогона: правка настроек потом прошлые отчёты не переписывает.
    Без раздельных цен в смете (прогоны до них) — цены настроек, а токены без разбивки — смешанной
    ценой сметы, по которой она и считала."""
    entered, produced = (
        _float(estimate.get("usd_per_1m_input")),
        _float(estimate.get("usd_per_1m_output")),
    )
    if entered is not None and produced is not None:
        return Prices(entered, produced, usd_per_million(input_usd=entered, output_usd=produced))
    unsplit = _float(estimate.get("usd_per_1m_tokens")) or usd_per_million()
    return Prices(
        sales_cfg.SEGMENT_INPUT_USD_PER_1M_TOKENS,
        sales_cfg.SEGMENT_OUTPUT_USD_PER_1M_TOKENS,
        unsplit,
    )


def split_of(numbers: dict[str, Any]) -> tuple[int, int, int]:
    """Токены раздельно: ввод, вывод и без разбивки. Без разбивки — то, что в `tokens` сверх ввода
    и вывода: так и у прогона, судившегося до раздельного счёта (его ранние токены есть только в
    `tokens`), и у провайдера, не назвавшего ввод и вывод."""
    tokens = _int(numbers.get("tokens")) or 0
    entered = _int(numbers.get("prompt_tokens")) or 0
    produced = _int(numbers.get("completion_tokens")) or 0
    return entered, produced, max(0, tokens - entered - produced)


def model_usd(numbers: dict[str, Any], prices: Prices) -> float:
    """Доллары токенов модели: ввод и вывод — своими ценами, без разбивки — смешанной."""
    entered, produced, unsplit = split_of(numbers)
    total = entered * prices.input + produced * prices.output + unsplit * prices.unsplit
    return round(total / MILLION, 4)


def _tokens_words(numbers: dict[str, Any], tokens: int, usd: float) -> str:
    """«токенов 40 211, из них вывод 6 900, ≈ $0,0241» — вывод назван, когда разбивка есть."""
    entered, produced, unsplit = split_of(numbers)
    if not entered + produced:
        return f"токенов {number(tokens)}, ≈ {money(usd)}"
    rest = f" (без разбивки {number(unsplit)})" if unsplit else ""
    return f"токенов {number(tokens)}, из них вывод {number(produced)}{rest}, ≈ {money(usd)}"


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
    usd = None if serp is None else _float(serp.get("cost_usd"))
    if usd is None and buying(report):
        return SpendRow("serp", row.title, BUYING, promised, promised_usd=promised_usd)
    if serp is None:
        return row
    if usd is None:
        return SpendRow("serp", row.title, "цена не записана", promised, promised_usd=promised_usd)
    usd = round(usd, 4)
    return SpendRow(
        "serp", row.title, money(usd), promised, fact_usd=usd, promised_usd=promised_usd
    )


def _judge(report: dict[str, Any], estimate: dict[str, Any], prices: Prices, mode: str) -> SpendRow:
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
    usd = model_usd(judge or {}, prices)
    fact = _tokens_words(judge or {}, tokens, usd)
    return SpendRow("judge", title, fact, promised, tokens, usd, promised_tokens, promised_usd)


#: У пересуда своя смета — перед каждым пересудом (`segment_rejudge_estimate`), не на запуске.
REJUDGE_PROMISED = "в смете запуска нет — у пересуда своя смета"


def _rejudge(report: dict[str, Any], prices: Prices) -> SpendRow | None:
    """Сумма всех пересудов прогона; пересудов не было — строки нет."""
    totals = totals_of(report)
    if not totals["count"]:
        return None
    usd = model_usd(totals, prices)
    fact = f"пересудов {number(totals['count'])}: " + _tokens_words(totals, totals["tokens"], usd)
    return SpendRow("rejudge", "Пересуд", fact, REJUDGE_PROMISED, totals["tokens"], usd)


def _counters(report: dict[str, Any]) -> dict[str, Any]:
    contacts = _section(report, "contacts")
    counters = None if contacts is None else contacts.get("counters")
    return counters if isinstance(counters, dict) else {}


#: Резерв платных поисков повтора (`contacts.paid_pending`, `segment_paid`): кусок поиска оставил
#: его перед собой и снимает после — не снятым он остаётся, только если проход упал посреди куска.
PENDING_KEY = "paid_pending"
#: Принято провайдером, но не записано в счёт: резерв упавшего прохода, сведённый следующим
#: (`segment_paid.settle`), — верхней границей.
UNRECORDED_KEY = "paid_unrecorded"


def pending(report: dict[str, Any]) -> int:
    """Резерв платных поисков, не снятый упавшим проходом повтора: остаток сметы его вычитает
    (`segment_queue.paid_left`) — повтор задачи не потратит его второй раз."""
    contacts = _section(report, "contacts") or {}
    return _int(contacts.get(PENDING_KEY)) or 0


def unrecorded(report: dict[str, Any]) -> int:
    """Платных поисков, принятых провайдером, но не записанных в счёт прогона (верхняя граница)."""
    contacts = _section(report, "contacts") or {}
    return _int(contacts.get(UNRECORDED_KEY)) or 0


def searched(report: dict[str, Any]) -> int:
    """Запросов поиска адреса, которые провайдер принял у прогона за все проходы: вошло в платную
    ступень минус отказы. Им же меряет остаток сметы повтор по сроку (`segment_queue.paid_left`)."""
    counters = _counters(report)
    refused = _int(counters.get("provider_refused")) or 0
    return max(0, (_int(counters.get("provider_entered")) or 0) - refused)


def _search(report: dict[str, Any], estimate: dict[str, Any]) -> SpendRow:
    title = "Hunter: поиск адреса"
    promised_units = _int(estimate.get("hunter_searches")) or 0
    promised = f"до {number(promised_units)}" if promised_units else "платный поиск выключен"
    if _section(report, "contacts") is None:
        return SpendRow("search", title, NOT_YET["search"], promised, None, None, promised_units)
    refused = _int(_counters(report).get("provider_refused")) or 0
    paid = searched(report)
    fact = f"поисков {number(paid)}" if paid else "платных поисков не было"
    if refused:
        fact += f"; отказов провайдера {number(refused)} — их он не засчитывает"
    if unsettled := pending(report) + unrecorded(report):
        fact += f"; ещё до {number(unsettled)} — не записаны: проход повтора упал посреди поиска"
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


def _paid_words(fact_usd: float | None, paid: list[SpendRow]) -> str:
    """Деньги вместе словами: сумма; без суммы — «после покупки», пока выдачу покупают (задачи
    уже оплачены у провайдера), иначе трат ещё не было."""
    if fact_usd is not None:
        return money(fact_usd)
    return AFTER_SERP if any(row.fact == BUYING for row in paid) else "трат ещё не было"


def _total(rows: list[SpendRow]) -> SpendRow:
    """Деньгами — выдача и модель (судья и пересуд), факт и смета. Hunter в сумму не входит: он
    не в долларах."""
    paid = [row for row in rows if row.item in ("serp", "judge", "rejudge")]
    facts = [row.fact_usd for row in paid if row.fact_usd is not None]
    promises = [row.promised_usd for row in paid if row.promised_usd is not None]
    fact_usd = round(sum(facts), 4) if facts else None
    promised_usd = round(sum(promises), 4) if promises else None
    return SpendRow(
        "money",
        "Деньгами: выдача и модель",
        _paid_words(fact_usd, paid),
        "смета не записала" if promised_usd is None else f"≈ {money(promised_usd)}",
        fact_usd=fact_usd,
        promised_usd=promised_usd,
    )


def spend_rows(run: SalesSegmentRunModel) -> list[SpendRow]:
    """Статьи расхода прогона с его сметой — и деньги вместе последней строкой."""
    report, estimate = run.report or {}, run.estimate or {}
    prices = prices_of(estimate)
    rejudge = _rejudge(report, prices)
    rows = [
        _serp(report, estimate),
        _judge(report, estimate, prices, run.mode),
        *([] if rejudge is None else [rejudge]),
        _search(report, estimate),
        _check(report, estimate),
    ]
    return [*rows, _total(rows)]
