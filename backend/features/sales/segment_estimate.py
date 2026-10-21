"""Смета прогона гипотезы до запуска: ключи, выдача, сайты, модель, Hunter — строкой, и отказ словами.

**Стоимость называется до того, как её потратят** — как у прогона доноров (`runs/estimate.py`).
Точное число сайтов известно только после выдачи, а выдача — уже трата, поэтому смета
говорит то, что знает:

- **ключи** — отсеиваются только мусор гигиены и точные повторы; почти повтор остаётся с
  пометкой — решает человек (`segment_keywords.py`); взятый ключ человек может снять галочкой
  (номер строки, как вписана) — снятый не покупается и в смету не входит (`segment_estimate_keys`);
- **выдача** — арифметика: ключи на глубину по цене страницы в режиме покупки, который выбрал
  человек (`serp/modes.page_price_usd`), и сколько её ждать (`EXPECTED_WAIT_S`); режим ложится
  в смету прогона (`serp_mode`) — по нему задача прогона и покупает выдачу;
- **сайтов** — результаты выдачи на долю сайтов из истории прогонов сбора
  (`sites_per_result`): выдача отдаёт не каждый запрошенный результат и повторяет сайты. Своей
  истории нет — запасная доля (`runs/estimate.UNIQUE_SHARE`), и смета так и говорит. Сайты,
  которые гипотеза уже собрала теми же ключами на том же рынке, вычитаются (`known_sites`):
  их не судят и адрес им не ищут;
- **модель** — новые сайты на токены судьи за сайт из истории прогонов с судьёй
  (`segment_history.tokens_per_site`; без истории — запасное `TOKENS_PER_SITE`, словами) и
  деньгами по ценам ввода и вывода модели, смешанным долей вывода из истории прогонов с
  раздельным счётом (`segment_history.output_share`; без истории — запасная, словами); выше
  остатка дневного потолка модели (общего и своего у продаж — как у остальных вызовов модели
  продаж) — **прогон не запускается**, отказ словами;
- **Hunter** — не больше поиска адреса на новый сайт (платная ступень — только тем, кому
  бесплатные ступени адреса не нашли) и не больше проверки адреса на лида, если она живая.

Деньги вместе — выдача и модель последней строкой; Hunter платится запросами своего тарифа и в
доллары не переводится (`okf/unit-economy.md`: счета разные, единицы не складываются).

**Счёт выдачи — последней ступенью отказа** (`serp/funds`, слово владельца 16.10.2026): на счету
DataForSEO меньше порога или выдача по смете дороже остатка — «пополните счёт», прогон не
запускается. Остаток не узнать — проверки нет: покупка у неответившего откажет сама. Запуск
пересчитывает смету на сервере и отказывает её словами — проверка та же.

**Модель — последней ступенью отказа** (слово владельца 20.10.2026): судья включён, а модель не
ответила проверке (`segment_judging.unready` — живой вызов в десятки токенов) — нет кредитов, ключ
не принят, не ответила — прогон не запускается, словами владельца; запуск пересчитывает смету, и
отказ у него тот же.

**Отпечаток сметы** (`digest`) — что человек видел: ключи, снятые, описание сегмента и режимы;
запуск идёт только по нему (`segment_estimate_keys.seen_digest`).
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
from backend.features.runs.estimate import RESULTS_PER_PAGE, UNIQUE_SHARE
from backend.features.sales import chain_text, usage_cap, verifier, verify_cap
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_estimate_keys import EXCLUDED_KEY, chosen, seen_digest
from backend.features.sales.segment_estimate_words import lines_of
from backend.features.sales.segment_history import (
    known_sites,
    output_share,
    sites_per_result,
    tokens_per_site,
)
from backend.features.sales.segment_judging import unready
from backend.features.sales.segment_keywords import clean_keywords
from backend.features.sales.segment_spend import number, tokens_usd, usd_per_million
from backend.features.serp import funds, markets
from backend.features.serp.factory import AHREFS, UnknownProviderError, build_provider
from backend.features.serp.modes import (
    EXPECTED_WAIT_S,
    SerpMode,
    UnknownModeError,
    mode_of,
    page_price_usd,
    serp_cost_usd,
)

logger = logging.getLogger(__name__)

#: Токенов судьи на сайт — запасное число, пока прогонов с судьёй в истории нет
#: (`segment_history.tokens_per_site`). Живой прогон сбора 14.10 (промпт v1–v2, рассуждение `low`,
#: сниппет выдачи и шапка главной) — около тысячи на сайт, который судила модель, 1 100 — с запасом на
#: длинное описание сегмента. Промпт v3 (вид сайта, «не видно», чужая география, сигналы посредника)
#: длиннее: eval 20.10 на живом наборе — 2 198 токенов на вызов против 1 332 у v2 на тех же сайтах
#: (×1,65), на наборе другой гипотезы — 2 088 против 1 279: 1 100 × 1,6 ≈ 1 800. Со строкой «Текст
#: главной» (общий разбор главной, #424) — ещё около +23 % (eval: 2 703). Судья v4 (факты строгой
#: формой, места строками сайта с цитатами) — живой eval 20.10 на наборе трёх гипотез со строкой
#: «Текст главной»: 3 142 токена на вызов (180 вызовов; вывод 845, из него рассуждение 661), на
#: втором наборе — 2 841. История сметы — только прогоны той же версии промпта, и до первых
#: прогонов v4 смета берёт это число: 3 200 — с запасом вверх.
TOKENS_PER_SITE = 3200

#: Слова отказов — без имён настроек: их задаёт администратор и находит в журнале.
NO_SEGMENT = "у гипотезы нет описания сегмента — судье не с чем сравнивать сайты: опишите сегмент"
NO_KEYWORDS = "ключей нет: впишите по ключу на строку"
ALL_EXCLUDED = "все взятые ключи сняты — выдаче покупать нечего: верните галочку хотя бы одному"
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
    """Что просит человек: гипотеза, ключи строками, рынок, язык писем, глубина, режим
    покупки выдачи (пусто — умолчание сервиса, `SERP_MODE`) и снятые ключи — номерами строк,
    как вписаны."""

    hypothesis_id: int
    keywords: str
    country: str
    language: str | None
    depth_pages: int
    serp_mode: str | None = None
    excluded: tuple[int, ...] = ()


@dataclass(slots=True)
class Estimate:
    """Смета: числа и слова, и отказ, если запускать нельзя."""

    hypothesis: str
    segment: str
    #: Взятые ключи — ровно те, за которые заплатит выдача (без снятых), в том порядке, как вписаны.
    keywords: list[str]
    #: Отсеянные ключи → причина словами.
    rejected: dict[str, str]
    country: str
    language: str
    depth_pages: int
    mode: str
    #: Пометки взятых ключей словами: почти повтор — ключ взят, решает человек.
    notes: dict[str, str] = field(default_factory=dict)
    #: Снятые человеком ключи → номер строки, как вписаны: не покупаются, стоят отдельно от
    #: отсеянных.
    excluded: dict[str, int] = field(default_factory=dict)
    #: Номер строки, как вписана, у каждого взятого ключа — покупаемого и снятого.
    keyword_lines: dict[str, int] = field(default_factory=dict)
    #: Отпечаток того, что человек видел в смете: запуск — только по нему.
    digest: str = ""
    #: Режим покупки выдачи (`SerpMode`): цена страницы и ожидание — по нему.
    serp_mode: str = SerpMode.LIVE.value
    serp_page_usd: float = 0.0
    serp_wait_s: int = 0
    results: int = 0
    #: Сайтов на запрошенный результат выдачи — из истории сбора или запасная доля.
    share: float = UNIQUE_SHARE
    #: По скольким прошлым прогонам сбора посчитана доля; 0 — своей истории нет.
    share_runs: int = 0
    #: Сайтов в выдаче: результаты на долю.
    hosts: int = 0
    #: Из них уже в сборе гипотезы: их не судят и адрес им не ищут.
    known: int = 0
    #: Новых сайтов — их судит судья и им ищут адрес.
    sites: int = 0
    serp_usd: float = 0.0
    tokens: int = 0
    #: Токенов судьи на сайт — из истории прогонов с судьёй или запасное число.
    tokens_per_site: int = TOKENS_PER_SITE
    #: По скольким прошлым прогонам посчитаны токены на сайт; 0 — своей истории нет.
    tokens_per_site_runs: int = 0
    #: Токены судьи деньгами — по цене миллиона токенов модели на запуске.
    tokens_usd: float = 0.0
    usd_per_1m_tokens: float = 0.0
    #: Цены ввода и вывода на запуске — по ним отчёт прогона считает факт (`segment_spend`).
    usd_per_1m_input: float = 0.0
    usd_per_1m_output: float = 0.0
    #: Доля вывода в токенах — по истории прогонов с раздельным счётом; 0 прогонов — запасная.
    output_share: float = 0.0
    output_share_runs: int = 0
    #: Остаток дневного потолка модели; `None` — потолка нет.
    tokens_left: int | None = None
    hunter_searches: int = 0
    hunter_checks: int = 0
    #: Проверки адреса этого месяца под своим потолком — у живой проверки (`verify_cap`).
    month: verify_cap.Month | None = None
    lines: list[str] = field(default_factory=list)
    refusal: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Для строки прогона: смета, как её видел человек на запуске, — по ней отчёт прогона
        ставит расход рядом с обещанным (`segment_spend`) и называет отсеянные ключи."""
        return {
            "keywords": len(self.keywords),
            "rejected": dict(self.rejected),
            "notes": dict(self.notes),
            EXCLUDED_KEY: dict(self.excluded),
            "digest": self.digest,
            "serp_mode": self.serp_mode,
            "serp_page_usd": self.serp_page_usd,
            "serp_wait_s": self.serp_wait_s,
            "results": self.results,
            "share": self.share,
            "share_runs": self.share_runs,
            "hosts": self.hosts,
            "known": self.known,
            "sites": self.sites,
            "serp_usd": self.serp_usd,
            "tokens": self.tokens,
            "tokens_per_site": self.tokens_per_site,
            "tokens_per_site_runs": self.tokens_per_site_runs,
            "tokens_usd": self.tokens_usd,
            "usd_per_1m_tokens": self.usd_per_1m_tokens,
            "usd_per_1m_input": self.usd_per_1m_input,
            "usd_per_1m_output": self.usd_per_1m_output,
            "output_share": self.output_share,
            "output_share_runs": self.output_share_runs,
            "tokens_left": self.tokens_left,
            "hunter_searches": self.hunter_searches,
            "hunter_checks": self.hunter_checks,
            "month": None if self.month is None else self.month.words,
            "lines": list(self.lines),
        }


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


