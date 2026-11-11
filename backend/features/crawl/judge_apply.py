"""Советы судьи рекламодателей — решениями: что судья сказал очереди и применение полосы порцией.

Решение владельца 10.11.2026. В очереди проверки сотни спорных пар «донор → домен», совет
судьи есть почти у каждой, а людей, которые разберут их руками, нет. Советы применяются **полосами**:
«не пишем» и «пишем» — каждая своим нажатием и после показа плана (`judge_plan.py`). «Не понять»
полосой не бывает: это и есть работа человека. Откат партии — `judge_revert.py`, журнал партий —
`judge_batches.py`.

**Тем же путём, что решение человека** (`review.decide`). Своего пути у судьи нет: «не пишем»
снимает рекламодателя домена и его письма (`promote.withdraw`), если других подходящих ссылок у
домена нет, и говорит, когда рекламодатель уже ответил. Отличает решение только автор —
`судья:<партия>` (`judge_mark.py`): по нему строку видно под «Показывать решённые», человек
переигрывает её обычным решением, а партия находится и откатывается.

**Какой совет применяется.** Последний суд пары в нынешней когорте — подсказка с отпечатком, примеры
и модель (`judge_store.cohort_of`), — не старше срока фактов (`JUDGEMENT_TTL_DAYS`). Свежесть — та же,
что у прохода судьи (`judge_pass.plan_pass`): что проход считает разобранным, то и применяется. Суд
другой когорты — «совет устарел»: не применяется и считается отдельно, обновляет его «Переобойти
спорные».

**Совет — всегда нынешней таблицей правил** (слово владельца 11.11.2026). Правка таблицы меняет
`RULES_VERSION`, а совет в строке суда остаётся прежним, пока его не пересчитают, — и запускать
консольный пересчёт на бою некому. Поэтому до отбора советы последних судов, поставленные прежней
таблицей, ставятся заново штатным пересчётом без модели и без токенов (`judge_store.relabel`): план
и применение идут по тому, что таблица говорит сейчас. Пересчитываются только суды ждущих пар, а не
вся таблица судов: чтение очереди не должно переписывать тысячи чужих строк. Строка, чьи факты не
той формы, пересчёту не даётся и остаётся «совет устарел». Пересчёт — запись: фиксирует её
вызывающий, и план тоже. С таблицы v6 совет зависит ещё и от признака донора-каталога
(`judge_store.Catalogs`): советы ждущих пар, которые от него зависят, пересчёт сверяет каждый раз.

**Только спорные** (`APPLIED`), как у кнопки «Переобойти спорные». «Куплено» идёт к письму без
человека, и «не пишем» судьи по такой паре снимало бы рекламодателя, которому уже пишут: это другое
решение, и его владелец не принимал.

**«Пишем» ждёт DR** (`units_stop.dr_settled`, слово владельца 06.11.2026: кандидат без DR к письму не
идёт). Человек, сказавший «пишем», пропускает пару мимо этого условия: он на сайт смотрел. Судья
DR не знает, и его «пишем» паре без DR провело бы к письму крупный сайт. Такая пара ждёт DR и
считается отдельно; «не пишем» DR не нужен.

**Порция и гонка.** Применение — порцией в одной транзакции, фиксирует её вызывающий: обрыв теряет
только летевшую порцию, а повтор продолжает с места — решённые пары в отбор не входят. Строки порции
берутся под замок до фиксации: пара, которую за это время решил человек, пропускается и считается, а
решение человека, пришедшее во время порции, ложится поверх решения судьи, а не под него.
"""

from __future__ import annotations

import logging
import secrets
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import NamedTuple

from sqlalchemy import ColumnElement, and_, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import AdvertiserJudgementModel, CandidateModel
from backend.features.crawl import review
from backend.features.crawl.judge_mark import marker_of, started_at
from backend.features.crawl.judge_rules import BY_DONOR, RULES_VERSION, Advice
from backend.features.crawl.judge_store import JUDGEMENT_TTL_DAYS, Cohort, cohort_of, relabel
from backend.features.crawl.units_stop import dr_settled

logger = logging.getLogger(__name__)

