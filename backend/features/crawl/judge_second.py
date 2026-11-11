"""Второй круг судьи рекламодателей: «не понять» первого круга перепроверяет вторая модель, сильнее.

**Зачем** (слово владельца 10.11.2026). «Не понять» копятся сотнями, разбирать их руками долго, а
первая модель именно на таких парах слаба: спорит с таблицей или отвечает «не пишем» там, где
ссылка продаёт. Второй круг — тот же суд: тот же текст (он уже скачан), та же подсказка и строгая
форма, та же сверка цитат и та же таблица правил — только модель другая
(`LLM_ADVERTISER_JUDGE_SECOND_MODEL`; пусто или та же, что у первого круга, — второго круга нет).
Решила — совет её; тоже «не понять» — пара остаётся человеку.

**Две строки суда.** Суд второй модели пишется обычным путём под своей когортой (модель — её
часть): это кэш — тот же текст вторую модель дважды не спросит — и след для сверки. Исход второго
круга (совет, слова его правила, модель) кладётся полями кода в факты строки ПЕРВОЙ когорты
(`judge_facts.SECOND_KEYS`) и той же записью — в её колонки: итоговый совет, правило и нынешняя
таблица. `judge_rules.advise` учитывает исход последним шагом, поэтому пересчёт таблицей даёт те же
совет и правило, а все, кто читает совет по строке первой когорты — колонками или пересчётом по
фактам, — видят итог без своей правки.

**Сроки.** Срок строки первой когорты запись исхода не сдвигает: срок фактов — их, как у пересчёта
таблицей. Суд второй модели получает срок фактов первой, на микросекунду раньше: обе строки — об
одном тексте, и «последней строкой пары» остаётся строка первой когорты — совет под решением
человека берёт последнюю строку пары по всем когортам. Когда строка второй модели заведена на
самом деле, помнит её `created_at`.

**Кого берёт проход** (`waiting`): кроме только что рассуженных — пары очереди, у которых свежая
строка первой когорты говорит «не понять», а нынешняя вторая модель их ещё не судила. Их страницу
проход открывает заново (переобход): текст тот же — первая модель отвечает из кэша, сменился —
спрашивается снова, как всегда.

**Деньги.** Потолки те же, но перед КАЖДЫМ вопросом второй модели, а не раз на пачку: её вызов
дороже. Расход — той же операцией журнала, своей строкой, сразу: оборванный проход теряет только
вызов, который летел. Вторая модель фактов не дала (не ответила, ответ обрезан, не та форма, цитата
не нашлась) — исхода нет: пара остаётся «не понять» и ждёт следующего прохода.

**Замер на стенде 11.11.2026 — по умолчанию круг выключен.** Размеченные пары, которым первый круг
сказал «не понять» (вне доноров-каталогов), живая вторая модель судила тем же входом, что на
проходе, на двух усилиях рассуждения. Решает она около половины таких пар, но не так чисто, как
нужно, чтобы совет шёл дальше без человека. Её «пишем» верно реже порога (85 %) на обоих усилиях;
из лишних половина и больше — у ссылок с пометкой `sponsored`, которую их донор ставит едва ли не
каждой внешней ссылке: пометка — свойство донора, а не ссылки, и почти каждое такое «пишем» —
лишнее. Её «не пишем» от прогона к прогону то держит допустимую пятую часть потерь, то теряет
каждого второго — правилом «источник факта»: ссылка стоит в тексте как источник, а статья её
продаёт. И сам ответ неустойчив: между усилиями совет расходится почти у трети пар. Поэтому
умолчание настройки — пусто, и включают её словом владельца. Усилие — `medium`: решает больше пар,
и «пишем» у него вернее, а стоит примерно на треть дороже `low`.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.features.core.models.advertiser import AdvertiserJudgementModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.crawl.judge import Case, Judged, judge_seen
from backend.features.crawl.judge_client import JudgeClient, steady_message
from backend.features.crawl.judge_facts import LinkFacts
from backend.features.crawl.judge_rules import (
    ADVICE_WORDS,
    RULES_VERSION,
    SECOND_RULE,
    Advice,
    Ruled,
    advise,
)
from backend.features.crawl.judge_store import JUDGEMENT_TTL_DAYS, Cohort, cohort_of
from backend.features.crawl.judge_text import Seen, fingerprint
from backend.shared.numbers import NBSP, spaced

logger = logging.getLogger(__name__)

Judgement = AdvertiserJudgementModel
Pair = tuple[str, str]

#: Усилие рассуждения второй модели — своё (у первой — `judge_client.REASONING_EFFORT`): по замеру
#: (шапка модуля).
SECOND_EFFORT = "medium"
#: Потолок вывода второй модели — выше, чем у первой (`judge_client.TOKENS_REASONING`): на своём
#: усилии она рассуждает дольше, и на потолке первой ответ в замере обрывался — оплачен, а исхода
#: нет, и такую пару спрашивал бы каждый проход. С этим потолком тот же случай рассужен.
SECOND_ROOM = 8000
#: Токенов на случай второго круга, пока у когорты второй модели нет истории: среднее замера на
#: этом усилии, с округлением вверх. Настоящее число даст первый проход — дальше история.
FALLBACK_TOKENS = 5800
#: Какая доля новых судов первого круга кончается «не понять», пока у когорты нет истории: оценка
#: сверху по суду очереди проверки. Настоящую долю даст история когорты.
UNCLEAR_SHARE = 0.15
#: Исходы второго круга в порядке итога — словами совета.
DECIDED = (Advice.WRITE, Advice.SKIP)


def model_for(first: str) -> str:
    """Модель второго круга для прохода с этой первой моделью; пусто — второго круга нет. Та же
    модель вторым кругом не судит: тот же вопрос — тот же ответ из её же кэша."""
    chosen = llm_cfg.ADVERTISER_JUDGE_SECOND_MODEL
    return "" if chosen == first else chosen


def _n(word: str, number: int) -> str:
    """Слово и его число — через неразрывный пробел: на экране число не отрывается от слова."""
    return f"{word}{NBSP}{spaced(number)}"


def _of(cohort: Cohort) -> list[ColumnElement[bool]]:
    """Строки суда этой когорты: поля когорты — одноимённые колонки строки."""
    named: dict[str, Any] = dataclasses.asdict(cohort)
    return [getattr(Judgement, name) == value for name, value in named.items()]


# --- план -----------------------------------------------------------------------------------------


async def waiting(session: AsyncSession, cohort: Cohort, now: datetime) -> frozenset[Pair]:
    """Пары, которые ждут второго круга: последняя свежая строка первой когорты — «не понять», и
    нынешняя вторая модель пару ещё не судила. Второго круга нет — пусто."""
    model = model_for(cohort.model)
    if not model:
        return frozenset()
    found = await session.execute(
        select(
            Judgement.donor_host,
            Judgement.target_root,
            Judgement.advice,
            Judgement.facts["second_model"].astext,
        )
        .where(*_of(cohort), Judgement.updated_at >= now - timedelta(days=JUDGEMENT_TTL_DAYS))
        .order_by(Judgement.updated_at, Judgement.id)
    )
    latest = {(donor, target): (advice, by) for donor, target, advice, by in found.tuples()}
    unclear = Advice.UNCLEAR.value
    return frozenset(
        pair for pair, (advice, by) in latest.items() if advice == unclear and by != model
    )


@dataclass(frozen=True, slots=True)
class SecondPlan:
    """Второй круг в плане прохода — до траты."""

    model: str
    #: Пары плана, которые ждут второго круга: первый круг уже сказал им «не понять».
    waiting: int
    #: Сколько новых случаев плана, по истории когорты, получит «не понять» и уйдёт сюда же.
    expected: int
    per_case: int
    #: `per_case` — по истории когорты второй модели; иначе — запасное число `FALLBACK_TOKENS`.
    from_history: bool

    @property
    def pairs(self) -> int:
        return self.waiting + self.expected

    @property
    def tokens(self) -> int:
        return self.pairs * self.per_case

    def line(self) -> str:
        """Строка плана и сметы — одна у консоли и у экрана."""
        source = "" if self.from_history else " (запасное число, истории ещё нет)"
        new = (
            f", из них среди новых случаев — {_n('около', self.expected)}" if self.expected else ""
        )
        return (
            f"Второй круг для «не понять» (модель {self.model}): {_n('пар', self.pairs)}{new}, "
            f"{_n('до', self.tokens)}{NBSP}токенов, {_n('по', self.per_case)} на пару{source}"
        )


async def _unclear_share(session: AsyncSession, cohort: Cohort) -> float:
    """Доля «не понять» первого круга в судах когорты; судов ещё нет — запасная."""
    undecided = (Judgement.advice == Advice.UNCLEAR.value) | (Judgement.rule == SECOND_RULE)
    counted = await session.execute(
        select(func.count(), func.count().filter(undecided)).where(*_of(cohort))
    )
    total, unclear = counted.one()
    return unclear / total if total else UNCLEAR_SHARE


async def planned(
    session: AsyncSession, cohort: Cohort, *, waiting: int, new: int
) -> SecondPlan | None:
    """Второй круг плана: сколько пар его ждёт, сколько новых уйдёт на него по истории и во что это
    обойдётся — по истории когорты второй модели, иначе запасным числом. Круга нет — `None`."""
    model = model_for(cohort.model)
    if not model:
        return None
    mean = await session.scalar(
        select(func.avg(Judgement.tokens)).where(*_of(cohort_of(model)), Judgement.tokens > 0)
    )
    expected = math.ceil(new * await _unclear_share(session, cohort))
    per_case = FALLBACK_TOKENS if mean is None else math.ceil(float(mean))
    return SecondPlan(model, waiting, expected, per_case, mean is not None)


# --- итог -----------------------------------------------------------------------------------------


@dataclass(slots=True)
class Tally:
    """Второй круг прохода числами: сколько пар на него ушло и чем он кончил."""

    model: str = ""
    sent: int = 0
    #: Что сказала вторая модель: «пишем», «не пишем» — решила; «не понять» — оставила человеку.
    advice: Counter[str] = field(default_factory=Counter)
    #: Фактов не дала: пара осталась «не понять» и ждёт следующего прохода.
    silent: int = 0
    cached: int = 0
    tokens: int = 0

    def add(self, model: str, judged: Judged) -> None:
        self.model = model
        self.sent += 1
        self.cached += judged.cached
        self.tokens += judged.tokens
        if judged.advice is None:
            self.silent += 1
        else:
            self.advice[judged.advice.value] += 1

    def lines(self) -> list[str]:
        """Итог второго круга словами — одними у консоли и у экрана; круга не было — пусто."""
        if not self.sent:
            return []
        said = ", ".join(_n(ADVICE_WORDS[advice], self.advice[advice.value]) for advice in DECIDED)
        left = _n("оставил «не понять»", self.advice[Advice.UNCLEAR.value])
        lines = [
            f"второй круг (модель {self.model}): {_n('пар', self.sent)}, решил — {said}; {left}; "
            f"{_n('токенов', self.tokens)}"
        ]
        if self.silent:
            lines.append(
                f"второй круг без ответа модели: {spaced(self.silent)} — спросит следующий проход"
            )
        if self.cached:
            lines.append(f"второй круг из кэша, без модели: {spaced(self.cached)}")
        return lines

    def fields(self) -> dict[str, Any]:
        """Поля строки журнала: по ним ищут, а не по словам."""
        return {
            "model": self.model,
            "sent": self.sent,
            "advice": dict(self.advice),
            "silent": self.silent,
            "cached": self.cached,
            "tokens": self.tokens,
        }


# --- суд ------------------------------------------------------------------------------------------


def _wanted(first: Judged, model: str) -> bool:
    """Нужен ли паре второй круг: первый дал факты и «не понять», а эта модель пару не судила."""
    if first.facts is None or first.advice is not Advice.UNCLEAR or first.no_credits:
        return False
    return first.facts.second_model != model


async def _first_row(
    session: AsyncSession, case_: Case, seen: Seen, cohort: Cohort
) -> tuple[int, datetime] | None:
    """Строка первой когорты, чьё «не понять» перепроверяет второй круг: номер и срок её фактов."""
    found = await session.execute(
        select(Judgement.id, Judgement.updated_at).where(
            Judgement.donor_host == case_.donor_host,
            Judgement.target_root == case_.link.target,
            Judgement.context_print == fingerprint(steady_message(case_.link, seen.text)),
            *_of(cohort),
        )
    )
    row = found.one_or_none()
    return None if row is None else (row[0], row[1])


async def _note(
    session: AsyncSession, row_id: int, facts: LinkFacts, *, catalog_donor: bool
) -> Ruled:
    """Исход второго круга — в строку первой когорты одной записью: факты с ним, итоговый совет и
    правило — в колонки, таблица — нынешняя. Срок строки — прежний (шапка модуля)."""
    ruled = advise(facts, catalog_donor=catalog_donor)
    await session.execute(
        update(Judgement)
        .where(Judgement.id == row_id)
        # Без явного значения `updated_at` сдвинулся бы.
        .values(
            facts=facts.as_json(),
            advice=ruled.advice.value,
            rule=ruled.rule,
            rules_version=RULES_VERSION,
            updated_at=Judgement.updated_at,
        )
        .execution_options(synchronize_session=False)
    )
    return ruled


@dataclass(slots=True)
class SecondRound:
    """Второй круг прохода: клиент второй модели; потолки и запись расхода — те же, что у прохода,
    и даёт их он. `stopped` — потолок выбран посреди пачки: проход встаёт этими словами."""

    client: JudgeClient
    #: Когорта первой модели: в её строку пишется исход.
    first: Cohort
    caps: Callable[[AsyncSession], Awaitable[None]]
    spend: Callable[[AsyncSession, int], Awaitable[None]]
    stopped: str = ""

    @property
    def model(self) -> str:
        return self.client.model

    async def settle(
        self,
        session: AsyncSession,
        case_: Case,
        seen: Seen,
        first: Judged,
        tally: Tally,
        catalog_donor: bool = False,
    ) -> Judged:
        """Суд пары после первого круга: «не понять» — тот же текст судит вторая модель, её исход —
        в строку первой когорты, совет пары — итоговый. Остальным — совет первого круга как есть."""
        if not _wanted(first, self.model) or first.facts is None:
            return first
        row = await _first_row(session, case_, seen, self.first)
        if row is None:
            logger.warning(
                "судья рекламодателей: строки первой когорты нет — исход второго круга не записать",
                extra={"donor": case_.donor_host, "target": case_.link.target},
            )
            return first
        try:
            await self.caps(session)
        except LlmCapExceededError as exc:
            logger.warning(
                "судья рекламодателей: потолок модели — второй круг встаёт",
                extra={"why": str(exc)},
            )
            self.stopped = str(exc)
            return first
        number, judged_at = row
        # Срок суда второй модели — срок фактов первой, на микросекунду раньше (шапка модуля).
        term = judged_at - timedelta(microseconds=1)
        # Признак донора-каталога — тот же, что у первого круга: он о доноре, а не о модели.
        again = await judge_seen(
            session, case_, seen, client=self.client, now=term, catalog_donor=catalog_donor
        )
        tally.add(self.model, again)
        spent = first.tokens + again.tokens
        if again.advice is None:
            await self.spend(session, again.tokens)
            return dataclasses.replace(first, tokens=spent, no_credits=again.no_credits)
        facts = dataclasses.replace(
            first.facts,
            second_advice=again.advice.value,
            second_words=again.words,
            second_model=self.model,
        )
        ruled = await _note(session, number, facts, catalog_donor=catalog_donor)
        await self.spend(session, again.tokens)
        return dataclasses.replace(
            first,
            advice=ruled.advice,
            rule=ruled.rule,
            words=ruled.words,
            facts=facts,
            tokens=spent,
            cached=first.cached and again.cached,
        )


def of_pass(
    client: JudgeClient,
    *,
    caps: Callable[[AsyncSession], Awaitable[None]],
    spend: Callable[[AsyncSession, int], Awaitable[None]],
) -> SecondRound | None:
    """Второй круг для прохода с этим клиентом первой модели: вторая модель — на том же HTTP-клиенте
    и с тем же ключом. Не задана — `None`: прохода это не меняет."""
    model = model_for(client.model)
    if not model:
        return None
    again = client.with_model(model, effort=SECOND_EFFORT, room=SECOND_ROOM)
    return SecondRound(again, cohort_of(client.model), caps, spend)