def _serp_mode(asked: str | None) -> tuple[str, str | None]:
    """Режим покупки выдачи из запроса; пусто — умолчание сервиса (`SERP_MODE`). Незнакомый —
    отказ словами: цены у режимов разные втрое, и молча взять другой нельзя."""
    try:
        return mode_of(asked).value, None
    except UnknownModeError as exc:
        logger.warning("сбор продаж: незнакомый режим выдачи", extra={"error": str(exc)})
        return (asked or "").strip().lower(), str(exc)


def _first_refusal(
    found: Estimate, *, market: str, limits: tuple[int, int], mode_refusal: str | None
) -> str | None:
    """Отказ по самому запросу — до денег: описание, ключи, глубина, рынок, язык, режим
    выдачи. Первый по порядку: правило, добавленное в таблицу, само попадает в отказ."""
    max_keywords, max_depth = limits
    count = len(found.keywords)
    languages = ", ".join(chain_text.LANGUAGES)
    checks: tuple[tuple[bool, str], ...] = (
        (not found.segment and found.mode != JudgeMode.OFF, NO_SEGMENT),
        (not found.keywords and bool(found.excluded), ALL_EXCLUDED),
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
        (mode_refusal is not None, mode_refusal or ""),
    )
    return next((words for failed, words in checks if failed), None)


