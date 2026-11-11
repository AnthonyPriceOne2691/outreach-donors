"""Проход судьи рекламодателей в тени: спорные и «куплено» штатной командой (проект автоматизации
спорных, PR 6). Ядро — один случай от текста до совета (`judge.py`); здесь — кого судить, сколько
это стоит и как не потратить лишнего.

**Тень.** Проход пишет только строки суда (`advertiser_judgements`): решение человека (`confirmed`)
он не трогает никогда, и очередь проверки, письма и доли выборки его не видят. Совет человек видит
только после своего решения: решение не должно зависеть от того, что идёт в замер
(`crawl/sample.py`).

**План — до траты** (`plan_pass`): кого судить и сколько это токенов — по истории той же когорты,
а нет истории — запасным числом, и оно названо. Кого: спорные и «куплено», сначала строки
случайной выборки в её порядке (md5 домена, как `accuracy.sample_turns`) — по ним идёт калибровка,
затем остальные, сильные первыми. **Набор по донорам** (`per_donor`) — не больше N случаев на донора,
по всем донорам, в том же порядке md5 домена: у одного донора бывают сотни спорных, и выборка по
домену может отдать ему большую часть случаев, а калибровка должна видеть все площадки и их ниши, а
не тех, у кого спорных больше всего. Пара со свежей строкой суда той же когорты пропускается: повтор
после обрыва продолжает с места и страниц заново не открывает. «Заново» (`again`) открывает их
снова, но тот же текст модель второй раз не спросит: кэш ядра — по отпечатку текста.

**Деньги** (урок #443–#447). Модель отвечает проверке до первой траты (`ensure_model`). Перед
каждой пачкой — потолки модели: общий или лимит части «Аутрич» и свой потолок судьи за сутки
(`ADVERTISER_JUDGE_DAILY_TOKEN_CAP`). Упёрлись или у ключа нет кредитов — проход встаёт словами, и
следующие пачки модель не зовут. Каждый суд — строка журнала расхода `advertiser_judge` сразу,
короткой фиксацией: оборванный проход теряет только случай, который летел.

**Кнопка.** «Переобойти спорные» идёт этим же проходом: смета — `judge_estimate.py`, задача —
`workers/judge_jobs.py`. Обходчик перезапускают — проход встаёт на границе пачки (`stop`), следующий
запуск продолжает с места.

**Сеть.** Клиент — с защитой адресов (`shared/net/url_guard.guarded_client`, его даёт вызывающий):
страницы и главные чужие, и адрес из данных обхода не уведёт запрос во внутреннюю сеть. Страницы
пачки открываются разом, но к одному донору — по одной и с паузой обходчика (`CRAWL_DELAY_SEC`):
у одного донора бывают сотни спорных. Модель — по одному случаю: сессия базы на проход одна.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import crawl as crawl_cfg
from backend.config import llm as llm_cfg
from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import AdvertiserJudgementModel, CandidateModel
from backend.features.core.usage import LlmCapExceededError, OwnCap, ensure_llm_within_cap
from backend.features.core.usage import record as record_usage
from backend.features.crawl.judge import Case, Judged, Outcome, case_of, judge_seen
from backend.features.crawl.judge_client import JudgeClient
from backend.features.crawl.judge_rules import ADVICE_WORDS, Advice
from backend.features.crawl.judge_store import JUDGEMENT_TTL_DAYS, Cohort, catalog_donors, cohort_of
from backend.features.crawl.judge_text import Seen, gather
from backend.features.crawl.sample import in_sample, sampled
from backend.shared.llm import ensure_model
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

Judgement = AdvertiserJudgementModel

#: Операция журнала расхода (`core/usage.OPERATION_PROVIDERS`).
JUDGE_OPERATION = "advertiser_judge"
#: Кого судит проход: спорных и «куплено» — оба уходят к письму по решению, которое судья меряет.
JUDGED = (Verdict.PENDING, Verdict.BOUGHT)
#: Случаев в пачке: перед каждой — потолки модели.
CHUNK = 20
#: Сколько страниц пачки открывается разом; к одному донору — всё равно по одной.
FETCH_CONCURRENCY = 4
#: Токенов на случай, пока у когорты нет истории: оценка по проверке прошлой подсказки на настоящих
#: спорных плюс общий вопрос, адрес и счётчики. Настоящее число даст первый проход — дальше история.
FALLBACK_TOKENS = 4200
NO_CREDITS = "у ключа модели нет кредитов — пополните счёт, затем повторите проход"
RESTARTING = "сервис перезапускался"
#: Исходы ядра словами (`crawl/judge.Outcome`) — одни у консоли и у экрана.
OUTCOMES = {
    "judged": "рассужены",
    "blind": "судить нечем (страница не открылась или ссылки на ней нет)",
    "denied": "заглушка защиты вместо страницы",
    "unanswered": "модель не ответила",
    "cut_off": "ответ обрезан потолком вывода",
    "form_broken": "ответ не строгой формы",
    "quote_missing": "цитата не нашлась и после второго вопроса",
}

Progress = Callable[[int, int], None]


def judge_cap() -> OwnCap:
    """Свой дневной потолок судьи — внутри общего, тем же журналом."""
    return OwnCap(
        "судьи рекламодателей",
        frozenset({JUDGE_OPERATION}),
        llm_cfg.ADVERTISER_JUDGE_DAILY_TOKEN_CAP,
        "ADVERTISER_JUDGE_DAILY_TOKEN_CAP",
    )


async def within_caps(session: AsyncSession) -> None:
    """Потолки модели до пачки: лимит части «Аутрич» (блок «Расхода» сохранён) или общий — и свой
    потолок судьи. Свой потолок с собственным именем часть блока не судит, поэтому вызова два."""
    await ensure_llm_within_cap(session)
    await ensure_llm_within_cap(session, own=judge_cap())


@dataclass(slots=True)
class JudgePlan:
    """Кого судить и во что это обойдётся — до единого вызова модели."""

    #: Кандидаты по порядку прохода: выборка первой.
    ids: list[int]
    #: Из них — строки случайной выборки.
    sampled: int
    by_verdict: dict[str, int]
    #: Уже судимы: у пары свежая строка суда нынешней когорты (`JUDGEMENT_TTL_DAYS`) — страницы не
    #: открываются; перечитываются по сроку, по смене когорты или по «заново».
    fresh: int
    #: Пропущены: у кандидата нет лучшей страницы — судить нечего.
    without_page: int
    #: Разных доноров среди тех, кого судим.
    donors: int
    per_case: int
    #: `per_case` — по истории когорты; иначе — запасное число `FALLBACK_TOKENS`.
    from_history: bool
    cohort: Cohort

    @property
    def tokens(self) -> int:
        """Верхняя граница: часть страниц не откроется, часть ответов — из кэша."""
        return len(self.ids) * self.per_case


async def _per_case(session: AsyncSession, cohort: Cohort) -> tuple[int, bool]:
    """Токенов на случай — среднее оплаченных судов той же когорты; нет — запасное число."""
    mean = await session.scalar(
        select(func.avg(Judgement.tokens)).where(*_of_cohort(cohort), Judgement.tokens > 0)
    )
    if mean is None:
        return FALLBACK_TOKENS, False
    return math.ceil(float(mean)), True


def _of_cohort(cohort: Cohort) -> tuple[ColumnElement[bool], ...]:
    return (
        Judgement.prompt_version == cohort.prompt_version,
        Judgement.prompt_print == cohort.prompt_print,
        Judgement.examples_print == cohort.examples_print,
        Judgement.model == cohort.model,
    )


async def _fresh_pairs(
    session: AsyncSession, cohort: Cohort, now: datetime
) -> set[tuple[str, str]]:
    found = await session.execute(
        select(Judgement.donor_host, Judgement.target_root).where(
            *_of_cohort(cohort), Judgement.updated_at >= now - timedelta(days=JUDGEMENT_TTL_DAYS)
        )
    )
    return {(donor, target) for donor, target in found.tuples()}


async def _candidates(
    session: AsyncSession, verdicts: Sequence[Verdict], *, sample_only: bool, spread: bool = False
) -> list[tuple[int, Verdict, str, str, str | None, bool]]:
    """Кандидаты по порядку прохода: выборка — в её порядке, затем остальные, сильные первыми.
    Набор по донорам (`spread`) — все разом в порядке md5 домена: потолок на донора ставит план."""
    columns = (
        CandidateModel.id,
        CandidateModel.verdict,
        CandidateModel.donor_host,
        CandidateModel.target_root,
        CandidateModel.best_page_url,
    )
    base = select(*columns).where(CandidateModel.verdict.in_(verdicts))
    if spread:
        if sample_only:
            base = base.where(in_sample())
        found = await session.execute(
            base.order_by(func.md5(CandidateModel.target_root), CandidateModel.id)
        )
        return [(*row, sampled(row[3])) for row in found.tuples()]
    sample = await session.execute(
        base.where(in_sample()).order_by(func.md5(CandidateModel.target_root), CandidateModel.id)
    )
    rows = [(*row, True) for row in sample.tuples()]
    if not sample_only:
        rest = await session.execute(
            base.where(~in_sample()).order_by(CandidateModel.points.desc(), CandidateModel.id)
        )
        rows += [(*row, False) for row in rest.tuples()]
    return rows


async def plan_pass(
    session: AsyncSession,
    *,
    verdicts: Sequence[Verdict] = JUDGED,
    sample_only: bool = False,
    limit: int | None = None,
    per_donor: int | None = None,
    again: bool = False,
    model: str | None = None,
    now: datetime | None = None,
) -> JudgePlan:
    """План прохода: кого, по порядку, сколько пропущено и почему, токены на случай. `per_donor` —
    набор по донорам: не больше стольких новых случаев на донора (уже судимые не в счёт)."""
    cohort = cohort_of(model or llm_cfg.ADVERTISER_JUDGE_MODEL)
    fresh = set() if again else await _fresh_pairs(session, cohort, now or datetime.now(UTC))
    ids: list[int] = []
    in_sample_count = skipped = without_page = 0
    by_verdict: Counter[str] = Counter()
    by_donor: Counter[str] = Counter()
    for candidate_id, verdict, donor, target, page, in_turn in await _candidates(
        session, verdicts, sample_only=sample_only, spread=per_donor is not None
    ):
        if (donor, target) in fresh:
            skipped += 1
            continue
        if not page:
            without_page += 1
            continue
        if per_donor is not None and by_donor[donor] >= per_donor:
            continue
        if limit is not None and len(ids) >= limit:
            continue
        ids.append(candidate_id)
        in_sample_count += in_turn
        by_verdict[Verdict(verdict).value] += 1
        by_donor[donor] += 1
    per_case, from_history = await _per_case(session, cohort)
    return JudgePlan(
        ids,
        in_sample_count,
        dict(by_verdict),
        skipped,
        without_page,
        len(by_donor),
        per_case,
        from_history,
        cohort,
    )


@dataclass(slots=True)
class PassReport:
    """Итог прохода — числами по исходам ядра, а не «сделано»: не-вердикт виден числом с причиной
    (урок #91), остановка — словами."""

    outcomes: Counter[str] = field(default_factory=Counter)
    advice: Counter[str] = field(default_factory=Counter)
    #: Вид отказа модели у `unanswered`.
    refusals: Counter[str] = field(default_factory=Counter)
    cached: int = 0
    tokens: int = 0
    retried: int = 0
    retry_held: int = 0
    #: Кандидата к моменту суда уже нет или у него не стало лучшей страницы.
    gone: int = 0
    #: Не спрошены: проход встал раньше.
    left: int = 0
    #: Уже судимы: свежий суд нынешней когорты, страницы не открывались (`JudgePlan.fresh`).
    judged_before: int = 0
    stopped: str = ""

    def add(self, judged: Judged) -> None:
        self.outcomes[judged.outcome.value] += 1
        if judged.advice is not None:
            self.advice[judged.advice.value] += 1
        if judged.refusal is not None:
            self.refusals[judged.refusal] += 1
        self.cached += judged.cached
        self.tokens += judged.tokens
        self.retried += judged.retried
        self.retry_held += judged.retry_held

    @property
    def done(self) -> int:
        return sum(self.outcomes.values())


def report_lines(report: PassReport) -> list[str]:
    """Итог словами — одними у консоли и у экрана: исходы, советы и что обошлось без модели."""
    lines = [
        f"{words}: {spaced(report.outcomes[name])}"
        for name, words in OUTCOMES.items()
        if report.outcomes.get(name, 0)
    ]
    advice = ", ".join(
        f"{ADVICE_WORDS[Advice(name)]} {spaced(count)}"
        for name, count in sorted(report.advice.items())
    )
    if advice:
        lines.append(f"советы: {advice}")
    if report.cached:
        lines.append(f"из кэша, без модели: {spaced(report.cached)}")
    if report.retried:
        lines.append(
            f"второй вопрос цитате: {spaced(report.retried)}, помог {spaced(report.retry_held)}"
        )
    if report.judged_before:
        lines.append(f"уже судимы раньше, без скачивания: {spaced(report.judged_before)}")
    if report.gone:
        lines.append(f"кандидата к суду уже не было: {spaced(report.gone)}")
    return lines


class Polite:
    """К одному донору — по одной странице и с паузой от конца прошлого запроса, как у обходчика."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def call[T](self, host: str, fetch: Callable[[], Awaitable[T]]) -> T:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            clock = asyncio.get_running_loop()
            wait = self._last.get(host, -math.inf) + self.delay - clock.time()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await fetch()
            finally:
                self._last[host] = clock.time()


async def _seen_all(http: httpx.AsyncClient, cases: Sequence[Case], polite: Polite) -> list[Seen]:
    """Страницы и главные пачки — разом, к одному донору — по очереди."""
    gate = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def one(case_: Case) -> Seen:
        async with gate:
            return await polite.call(
                case_.donor_host,
                lambda: gather(
                    http,
                    page_url=case_.page_url,
                    target_root=case_.link.target,
                    anchor=case_.link.anchor,
                ),
            )

    return list(await asyncio.gather(*(one(case_) for case_ in cases)))


async def _cases(session: AsyncSession, ids: Sequence[int], report: PassReport) -> list[Case]:
    """Кандидаты пачки → случаи судьи; исчезнувшие с плана — числом."""
    found = await session.scalars(select(CandidateModel).where(CandidateModel.id.in_(ids)))
    by_id = {candidate.id: candidate for candidate in found.all()}
    cases: list[Case] = []
    for candidate_id in ids:
        candidate = by_id.get(candidate_id)
        case_ = await case_of(session, candidate) if candidate is not None else None
        if case_ is None:
            report.gone += 1
        else:
            cases.append(case_)
    return cases


async def run_pass(
    session: AsyncSession,
    plan: JudgePlan,
    *,
    client: JudgeClient,
    http: httpx.AsyncClient,
    progress: Progress | None = None,
    delay: float | None = None,
    stop: Callable[[], bool] | None = None,
) -> PassReport:
    """Суд по плану — пачками; каждый суд — в базу сразу, со строкой журнала расхода. `stop` —
    просьба встать: спрашивается перед каждой пачкой."""
    await ensure_model(client.model)
    report = PassReport(judged_before=plan.fresh)
    polite = Polite(crawl_cfg.DELAY_SEC if delay is None else delay)
    for start in range(0, len(plan.ids), CHUNK):
        chunk = plan.ids[start : start + CHUNK]
        if not report.stopped and stop is not None and stop():
            report.stopped = RESTARTING
        if not report.stopped:
            await _chunk(session, chunk, client=client, http=http, polite=polite, report=report)
        else:
            report.left += len(chunk)
        if progress is not None:
            progress(min(start + CHUNK, len(plan.ids)), len(plan.ids))
    _log(report)
    return report


async def _chunk(
    session: AsyncSession,
    ids: Sequence[int],
    *,
    client: JudgeClient,
    http: httpx.AsyncClient,
    polite: Polite,
    report: PassReport,
) -> None:
    """Пачка: потолки, страницы разом, затем суд по одному случаю с фиксацией каждого."""
    try:
        await within_caps(session)
    except LlmCapExceededError as exc:
        logger.warning(
            "судья рекламодателей: потолок модели — проход встаёт", extra={"why": str(exc)}
        )
        report.stopped = str(exc)
        report.left += len(ids)
        return
    cases = await _cases(session, ids, report)
    seen = await _seen_all(http, cases, polite)
    # Доноры-каталоги (`judge_rules.is_catalog`) — одним запросом на пачку, а не на пару: донор,
    # набравший пары в этом же проходе, получает признак со следующей пачки, а не со следующего прохода.
    catalogs = await catalog_donors(session, cohort_of(client.model))
    for at, (case_, view) in enumerate(zip(cases, seen, strict=True)):
        judged = await judge_seen(
            session, case_, view, client=client, catalog_donor=case_.donor_host in catalogs
        )
        if judged.tokens:
            record_usage(session, operation=JUDGE_OPERATION, units=judged.tokens)
        await session.commit()
        report.add(judged)
        if judged.no_credits:
            report.stopped = NO_CREDITS
            report.left += len(cases) - at - 1
            return


def _log(report: PassReport) -> None:
    """Итог — строкой журнала с полями: по ним ищут, а не по словам."""
    logger.info(
        "судья рекламодателей: проход окончен",
        extra={
            "outcomes": dict(report.outcomes),
            "advice": dict(report.advice),
            "refusals": dict(report.refusals),
            "cached": report.cached,
            "tokens": report.tokens,
            "retried": report.retried,
            "retry_held": report.retry_held,
            "gone": report.gone,
            "left": report.left,
            "judged_before": report.judged_before,
            "stopped": report.stopped,
            "judged": report.outcomes.get(Outcome.JUDGED.value, 0),
        },
    )
