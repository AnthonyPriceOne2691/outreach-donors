"""Окно судьи сегмента: сайты судятся скользящим окном, полосы ложатся чекпоинтами под условием.

Механика одна у прохода прогона (`segment_judging.judge_run`) и у пересуда после правки описания
(`segment_rejudge.py`); чем они различаются — кого судят, мимо ли кэша, по какому тексту и куда
ложится полоса (`Sink`).

- **Окно, а не партии** (`SALES_SEGMENT_JUDGE_CONCURRENCY`, своя настройка продаж — общий
  `JUDGE_CONCURRENCY` у судьи доноров): до N сайтов разом, освободилось место — берётся
  следующий. Партия ждала самый медленный сайт (главная — до 12 с на попытку), и окно стояло.
- **Чекпоинт** — каждые `BATCH` вердиктов одной транзакцией: полосы, вердикты и строка расхода
  `sales_segment_judge` за их токены. Сессию трогает только сам проход, задачи окна — сеть.
- **Потолок модели продаж** (`usage.ensure_llm_within_cap` со своим потолком продаж) — до первого
  сайта и после каждого чекпоинта; выбран — новые сайты не берутся, начатые доходят, проход
  останавливается словами (`JudgeTally.stopped`).
- **Полоса ложится одной строкой UPDATE под условием** (`land`): решение человека, пришедшее
  посреди прохода, судья не переписывает.
- **Кэш вердиктов** (`cached`): свежий вердикт того же домена, гипотезы, версии промпта и
  описания не пересуживается и токенов не стоит; «на проверку» без цитаты — не вердикт
  (`segment_judge.proven_again`): не пишется и не отдаётся.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import ColumnElement, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.core import usage
from backend.features.donors.home_signals import check_home
from backend.features.sales import usage_cap
from backend.features.sales.segment_judge import (
    JUDGE_OFF,
    OPERATION,
    PROMPT_VERSION,
    SegmentClient,
    SegmentVerdict,
    SiteText,
    by_host,
    decide,
    effective,
    proven_again,
    segment_digest,
    site_text,
)
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentVerdictModel,
)

logger = logging.getLogger(__name__)

#: Вердиктов на чекпоинт: полосы, вердикты и строка расхода — одной транзакцией.
BATCH = 20
#: Срок вердикта: чем сайт живёт, он меняет раз в годы — как у судьи площадки.
VERDICT_TTL_DAYS = 180
#: Токены раздельно в итоге прохода (`JudgeTally`): копятся от прохода к проходу, как `tokens`.
#: У прогона, судившегося до разбивки, ранние токены есть только в `tokens`: раздельно
#: неизвестное у такого отчёта — `tokens` без ввода и вывода, а не `unsplit_tokens`.
SPLIT_TOKENS = ("prompt_tokens", "completion_tokens", "unsplit_tokens", "reasoning_tokens")

Candidate = SalesSegmentCandidateModel
Verdict = SalesSegmentVerdictModel


@dataclass(slots=True)
class JudgeTally:
    """Что судья сделал за проход. Числа — в отчёт прогона."""

    judged: int = 0
    from_cache: int = 0
    by_rule: int = 0
    #: Модель не ответила: вердикта нет, домен — человеку и следующему проходу.
    unanswered: int = 0
    unanswered_reason: str | None = None
    #: Модель сказала «нет»: в `enforce` отсечены, в наблюдении — человеку с пометкой.
    would_cut: int = 0
    #: Упали мимо судьи (ошибка кода): остались неосуждёнными, следующий проход повторит.
    failed: int = 0
    tokens: int = 0
    #: Те же токены раздельно (`segment_judge.Spent`): ввод, вывод (с рассуждением) — у вывода
    #: своя цена; провайдер не назвал раздельно — в `unsplit_tokens`. Сумма трёх — `tokens`.
    #: `reasoning_tokens` — сколько из вывода ушло на рассуждение (часть вывода, не слагаемое).
    prompt_tokens: int = 0
    completion_tokens: int = 0
    unsplit_tokens: int = 0
    reasoning_tokens: int = 0
    #: Потолок модели выбран: почему проход остановлен.
    stopped: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "judged": self.judged,
            "from_cache": self.from_cache,
            "by_rule": self.by_rule,
            "unanswered": self.unanswered,
            "unanswered_reason": self.unanswered_reason,
            "would_cut": self.would_cut,
            "failed": self.failed,
            "tokens": self.tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "unsplit_tokens": self.unsplit_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "stopped": self.stopped,
        }

    def spend(self, verdict: SegmentVerdict) -> None:
        """Токены вызова — в счёт прохода: всего и раздельно."""
        self.tokens += verdict.tokens
        if verdict.spent is None:
            self.unsplit_tokens += verdict.tokens
            return
        self.prompt_tokens += verdict.spent.prompt
        self.completion_tokens += verdict.spent.completion
        self.reasoning_tokens += verdict.spent.reasoning


@dataclass(frozen=True, slots=True)
class Site:
    """Кандидат глазами судьи — снимок без строки базы: задачи окна сессию не трогают."""

    candidate_id: int
    domain_id: int
    hypothesis_id: int
    host: str
    title: str | None
    description: str | None
    #: Текст, который видел прошлый суд (`site_text`): по нему судит пересуд.
    stored: str | None = None

    @classmethod
    def of(cls, candidate: Candidate, host: str) -> Site:
        return cls(
            candidate.id,
            candidate.domain_id,
            candidate.hypothesis_id,
            host,
            candidate.page_title,
            candidate.page_description,
            candidate.site_text,
        )


@dataclass(frozen=True, slots=True)
class Ask:
    """Как судить: описание сегмента, режим, модель (пусто — судья выключен), главные сайтов."""

    segment: str
    mode: JudgeMode
    client: SegmentClient | None
    home: httpx.AsyncClient | None
    moment: datetime
    #: Кэш вердиктов: пересуд идёт мимо него — правка описания и есть вопрос заново.
    cached: bool = True
    #: Судить по тексту прошлого суда, не открывая главную: пересуд.
    stored_text: bool = False


@dataclass(frozen=True, slots=True)
class Judged:
    """Вердикт по кандидату и текст, по которому он вынесен."""

    verdict: SegmentVerdict | None
    text: SiteText
    cached: bool = False


@dataclass(frozen=True, slots=True)
class Landing:
    """Что ложится на кандидата: полоса и всё, чем она подтверждена."""

    lane: Lane
    reason: str | None
    verdict: Lane | None = None
    quote: str | None = None
    decided_by: Decider | None = None
    model: str | None = None
    unanswered: bool = False
    #: Текст, по которому судили; пусто — прежний остаётся (стоп-лист текста не читает).
    site_text: str | None = None

    def values(self, moment: datetime) -> dict[str, Any]:
        """Колонки кандидата. «Когда решено» — у решённого правилом или моделью."""
        found: dict[str, Any] = {
            "lane": self.lane,
            "reason": self.reason,
            "verdict": self.verdict,
            "quote": self.quote,
            "decided_by": self.decided_by,
            "model": self.model,
            "unanswered": self.unanswered,
            "decided_at": None if self.decided_by is None else moment,
        }
        if self.site_text is not None:
            found["site_text"] = self.site_text
        return found


def landing(judged: Judged, mode: JudgeMode) -> Landing:
    """Вердикт и режим → что ложится на кандидата."""
    text = judged.text.for_model() or None
    verdict = judged.verdict
    if verdict is None:  # судья выключен
        return Landing(Lane.REVIEW, JUDGE_OFF, site_text=text)
    if verdict.unanswered:
        return Landing(
            Lane.REVIEW, verdict.reason, model=verdict.model, unanswered=True, site_text=text
        )
    lane, reason = effective(verdict, mode)
    return Landing(
        lane, reason, verdict.lane, verdict.quote, verdict.decided_by, verdict.model, False, text
    )


def count(judged: Judged, tally: JudgeTally) -> None:
    """Счёт прохода по одному вердикту."""
    verdict = judged.verdict
    if verdict is None:
        return
    tally.spend(verdict)
    if verdict.unanswered:
        tally.unanswered += 1
        tally.unanswered_reason = tally.unanswered_reason or verdict.reason[:300]
        return
    tally.from_cache += int(judged.cached)
    tally.judged += int(not judged.cached)
    tally.by_rule += int(verdict.decided_by is Decider.RULE)
    tally.would_cut += int(verdict.decided_by is Decider.MODEL and verdict.would_cut)


async def land(
    session: AsyncSession,
    candidate_id: int,
    found: Landing,
    moment: datetime,
    *,
    guard: ColumnElement[bool],
) -> bool:
    """Полоса — на кандидата одной строкой UPDATE, пока строка под условием `guard`; иначе —
    ничего (решил человек, завёлся лид). Объект кандидата в сессии получает те же значения."""
    applied = await session.scalar(
        update(Candidate)
        .where(Candidate.id == candidate_id, guard)
        .values(**found.values(moment))
        .returning(Candidate.id)
        .execution_options(synchronize_session="fetch")
    )
    return applied is not None


class Sink(Protocol):
    """Куда окно отдаёт вердикты: полосы кандидатов — на чекпоинте, своим условием."""

    def take(self, site: Site, found: Landing) -> None: ...

    async def flush(self, session: AsyncSession, moment: datetime) -> None: ...


async def cached(
    session: AsyncSession,
    *,
    hypothesis_id: int,
    digest: str,
    domain_ids: Sequence[int],
    now: datetime,
) -> dict[int, SegmentVerdict]:
    """Свежие вердикты той же версии промпта и того же описания сегмента — по домену. Строка,
    которая по нынешнему правилу цитаты не вердикт (`proven_again`), не отдаётся."""
    if not domain_ids:
        return {}
    rows = await session.scalars(
        select(Verdict).where(
            Verdict.hypothesis_id == hypothesis_id,
            Verdict.prompt_version == PROMPT_VERSION,
            Verdict.segment_digest == digest,
            Verdict.domain_id.in_(list(domain_ids)),
            Verdict.updated_at >= now - timedelta(days=VERDICT_TTL_DAYS),
        )
    )
    return {
        row.domain_id: SegmentVerdict(
            Lane(row.verdict), row.reason, row.quote, row.model, 0, Decider(row.decided_by)
        )
        for row in rows.all()
        if proven_again(row.decided_by, row.quote)
    }


async def _text(site: Site, ask: Ask) -> SiteText:
    """Текст сайта судье: у пересуда — тот, что видел прошлый суд; иначе выдача и главная."""
    if ask.stored_text and site.stored:
        return SiteText.parse(site.stored)
    signals = await check_home(ask.home, site.host) if ask.home is not None else None
    return site_text(title=site.title, description=site.description, home=signals)


async def _one(site: Site, ask: Ask, known: SegmentVerdict | None) -> Judged:
    """Вердикт одному сайту: правило по домену, кэш, главная, правила текста, модель. Модели
    нет — судья выключен: правило по домену режет, остальное решает человек, и прежний вердикт
    модели из кэша не решает за него."""
    page = site_text(title=site.title, description=site.description)
    if (ruled := by_host(site.host)) is not None:
        return Judged(ruled, page)
    if ask.client is None:
        return Judged(None, page)
    if known is not None:
        return Judged(known, page, cached=True)
    text = await _text(site, ask)
    return Judged(await decide(ask.client, host=site.host, segment=ask.segment, text=text), text)


async def _remember(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    """Вердикты чекпоинта — в таблицу вердиктов: новый ключ — строка, тот же — поверх."""
    if not rows:
        return
    statement = insert(Verdict).values(list(rows))
    fresh = statement.excluded
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_sales_segment_verdicts_key",
            set_={
                "verdict": fresh.verdict,
                "quote": fresh.quote,
                "reason": fresh.reason,
                "model": fresh.model,
                "decided_by": fresh.decided_by,
                "tokens": fresh.tokens,
                "updated_at": fresh.updated_at,
            },
        )
    )


def _verdict_row(
    site: Site, verdict: SegmentVerdict, digest: str, moment: datetime
) -> dict[str, Any]:
    return {
        "domain_id": site.domain_id,
        "hypothesis_id": site.hypothesis_id,
        "prompt_version": PROMPT_VERSION,
        "segment_digest": digest,
        "verdict": verdict.lane.value,
        "quote": verdict.quote,
        "reason": verdict.reason[:1000],
        "model": verdict.model,
        "decided_by": verdict.decided_by.value,
        "tokens": verdict.tokens,
        "updated_at": moment,
    }


class _Window:
    """Скользящее окно судьи: до `SALES_SEGMENT_JUDGE_CONCURRENCY` сайтов разом; чекпоинт —
    каждые `BATCH` вердиктов. Сессию трогает только сам проход, задачи окна — сеть."""

    def __init__(self, session: AsyncSession, ask: Ask, sink: Sink, tally: JudgeTally) -> None:
        self.session, self.ask, self.sink, self.tally = session, ask, sink, tally
        self.limit = max(1, sales_cfg.SEGMENT_JUDGE_CONCURRENCY)
        self.digest = segment_digest(ask.segment)
        self.known: dict[int, SegmentVerdict] = {}
        self.running: dict[asyncio.Task[Judged], Site] = {}
        self.rows: list[dict[str, Any]] = []
        self.landed = 0
        self.spent = tally.tokens
        self.cap_checked = False

    async def run(self, sites: Sequence[Site]) -> None:
        await self._recall(sites)
        queue = deque(sites)
        try:
            while await self._fill(queue):
                await self._settle()
        finally:
            for task in self.running:
                task.cancel()
        await self._checkpoint()

    async def _recall(self, sites: Sequence[Site]) -> None:
        """Кэш — только когда судья зовётся и проход не мимо кэша (пересуд)."""
        if not sites or self.ask.client is None or not self.ask.cached:
            return
        self.known = await cached(
            self.session,
            hypothesis_id=sites[0].hypothesis_id,
            digest=self.digest,
            domain_ids=[site.domain_id for site in sites],
            now=self.ask.moment,
        )

    async def _fill(self, queue: deque[Site]) -> bool:
        """Добрать окно; `False` — ждать нечего: сайтов нет или потолок выбран, а окно пусто."""
        while queue and len(self.running) < self.limit and await self._within_cap():
            site = queue.popleft()
            known = self.known.get(site.domain_id)
            self.running[asyncio.create_task(_one(site, self.ask, known))] = site
        return bool(self.running)

    async def _within_cap(self) -> bool:
        """Потолок модели — до первого сайта и после каждого чекпоинта: расход пишется на нём."""
        if self.tally.stopped is not None:
            return False
        if self.ask.client is None or self.cap_checked:
            return True
        self.cap_checked = True
        own = usage_cap.sales_cap()
        try:
            await usage.ensure_llm_within_cap(self.session, now=self.ask.moment, own=own)
        except usage.LlmCapExceededError as exc:
            logger.warning("судья сегмента остановлен потолком модели", extra={"error": str(exc)})
            self.tally.stopped = str(exc)
            return False
        return True

    async def _settle(self) -> None:
        """Кончившиеся сайты — по одному; набралось `BATCH` вердиктов — чекпоинт."""
        done, _ = await asyncio.wait(self.running, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            self._land(self.running.pop(task), task)
            if self.landed >= BATCH:
                await self._checkpoint()

    def _land(self, site: Site, task: asyncio.Task[Judged]) -> None:
        if (error := task.exception()) is not None:
            # Судья не та ступень, ради которой стоит терять оплаченную выдачу: кандидат
            # остаётся как был, следующий проход повторит его.
            logger.warning(
                "судья сегмента упал на сайте", extra={"host": site.host, "error": repr(error)}
            )
            self.tally.failed += 1
            return
        judged = task.result()
        count(judged, self.tally)
        self.sink.take(site, landing(judged, self.ask.mode))
        verdict = judged.verdict
        if verdict is not None and verdict.proven and not judged.cached:
            self.rows.append(_verdict_row(site, verdict, self.digest, self.ask.moment))
        self.landed += 1

    async def _checkpoint(self) -> None:
        """Полосы, вердикты и строка расхода за токены с прошлого чекпоинта — одной транзакцией."""
        if not self.landed and self.tally.tokens == self.spent:
            return
        await self.sink.flush(self.session, self.ask.moment)
        await _remember(self.session, self.rows)
        if self.tally.tokens > self.spent:
            usage.record(self.session, operation=OPERATION, units=self.tally.tokens - self.spent)
        await self.session.commit()
        self.rows, self.landed, self.spent, self.cap_checked = [], 0, self.tally.tokens, False


async def judge_sites(
    session: AsyncSession, sites: Sequence[Site], ask: Ask, sink: Sink, tally: JudgeTally
) -> None:
    """Судить сайты окном; полосы кладёт `sink` на каждом чекпоинте. Коммит — здесь."""
    await _Window(session, ask, sink, tally).run(sites)
