"""Проход судьи по кандидатам прогона: кого судим, что считаем, кого режем.

**Судья стоит ДО Ahrefs, а не после.** Это и есть весь смысл: за домен,
который мы всё равно отбросим, платить метриками незачем. Сравнение цен:
~430 токенов дешёвой модели против 2 юнитов за просев по DR и 28 за пороги.

**В наблюдении он всё равно судит всех — и это не расточительство.**
Отрезать он не отрезает, но считает, сколько юнитов СЭКОНОМИЛ БЫ, если бы
резал. Это единственное число, которым можно обосновать включение: до него
разговор о пользе судьи остаётся разговором.

**Кэш отдельного хранилища не требует.** Вердикт лежит на домене с отметкой
времени, и свежий домен просто не попадает в список на суд. Срок свой,
длиннее метрик: способ заработка сайт меняет раз в годы.

**Сбой судьи не отбирает домен.** Любой отказ — «посмотри», и в `enforce`
такой домен идёт дальше как обычно: терять его из-за нашей поломки дороже,
чем оплатить ему метрики.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace

import httpx

from backend.config import judge as cfg
from backend.features.donors.author_door import author_door, open_door
from backend.features.donors.home_signals import HomeSignals, check_home
from backend.features.donors.publisher_judge import (
    PROMPT_VERSION,
    Decider,
    Intent,
    Judgement,
    Recommendation,
    arbitrate,
    judge_host,
    source_text,
)
from backend.features.donors.repository import JudgeRecord
from backend.features.donors.site_index import index_homes
from backend.features.runs.planning import SerpText
from backend.features.serp.protocol import SerpProvider

logger = logging.getLogger(__name__)

#: Цена ступеней, которых стоил бы отрезанный домен (okf/unit-economy.md).
#: Считаем только две первые: страны зовутся не всем, и приписывать их
#: каждому значило бы завышать экономию.
UNITS_DR = 2
UNITS_METRICS = 28


@dataclass(slots=True)
class JudgeSummary:
    """Что судья сделал за прогон. Читается человеком в отчёте."""

    judged: int = 0
    """Скольких судили на самом деле — то есть заплатили за них токенами."""

    from_cache: int = 0
    """Скольких не пересуживали: вердикт ещё свеж."""

    would_cut: int = 0
    """Скольких отрезал бы. В `enforce` это и есть отрезанные."""

    to_review: int = 0
    """Скольких отправил человеку: спорные, отказы доступа, сбои модели."""

    tokens: int = 0
    by_intent: dict[str, int] = field(default_factory=dict)
    by_decider: dict[str, int] = field(default_factory=dict)
    """Кто решил: правило, модель, арбитр. Точность меряется по каждому."""

    would_cut_paid: int = 0
    """Отрезал бы из тех, за кого ещё не платили. Только они и дают экономию:
    свежий домен досуживается ради знания, его метрики уже куплены."""

    home_unreached: int = 0
    """Главная не открылась. Отдельным числом: иначе закрывшийся сайт
    выглядит как сайт без признаков магазина."""

    from_index: int = 0
    """Скольких судили по образу главной из индекса поиска."""

    index_usd: float = 0.0
    """Цена запросов `site:` — деньги источника выдачи, в журнал отдельно."""

    @property
    def units_saved(self) -> int:
        """Во что обошлись бы отрезанные, дойди они до Ahrefs.

        Нижняя граница: считаются просев по DR и пороги, запрос по странам
        не считается вовсе. Завышать экономию нельзя — на неё будут
        ссылаться, решая, включать ли отказ.
        """
        return self.would_cut_paid * (UNITS_DR + UNITS_METRICS)

    def merge(self, other: JudgeSummary) -> None:
        """Сложить итог другого прохода — досуд идёт пачками."""
        self.judged += other.judged
        self.from_cache += other.from_cache
        self.would_cut += other.would_cut
        self.would_cut_paid += other.would_cut_paid
        self.to_review += other.to_review
        self.tokens += other.tokens
        self.home_unreached += other.home_unreached
        self.from_index += other.from_index
        self.index_usd += other.index_usd
        for key, count in other.by_intent.items():
            self.by_intent[key] = self.by_intent.get(key, 0) + count
        for key, count in other.by_decider.items():
            self.by_decider[key] = self.by_decider.get(key, 0) + count

    def record(self, verdict: Judgement, *, paid: bool) -> None:
        self.judged += 1
        self.tokens += verdict.tokens
        self.by_intent[verdict.intent.value] = self.by_intent.get(verdict.intent.value, 0) + 1
        who = verdict.decided_by.value
        self.by_decider[who] = self.by_decider.get(who, 0) + 1
        if verdict.recommendation is Recommendation.REJECT:
            self.would_cut += 1
            self.would_cut_paid += int(paid)
        elif verdict.recommendation is Recommendation.REVIEW:
            self.to_review += 1


@dataclass(frozen=True, slots=True)
class JudgePass:
    """Итог прохода: что записать и с кем идти дальше."""

    verdicts: dict[str, JudgeRecord]
    summary: JudgeSummary
    rejected: set[str]
    """Кого судья отрезал. В наблюдении этот список НЕ применяется."""


async def second_opinion(
    http: httpx.AsyncClient,
    home_client: httpx.AsyncClient | None,
    *,
    host: str,
    text: SerpText | None,
    verdict: Judgement,
) -> tuple[Judgement, HomeSignals | None]:
    """Главная как вторая сторона. Способ заработка — свойство сайта, а не
    страницы, и выдача показывает страницу.

    **Правило доказательства: отрезать может только структура.** Модель
    отказывает, лишь когда главная подтверждает продажу своего — корзиной,
    разметкой услуги, тарифами. Без подтверждения её отказ идёт человеку:
    замер 23.09 — арбитр, судивший всех, поймал 16 из 16 компаний-услуг,
    но отрезал и 5 из 77 изданий, у которых сбоку свой магазин или курс.
    С правилом — ноль ложных отказов, и ни одна компания не прошла в приём.

    Исходы:
    - «продаёт своё» по выдаче и продажа на главной — решено ПРАВИЛОМ;
    - «издание» по выдаче — всегда к арбитру, если главная открылась:
      блог компании по выдаче неотличим от издания (стоматология, агентство,
      страховщик — 23.09 модель пропустила восемь таких);
    - главная не открылась — остаётся вердикт модели.
    """
    if home_client is None or verdict.decided_by is Decider.RULE or verdict.intent not in DISPUTED:
        return verdict, None
    home = await check_home(home_client, host)
    return await settle(http, host=host, text=text, verdict=verdict, home=home), home


#: Вердикты модели, которые главная может подтвердить или оспорить.
#: `sells_placement` и `link_vendor` сюда не входят: продажа размещения —
#: вопрос о СТРАНИЦЕ приёма, а не о витрине, и корзина на главной его
#: не отменяет (прогон №18: `/pricing` отрезал продавца гостевых статей).
DISPUTED = frozenset({Intent.SELLS_OWN, Intent.REFERS_OUT, Intent.EDITORIAL_ADS})


async def settle(
    http: httpx.AsyncClient,
    *,
    host: str,
    text: SerpText | None,
    verdict: Judgement,
    home: HomeSignals,
) -> Judgement:
    """Вердикт модели и уже прочитанная главная → итог. Без скачивания:
    эталон (`scripts/eval_judge.py`) подаёт сюда главную из файла."""
    if not home.reached:
        return verdict
    if verdict.intent is Intent.SELLS_OWN:
        if not home.sells:
            return verdict
        marks = ", ".join((*home.shop, *home.service)[:3])
        return replace(
            verdict, reason=f"{verdict.reason} · главная: {marks}", decided_by=Decider.RULE
        )

    serp = source_text(text.title, text.description) if text else ""
    ruling = await arbitrate(http, host=host, serp=serp, home=home)
    ruling = replace(ruling, tokens=ruling.tokens + verdict.tokens)
    if ruling.recommendation is Recommendation.REJECT and not home.sells:
        ruling = replace(
            ruling,
            recommendation=Recommendation.REVIEW,
            reason=f"{ruling.reason} · на главной не видно продажи — посмотри",
        )
    return ruling


async def through_index(
    http: httpx.AsyncClient,
    index: SerpProvider,
    results: dict[str, tuple[Judgement, HomeSignals | None]],
    texts: Mapping[str, SerpText],
) -> float:
    """Второй проход по тем, чья главная закрылась: образ главной из индекса.

    Спрашиваем только про принятых моделью — у отрезанных и отправленных
    к человеку спорить не о чем. Пакетом, одним запросом на всех: `site:`
    у источника выдачи стоит денег, и по одному это десятки вызовов.
    """
    blind = [
        host
        for host, (verdict, home) in results.items()
        if home is not None
        and not home.reached
        and verdict.decided_by is Decider.MODEL
        and verdict.recommendation is Recommendation.ACCEPT
    ]
    homes, cost = await index_homes(index, blind)
    for host, home in homes.items():
        verdict, _ = results[host]
        text = texts.get(host)
        serp = source_text(text.title, text.description) if text else ""
        ruling = await arbitrate(http, host=host, serp=serp, home=home)
        ruling = replace(ruling, tokens=ruling.tokens + verdict.tokens)
        if ruling.recommendation is Recommendation.REJECT:
            # Структуры в индексе нет — правило доказательства шлёт к человеку.
            ruling = replace(
                ruling,
                recommendation=Recommendation.REVIEW,
                reason=f"{ruling.reason} · главная закрыта, судил по индексу — посмотри",
            )
        results[host] = (ruling, home)
    return cost


def door_of(text: SerpText | None, home: HomeSignals | None) -> str | None:
    """Зовёт ли сайт авторов: страница из выдачи или меню главной."""
    return author_door(
        text.url if text else None,
        text.title if text else None,
        home.nav if home is not None and home.reached else (),
    )


def _collect(
    results: Mapping[str, tuple[Judgement, HomeSignals | None]],
    texts: Mapping[str, SerpText],
    summary: JudgeSummary,
    unpaid: Collection[str],
) -> tuple[dict[str, JudgeRecord], set[str]]:
    """Вердикты — в записи для базы и в счёт прохода."""
    verdicts: dict[str, JudgeRecord] = {}
    rejected: set[str] = set()
    for host, (judged, home) in results.items():
        text = texts.get(host)
        verdict = open_door(judged, door_of(text, home))
        summary.record(verdict, paid=host in unpaid)
        if home is not None and not home.reached:
            summary.home_unreached += 1
        if home is not None and home.via == "index":
            summary.from_index += 1
        verdicts[host] = JudgeRecord(
            intent=verdict.intent.value,
            recommendation=verdict.recommendation.value,
            reason=verdict.reason[:256],
            quote=verdict.quote,
            source_url=text.url if text else None,
            model=verdict.model,
            version=PROMPT_VERSION if verdict.model else None,
            decided_by=verdict.decided_by.value,
            home=home.as_dict() if home is not None else None,
        )
        if verdict.recommendation is Recommendation.REJECT:
            rejected.add(host)
    return verdicts, rejected


async def judge_candidates(
    http: httpx.AsyncClient,
    hosts: list[str],
    texts: Mapping[str, SerpText],
    *,
    already_judged: Mapping[str, str] | None = None,
    paid: Collection[str] | None = None,
    home_client: httpx.AsyncClient | None = None,
    index: SerpProvider | None = None,
    concurrency: int | None = None,
) -> JudgePass:
    """Судит домены, которые ещё не судили. Не бросает.

    `already_judged` — свежие вердикты из базы: их владелец берёт одним
    запросом до прохода, чтобы не ходить в базу на каждый домен.
    `paid` — за кого ещё предстоит платить Ahrefs; только они дают экономию.
    Пусто — все. `home_client` — клиент для главных; нет его — без главной.
    `index` — источник выдачи для `site:`, когда главная закрыта; нет его —
    закрытая главная оставляет вердикт модели.
    """
    fresh = already_judged or {}
    unpaid = set(hosts) if paid is None else set(paid)
    summary = JudgeSummary(from_cache=sum(1 for host in hosts if host in fresh))
    pending = [host for host in hosts if host not in fresh]
    verdicts: dict[str, JudgeRecord] = {}
    rejected = {host for host, rec in fresh.items() if rec == Recommendation.REJECT.value}

    if not pending:
        return JudgePass(verdicts, summary, rejected)

    gate = asyncio.Semaphore(concurrency or cfg.CONCURRENCY)
    results: dict[str, tuple[Judgement, HomeSignals | None]] = {}

    async def one(host: str) -> None:
        text = texts.get(host)
        async with gate:
            first = await judge_host(
                http,
                host=host,
                title=text.title if text else None,
                description=text.description if text else None,
            )
            results[host] = await second_opinion(
                http, home_client, host=host, text=text, verdict=first
            )

    # `gather` без `return_exceptions` уронил бы прогон из-за одного домена,
    # а судья — не та ступень, ради которой стоит терять оплаченную выдачу.
    outcomes = await asyncio.gather(*(one(host) for host in pending), return_exceptions=True)
    for host, outcome in zip(pending, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.warning("судья площадки упал на %s: %s", host, outcome)

    if index is not None:
        try:
            summary.index_usd = await through_index(http, index, results, texts)
        except Exception as exc:  # noqa: BLE001 — индекс необязателен, вердикты уже есть
            logger.warning("судья площадки: индекс не ответил (%r) — остаются вердикты модели", exc)

    collected, cut = _collect(results, texts, summary, unpaid)
    verdicts.update(collected)
    rejected |= cut
    return JudgePass(verdicts, summary, rejected)