Judgement = AdvertiserJudgementModel

#: Чьи советы применяются: спорные (шапка модуля).
APPLIED = (Verdict.PENDING,)
#: Пар в порции по умолчанию и больше которых за запрос не берём: решение — несколько запросов к
#: базе, и порция должна уложиться в секунды.
PORTION = 200
MAX_PORTION = 500
#: Сколько доменов «уже ответил» называет итог порции поимённо; остальные — числом.
ANSWERED_NAMED = 20
#: Значений в одном списке запроса: у запроса не больше 32 767 параметров, а полоса бывает длиннее.
IN_CHUNK = 1000
#: На сколько время в имени партии может опережать часы сервера: расхождение часов, не больше.
NAME_SLACK = timedelta(hours=1)

Pair = tuple[str, str]


def chunked[T](values: Sequence[T], size: int = IN_CHUNK) -> Iterator[Sequence[T]]:
    """Список значений — кусками под один запрос (`IN_CHUNK`)."""
    for at in range(0, len(values), size):
        yield values[at : at + size]


class Band(StrEnum):
    """Полоса — совет судьи, который применяется решением. «Не понять» полосой не бывает."""

    SKIP = "skip"
    WRITE = "write"

    @property
    def confirmed(self) -> bool:
        """Каким решением ложится совет полосы: «пишем» — да, «не пишем» — нет."""
        return self is Band.WRITE


class BatchBandError(ValueError):
    """Партия названа не так или начата другой полосой: под одной меткой полосы не смешиваются."""


@dataclass(frozen=True, slots=True)
class Said:
    """Спорная пара, которая ждёт человека, и что о ней сказал судья."""

    candidate_id: int
    donor_host: str
    target_root: str
    points: int
    #: DR не держит пару у перевода (`units_stop.dr_settled`): «пишем» судьи без него ждёт.
    dr_known: bool
    #: Совет нынешней когорты и нынешней таблицы; пусто — совета нет или он устарел.
    advice: Advice | None = None
    rule: str = ""
    #: Суд есть, но не нынешний: другая когорта, срок вышел или факты не дались пересчёту таблицей.
    stale: bool = False
    judgement_id: int | None = None

    def fits(self, band: Band) -> bool:
        """Пара идёт полосой: совет тот, а «пишем» — только с известным DR."""
        if self.advice is None or self.advice.value != band.value:
            return False
        return band is Band.SKIP or self.dr_known


@dataclass(frozen=True, slots=True)
class Standing:
    """Что судья сказал очереди — одной строкой: числа сходятся в «ждут человека»."""

    waiting: int = 0
    skip: int = 0
    write: int = 0
    #: Совет «пишем», а DR не спрошен: ждут его, в полосу не идут.
    waiting_dr: int = 0
    unclear: int = 0
    stale: int = 0
    without: int = 0


@dataclass(slots=True)
class Applied:
    """Итог порции — числами: что применено, что пропущено и почему, сколько осталось."""

    batch: str
    band: Band
    applied: int = 0
    #: Пропущены: пока шла порция, пару решил человек (или другая партия).
    decided: int = 0
    #: Пропущены: строку заменил новый обход — пара вернётся в отбор следующей порцией.
    gone: int = 0
    #: Пар полосы осталось после порции.
    left: int = 0
    #: Что решения сделали с рекламодателями (`promote.Withdrawn`): сняты и писем снято.
    removed: int = 0
    stopped: int = 0
    #: Домены, которые уже ответили: «не пишем» переписку не останавливает (`review.Decided`).
    answered: list[str] = field(default_factory=list)


class _Last(NamedTuple):
    """Последний нынешний суд пары: номер строки, совет, правило и какой таблицей он поставлен."""

    number: int
    advice: str
    rule: str
    table: str


def _waiting() -> tuple[ColumnElement[bool], ...]:
    """Пары, которые ждут человека."""
    return (CandidateModel.verdict.in_(APPLIED), CandidateModel.confirmed.is_(None))


