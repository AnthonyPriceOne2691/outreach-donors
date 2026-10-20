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
поиск адреса сайтам, которые ждут повтора, сам по сроку или кнопкой «Повторить сейчас»
(`Again`), и лиды из найденного; или очистка ждущих лидов прогона. Стадия после него —
прежняя. В конце любого прохода очередь получает задачу на срок молчавших сайтов
(`segment_again.plan`).
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
from backend.features.sales import cleaning, segment_again
from backend.features.sales.clean_jobs import report_of
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_estimate import NO_SEGMENT
from backend.features.sales.segment_judge import SegmentClient
from backend.features.sales.segment_judging import JudgeTally, judge_run
from backend.features.sales.segment_leads import make_leads
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from backend.features.sales.segment_progress import advanced, enter, judge_waiting
from backend.features.sales.segment_queue import Retry, SegmentContactQueue
from backend.features.sales.segment_serp import bought, collect, provider_mode
from backend.features.sales.segment_waits import waiting_leads
from backend.features.sales.segment_window import SPLIT_TOKENS
from backend.features.sales.verifier import FIXTURE, EmailVerifier
from backend.features.serp.protocol import SerpProvider

logger = logging.getLogger(__name__)

#: Числа судьи, которые копятся от прохода к проходу: каждый судил своих.
_SUMMED = ("judged", "from_cache", "by_rule", "would_cut", "tokens", *SPLIT_TOKENS)
#: Числа поиска адреса, которые копятся; остальное (`pending`, заметки) — последнего прохода.
_CONTACTS_SUMMED = ("walked", "saved")
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
    #: Поиск адреса сайтам, которые ждут повтора, — сейчас, не дожидаясь срока: кнопка отчёта.
    ADDRESSES = "addresses"
    #: Очистка ждущих лидов прогона — сейчас: кнопка отчёта.
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


def _number(value: object) -> int:
    """Число из отчёта; не число (поле правили руками, его нет) — ноль, а не отказ."""
    return int(value) if isinstance(value, int | float) else 0


def _counts(*passes: object) -> dict[str, int]:
    """Счётчики проходов по ключам — суммой: ключ одного прохода — как есть."""
    found: dict[str, int] = {}
    for counted in passes:
        if isinstance(counted, Mapping):
            for key, value in counted.items():
                found[str(key)] = found.get(str(key), 0) + _number(value)
    return found


def _judge_report(before: dict[str, Any] | None, tally: JudgeTally) -> dict[str, Any]:
    """Итог судьи за все проходы: расход и счёт — суммой, остановка и сбой — последнего."""
    now = tally.as_dict()
    earlier = before or {}
    return {**now, **{key: int(earlier.get(key, 0)) + int(now[key]) for key in _SUMMED}}


def contacts_report(before: Mapping[str, Any] | None, now: Mapping[str, Any]) -> dict[str, Any]:
    """Итог поиска адреса за все проходы: пройдено, найдено и ступени (`counters` — в них
    запросы платного сервиса) — суммой; ждали и заметки — последнего прохода."""
    earlier = dict(before or {})
    return {
        **earlier,
        **now,
        **{key: _number(earlier.get(key)) + _number(now.get(key)) for key in _CONTACTS_SUMMED},
        "counters": _counts(earlier.get("counters"), now.get("counters")),
    }


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
    # Проход идёт: причины прежней остановки и конца прежнего прохода у него нет.
    run.error, run.finished_at = None, None
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
    await segment_again.plan(session, run.id, now=run.finished_at)
    logger.info("сбор продаж: прогон прошёл", extra={"run": run.id})
    return dict(run.report)


async def _clean_waiting(
    session: AsyncSession, run: SalesSegmentRunModel, verifier: EmailVerifier, started: datetime
) -> None:
    """Этап очистки ждущих лидов прогона — тем же проходом, что у новых (`cleaning.clean_leads`):
    кого возьмёт очистка, решает её условие (`segment_waits.waiting_leads`)."""
    leads = await waiting_leads(session, run.id, real=verifier.name != FIXTURE, now=started)
    await enter(session, run, Stage.LEADS, total=len(leads), pass_started=started)
    found = await cleaning.clean_leads(session, leads, verifier, now=started) if leads else None
    run.report = advanced(run, done=len(leads))
    if found is not None:
        made = {"created": 0, "cleaning": report_of(found)}
        run.report = {**run.report, "leads": leads_report(run.report.get("leads"), made)}


async def again(
    session: AsyncSession,
    run_id: int,
    verifier: EmailVerifier,
    what: Again,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Повтор без выдачи и судьи (`Again`): поиск адреса сайтам, которые ждут повтора, и лиды из
    найденного — или очистка ждущих лидов. Стадия после прохода — прежняя: готовый остаётся
    готовым, остановленный — остановленным со своей причиной."""
    run = await session.get(SalesSegmentRunModel, run_id)
    if run is None:
        raise UnknownRunError(f"прогона сбора №{run_id} нет")
    before, started = run.stage, datetime.now(UTC)
    run.finished_at = None
    if what is Again.CLEANING:
        await _clean_waiting(session, run, verifier, started)
    else:
        retry = Retry.DUE if what is Again.DUE else Retry.NOW
        await _addresses(session, run, started, retry)
        await _leads(session, run, verifier, started, since=started)
    finished = before in (Stage.CONTACTS, Stage.LEADS)  # прежний проход прошёл адреса
    run.stage = Stage.DONE if finished else before
    run.finished_at = now or datetime.now(UTC)
    await session.commit()
    await segment_again.plan(session, run.id, now=run.finished_at)
    logger.info("сбор продаж: повтор прошёл", extra={"run": run.id, "what": what.value})
    return dict(run.report)
