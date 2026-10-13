"""Смета прогона гипотезы до запуска: выдача, модель, Hunter — строкой, и отказ словами.

**Стоимость называется до того, как её потратят** — как у прогона доноров (`runs/estimate.py`).
Точное число сайтов известно только после выдачи, а выдача — уже трата, поэтому смета
говорит то, что знает:

- **выдача** — арифметика: ключи на глубину по цене задачи (`SERP_PRICE_PER_KEYWORD_USD`);
- **сайтов** — результаты выдачи на долю уникальных доменов (та же доля, что у сметы
  доноров, `runs/estimate.UNIQUE_SHARE`), худший случай — все новые;
- **модель** — сайты на токены судьи за сайт (`TOKENS_PER_SITE`, предложение до замера);
  выше остатка дневного потолка модели (общего и своего у продаж — как у остальных
  вызовов модели продаж) — **прогон не запускается**, отказ словами;
- **Hunter** — не больше поиска адреса на сайт (платная ступень — только тем, кому
  бесплатные ступени адреса не нашли) и не больше проверки адреса на лида, если она живая.

Ключи — тем же путём, что у пула доноров: гигиена (`keywords/hygiene.clean`), почти-повторы
(`keywords/dedup`), один запрос на ключ (`serp/protocol.distinct_keywords`). Отсеянные — с
причиной: по ним видно, что не так с набором.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as contacts_cfg
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config import serp as serp_cfg
from backend.config.judge import JudgeMode
from backend.config.startup_checks import ConfigError
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core import usage
from backend.features.keywords import dedup, hygiene
from backend.features.runs.estimate import RESULTS_PER_PAGE, UNIQUE_SHARE
from backend.features.sales import chain_text, usage_cap, verifier, verify_cap
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import SalesHypothesisModel
from backend.features.serp import markets
from backend.features.serp.factory import AHREFS, UnknownProviderError, build_provider
from backend.features.serp.protocol import distinct_keywords

logger = logging.getLogger(__name__)

#: Токенов судьи на сайт — предложение до замера: промпт, описание сегмента, текст выдачи и
#: главной и рассуждение на `low` (у судьи площадки на `low` — около 720 на домен по одной
#: выдаче). Живой прогон координатора назовёт настоящее число.
TOKENS_PER_SITE = 1500

#: Слова отказов — без имён настроек: их задаёт администратор и находит в журнале.
NO_SEGMENT = "у гипотезы нет описания сегмента — судье не с чем сравнивать сайты: опишите сегмент"
NO_KEYWORDS = "ключей нет: впишите по ключу на строку"
SANDBOX = (
    "песочница выдачи прогон не запускает: сайты в ней выдуманы, а судья и поиск адресов "
    "по ним — настоящие траты"
)
SERP_NOT_SET = "источник выдачи не настроен — настраивает администратор"
#: Юниты выдачи Ahrefs сбор не пишет: строка выдачи несёт только доллары, а у Ahrefs они 0, — траты
#: ушли бы мимо журнала и месячного потолка Ahrefs, а ключ общий с донорами (`runs` пишет юниты сам).
SERP_AHREFS = (
    "выдача из Ahrefs сбору не подходит: её траты не попали бы ни в журнал расходов, ни в "
    "месячный потолок Ahrefs, а смета считает деньги по цене другого источника — источник "
    "выдачи меняет администратор"
)
MODEL_NOT_SET = "ключ модели не задан: судья сегмента не ответит — настраивает администратор"
#: Свой потолок модели продаж не прочитан из настроек (`llm_cfg.UNREAD_CAPS`): токенов у продаж
#: ноль, вызовы модели продаж отказывают (`usage.ensure_llm_within_cap`) — судья не ответит.
CAP_UNREAD = (
    "дневной потолок модели продаж не прочитан из настроек — токенов ноль, судья сегмента не "
    "ответит, пока его не поправит администратор"
)
VERIFIER_NOT_SET = "проверка адресов не настроена — настраивает администратор"
SALES_OFF = (
    "модуль продаж выключен — сбор не запускается: выдача, модель и поиск адресов стоят денег; "
    "включает администратор"
)


@dataclass(frozen=True, slots=True)
class Ask:
    """Что просит человек: гипотеза, ключи строками, рынок, язык писем, глубина."""

    hypothesis_id: int
    keywords: str
    country: str
    language: str | None
    depth_pages: int


@dataclass(slots=True)
class Estimate:
    """Смета: числа и слова, и отказ, если запускать нельзя."""

    hypothesis: str
    segment: str
    keywords: list[str]
    #: Отсеянные ключи → причина словами.
    rejected: dict[str, str]
    country: str
    language: str
    depth_pages: int
    mode: str
    results: int = 0
    sites: int = 0
    serp_usd: float = 0.0
    tokens: int = 0
    #: Остаток дневного потолка модели; `None` — потолка нет.
    tokens_left: int | None = None
    hunter_searches: int = 0
    hunter_checks: int = 0
    #: Проверки адреса этого месяца под своим потолком — у живой проверки (`verify_cap`).
    month: verify_cap.Month | None = None
    lines: list[str] = field(default_factory=list)
    refusal: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Для строки прогона: смета, как её видел человек на запуске."""
        return {
            "keywords": len(self.keywords),
            "rejected": dict(self.rejected),
            "results": self.results,
            "sites": self.sites,
            "serp_usd": self.serp_usd,
            "tokens": self.tokens,
            "tokens_left": self.tokens_left,
            "hunter_searches": self.hunter_searches,
            "hunter_checks": self.hunter_checks,
            "month": None if self.month is None else self.month.words,
            "lines": list(self.lines),
        }


