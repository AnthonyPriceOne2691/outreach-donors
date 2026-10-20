"""Прогон гипотезы с экрана: смета до запуска, запуск задачей, отчёт и решение человека.

`GET /sales/segment?hypothesis=N` — вкладка сбора: описание сегмента, режим судьи, рынки
выдачи, языки писем, границы формы, режим покупки выдачи по умолчанию и прогоны гипотезы. `PATCH /sales/hypotheses/{N}` —
описание сегмента и имя гипотезы (окно гипотезы): описание читает судья сегмента (правка — новый
вопрос модели, прежние вердикты по нему не годятся); правило — `features/sales/hypotheses.edit`.

`POST /sales/segment/estimate` — смета без записи: выдача, модель, Hunter строкой и отказ
словами; снятые человеком ключи (`excluded`, номера строк, как вписаны) не покупаются и в смету не
входят. `POST /sales/segment/runs` — запуск только по смете, которую видел человек: смета
считается заново на сервере, отказ — 409 словами до очереди задач; ключи, снятые, описание
сегмента и режимы — сверкой отпечатка (`digest` сметы): не совпал — 409 «смета устарела —
посчитайте заново» (до 15.10 запуск лишь пересчитывал смету и шёл по тому, что получилось, —
правка описания между сметой и запуском уходила в прогон, которого человек не видел); иначе
прогон заводится и встаёт задачей очереди долгих задач продаж
(`sales-bulk`, `worker-sales-bulk`; `sales/segment_jobs.py`): сбор идёт до получаса и не держит
разбор ответа лида. `POST /sales/segment/runs/{N}/resume` — продолжить прогон: после
потолка модели, сбоя или решений человека по «на проверку». Стадию продолжение не трогает
(«в очереди» значило бы для прохода «купить выдачу»): проход поставлен — пуст `finished_at`,
и экран, спросив очередь, видит прогон идущим. Задачу умершего воркера, которую rq держит
«идёт» до таймаута, продолжение убирает (`segment_watch.drop_dead`), а не отвечает 409.

`POST /sales/segment/runs/{N}/again` — «Повторить сейчас» из отчёта прогона: `segment_again.py`.

`GET /sales/segment/runs/{N}` — прогон и его отчёт; `POST /sales/segment/candidates/{N}/decision`
— решение человека по сайту «на проверку» или «судья отказал бы» (из неё лид — только так): то же
решение второй раз — 200 без второй записи журнала, противоположное по решённому — 409 словами;
в журнале — и полоса, из которой решили. `DELETE` того же адреса — отмена решения
человека (`features/sales/segment_undo.py`): сайт — в полосу и со словами судьи, автор и время
очищены; решение правила или судьи и сайт с заведённым лидом — 409 словами, повтор — 200 без второй
записи журнала. Ответ обоих — строкой списка сайтов прогона (`segment_sites.SiteView`).

**Ход и живость — при чтении.** Строка прогона несёт стадию и запись хода; «сделано» судьи
и адресов — живое, из кандидатов (`segment_progress.live`), а жив ли прогон — ответ очереди
задач (`segment_watch.py`): задачи нет или умер её воркер — «остановлен» словами и
«Продолжить», воркеров нет — «Задачу некому взять». Очередь спрашивается только о прогонах,
которые идут или поставлены продолжением; клиент Redis синхронный — в пуле потоков.

**Права.** Смотреть, считать смету и решать «на проверку» — `sales`. Запуск и продолжение —
`sales` и `run`: выдача, модель и платный поиск адресов — траты, как у прогона доноров и
очистки лидов.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.exceptions import RedisError
from rq import Queue
from rq.job import Job
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.letters.once import enqueue_once
from backend.api.sales.segment_schemas import (
    DecisionBody,
    EstimateView,
    RunAsk,
    RunDetail,
    RunQueued,
    RunRow,
    SegmentAsk,
    SegmentBody,
    SegmentCard,
    SegmentHome,
    default_serp_mode,
)
from backend.api.sales.segment_sites import SiteView, refuse_while_rejudging
from backend.config import sales as sales_cfg
from backend.features.access.repository import AccessRepository
from backend.features.core import domain
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import (
    chain_text,
    segment_progress,
    segment_report,
    segment_undo,
    segment_watch,
)
from backend.features.sales.hypotheses import HypothesisError, HypothesisRefusedError, edit
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_estimate import Ask, Estimate, estimate
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_models import Lane, SalesSegmentRunModel, Stage
from backend.features.sales.segment_sites import SiteRow, site_row
from backend.features.serp import markets
from backend.shared.queue import sales_bulk_queue, with_retries

logger = logging.getLogger(__name__)

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`).
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
_runner = Depends(needs(Permission.RUN))

RUN_GOING = (
    "Этот прогон уже идёт — дождитесь его итога: второй проход судил бы и искал адреса "
    "тем же сайтам и заплатил бы дважды"
)
UNKNOWN_RUN = "прогона сбора №{run_id} нет — обновите список прогонов"
#: Смета на запуске — не та, что видел человек: ключи, снятые, описание сегмента или режим другие.
STALE = "Прогон не запущен: смета устарела — посчитайте заново"
#: Запрос без отпечатка сметы: так шлёт только страница старше сверки — её надо обновить.
UNSEEN = (
    "Прогон не запущен: запуск — только по смете, которую вы видели, а её отпечатка в запросе "
    "нет — обновите страницу и посчитайте смету"
)


def _ask(body: SegmentAsk) -> Ask:
    return Ask(
        hypothesis_id=body.hypothesis_id,
        keywords=body.keywords,
        country=body.country,
        language=body.language,
        depth_pages=body.depth_pages,
        serp_mode=body.serp_mode,
        excluded=tuple(body.excluded),
    )


def _seen(body: RunAsk, found: Estimate) -> None:
    """Запуск — только по смете, которую видел человек: отказ сметы — её словами, другой
    отпечаток — «смета устарела». Отказ — первым: он называет, что не так, точнее."""
    if found.refusal is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Прогон не запущен: {found.refusal}")
    if body.digest is None:
        raise HTTPException(status.HTTP_409_CONFLICT, UNSEEN)
    if body.digest != found.digest:
        raise HTTPException(status.HTTP_409_CONFLICT, STALE)


async def _hypothesis(session: AsyncSession, hypothesis_id: int) -> SalesHypothesisModel:
    found = await session.get(SalesHypothesisModel, hypothesis_id)
    if found is None:
        raise UnknownHypothesisError(f"гипотезы №{hypothesis_id} нет — обновите список гипотез")
    return found


@router.get("/segment", response_model=SegmentHome, summary="Сбор базы гипотезы: форма и прогоны")
async def read_segment(
    hypothesis: int = Query(ge=1, description="номер гипотезы"),
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SegmentHome:
    found = await _hypothesis(session, hypothesis)
    runs = await segment_report.runs_of(session, hypothesis)
    watched = await _watched(runs)
    return SegmentHome(
        hypothesis_id=hypothesis,
        segment=found.description,
        mode=sales_cfg.SEGMENT_JUDGE_MODE.value,
        countries=sorted(markets.SERP_LANGUAGE),
        languages=list(chain_text.LANGUAGES),
        serp_mode=default_serp_mode(),
        runs=[
            RunRow.of(
                run,
                progress=await segment_progress.live(session, run) if run.id in watched else None,
                again=await _again_live(session, run) if run.id in watched else None,
                watch=watched.get(run.id),
            )
            for run in runs
        ],
    )


async def _again_live(session: AsyncSession, run: SalesSegmentRunModel) -> dict[str, object] | None:
    """Живой ход повтора идущего прогона (`segment_progress.AGAIN_KEY`)."""
    return await segment_progress.live(session, run, key=segment_progress.AGAIN_KEY)


async def _watched(runs: Sequence[SalesSegmentRunModel]) -> dict[int, segment_watch.Watch]:
    """Ответ очереди о прогонах, которые идут или поставлены продолжением. Клиент Redis
    синхронный — в пуле потоков: зависший Redis не держит процесс API (аудит 10.10.2026)."""
    asked = [(run.id, run.stage) for run in runs if segment_watch.unfinished(run)]
    if not asked:
        return {}
    return await asyncio.to_thread(segment_watch.watch_runs, asked)


@router.patch(
    "/hypotheses/{hypothesis_id}",
    response_model=SegmentCard,
    summary="Правка гипотезы: имя и описание сегмента",
)
async def write_segment(
    hypothesis_id: int,
    body: SegmentBody,
    author: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SegmentCard:
    """Описание сегмента («Сбор базы» и окно гипотезы) и имя (окно гипотезы) — одним правилом
    (`hypotheses.edit`): пустое описание — описания нет, то же самое — не правка; имя — правилом
    заведения, и рассылка гипотезы переименовывается с ним. Правка — в журнал той же транзакцией.
    Негодное имя или пустая правка — 422 словами; имя занято гипотезой или рассылкой, строку держит
    другая операция — 409 словами."""
    try:
        found = await edit(
            session,
            hypothesis_id,
            name=body.name,
            description=body.description,
            author_id=author.id,
        )
    except HypothesisRefusedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except HypothesisError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await session.commit()
    return SegmentCard(hypothesis_id=hypothesis_id, name=found.name, description=found.description)


@router.post("/segment/estimate", response_model=EstimateView, summary="Смета прогона: без записи")
async def read_estimate(
    body: SegmentAsk, _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> EstimateView:
    return EstimateView.of(await estimate(session, _ask(body)))


@router.post(
    "/segment/runs",
    response_model=RunQueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить прогон гипотезы",
)
async def start_run(
    body: RunAsk,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> RunQueued:
    """Смета — заново, на сервере: выше потолка или негодный запрос — 409 словами, смета не та,
    что видел человек, — 409 «смета устарела», до очереди задач. Прогон и запись журнала — одна
    транзакция с постановкой."""
    found = await estimate(session, _ask(body))
    _seen(body, found)
    run = SalesSegmentRunModel(
        hypothesis_id=body.hypothesis_id,
        keywords=found.keywords,
        country=found.country,
        language=found.language,
        depth_pages=found.depth_pages,
        mode=found.mode,
        estimate=found.as_dict(),
        report={},
        stage=Stage.QUEUED,
        created_by=author.email,
    )
    session.add(run)
    # Строка прогона — в базе до постановки: задача читает её своей сессией, и взятая воркером
    # раньше коммита не нашла бы прогона. Очередь не ответила — строка уходит: прогона без
    # задачи, который «стоит в очереди», нет.
    await session.commit()
    try:
        job = await asyncio.to_thread(
            enqueue_once,
            sales_bulk_queue(),
            segment_job_id(run.id),
            SEGMENT_JOB,
            run.id,
            **with_retries(),
        )
    except RedisError:
        await session.delete(run)
        await session.commit()
        raise
    if job is None:  # номер свежего прогона занят быть не может — но молча не теряем
        raise HTTPException(status.HTTP_409_CONFLICT, RUN_GOING)
    run.job_id = str(job.id)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details=_started(found),
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона (`Stage` ниже)
    )
    await session.commit()
    return RunQueued(run_id=run.id, job_id=str(job.id))


def _started(found: Estimate) -> dict[str, object]:
    """Запуск в журнале действий: гипотеза, ключи, рынок и смета словами; снятые человеком
    ключи — как вписаны в смету, когда они есть."""
    details: dict[str, object] = {
        "действие": "сбор базы продаж",
        "гипотеза": found.hypothesis,
        "ключей": len(found.keywords),
        "рынок": found.country,
        "смета": " ".join(found.lines),
    }
    if found.excluded:
        details["снято ключей"] = ", ".join(f"«{phrase}»" for phrase in found.excluded)
    return details


async def _enqueue(jobs: Queue, run_id: int, *more: object) -> Job | None:
    """Проход прогона — в очередь долгих задач продаж; `None` — номер занят (`enqueue_once`).
    `more` — довод повтора (`segment_run.Again`)."""
    return await asyncio.to_thread(
        enqueue_once, jobs, segment_job_id(run_id), SEGMENT_JOB, run_id, *more, **with_retries()
    )


def bulk_queue() -> Queue:
    """Очередь долгих задач продаж маршрутов прогона — одна у постановки прохода и у вопроса о
    пересуде, здесь и у «Повторить сейчас» (`segment_again.py`)."""
    return sales_bulk_queue()


async def queued_pass(run_id: int, *more: object) -> Job | None:
    """Поставить проход продолжения. Номер занят — «идёт» или задача умершего воркера, которую
    rq держит «идёт» до таймаута: такая убирается (`segment_watch.drop_dead`), и проход
    ставится снова. `None` — прогон и правда идёт."""
    jobs = bulk_queue()
    job = await _enqueue(jobs, run_id, *more)
    if job is None and await asyncio.to_thread(segment_watch.drop_dead, jobs, run_id):
        job = await _enqueue(jobs, run_id, *more)
    return job


@router.post(
    "/segment/runs/{run_id}/resume",
    response_model=RunQueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Продолжить прогон гипотезы",
)
async def resume_run(
    run_id: int,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> RunQueued:
    """Тот же прогон с места, где он стоит: выдачу второй раз не покупает. Пока идёт пересуд
    прогона — отказ словами (`segment_sites.refuse_while_rejudging`)."""
    run = await session.get(SalesSegmentRunModel, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, UNKNOWN_RUN.format(run_id=run_id))
    await refuse_while_rejudging(run.id, sales_bulk_queue())
    job = await queued_pass(run.id)
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, RUN_GOING)
    # Проход поставлен: прогон идёт, пока проход не кончится. Стадия — прежняя: «в очереди»
    # значило бы для прохода «купить выдачу», а куплена ли она — отметка в отчёте.
    run.job_id, run.finished_at = str(job.id), None
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details={"действие": "продолжение сбора базы продаж", "прогон": run.id},
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона (`Stage` ниже)
    )
    await session.commit()
    return RunQueued(run_id=run.id, job_id=str(job.id))


@router.get("/segment/runs/{run_id}", response_model=RunDetail, summary="Прогон гипотезы и отчёт")
async def read_run(
    run_id: int, _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> RunDetail:
    view = await segment_report.run_view(session, run_id)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, UNKNOWN_RUN.format(run_id=run_id))
    watched = await _watched([view.run])
    return RunDetail.of(view, watch=watched.get(run_id))


@router.post(
    "/segment/candidates/{candidate_id}/decision",
    response_model=SiteView,
    summary="Решение человека по сайту «на проверку» или «судья отказал бы»",
)
async def decide(
    candidate_id: int,
    body: DecisionBody,
    author: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SiteView:
    """То же решение второй раз (двойной клик) — 200 без второй записи журнала: сайт уже там,
    куда его просят. Противоположное по решённому — 409 словами."""
    try:
        decision = await segment_report.decide(
            session, candidate_id, accept=body.accept, author=author.email
        )
    except segment_report.UnknownCandidateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except segment_report.DecisionRefusedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    row = await site_row(session, decision.candidate)
    if not decision.repeated:
        await _journal_decision(session, author.id, row, decision.was)
    await session.commit()
    return SiteView.of(row)


@router.delete(
    "/segment/candidates/{candidate_id}/decision",
    response_model=SiteView,
    summary="Отменить решение человека по сайту сбора",
)
async def undo_decision(
    candidate_id: int,
    author: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SiteView:
    """Сайт — в полосу и со словами судьи (`segment_undo`). Решение правила или судьи, сайт с
    заведённым лидом, строка, занятая заведением лидов дольше предела, — 409 словами; повтор
    (двойной клик) — 200 без второй записи журнала."""
    try:
        undone = await segment_undo.undo(session, candidate_id)
    except segment_undo.UndoRefusedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if undone is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"сайта сбора №{candidate_id} нет — обновите отчёт прогона"
        )
    row = await site_row(session, undone.candidate)
    if undone.was is not None:
        await _journal_undo(session, author.id, row, undone.was)
    await session.commit()
    return SiteView.of(row)


def _code(lane: str) -> str:
    """Решение человека кодом записи журнала — одним у решения и у его отмены."""
    return "accepted" if lane == Lane.ACCEPT else "rejected"


async def _journal_decision(
    session: AsyncSession, author_id: int, row: SiteRow, was: str | None
) -> None:
    """Решение — в журнал, как решение по сайту на «Отборе»: оно меняет, кому уйдёт письмо, и
    по нему считают, как ошибается судья сегмента. Этап — в записи (без права «Продажи» её не
    видно); «действие» отличает её от решения по донору; «было» — полоса, из которой решили:
    «Принять» из «судья отказал бы» — единственный путь к лиду у сайта, которому судья отказал."""
    what = {"решение": _code(row.candidate.lane)}
    if was is not None:
        what["было"] = segment_report.LANE_WORDS.get(was, was)
    await _journal(session, author_id, row, "решение по сайту сбора базы", what)


async def _journal_undo(session: AsyncSession, author_id: int, row: SiteRow, was: str) -> None:
    """Отмена — тем же действием журнала, что решение, и с той же целью: рядом с ним. Что
    отменено — кодом, как «решение» у решения, и куда вернулся сайт — полосой словами."""
    lane = row.candidate.lane
    what = {"отменено": _code(was), "стало": segment_report.LANE_WORDS.get(lane, lane)}
    await _journal(session, author_id, row, "отмена решения по сайту сбора базы", what)


async def _journal(
    session: AsyncSession, author_id: int, row: SiteRow, action: str, what: Mapping[str, str]
) -> None:
    verdict = row.candidate.verdict or ""
    await AccessRepository(session).record(
        AuditAction.SITE_REVIEWED,
        author_id=author_id,
        target=f"sales_segment_candidate:{row.candidate.id}",
        details={
            "действие": action,
            "домен": row.host,
            **what,
            "судья сегмента": segment_report.LANE_WORDS.get(verdict, verdict),
            "прогон": row.candidate.run_id,
        },
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона (`Stage` выше)
    )