async def _sites(session: AsyncSession, found: Estimate, hypothesis_id: int) -> None:
    """Сайтов в выдаче — по истории сбора; новых — за вычетом уже собранных гипотезой."""
    found.share, found.share_runs = await sites_per_result(session)
    found.hosts = round(found.results * found.share)
    known = await known_sites(
        session,
        hypothesis_id=hypothesis_id,
        country=found.country,
        keywords=found.keywords,
        per_keyword=found.depth_pages * RESULTS_PER_PAGE * found.share,
    )
    found.known = min(found.hosts, known)
    found.sites = found.hosts - found.known


async def estimate(session: AsyncSession, ask: Ask, *, now: datetime | None = None) -> Estimate:
    """Смета прогона. Ничего не пишет; кроме базы, спрашивает только счёт выдачи и модель."""
    hypothesis = await session.get(SalesHypothesisModel, ask.hypothesis_id)
    if hypothesis is None:
        raise UnknownHypothesisError(f"гипотезы №{ask.hypothesis_id} нет — обновите список гипотез")
    words = clean_keywords(ask.keywords)
    # Снятые — после отсева: номера «повтор строки N» и сам отсев от снятия не меняются.
    picked = chosen(ask.keywords, words.taken, ask.excluded)
    serp_mode, mode_refusal = _serp_mode(ask.serp_mode)
    country = ask.country.strip().lower()
    # Язык выдачи рынка — из той же карты, что у выдачи (`serp/markets.py`); нет рынка —
    # отказ словами ниже, а не тихий английский.
    market = markets.SERP_LANGUAGE.get(country, "")
    found = Estimate(
        hypothesis=hypothesis.name,
        segment=(hypothesis.description or "").strip(),
        keywords=picked.bought,
        rejected=words.rejected,
        notes=words.notes,
        excluded=picked.excluded,
        keyword_lines=picked.lines,
        country=country,
        language=(ask.language or market).strip().lower(),
        depth_pages=ask.depth_pages,
        mode=sales_cfg.SEGMENT_JUDGE_MODE.value,
        serp_mode=serp_mode,
    )
    found.digest = seen_digest(found, hypothesis.id)
    found.refusal = _first_refusal(
        found,
        market=market,
        limits=(serp_cfg.MAX_KEYWORDS_PER_RUN, serp_cfg.MAX_DEPTH_PAGES),
        mode_refusal=mode_refusal,
    )
    if found.refusal is not None:
        return found
    found.results = len(found.keywords) * found.depth_pages * RESULTS_PER_PAGE
    await _sites(session, found, hypothesis.id)
    found.serp_page_usd = page_price_usd(SerpMode(found.serp_mode))
    found.serp_wait_s = EXPECTED_WAIT_S[SerpMode(found.serp_mode)]
    # Цена выдачи — общей формулой сметы доноров и сбора: ключи × страницы × цена страницы режима.
    found.serp_usd = serp_cost_usd(
        len(found.keywords), found.depth_pages, SerpMode(found.serp_mode)
    )
    await _tokens(session, found)
    found.output_share, found.output_share_runs = await output_share(session)
    found.usd_per_1m_input = sales_cfg.SEGMENT_INPUT_USD_PER_1M_TOKENS
    found.usd_per_1m_output = sales_cfg.SEGMENT_OUTPUT_USD_PER_1M_TOKENS
    found.usd_per_1m_tokens = usd_per_million(found.output_share)
    found.tokens_usd = tokens_usd(found.tokens, found.usd_per_1m_tokens)
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
    found.lines = lines_of(found, cap_unread=_cap_unread())
    found.refusal = await _money_refusal(found)
    return found