def _number(value: int) -> str:
    """12345 → «12 345»: узкие группы, как пишут числа на экранах."""
    return f"{value:,}".replace(",", " ")


def _money(value: float) -> str:
    return f"${value:.4f}".replace(".", ",")


def clean_keywords(text: str) -> tuple[list[str], dict[str, str]]:
    """Ключи строками → годные и отсеянные с причиной. Порядок — как вписаны."""
    lines = [line for line in text.splitlines() if line.strip()]
    good, rejected = hygiene.clean(lines)
    kept = dedup.drop_near_duplicates(good)
    for phrase in good:
        if phrase not in kept:
            rejected[phrase] = "почти повтор другого ключа: выдача у них общая"
    return distinct_keywords(kept), rejected


async def tokens_left(session: AsyncSession, now: datetime | None = None) -> int | None:
    """Остаток дневного потолка модели для судьи сегмента: меньший из общего и своего
    потолка продаж (`usage_cap.sales_cap`). `None` — ни того, ни другого нет. Свой потолок не
    прочитан из настроек — ноль (`CAP_UNREAD`), а не доля общего: вызовы модели продаж тогда
    отказывают все."""
    if _cap_unread():
        return 0
    moment = now or datetime.now(UTC)
    since = datetime.combine(moment.date(), time.min, tzinfo=UTC)
    limits: list[int] = []
    if llm_cfg.DAILY_TOKEN_CAP:
        limits.append(llm_cfg.DAILY_TOKEN_CAP - await usage.llm_tokens_spent(session, since=since))
    own = usage_cap.sales_cap()
    if own.tokens:
        spent = await usage.llm_tokens_spent(session, since=since, operations=own.operations)
        limits.append(own.tokens - spent)
    return max(0, min(limits)) if limits else None


def _cap_unread() -> bool:
    return usage_cap.sales_cap().setting in llm_cfg.UNREAD_CAPS


async def serp_refusal() -> str | None:
    """Собирается ли источник выдачи — тем же выбором, что у прогона доноров
    (`serp/factory.build_provider`), без сети. Имя настройки — в журнал."""
    if serp_cfg.SANDBOX:
        return SANDBOX
    client = AhrefsClient()
    try:
        provider = build_provider(client)
    except UnknownProviderError as exc:
        logger.warning("сбор продаж: источник выдачи не собран", extra={"error": str(exc)})
        return SERP_NOT_SET
    finally:
        await client.aclose()
    closing = getattr(provider, "aclose", None)
    if closing is not None:
        await closing()
    if provider.name == AHREFS:
        logger.warning("сбор продаж: выдача из Ahrefs — сбор её не берёт (юниты мимо журнала)")
        return SERP_AHREFS
    return None


def _paid_search_on() -> bool:
    """Платный поиск адреса у сбора: ключ Hunter есть и флаг сбора включён — ключ общий с
    донорами, и без флага его включение для доноров платное сбору не включает."""
    return bool(contacts_cfg.HUNTER_API_KEY) and sales_cfg.SEGMENT_PAID_CONTACTS


def _verifier_paid() -> bool:
    """Платная ли проверка адресов при очистке. Негодная настройка — `ConfigError`."""
    return verifier.configured() == verifier.LIVE


def _first_refusal(found: Estimate, *, market: str, limits: tuple[int, int]) -> str | None:
    """Отказ по самому запросу — до денег: описание, ключи, глубина, рынок, язык. Первый
    по порядку: правило, добавленное в таблицу, само попадает в отказ."""
    max_keywords, max_depth = limits
    count = len(found.keywords)
    languages = ", ".join(chain_text.LANGUAGES)
    checks: tuple[tuple[bool, str], ...] = (
        (not found.segment and found.mode != JudgeMode.OFF, NO_SEGMENT),
        (not found.keywords, NO_KEYWORDS),
        (
            count > max_keywords,
            f"ключей {count} — за прогон не больше {max_keywords}: разделите набор",
        ),
        (
            not 1 <= found.depth_pages <= max_depth,
            f"глубина выдачи — от 1 до {max_depth} страниц по 10 результатов",
        ),
        (not market, f"рынка «{found.country}» нет в карте выдачи — выберите страну из списка"),
        (
            found.language not in chain_text.LANGUAGES,
            f"язык писем «{found.language}» — цепочка пишется на {languages}",
        ),
    )
    return next((words for failed, words in checks if failed), None)


