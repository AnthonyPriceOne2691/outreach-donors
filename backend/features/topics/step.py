"""Тема сайта пачкой: в прогоне — рядом с судьёй, в досуде — по базе.

**В прогоне тема определяется там, где судья уже читает сайты** (`runs/judge_stage.py`):
та же пачка, та же страница из выдачи, та же главная, которую судья открыл, — ни одного
лишнего захода на сайт. Судья или тематика выключены — тема в прогоне не определяется.

**Каждый вызов — в счёт потолка модели.** Перед пачкой — проверка дневного потолка и потолка
прогона (`usage.ensure_llm_within_cap`): выбран — прогон останавливается причиной, как у судьи.

**Каждый ответ — в базу сразу, короткой фиксацией** (`Answered`): тема на домен, строка журнала
`site_topic` с его токенами. Прогон или досуд, оборванный посреди пачки, теряет только ответы,
которые ещё летят: оплаченные — и в журнале, и в потолке, и их темы — у доменов.

**Счёт прохода не путает исходы** (`TopicSummary`): тема названа; «не ясна» (модель сказала
`null` — записано); ответ не по форме (не записано, своим числом: проза, обрезан, тема не из
списка); модель не ответила (не записано, причина — первая); читать было нечего (модель не
звали); тема уже свежая. Ноль определённых при неработающем ключе выглядел бы «тем нет».
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from backend.config import topics as cfg
from backend.features.donors.home_signals import HomeSignals
from backend.features.runs.failures import described
from backend.features.runs.planning import SerpText
from backend.features.topics.classifier import (
    TOPIC_VERSION,
    SiteText,
    TopicVerdict,
    classify,
    site_text,
)
from backend.features.topics.store import Known, TopicRecord, TopicStore
from backend.shared.llm import NO_CREDITS_WORDS

logger = logging.getLogger(__name__)

#: Строка журнала расхода (`core/usage.OPERATION_PROVIDERS`): единица — токен.
TOPIC_OPERATION = "site_topic"

#: Что сделать с ответом, как только он пришёл: записать тему и расход.
Answered = Callable[[str, TopicVerdict], Awaitable[None]]


@dataclass(slots=True)
class TopicSummary:
    """Что проход сделал с темами. Читается человеком в отчёте прогона и досуда."""

    classified: int = 0
    """Тема названа и записана."""

    unclear: int = 0
    """Модель сказала, что по тексту темы не понять (`null`), — записано «не ясна»."""

    unreadable: int = 0
    """Ответ пришёл не по форме: проза, обрезан потолком, тема не из списка. Не записано."""

    unreadable_reason: str | None = None

    unanswered: int = 0
    """Модель не ответила: отказ, сеть, пустой ответ. Не записано — следующий проход спросит."""

    unanswered_reason: str | None = None
    """Почему не ответила — первая причина: что чинить."""

    no_text: int = 0
    """Читать было нечего: ни страницы из выдачи, ни главной. Модель не звали."""

    from_cache: int = 0
    """Тема уже свежая — не спрашивали."""

    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_topic: dict[str, int] = field(default_factory=dict)

    def record(self, verdict: TopicVerdict) -> None:
        self.tokens += verdict.tokens
        self.prompt_tokens += verdict.prompt_tokens
        self.completion_tokens += verdict.completion_tokens
        if verdict.unreadable:
            self.unreadable += 1
            self.unreadable_reason = self.unreadable_reason or verdict.reason[:300]
        elif verdict.unanswered:
            self.unanswered += 1
            self.unanswered_reason = self.unanswered_reason or verdict.reason[:300]
        elif verdict.topic is None:
            self.unclear += 1
        else:
            self.classified += 1
            self.by_topic[verdict.topic] = self.by_topic.get(verdict.topic, 0) + 1

    @property
    def asked(self) -> int:
        """Сколько раз звали модель: ответила или нет."""
        return self.classified + self.unclear + self.unreadable + self.unanswered

    def as_dict(self) -> dict[str, Any]:
        """Для записи в отчёт прогона (`runs/report.run_stats`)."""
        return {
            "version": TOPIC_VERSION,
            "classified": self.classified,
            "unclear": self.unclear,
            "unreadable": self.unreadable,
            "unreadable_reason": self.unreadable_reason,
            "unanswered": self.unanswered,
            "unanswered_reason": self.unanswered_reason,
            "no_text": self.no_text,
            "from_cache": self.from_cache,
            "tokens": self.tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "by_topic": dict(self.by_topic),
        }


def record_of(verdict: TopicVerdict) -> TopicRecord:
    return TopicRecord(
        topic=verdict.topic,
        secondary=verdict.secondary,
        reason=verdict.reason,
        model=verdict.model,
        version=TOPIC_VERSION,
        source=verdict.source,
    )


async def classify_many(
    http: httpx.AsyncClient,
    texts: Mapping[str, SiteText],
    *,
    on_answer: Answered | None = None,
    concurrency: int | None = None,
) -> dict[str, TopicVerdict]:
    """Темы сайтов параллельно; каждый ответ — сразу в `on_answer`, по одному за раз.

    Упавший вызов модели — «не ответила» с причиной: тема — не та ступень, ради которой стоит
    терять оплаченные ответы соседей. Упавшая запись в базу — громко, после прохода: ответы,
    записанные до неё, остаются. У ключа нет кредитов — дальше модель не зовётся: остальным
    сайтам пачки — «не ответила» той же причиной, без вызова (`unasked`, 20.10.2026).
    """
    gate = asyncio.Semaphore(concurrency or cfg.CONCURRENCY)
    writing = asyncio.Lock()
    broke = asyncio.Event()
    hosts = list(texts)

    async def one(host: str) -> TopicVerdict:
        async with gate:
            text = texts[host]
            verdict = unasked(text) if broke.is_set() else await _asked(http, host, text)
        if verdict.no_credits:
            broke.set()
        if on_answer is not None:
            # Сессия базы одна на проход: записи — по одной, а не наперегонки.
            async with writing:
                await on_answer(host, verdict)
        return verdict

    outcomes = await asyncio.gather(*(one(host) for host in hosts), return_exceptions=True)
    verdicts: dict[str, TopicVerdict] = {}
    for host, outcome in zip(hosts, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            # Все ответы уже дошли до записи: падает проход, а не соседи.
            raise outcome
        verdicts[host] = outcome
    return verdicts


async def _asked(http: httpx.AsyncClient, host: str, text: SiteText) -> TopicVerdict:
    """Тема одного сайта моделью; упавший вызов — «не ответила» с причиной."""
    try:
        return await classify(http, host=host, text=text)
    except Exception as exc:  # noqa: BLE001 — сбой одного сайта не роняет пачку
        logger.warning("тематика сайта упала на %s: %s", host, exc)
        return TopicVerdict(None, None, described(exc)[:300], source=text.source, unanswered=True)


def unasked(text: SiteText) -> TopicVerdict:
    """У ключа модели нет кредитов: сайт не спрашивали — темы нет, следующий проход спросит."""
    return TopicVerdict(
        None, None, NO_CREDITS_WORDS, source=text.source, unanswered=True, no_credits=True
    )


class RunLedger(Protocol):
    """Чем пачка прогона пишет расход и фиксирует сделанное (`RunRepository`)."""

    async def ensure_llm_within_cap(self, *, run_id: int | None) -> None: ...

    async def record_tokens(self, *, run_id: int | None, operation: str, tokens: int) -> object: ...

    async def session_commit(self) -> None: ...


class TopicStep:
    """Тема в прогоне: по пачке судьи, тем же текстом. Счёт копится через пачки. У ключа нет
    кредитов (`stopped`) — до конца прохода модель не зовётся: сайтам, которых спросили бы, —
    «не ответила» той же причиной, без вызова (`unasked`), и в отчёте — причина, а не нули."""

    def __init__(self, store: TopicStore, ledger: RunLedger, run_id: int | None) -> None:
        self._store = store
        self._ledger = ledger
        self._run_id = run_id
        self.summary = TopicSummary()
        #: У ключа модели нет кредитов — словами: до конца прохода тема модель не зовёт.
        self.stopped: str | None = None

    async def classify(
        self,
        http: httpx.AsyncClient,
        hosts: Sequence[str],
        pages: Mapping[str, SerpText],
        homes: Mapping[str, HomeSignals],
        *,
        stopped: str | None = None,
    ) -> None:
        """Темы сайтов пачки: свежие — из базы, остальные — моделью; каждый ответ — сразу.
        `stopped` — суд прохода встал без кредитов: модель не зовётся и здесь."""
        if not hosts:
            return
        known = await self._store.known(hosts, version=TOPIC_VERSION)
        texts = self._to_ask(known, hosts, pages, homes)
        if not texts:
            return
        self.stopped = self.stopped or stopped
        if self.stopped is not None:
            for text in texts.values():
                self.summary.record(unasked(text))
            return
        await self._ledger.ensure_llm_within_cap(run_id=self._run_id)
        log_silent(await classify_many(http, texts, on_answer=self._write))

    def _to_ask(
        self,
        known: Known,
        hosts: Sequence[str],
        pages: Mapping[str, SerpText],
        homes: Mapping[str, HomeSignals],
    ) -> dict[str, SiteText]:
        """Кого спросить и каким текстом; свежие и сайты без текста — в счёт, а не в модель."""
        texts: dict[str, SiteText] = {}
        for host in hosts:
            if host in known.fresh:
                self.summary.from_cache += 1
                continue
            text = site_text(pages.get(host), homes.get(host), known.home_titles.get(host))
            if text.empty:
                self.summary.no_text += 1
            else:
                texts[host] = text
        return texts

    async def _write(self, host: str, verdict: TopicVerdict) -> None:
        """Ответ — тема на домен и строка журнала, короткой фиксацией; потом — в счёт."""
        if not verdict.unanswered:
            await self._store.save({host: record_of(verdict)})
        if verdict.tokens:
            await self._ledger.record_tokens(
                run_id=self._run_id, operation=TOPIC_OPERATION, tokens=verdict.tokens
            )
        await self._ledger.session_commit()
        self.summary.record(verdict)
        if verdict.no_credits:
            self.stopped = NO_CREDITS_WORDS


def log_silent(verdicts: Mapping[str, TopicVerdict]) -> None:
    """Громко: темы у этих сайтов нет, и будет она только при следующем проходе."""
    silent = [verdict.reason for verdict in verdicts.values() if verdict.unanswered]
    if silent:
        logger.error("Тематика: темы нет у %s сайтов — %s", len(silent), silent[0])
