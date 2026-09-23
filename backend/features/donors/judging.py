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
from backend.features.donors.home_signals import HomeSignals, check_home
from backend.features.donors.publisher_judge import (
    Decider,
    Intent,
    Judgement,
    Recommendation,
    arbitrate,
    judge_host,
    source_text,
)
from backend.features.donors.repository import JudgeRecord
from backend.features.runs.planning import SerpText

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
    """Главная не открылась — решала одна выдача. Отдельным числом: иначе
    закрывшийся сайт выглядит как сайт без признаков магазина."""

    @property
    def units_saved(self) -> int:
        """Во что обошлись бы отрезанные, дойди они до Ahrefs.

        Нижняя граница: считаются просев по DR и пороги, запрос по странам
        не считается вовсе. Завышать экономию нельзя — на неё будут
        ссылаться, решая, включать ли отказ.
        """
        return self.would_cut_paid * (UNITS_DR + UNITS_METRICS)

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
    """Главная как вторая сторона: подтверждает судью или зовёт арбитра.

    Три исхода, и у каждого свой автор:
    - «продаёт своё» и на главной корзина — решено ПРАВИЛОМ: две независимые
      стороны сказали одно, человеку тут смотреть нечего;
    - «издание» и на главной корзина — СПОР, решает арбитр по обеим сторонам.
      Сразу в отказ нельзя: замер 23.09 — корзина есть и у изданий, которые
      продают свои тесты (konsument.at), и у сообществ (wunschkind);
    - главная молчит или не открылась — остаётся вердикт модели.

    Главную спрашиваем только у тех, о ком модель вынесла суждение: у
    платформы, «посмотри» и некоммерческих спорить не с чем.
    """
    judged = {Intent.SELLS_OWN, Intent.REFERS_OUT, Intent.EDITORIAL_ADS}
    if home_client is None or verdict.decided_by is Decider.RULE or verdict.intent not in judged:
        return verdict, None

    home = await check_home(home_client, host)
    if not home.reached or not home.is_shop:
        return verdict, home
    if verdict.intent is Intent.SELLS_OWN:
        marks = ", ".join(home.shop[:3])
        return replace(
            verdict, reason=f"{verdict.reason} · главная: {marks}", decided_by=Decider.RULE
        ), home

    serp = source_text(text.title, text.description) if text else ""
    ruling = await arbitrate(http, host=host, serp=serp, home=home)
    # Токены обоих вызовов: арбитр — не бесплатное уточнение.
    return replace(ruling, tokens=ruling.tokens + verdict.tokens), home


async def judge_candidates(
    http: httpx.AsyncClient,
    hosts: list[str],
    texts: Mapping[str, SerpText],
    *,
    already_judged: Mapping[str, str] | None = None,
    paid: Collection[str] | None = None,
    home_client: httpx.AsyncClient | None = None,
    concurrency: int | None = None,
) -> JudgePass:
    """Судит домены, которые ещё не судили. Не бросает.

    `already_judged` — свежие вердикты из базы: их владелец берёт одним
    запросом до прохода, чтобы не ходить в базу на каждый домен.
    `paid` — за кого ещё предстоит платить Ahrefs; только они дают экономию.
    Пусто — все. `home_client` — клиент для главных; нет его — без главной.
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

    async def one(host: str) -> None:
        text = texts.get(host)
        async with gate:
            first = await judge_host(
                http,
                host=host,
                title=text.title if text else None,
                description=text.description if text else None,
            )
            verdict, home = await second_opinion(
                http, home_client, host=host, text=text, verdict=first
            )
        summary.record(verdict, paid=host in unpaid)
        if home is not None and not home.reached:
            summary.home_unreached += 1
        verdicts[host] = JudgeRecord(
            intent=verdict.intent.value,
            recommendation=verdict.recommendation.value,
            reason=verdict.reason[:256],
            quote=verdict.quote,
            source_url=text.url if text else None,
            model=verdict.model,
            decided_by=verdict.decided_by.value,
            home=home.as_dict() if home is not None else None,
        )
        if verdict.recommendation is Recommendation.REJECT:
            rejected.add(host)

    # `gather` без `return_exceptions` уронил бы прогон из-за одного домена,
    # а судья — не та ступень, ради которой стоит терять оплаченную выдачу.
    outcomes = await asyncio.gather(*(one(host) for host in pending), return_exceptions=True)
    for host, outcome in zip(pending, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.warning("судья площадки упал на %s: %s", host, outcome)

    return JudgePass(verdicts, summary, rejected)
