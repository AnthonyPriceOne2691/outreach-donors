"""Предварительный расчёт плана работ до запуска — по гипотезе и на весь план, диапазоном.

Потолок расхода ставится на гипотезу и на план (решение владельца 16.11), а смета видна при
загрузке книги (`plan_file.py`). Глубина нарастает: все ключи — топ-10; не хватило до цели — топ-20, 30, 50,
но не дальше «Глубины поиска до» листа. Где конвейер остановится, до выдачи не знает никто: цель
считает сайты, прошедшие судью. Поэтому смета — диапазон:

- **от** (`low`) — первый проход: все ключи гипотезы на топ-10 по каждому её рынку;
- **до** (`high`) — все ключи до предельной глубины.

**Числа — сметой прогона сбора** (`segment_estimate.priced`), своего расчёта здесь нет: на каждый
рынок гипотезы — смета прогона с её ключами, глубиной и режимом выдачи; рынки, гипотезы и план —
суммой. Доля сайтов в выдаче, токены судьи на сайт, доля вывода — из той же истории прогонов, цены —
те же. Гипотез плана в базе ещё нет, и сайтов, уже собранных гипотезой, смета не вычитает: скорее
завысит, чем занизит. Предел ключей прогона (`SERP_MAX_KEYWORDS_PER_RUN`) здесь не отказ: ключи листа
конвейер нарежет на прогоны, а от нарезки цена не меняется.

**Верхняя граница верна, если углубление покупает только новые страницы.** Выдача сейчас берёт
глубину с первой позиции (`serp/dataforseo.py`): проход на топ-20 после топ-10 оплатил бы топ-10
второй раз, а ступенями до топ-50 вышло бы 1 + 2 + 3 + 5 = 11 страниц на ключ вместо пяти. Покупка
одних новых страниц — правка общего кода выдачи вместе с конвейером.

**Режим** — режим покупки выдачи (`serp/modes.py`): экономный — `standard`. Судья пакетом (вдвое
дешевле) в смету не входит: пакетного пути у модели ещё нет, и смета назвала бы цену, которую прогон
не заплатит. Появится — цена токена по режиму встанет в `segment_spend.usd_per_million`, и её возьмут
обе сметы сразу.

**Листы с ошибками** в смету не входят: к запуску они не идут. **Ключей мало для цели** —
предупреждение гипотезы: даже до предельной глубины выдача даст меньше сайтов, чем цель, а судья
пропустит не все.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.features.runs.estimate import UNIQUE_SHARE
from backend.features.sales.plan_card import DEPTH_CHOICES
from backend.features.sales.plan_file import PlanDraft, PlanHypothesis
from backend.features.sales.segment_estimate import TOKENS_PER_SITE, Estimate, priced
from backend.features.sales.segment_spend import number
from backend.features.serp.modes import SerpMode

#: Глубина страницами — словами книги: «топ-50».
TOP_WORDS = {pages: words for words, pages in DEPTH_CHOICES.items()}
#: Первый проход — топ-10: одна страница выдачи.
FIRST_PAGES = 1


@dataclass(frozen=True, slots=True)
class Spend:
    """Проход: числа сметы прогона, сложенные по рынкам, а на плане — и по гипотезам."""

    #: Запросов выдачи: ключей на рынки.
    queries: int = 0
    #: Результатов выдачи: запросы на страницы по 10.
    results: int = 0
    #: Сайтов в выдаче: их судит судья и им ищут адрес.
    sites: int = 0
    serp_usd: float = 0.0
    tokens: int = 0
    tokens_usd: float = 0.0
    #: Hunter — запросами своего тарифа, не долларами: не больше поиска и проверки на сайт.
    hunter_searches: int = 0
    hunter_checks: int = 0

    @property
    def usd(self) -> float:
        """Деньги вместе — выдача и модель; Hunter в доллары не переводится (`okf/unit-economy.md`:
        счета разные)."""
        return round(self.serp_usd + self.tokens_usd, 4)

    def __add__(self, other: Spend) -> Spend:
        return Spend(
            queries=self.queries + other.queries,
            results=self.results + other.results,
            sites=self.sites + other.sites,
            serp_usd=round(self.serp_usd + other.serp_usd, 4),
            tokens=self.tokens + other.tokens,
            tokens_usd=round(self.tokens_usd + other.tokens_usd, 4),
            hunter_searches=self.hunter_searches + other.hunter_searches,
            hunter_checks=self.hunter_checks + other.hunter_checks,
        )


@dataclass(frozen=True, slots=True)
class HypothesisSpend:
    """Смета гипотезы плана: от первого прохода до предельной глубины."""

    sheet: str
    name: str
    #: Предельная глубина — страницами по 10.
    depth_pages: int
    #: Цель — своя у листа или общая из окна загрузки; `None` — цели нет.
    target: int | None
    low: Spend
    high: Spend
    #: Ключей мало для цели — словами; `None` — хватает или цели нет.
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class PlanEstimate:
    """Смета плана: гипотезы без ошибок и всего; откуда доли — как у сметы прогона."""

    serp_mode: str
    hypotheses: tuple[HypothesisSpend, ...]
    low: Spend
    high: Spend
    #: Сайтов на результат выдачи и по скольким прогонам сбора посчитано (0 — запасная доля).
    share: float = UNIQUE_SHARE
    share_runs: int = 0
    #: Токенов судьи на сайт и по скольким прогонам посчитано (0 — запасное число).
    tokens_per_site: int = TOKENS_PER_SITE
    tokens_per_site_runs: int = 0
    #: Отказы настроек сметы прогона словами (проверка адресов не настроена) — по разу.
    notes: tuple[str, ...] = ()


async def plan_estimate(
    session: AsyncSession, draft: PlanDraft, serp_mode: SerpMode, *, goal: int | None = None
) -> PlanEstimate:
    """Смета плана в режиме выдачи: гипотезы без ошибок — от топ-10 до своей предельной глубины.
    `goal` — общая цель из окна загрузки: для листов, где своей нет. Ничего не пишет; кроме базы
    (история прогонов), никуда не ходит."""
    rows: list[HypothesisSpend] = []
    seen: list[Estimate] = []
    for hypothesis in draft.ready:
        low = await _pass(session, hypothesis, FIRST_PAGES, serp_mode, seen)
        depth = hypothesis.card.depth_pages
        high = (
            low
            if depth == FIRST_PAGES
            else await _pass(session, hypothesis, depth, serp_mode, seen)
        )
        target = hypothesis.card.target or goal
        rows.append(
            HypothesisSpend(
                hypothesis.sheet,
                hypothesis.card.name,
                depth,
                target,
                low,
                high,
                _short(high, depth, target),
            )
        )
    found = PlanEstimate(
        serp_mode.value,
        tuple(rows),
        sum((row.low for row in rows), Spend()),
        sum((row.high for row in rows), Spend()),
        notes=tuple(dict.fromkeys(basis.refusal for basis in seen if basis.refusal)),
    )
    if not seen:
        return found
    # Доли и токены на сайт — из общей истории прогонов: у всех смет плана они одни и те же.
    basis = seen[-1]
    return replace(
        found,
        share=basis.share,
        share_runs=basis.share_runs,
        tokens_per_site=basis.tokens_per_site,
        tokens_per_site_runs=basis.tokens_per_site_runs,
    )


async def _pass(
    session: AsyncSession,
    hypothesis: PlanHypothesis,
    depth_pages: int,
    serp_mode: SerpMode,
    seen: list[Estimate],
) -> Spend:
    """Проход гипотезы на глубину: смета прогона на каждый рынок — суммой. Сметы — в `seen`: по
    ним план называет, откуда доли, и отказы настроек."""
    total = Spend()
    for market in hypothesis.card.markets:
        found = Estimate(
            hypothesis=hypothesis.card.name,
            segment=hypothesis.card.segment,
            keywords=list(hypothesis.keys.taken),
            rejected=dict(hypothesis.keys.rejected),
            country=market,
            language=hypothesis.card.language,
            depth_pages=depth_pages,
            mode=sales_cfg.SEGMENT_JUDGE_MODE.value,
            serp_mode=serp_mode.value,
        )
        found.refusal = await priced(session, found, None)
        seen.append(found)
        total += Spend(
            queries=len(found.keywords),
            results=found.results,
            sites=found.sites,
            serp_usd=found.serp_usd,
            tokens=found.tokens,
            tokens_usd=found.tokens_usd,
            hunter_searches=found.hunter_searches,
            hunter_checks=found.hunter_checks,
        )
    return total


def _short(high: Spend, depth_pages: int, target: int | None) -> str | None:
    """Ключей мало для цели: даже до предельной глубины сайтов в выдаче меньше цели."""
    if target is None or high.sites >= target:
        return None
    return (
        f"ключей мало для цели: даже до {TOP_WORDS[depth_pages]} выдача даст ≈ "
        f"{number(high.sites)} сайтов, а цель — {number(target)} прошедших судью; добавьте ключей"
    )
