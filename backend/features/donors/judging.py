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
from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from backend.config import judge as cfg
from backend.features.donors.publisher_judge import Recommendation, judge_host
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

    @property
    def units_saved(self) -> int:
        """Во что обошлись бы отрезанные, дойди они до Ahrefs.

        Нижняя граница: считаются просев по DR и пороги, запрос по странам
        не считается вовсе. Завышать экономию нельзя — на неё будут
        ссылаться, решая, включать ли отказ.
        """
        return self.would_cut * (UNITS_DR + UNITS_METRICS)

    def record(self, recommendation: Recommendation, intent: str, tokens: int) -> None:
        self.judged += 1
        self.tokens += tokens
        self.by_intent[intent] = self.by_intent.get(intent, 0) + 1
        if recommendation is Recommendation.REJECT:
            self.would_cut += 1
        elif recommendation is Recommendation.REVIEW:
            self.to_review += 1


@dataclass(frozen=True, slots=True)
class JudgePass:
    """Итог прохода: что записать и с кем идти дальше."""

    verdicts: dict[str, JudgeRecord]
    summary: JudgeSummary
    rejected: set[str]
    """Кого судья отрезал. В наблюдении этот список НЕ применяется."""


async def judge_candidates(
    http: httpx.AsyncClient,
    hosts: list[str],
    texts: Mapping[str, SerpText],
    *,
    already_judged: Mapping[str, str] | None = None,
    concurrency: int | None = None,
) -> JudgePass:
    """Судит домены, которые ещё не судили. Не бросает.

    `already_judged` — свежие вердикты из базы: их владелец берёт одним
    запросом до прохода, чтобы не ходить в базу на каждый домен.
    """
    fresh = already_judged or {}
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
            verdict = await judge_host(
                http,
                host=host,
                title=text.title if text else None,
                description=text.description if text else None,
            )
        summary.record(verdict.recommendation, verdict.intent.value, verdict.tokens)
        verdicts[host] = JudgeRecord(
            intent=verdict.intent.value,
            recommendation=verdict.recommendation.value,
            reason=verdict.reason,
            quote=verdict.quote,
            source_url=text.url if text else None,
            model=verdict.model,
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