async def _latest(
    session: AsyncSession, cohort: Cohort, since: datetime
) -> tuple[dict[Pair, _Last], set[Pair]]:
    """Последний нынешний суд каждой ждущей пары и пары, у которых суд есть хоть какой. Факты и
    текст не читаются: строк тысячи, а нужны четыре колонки."""
    found = await session.execute(
        select(
            Judgement.donor_host,
            Judgement.target_root,
            Judgement.id,
            Judgement.advice,
            Judgement.rule,
            Judgement.rules_version,
            Judgement.prompt_version,
            Judgement.prompt_print,
            Judgement.examples_print,
            Judgement.model,
            Judgement.updated_at,
        )
        .join(
            CandidateModel,
            and_(
                CandidateModel.donor_host == Judgement.donor_host,
                CandidateModel.target_root == Judgement.target_root,
            ),
        )
        .where(*_waiting())
        .order_by(Judgement.updated_at, Judgement.id)
    )
    now_cohort = (cohort.prompt_version, cohort.prompt_print, cohort.examples_print, cohort.model)
    latest: dict[Pair, _Last] = {}
    judged: set[Pair] = set()
    for row in found.tuples():
        donor, target, number, advice, rule, table, *print_, judged_at = row
        judged.add((donor, target))
        if tuple(print_) == now_cohort and judged_at >= since:
            latest[(donor, target)] = _Last(number, advice, rule, table)
    return latest, judged


async def _by_table_in_force(session: AsyncSession, latest: dict[Pair, _Last]) -> None:
    """Советы последних судов, поставленные прежней таблицей, — заново нынешней (шапка модуля), и
    перечитать их: дальше отбор идёт по пересчитанному. Туда же — советы нынешней таблицы, которые
    зависят от признака донора-каталога (`judge_rules.BY_DONOR`): донор набирает суды и становится
    каталогом, и спор его пары из «не понять» переходит в «не пишем» без смены таблицы."""
    outdated = sorted(
        last.number
        for last in latest.values()
        if last.table != RULES_VERSION or last.rule in BY_DONOR
    )
    changed = 0
    for chunk in chunked(outdated):
        changed += await relabel(session, ids=list(chunk))
    if not changed:
        return
    logger.info(
        "судья рекламодателей: советы ждущих пар пересчитаны нынешней таблицей",
        extra={"asked": len(outdated), "changed": changed, "rules_version": RULES_VERSION},
    )
    fresh: dict[int, _Last] = {}
    for chunk in chunked(outdated):
        read = await session.execute(
            select(Judgement.id, Judgement.advice, Judgement.rule, Judgement.rules_version).where(
                Judgement.id.in_(chunk)
            )
        )
        fresh.update({row[0]: _Last(*row) for row in read.tuples()})
    for pair, last in latest.items():
        latest[pair] = fresh.get(last.number, last)


def _advice(last: _Last | None) -> Advice | None:
    """Совет последнего суда — только нынешней таблицей; незнакомое слово — не совет."""
    if last is None or last.table != RULES_VERSION or last.advice not in Advice:
        return None
    return Advice(last.advice)


async def said(session: AsyncSession, *, now: datetime | None = None) -> list[Said]:
    """Очередь спорных и совет судьи каждой паре — по номеру строки. Решений не пишет; совет,
    поставленный прежней таблицей правил, пересчитывает (шапка модуля) — фиксирует вызывающий."""
    cohort = cohort_of(llm_cfg.ADVERTISER_JUDGE_MODEL)
    since = (now or datetime.now(UTC)) - timedelta(days=JUDGEMENT_TTL_DAYS)
    latest, judged = await _latest(session, cohort, since)
    await _by_table_in_force(session, latest)
    waiting = await session.execute(
        select(
            CandidateModel.id,
            CandidateModel.donor_host,
            CandidateModel.target_root,
            CandidateModel.points,
            func.coalesce(dr_settled(), false()),
        )
        .where(*_waiting())
        .order_by(CandidateModel.id)
    )
    rows: list[Said] = []
    for candidate_id, donor, target, points, dr_known in waiting.tuples():
        pair = Said(candidate_id, donor, target, points, bool(dr_known))
        last = latest.get((donor, target))
        advice = _advice(last)
        if last is not None and advice is not None:
            pair = replace(pair, advice=advice, rule=last.rule, judgement_id=last.number)
        elif (donor, target) in judged:
            pair = replace(pair, stale=True)
        rows.append(pair)
    return rows