async def _tokens(session: AsyncSession, found: Estimate) -> None:
    """Токены судьи: новые сайты на токены за сайт из истории прогонов с судьёй (без неё —
    запасное число); судья выключен — ноль."""
    per_site, runs = await tokens_per_site(session)
    if runs:
        found.tokens_per_site, found.tokens_per_site_runs = per_site, runs
    found.tokens = 0 if found.mode == JudgeMode.OFF else found.sites * found.tokens_per_site


async def _money_refusal(found: Estimate) -> str | None:
    """Отказ по деньгам и настройкам: модуль выключен, модель выше остатка потолка, нет ключа,
    нет выдачи, на счету выдачи не хватает денег, модель не ответила проверке."""
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
            f"смета модели выше остатка дневного потолка: ≈ {number(found.tokens)} токенов при "
            f"остатке {number(found.tokens_left)} — уменьшите число ключей или глубину, "
            "или запустите завтра"
        )
    refused = await serp_refusal() or await _funds_refusal(found.serp_usd)
    return refused if refused is not None else await unready(JudgeMode(found.mode))


async def _funds_refusal(cost: float) -> str | None:
    """Хватит ли счёта выдачи на прогон (`serp/funds.refusal`): мало — слова «пополните счёт»;
    остаток не узнать — проверки нет. Остаток — через модуль: тесты подменяют его там
    (`conftest._no_real_serp_balance`), и настоящий счёт они не спрашивают."""
    client = AhrefsClient()
    try:
        balance = await funds.provider_balance(client)
    finally:
        await client.aclose()
    return funds.refusal(balance, cost)
