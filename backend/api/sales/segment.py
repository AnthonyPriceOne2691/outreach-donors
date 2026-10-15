"""Прогон гипотезы с экрана: смета до запуска, запуск задачей, отчёт и решение человека.

`GET /sales/segment?hypothesis=N` — вкладка сбора: описание сегмента, режим судьи, рынки
выдачи, языки писем, границы формы, режим покупки выдачи по умолчанию и прогоны гипотезы. `PATCH /sales/hypotheses/{N}` —
описание сегмента: его читает судья сегмента (правка — новый вопрос модели, прежние вердикты
по нему не годятся); правка — в журнале действий: кто, было и стало.

`POST /sales/segment/estimate` — смета без записи: выдача, модель, Hunter строкой и отказ
словами. `POST /sales/segment/runs` — запуск: смета считается заново на сервере, отказ — 409
словами до очереди задач; иначе прогон заводится и встаёт задачей очереди долгих задач продаж
(`sales-bulk`, `worker-sales-bulk`; `sales/segment_jobs.py`): сбор идёт до получаса и не держит
разбор ответа лида. `POST /sales/segment/runs/{N}/resume` — продолжить прогон: после
потолка модели, сбоя или решений человека по «на проверку». Стадию продолжение не трогает
(«в очереди» значило бы для прохода «купить выдачу»): проход поставлен — пуст `finished_at`,
и экран, спросив очередь, видит прогон идущим. Задачу умершего воркера, которую rq держит
«идёт» до таймаута, продолжение убирает (`segment_watch.drop_dead`), а не отвечает 409.

`GET /sales/segment/runs/{N}` — прогон и его отчёт; `POST /sales/segment/candidates/{N}/decision`
— решение человека по сайту «на проверку»: то же решение второй раз — 200 без второй записи
журнала, противоположное по решённому — 409 словами.

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
from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.exceptions import RedisError
from rq import Queue
from rq.job import Job
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.letters.once import enqueue_once
from backend.api.sales.segment_schemas import (
    CandidateRow,
    DecisionBody,
    EstimateView,
    RunDetail,
    RunQueued,
    RunRow,
    SegmentAsk,
    SegmentBody,
    SegmentCard,
    SegmentHome,
    default_serp_mode,
)
from backend.config import sales as sales_cfg
from backend.features.access.repository import AccessRepository
from backend.features.core import domain
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.sales import chain_text, segment_progress, segment_report, segment_watch
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_estimate import Ask, estimate
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    Stage,
)
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


def _ask(body: SegmentAsk) -> Ask:
    return Ask(
        hypothesis_id=body.hypothesis_id,
        keywords=body.keywords,
        country=body.country,
        language=body.language,
        depth_pages=body.depth_pages,
        serp_mode=body.serp_mode,
    )


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
                watch=watched.get(run.id),
            )
            for run in runs
        ],
    )


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
    summary="Описание сегмента гипотезы",
)
async def write_segment(
    hypothesis_id: int,
    body: SegmentBody,
    author: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SegmentCard:
    """Описание сегмента — данными в базе: репозиторий публичный. Пусто — описания нет.
    Правка — в журнал той же транзакцией; то же описание — не правка, записи нет."""
    found = await _hypothesis(session, hypothesis_id)
    was = found.description
    found.description = body.description.strip() or None
    if found.description != was:
        await _journal_segment(session, author.id, found, was)
    await session.commit()
    return SegmentCard(hypothesis_id=hypothesis_id, description=found.description)


def _segment_change(was: str | None, now: str | None) -> str:
    """Что стало с описанием — словами «действия». Пустое «было» или «стало» экран журнала
    строкой подробностей не выводит: «задано» и «снято» говорят это сами."""
    if was is None:
        return "описание сегмента задано"
    if now is None:
        return "описание сегмента снято"
    return "описание сегмента поправлено"


async def _journal_segment(
    session: AsyncSession, author_id: int, hypothesis: SalesHypothesisModel, was: str | None
) -> None:
    """Правка описания — новый вопрос судье сегмента: по нему решается, каким сайтам продажи
    напишут (решение владельца 13.10). В журнале — кто, гипотеза, было и стало: «было» и «стало» —
    своими строками, как у правки лида (`lead_edit`), — экран журнала режет каждую строку
    подробностей на трёх, и «стало» за длинным «было» не пропадает. Своего действия журнала нет
    (новое значение перечисления — миграция): общая «правка» со словами «действия», как у
    заведения гипотезы (`hypotheses.add`); этап — в записи."""
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author_id,
        target=f"sales_hypothesis:{hypothesis.id}",
        details={
            "действие": _segment_change(was, hypothesis.description),
            "гипотеза": hypothesis.name,
            "было": was,
            "стало": hypothesis.description,
        },
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона (`Stage` выше)
    )


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
    body: SegmentAsk,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> RunQueued:
    """Смета — заново, на сервере: выше потолка или негодный запрос — 409 словами, до
    очереди задач. Прогон и запись журнала — одна транзакция с постановкой."""
    found = await estimate(session, _ask(body))
    if found.refusal is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Прогон не запущен: {found.refusal}")
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
        details={
            "действие": "сбор базы продаж",
            "гипотеза": found.hypothesis,
            "ключей": len(found.keywords),
            "рынок": found.country,
            "смета": " ".join(found.lines),
        },
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона (`Stage` ниже)
    )
    await session.commit()
    return RunQueued(run_id=run.id, job_id=str(job.id))


async def _enqueue(jobs: Queue, run_id: int) -> Job | None:
    """Проход прогона — в очередь долгих задач продаж; `None` — номер занят (`enqueue_once`)."""
    return await asyncio.to_thread(
        enqueue_once, jobs, segment_job_id(run_id), SEGMENT_JOB, run_id, **with_retries()
    )


async def _queued(run_id: int) -> Job | None:
    """Поставить проход продолжения. Номер занят — «идёт» или задача умершего воркера, которую
    rq держит «идёт» до таймаута: такая убирается (`segment_watch.drop_dead`), и проход
    ставится снова. `None` — прогон и правда идёт."""
    jobs = sales_bulk_queue()
    job = await _enqueue(jobs, run_id)
    if job is None and await asyncio.to_thread(segment_watch.drop_dead, jobs, run_id):
        job = await _enqueue(jobs, run_id)
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
    """Тот же прогон с места, где он стоит: выдачу второй раз не покупает."""
    run = await session.get(SalesSegmentRunModel, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, UNKNOWN_RUN.format(run_id=run_id))
    job = await _queued(run.id)
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
    response_model=CandidateRow,
    summary="Решение человека по сайту «на проверку»",
)
async def decide(
    candidate_id: int,
    body: DecisionBody,
    author: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> CandidateRow:
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
    candidate = decision.candidate
    host = await session.scalar(
        select(DomainModel.host).where(DomainModel.id == candidate.domain_id)
    )
    if not decision.repeated:
        await _journal_decision(session, author.id, candidate, str(host))
    await session.commit()
    return CandidateRow.of(candidate, str(host))


async def _journal_decision(
    session: AsyncSession, author_id: int, candidate: SalesSegmentCandidateModel, host: str
) -> None:
    """Решение — в журнал, как решение по сайту на «Отборе»: оно меняет, кому уйдёт письмо, и
    по нему считают, как ошибается судья сегмента. Этап — в записи (без права «Продажи» её не
    видно); «действие» отличает её от решения по донору."""
    verdict = candidate.verdict or ""
    await AccessRepository(session).record(
        AuditAction.SITE_REVIEWED,
        author_id=author_id,
        target=f"sales_segment_candidate:{candidate.id}",
        details={
            "действие": "решение по сайту сбора базы",
            "домен": host,
            "решение": "accepted" if candidate.lane == Lane.ACCEPT else "rejected",
            "судья сегмента": segment_report.LANE_WORDS.get(verdict, verdict),
            "прогон": candidate.run_id,
        },
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона (`Stage` выше)
    )
