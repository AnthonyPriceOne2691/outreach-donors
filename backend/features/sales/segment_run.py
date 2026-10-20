"""Прогон гипотезы: ключи → выдача → судья сегмента → адрес лестницей → лиды → очистка.

Этапы идут по порядку и каждый коммитит своё — прогон можно продолжить с места, где он
встал: упавшая задача повторяется очередью, остановленный потолком модели — продолжается
кнопкой, решения человека по «на проверку» — тем же продолжением.

**Стадия — при входе в этап**, своим коммитом до его работы (`segment_progress.enter`):
человек видит «покупаем выдачу», «судья: X из Y», «адреса: X из Y», «лиды и очистка», а не
«в очереди» шесть минут выдачи (тест прогона 14.10). Ход — в `report.progress`.

1. **Выдача** (`segment_serp.collect`) — один раз: куплена — её итог в отчёте
   (`segment_serp.bought`), и ни повтор задачи, ни продолжение её не покупают.
2. **Судья** (`segment_judging.judge_run`) — кандидаты, ждущие судью; потолок модели
   выбран — прогон останавливается словами, неосуждённые ждут продолжения.
3. **Адрес** — общий вход лестницы `search_contacts(queue=…)` со своей очередью
   кандидатов (`segment_queue.py`): принятым без живого адреса на домене.
4. **Лиды и очистка** (`segment_leads.make_leads`) — принятым с адресом; очистка — та же,
   что у загрузки базы.

**Числа проходов складываются** (`report.judge`, `report.contacts`, `report.leads`): расход
прогона — токены судьи, запросы Hunter, проверки адресов — за все проходы, а не за
последний (тест прогона 14.10: «Найти адреса принятым» затирало счёт первого прохода).
Ключи прежние — их читает панель расхода. Сколько сайтов в какой полосе, отчёт читает из
кандидатов (`segment_report.py`).

**Повтор — своим проходом** (`again`, разбор прогона сбора 20.10): без выдачи и судьи —
поиск адреса сайтам, чей срок повтора настал, сам по сроку или кнопкой «Повторить сейчас»
(`Again`), и лиды из найденного; или очистка ждущих лидов прогона. **Стадию, причину остановки и
ход прохода повтор не трогает** (ревью 20.10): его ход — своим ключом
(`segment_progress.enter_again`), и повтор задачи после сбоя не превращает остановленный прогон в
готовый. Повторять нечего — повтор кончается сразу, без записи хода. Платный поиск повтора — в
пределах сметы (`segment_queue.paid_left`). Срок следующего повтора ставит задача прохода
(`segment_jobs.run_once`) — после любого исхода, и после сбоя тоже.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.contacts.search import search_contacts
from backend.features.sales import cleaning, segment_paid
from backend.features.sales.clean_jobs import report_of
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_counts import contacts_report
from backend.features.sales.segment_counts import counts as _counts
from backend.features.sales.segment_counts import number as _number
from backend.features.sales.segment_estimate import NO_SEGMENT
from backend.features.sales.segment_judge import SegmentClient
from backend.features.sales.segment_judging import JudgeTally, judge_run
from backend.features.sales.segment_leads import make_leads
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from backend.features.sales.segment_progress import (
    AGAIN_KEY,
    advanced,
    enter,
    enter_again,
    judge_waiting,
    without_again,
)
from backend.features.sales.segment_queue import Retry, SegmentContactQueue, paid_left
from backend.features.sales.segment_serp import bought, collect, provider_mode
from backend.features.sales.segment_waits import waiting_leads
from backend.features.sales.segment_window import SPLIT_TOKENS
from backend.features.sales.verifier import FIXTURE, EmailVerifier
from backend.features.serp.protocol import SerpProvider

logger = logging.getLogger(__name__)

#: Числа судьи, которые копятся от прохода к проходу: каждый судил своих.
_SUMMED = ("judged", "from_cache", "by_rule", "would_cut", "tokens", *SPLIT_TOKENS)
#: Числа очистки лидов, которые копятся: каждый проход чистит своих новых лидов.
_CLEANING_SUMMED = (
    "checked",
    "ready",
    "unconfirmed",
    "unverified",
    "mx_unknown",
    "verified",
    "paid_units",
)


class UnknownRunError(LookupError):
    """Прогона нет: удалён или номер чужой. Повтор задачи это не исправит."""

    permanent = True


class Again(StrEnum):
    """Что повторяет проход без выдачи и судьи (`again`)."""

    #: Поиск адреса сайтам, чей срок повтора настал, — сам (`segment_jobs.due_again`).
    DUE = "due"
    #: Поиск адреса сайтам, чей срок настал, и «не спросили» при платной ступени — кнопкой.
    ADDRESSES = "addresses"
    #: Очистка ждущих лидов прогона — кнопкой отчёта (`segment_waits.waiting_leads`).
    CLEANING = "cleaning"


@dataclass(frozen=True, slots=True)
class RunDeps:
    """Исполнители прогона: выдача, модель, главные сайтов и проверка адресов."""

    serp: SerpProvider
    #: Клиент модели судьи; пусто — судья выключен.
    judge: SegmentClient | None
    #: Клиент для главных сайтов; пусто — судья судит по выдаче.
    home: httpx.AsyncClient | None
    verifier: EmailVerifier


def _judge_report(before: dict[str, Any] | None, tally: JudgeTally) -> dict[str, Any]:
    """Итог судьи за все проходы: расход и счёт — суммой, остановка и сбой — последнего."""
    now = tally.as_dict()
    earlier = before or {}
    return {**now, **{key: int(earlier.get(key, 0)) + int(now[key]) for key in _SUMMED}}


def _cleaning(before: object, now: object) -> dict[str, Any]:
    earlier = dict(before) if isinstance(before, Mapping) else {}
    latest = dict(now) if isinstance(now, Mapping) else {}
    return {
        **earlier,
        **latest,
        **{key: _number(earlier.get(key)) + _number(latest.get(key)) for key in _CLEANING_SUMMED},
        "rejected": _counts(earlier.get("rejected"), latest.get("rejected")),
    }


def leads_report(before: Mapping[str, Any] | None, now: Mapping[str, Any]) -> dict[str, Any]:
    """Итог лидов за все проходы: заведено и счёт очистки (`cleaning.paid_units` — проверки
    адресов) — суммой; остановка проверки и срок перепроверки — последнего прохода."""
    earlier = dict(before or {})
    found = {
        **earlier,
        **now,
        "created": _number(earlier.get("created")) + _number(now.get("created")),
    }
    if "cleaning" in earlier or "cleaning" in now:
        found["cleaning"] = _cleaning(earlier.get("cleaning"), now.get("cleaning"))
    return found


async def _stop(
    session: AsyncSession, run: SalesSegmentRunModel, words: str, now: datetime | None
) -> dict[str, Any]:
    """Прогон встал словами: стадия, причина и конец прохода — одной записью."""
    run.stage, run.error, run.finished_at = Stage.STOPPED, words, now or datetime.now(UTC)
    await session.commit()
    logger.warning("сбор продаж: прогон остановлен", extra={"run": run.id, "error": words})
    return dict(run.report)


async def _contacts(
    session: AsyncSession, run: SalesSegmentRunModel, queue: SegmentContactQueue, waiting: int
) -> dict[str, Any]:
    """Поиск адреса принятым — общим входом лестницы, своей очередью кандидатов.

    Платная ступень — только когда она включена у сбора (`SALES_SEGMENT_PAID_CONTACTS`) и
    была в смете, которую человек подтвердил запуском: ключ Hunter общий с донорами, и его
    включение для доноров не должно молча включать платное сбору."""
    if waiting == 0:
        return {"pending": 0, "walked": 0, "saved": 0}
    paid = sales_cfg.SEGMENT_PAID_CONTACTS and int(run.estimate.get("hunter_searches", 0)) > 0
    found = await search_contacts(session, limit=waiting, queue=queue, no_paid=not paid)
    return found.as_dict()


async def _serp(
    session: AsyncSession, run: SalesSegmentRunModel, deps: RunDeps, started: datetime
) -> None:
    """Этап выдачи: «покупаем выдачу» — до покупки, «готово X из Y ключей» — по ходу
    (`segment_serp.SerpTrail`), итог и «куплена» — одним коммитом."""
    await enter(
        session,
        run,
        Stage.SERP,
        total=len(run.keywords),
        pass_started=started,
        mode=provider_mode(deps.serp),
    )
    found = await collect(session, run, deps.serp)
    run.report = advanced(run, done=_number(found["keywords"]), total=_number(found["keywords"]))
    await session.commit()


async def _judge(
    session: AsyncSession,
    run: SalesSegmentRunModel,
    deps: RunDeps,
    *,
    segment: str,
    started: datetime,
    now: datetime | None,
) -> JudgeTally:
    """Этап судьи: «судья: X из Y» — Y ждали судью на входе, X — сколько из них уже нет."""
    total = await judge_waiting(session, run.id)
    await enter(session, run, Stage.JUDGE, total=total, pass_started=started)
    tally = await judge_run(
        session,
        run_id=run.id,
        segment=segment,
        mode=JudgeMode(run.mode),
        client=deps.judge,
        home=deps.home,
        now=now,
    )
    left = await judge_waiting(session, run.id)
    run.report = {
        **advanced(run, done=max(0, total - left)),
        "judge": _judge_report(run.report.get("judge"), tally),
    }
    return tally


async def _addresses(
    session: AsyncSession, run: SalesSegmentRunModel, started: datetime, retry: Retry | None = None
) -> None:
    """Этап адресов: «адреса: X из Y» — Y принятых ждали поиска на входе; `retry` — только
    сайтам, которые ждут повтора (`segment_queue.Retry`)."""
    queue = SegmentContactQueue(session, run.id, retry=retry)
    waiting = await queue.pending_count()
    await enter(session, run, Stage.CONTACTS, total=waiting, pass_started=started)
    found = await _contacts(session, run, queue, waiting)
    run.report = {
        **advanced(run, done=_number(found.get("walked"))),
        "contacts": contacts_report(run.report.get("contacts"), found),
    }


async def _leads(
    session: AsyncSession,
    run: SalesSegmentRunModel,
    verifier: EmailVerifier,
    started: datetime,
    *,
    since: datetime | None = None,
) -> None:
    """Этап лидов: заведение и очистка; сколько лидов выйдет, заранее не известно. `since` —
    лиды только из найденного с этого момента (`segment_leads.make_leads`)."""
    await enter(session, run, Stage.LEADS, total=None, pass_started=started)
    made = await make_leads(session, run, verifier, since=since)
    created = _number(made.get("created"))
    run.report = {
        **advanced(run, done=created, total=created),
        "leads": leads_report(run.report.get("leads"), made),
    }


async def execute(
    session: AsyncSession, run_id: int, deps: RunDeps, *, now: datetime | None = None
) -> dict[str, Any]:
    """Провести прогон с места, где он стоит. Возвращает отчёт прогона."""
    run = await session.get(SalesSegmentRunModel, run_id)
    if run is None:
        raise UnknownRunError(f"прогона сбора №{run_id} нет")
    hypothesis = await session.get(SalesHypothesisModel, run.hypothesis_id)
    segment = (hypothesis.description or "").strip() if hypothesis is not None else ""
    started = datetime.now(UTC)
    # Проход идёт: причины прежней остановки, конца прежнего прохода и хода повтора у него нет.
    run.error, run.finished_at = None, None
    run.report = without_again(run.report)
    await segment_paid.settle(session, run)
    if not bought(run):
        await _serp(session, run, deps, started)
    if not segment and JudgeMode(run.mode) is not JudgeMode.OFF:
        # Описание стёрли после запуска: судить не с чем, а угадывать сегмент модель не должна.
        return await _stop(session, run, f"прогон остановлен: {NO_SEGMENT}", now)
    tally = await _judge(session, run, deps, segment=segment, started=started, now=now)
    if tally.stopped is not None:
        return await _stop(session, run, f"судья остановлен: {tally.stopped}", now)
    await _addresses(session, run, started)
    await _leads(session, run, deps.verifier, started)
    run.stage, run.finished_at = Stage.DONE, now or datetime.now(UTC)
    await session.commit()
    logger.info("сбор продаж: прогон прошёл", extra={"run": run.id})
    return dict(run.report)


def _again_started(run: SalesSegmentRunModel, what: Again) -> datetime | None:
    """Начало повтора, который задача повторяет после сбоя: лиды — из найденного с него, а не с
    повтора задачи (адреса первой попытки уже записаны). Записи нет — повтор новый."""
    stored = run.report.get(AGAIN_KEY)
    if not isinstance(stored, Mapping) or stored.get("what") != what.value:
        return None
    raw = stored.get("pass_started_at")
    try:
        return datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        # Запись правили руками: повтор начнётся заново — лиды из найденного первой попыткой
        # заведёт следующий проход прогона.
        logger.warning("сбор продаж: начало повтора не разобрано", extra={"value": raw})
        return None


async def _retry_walk(session: AsyncSession, run: SalesSegmentRunModel, retry: Retry) -> int:
    """Поиск адреса повтора: сначала с платной ступенью в пределах сметы (`segment_paid.walk`),
    затем — если платная часть не задержала остальных — сайтам, чей срок настал, без неё; «не
    спросили» без платной ступени не берутся (`segment_queue.Retry`). Итог — в отчёт прогона по
    кускам. Возвращает, скольких сайтов прошёл поиск."""
    walked, held = await segment_paid.walk(session, run, retry)
    queue = SegmentContactQueue(session, run.id, retry=retry)
    rest = 0 if held else await queue.pending_count()
    if rest > 0:
        found = await search_contacts(session, limit=rest, queue=queue, no_paid=True)
        walked += await segment_paid.merged(session, run, found.as_dict())
    return walked


async def _retry_addresses(
    session: AsyncSession,
    run: SalesSegmentRunModel,
    verifier: EmailVerifier,
    what: Again,
    started: datetime,
    *,
    resumed: bool = False,
) -> bool:
    """Повтор поиска адреса — сайтам, чей срок настал, и лиды только из найденного с начала
    повтора. `resumed` — повтор задачи после сбоя: лиды из найденного первой попыткой заводятся,
    даже если искать уже некому. `False` — делать нечего: ни хода, ни записи."""
    retry = Retry.DUE if what is Again.DUE else Retry.NOW
    paid = paid_left(run.report, run.estimate) > 0
    waiting = await SegmentContactQueue(session, run.id, retry=retry, paid=paid).pending_count()
    if waiting == 0 and not resumed:
        return False
    if waiting > 0:
        await enter_again(
            session, run, what.value, Stage.CONTACTS, total=waiting, pass_started=started
        )
        walked = await _retry_walk(session, run, retry)
        run.report = advanced(run, done=walked, key=AGAIN_KEY)
    await enter_again(session, run, what.value, Stage.LEADS, total=None, pass_started=started)
    made = await make_leads(session, run, verifier, since=started)
    created = _number(made.get("created"))
    run.report = {
        **advanced(run, done=created, total=created, key=AGAIN_KEY),
        "leads": leads_report(run.report.get("leads"), made),
    }
    return True


async def _clean_waiting(
    session: AsyncSession, run: SalesSegmentRunModel, verifier: EmailVerifier, started: datetime
) -> bool:
    """Очистка ждущих лидов прогона — тем же проходом, что у новых (`cleaning.clean_leads`); кого
    возьмёт, решает условие кнопки (`segment_waits.waiting_leads`). Лиды — под блокировкой строк:
    задача перепроверки (`verify_again_jobs`) их в это время не возьмёт. `False` — чистить
    некого."""
    real, moment = verifier.name != FIXTURE, datetime.now(UTC)
    count = len(await waiting_leads(session, run.id, real=real, now=moment))
    if count == 0:
        return False
    await enter_again(
        session, run, Again.CLEANING.value, Stage.LEADS, total=count, pass_started=started
    )
    leads = await waiting_leads(session, run.id, real=real, now=moment, lock=True)
    found = await cleaning.clean_leads(session, leads, verifier, now=moment) if leads else None
    run.report = advanced(run, done=len(leads), total=len(leads), key=AGAIN_KEY)
    if found is not None:
        made = {"created": 0, "cleaning": report_of(found)}
        run.report = {**run.report, "leads": leads_report(run.report.get("leads"), made)}
    return True


async def again(
    session: AsyncSession,
    run_id: int,
    verifier: EmailVerifier,
    what: Again,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Повтор без выдачи и судьи (`Again`): поиск адреса сайтам, чей срок настал, и лиды из
    найденного — или очистка ждущих лидов. Стадия, причина остановки и ход прохода — прежние;
    ход повтора — своим ключом и снимается в конце. Повторять нечего — конец без хода."""
    run = await session.get(SalesSegmentRunModel, run_id)
    if run is None:
        raise UnknownRunError(f"прогона сбора №{run_id} нет")
    await segment_paid.settle(session, run)
    resumed = _again_started(run, what)
    started = resumed or datetime.now(UTC)
    if what is Again.CLEANING:
        worked = await _clean_waiting(session, run, verifier, started)
    else:
        worked = await _retry_addresses(
            session, run, verifier, what, started, resumed=resumed is not None
        )
    run.report = without_again(run.report)
    if worked or run.finished_at is None:
        run.finished_at = now or datetime.now(UTC)
    await session.commit()
    logger.info(
        "сбор продаж: повтор прошёл", extra={"run": run.id, "what": what.value, "worked": worked}
    )
    return dict(run.report)