def standing_of(rows: Sequence[Said]) -> Standing:
    """Итог очереди числами: каждая пара — ровно в одном числе."""
    counted: Counter[str] = Counter()
    for one in rows:
        if one.advice is None:
            counted["stale" if one.stale else "without"] += 1
        elif one.advice is Advice.WRITE and not one.dr_known:
            counted["waiting_dr"] += 1
        else:
            counted[one.advice.value] += 1
    return Standing(waiting=len(rows), **counted)


async def standing(session: AsyncSession) -> Standing:
    """Что судья сказал очереди — для строки над ней. Пересчёт советов таблицей — как у `said`."""
    return standing_of(await said(session))


def new_batch(band: Band, *, now: datetime | None = None) -> str:
    """Имя новой партии: полоса, начало и четыре случайных знака (`judge_mark.BATCH`)."""
    moment = now or datetime.now(UTC)
    return f"{band.value}-{moment:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def band_of(batch: str) -> Band:
    """Полоса партии — из её имени. Имя не той формы, с невозможной датой или из будущего — отказ:
    по времени в имени журнал ищет записи партии (`judge_batches.py`)."""
    refusal = f"партия «{batch}» названа не так, как называет их сервис"
    try:
        started = started_at(batch)
    except ValueError as exc:
        raise BatchBandError(refusal) from exc
    if started > datetime.now(UTC) + NAME_SLACK:
        raise BatchBandError(refusal)
    return Band(batch.split("-", 1)[0])


async def _locked(session: AsyncSession, ids: Sequence[int]) -> list[CandidateModel]:
    """Строки порции — под замок до фиксации, по номеру: два применения разом встают в очередь, а
    не в клинч. Перечитываются: решение человека, записанное после отбора, видно здесь."""
    if not ids:
        return []
    rows = await session.scalars(
        select(CandidateModel)
        .where(CandidateModel.id.in_(ids))
        .order_by(CandidateModel.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(rows.all())


async def apply(session: AsyncSession, band: Band, *, batch: str, limit: int = PORTION) -> Applied:
    """Применить порцию полосы решениями партии `batch` — каждое путём человека (`review.decide`).
    Без фиксации: её делает вызывающий вместе с записью журнала. Повтор продолжает с места."""
    if band_of(batch) is not band:
        raise BatchBandError(f"партия «{batch}» начата другой полосой — начните новую")
    members = [one for one in await said(session) if one.fits(band)]
    taken = [one.candidate_id for one in members[:limit]]
    result = Applied(batch, band, left=len(members) - len(taken))
    # Список держит строки в сессии до конца порции: `review.decide` берёт их отсюда, под замком.
    held = await _locked(session, taken)
    for candidate_id in taken:
        try:
            decided = await review.decide(
                session, candidate_id, confirmed=band.confirmed, by=marker_of(batch)
            )
        except review.AlreadyDecidedError as exc:
            # Человек успел раньше: его решение в силе, пара — числом в итоге порции.
            logger.info(
                "судья рекламодателей: пару решили, пока шла порция",
                extra={"batch": batch, "candidate_id": candidate_id, "why": str(exc)},
            )
            result.decided += 1
            continue
        except review.UnknownCandidateError as exc:
            logger.info(
                "судья рекламодателей: строку кандидата заменил обход",
                extra={"batch": batch, "candidate_id": candidate_id, "why": str(exc)},
            )
            result.gone += 1
            continue
        result.applied += 1
        result.removed += int(decided.withdrawn.removed)
        result.stopped += decided.withdrawn.stopped
        if decided.answered:
            result.answered.append(decided.candidate.target_root)
    await session.flush()
    logger.info(
        "судья рекламодателей: порция партии применена",
        extra={
            "batch": batch,
            "applied": result.applied,
            "decided_meanwhile": result.decided,
            "gone": result.gone,
            "left": result.left,
            "locked": len(held),
        },
    )
    return result