def _lines(found: Estimate) -> list[str]:
    """Смета словами — строкой на статью."""
    lines = [
        f"Выдача: ключей {_number(len(found.keywords))} × страниц {found.depth_pages} — "
        f"результатов до {_number(found.results)}, ≈ {_money(found.serp_usd)}."
    ]
    if found.mode == JudgeMode.OFF:
        lines.append(
            f"Судья сегмента выключен: модель не зовётся, сайтов до {_number(found.sites)} — решает человек."
        )
    else:
        left = (
            ""
            if found.tokens_left is None
            else ", дневной потолок модели продаж не прочитан из настроек — токенов ноль"
            if _cap_unread()
            else f", остаток дневного потолка — {_number(found.tokens_left)}"
        )
        lines.append(
            f"Судья сегмента: сайтов до {_number(found.sites)}, токенов модели ≈ "
            f"{_number(found.tokens)}{left}."
        )
        if found.mode == JudgeMode.SHADOW:
            lines.append("Судья в наблюдении: его «отказ» не отсекает сайт, а отдаёт человеку.")
    searches = (
        f"поисков адреса до {_number(found.hunter_searches)} — только тем, кому бесплатные "
        "ступени адреса не нашли"
        if found.hunter_searches
        else "платный поиск адреса у сбора выключен или не настроен — только бесплатные ступени"
    )
    month = "" if found.month is None else f", {found.month.words}"
    checks = (
        f"проверок адреса до {_number(found.hunter_checks)}{month}"
        if found.hunter_checks
        else "проверка адреса выдуманная"
    )
    lines.append(f"Hunter: {searches}; {checks}.")
    return lines


async def estimate(session: AsyncSession, ask: Ask, *, now: datetime | None = None) -> Estimate:
    """Смета прогона. Ничего не пишет и никуда не ходит, кроме базы."""
    hypothesis = await session.get(SalesHypothesisModel, ask.hypothesis_id)
    if hypothesis is None:
        raise UnknownHypothesisError(f"гипотезы №{ask.hypothesis_id} нет — обновите список гипотез")
    segment = (hypothesis.description or "").strip()
    keywords, rejected = clean_keywords(ask.keywords)
    country = ask.country.strip().lower()
    # Язык выдачи рынка — из той же карты, что у выдачи (`serp/markets.py`); нет рынка —
    # отказ словами ниже, а не тихий английский.
    market = markets.SERP_LANGUAGE.get(country, "")
    found = Estimate(
        hypothesis=hypothesis.name,
        segment=segment,
        keywords=keywords,
        rejected=rejected,
        country=country,
        language=(ask.language or market).strip().lower(),
        depth_pages=ask.depth_pages,
        mode=sales_cfg.SEGMENT_JUDGE_MODE.value,
    )
    found.refusal = _first_refusal(
        found, market=market, limits=(serp_cfg.MAX_KEYWORDS_PER_RUN, serp_cfg.MAX_DEPTH_PAGES)
    )
    if found.refusal is not None:
        return found
    found.results = len(keywords) * found.depth_pages * RESULTS_PER_PAGE
    found.sites = round(found.results * UNIQUE_SHARE)
    found.serp_usd = round(len(keywords) * found.depth_pages * serp_cfg.PRICE_PER_KEYWORD_USD, 4)
    found.tokens = 0 if found.mode == JudgeMode.OFF else found.sites * TOKENS_PER_SITE
    found.tokens_left = await tokens_left(session, now)
    found.hunter_searches = found.sites if _paid_search_on() else 0
    try:
        found.hunter_checks = found.sites if _verifier_paid() else 0
    except ConfigError as exc:
        logger.warning("сбор продаж: проверка адресов не настроена", extra={"error": str(exc)})
        found.refusal = VERIFIER_NOT_SET
        return found
    if found.hunter_checks:
        found.month = await verify_cap.month(session, now=now)
    found.lines = _lines(found)
    found.refusal = await _money_refusal(found)
    return found


async def _money_refusal(found: Estimate) -> str | None:
    """Отказ по деньгам и настройкам: модуль выключен, модель выше остатка потолка, нет ключа,
    нет выдачи."""
    if not sales_cfg.ENABLED:
        return SALES_OFF
    if found.mode != JudgeMode.OFF and not llm_cfg.API_KEY:
        return MODEL_NOT_SET
    if found.month is not None and isinstance(found.month.cap, str):
        return found.month.cap  # потолок проверок не задан: проверка адресов встала бы
    if found.tokens and _cap_unread():
        return CAP_UNREAD
    if found.tokens_left is not None and found.tokens > found.tokens_left:
        return (
            f"смета модели выше остатка дневного потолка: ≈ {_number(found.tokens)} токенов при "
            f"остатке {_number(found.tokens_left)} — уменьшите число ключей или глубину, "
            "или запустите завтра"
        )
    return await serp_refusal()
