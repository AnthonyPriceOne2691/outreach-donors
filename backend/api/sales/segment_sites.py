"""Сайты прогона гипотезы по одному и пересуд прогона после правки описания сегмента.

`GET /sales/segment/runs/{N}/sites?lane=…&decider=…&page=…` — все сайты прогона страницами:
полоса, вердикт судьи, кто решил и когда, причина словами, цитата, ключ и итог (лид, только
форма, адреса нет, не искали); у сменивших полосу при пересуде — «было», у сайтов с лидом —
«стало бы»; у решённых человеком — слова судьи и можно ли решение отменить
(`features/sales/segment_sites.py`).

`GET /sales/segment/runs/{N}/rejudge` — смета пересуда (сайтов, к модели, токенов), ход и итог
последнего пересуда «было → стало». `POST /sales/segment/runs/{N}/rejudge` — запуск: смета
заново на сервере, отказ — 409 словами до очереди (прогон идёт, пересуд уже идёт, смета
отказала, описание поменялось после сметы); иначе задача в очереди долгих задач продаж
(`sales-bulk`, `features/sales/segment_rejudge.py`) и запись в журнале действий.

**Проход и пересуд одного прогона — не разом**: судили бы одни и те же сайты, а проход пишет
отчёт прогона целиком и затёр бы ход пересуда. Пересуд отказывает, пока идёт проход, а
продолжение прогона (`api/sales/segment.py`, `resume_run`) — пока идёт пересуд
(`refuse_while_rejudging`); очередь не ответила — 503 в обе стороны.

**Незаконченный пересуд того же описания продолжается со своего снимка** — оборванный умершим
воркером (его задача «идёт» — убирается, `segment_watch.drop_dead_job`), остановленный потолком
модели или отказом посреди работы: «было» у пересуженных сайтов цело, к модели второй раз они не
идут. Новый пересуд со снятием снимка — только если прошлый закончен или описание другое
(`segment_rejudge_report.left_of`); смета и экран говорят, какой будет.

**Права.** Смотреть сайты и смету — `sales`; пересуд — `sales` и `run`: модель — трата, как у
запуска и продолжения прогона.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from rq import Queue
from rq.job import Job
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.letters.once import enqueue_once
from backend.features.access.repository import AccessRepository
from backend.features.core import domain
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.ops.job_outcome import job_outcome
from backend.features.sales import segment_watch
from backend.features.sales.segment_jobs import segment_job_id
from backend.features.sales.segment_models import Decider, SalesSegmentRunModel
from backend.features.sales.segment_rejudge import REJUDGE_JOB, rejudge_job_id
from backend.features.sales.segment_rejudge_estimate import RejudgeEstimate, estimate
from backend.features.sales.segment_rejudge_report import (
    GOING,
    QUEUED,
    block_of,
    summary,
    write_block,
)
from backend.features.sales.segment_sites import (
    Outcome,
    SiteLane,
    SiteRow,
    SitesAsk,
    sites_page,
)
from backend.shared.queue import job_alive, sales_bulk_queue, with_retries

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`).
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
_runner = Depends(needs(Permission.RUN))

UNKNOWN_RUN = "прогона сбора №{run_id} нет — обновите список прогонов"
RUN_GOING = (
    "Прогон идёт — пересуд после его итога: прогон и пересуд судили бы одни и те же сайты разом"
)
REJUDGE_GOING = (
    "Пересуд этого прогона уже идёт — дождитесь его итога: второй судил бы те же сайты и "
    "потратил модель дважды"
)
QUEUE_SILENT = (
    "Очередь задач не отвечает — не узнать, идёт ли прогон или пересуд: попробуйте через минуту"
)
DIGEST_STALE = "Описание сегмента поменялось после сметы — посчитайте смету пересуда заново"
RESUME_REJUDGING = (
    "Идёт пересуд этого прогона — продолжите после его итога: проход и пересуд судили бы одни и "
    "те же сайты разом"
)
RESUME_SILENT = (
    "Очередь задач не отвечает — не узнать, идёт ли пересуд этого прогона: попробуйте через минуту"
)


# --- схемы ----------------------------------------------------------------------------------------


class OutcomeView(BaseModel):
    kind: str
    words: str
    lead_id: int | None
    lead_status: str | None
    lead_reason: str | None

    @classmethod
    def of(cls, found: Outcome | None) -> OutcomeView | None:
        if found is None:
            return None
        return cls(
            kind=found.kind,
            words=found.words,
            lead_id=found.lead_id,
            lead_status=found.lead_status,
            lead_reason=found.lead_reason,
        )


class ChangeView(BaseModel):
    """Пересуд сменил полосу сайта — или сменил бы: у сайта с лидом полоса та же."""

    was: str
    now: str
    would: bool
    reason: str | None
    quote: str | None


class SiteView(BaseModel):
    """Сайт прогона: что о нём сказала выдача, судья или человек — и чем кончилось. Тем же видом —
    ответ на решение человека и на его отмену (`api/sales/segment.py`)."""

    id: int
    host: str
    page_url: str | None
    page_title: str | None
    keyword: str | None
    lane: str
    verdict: str | None
    decided_by: str | None
    decided_by_user: str | None
    decided_at: datetime | None
    reason: str | None
    quote: str | None
    unanswered: bool
    model: str | None
    #: Решение человека можно отменить: решил человек, и лида из сайта нет.
    undoable: bool
    #: Решил человек — слова судьи, с которыми он не согласился (или согласился); иначе пусто.
    judge_reason: str | None
    outcome: OutcomeView | None
    change: ChangeView | None

    @classmethod
    def of(cls, row: SiteRow) -> SiteView:
        site = row.candidate
        change = row.change
        return cls(
            id=site.id,
            host=row.host,
            page_url=site.page_url,
            page_title=site.page_title,
            keyword=site.keyword,
            lane=site.lane,
            verdict=site.verdict,
            decided_by=site.decided_by,
            decided_by_user=site.decided_by_user,
            decided_at=site.decided_at,
            reason=site.reason,
            quote=site.quote,
            unanswered=site.unanswered,
            model=site.model,
            undoable=row.undoable,
            judge_reason=row.judge,
            outcome=OutcomeView.of(row.outcome),
            change=None
            if change is None
            else ChangeView(
                was=change.was,
                now=change.now,
                would=change.would,
                reason=change.reason,
                quote=change.quote,
            ),
        )


class SitesView(BaseModel):
    """Страница сайтов прогона: строки, всего под фильтром и числа вкладок."""

    run_id: int
    lane: str
    decider: str | None
    page: int
    limit: int
    total: int
    lanes: dict[str, int]
    deciders: dict[str, int]
    rows: list[SiteView]


class EstimateView(BaseModel):
    sites: int
    model: int
    per_site: int
    tokens: int
    tokens_left: int | None
    kept_human: int
    with_lead: int
    pending: int
    digest: str
    mode: str
    #: Продолжение незаконченного пересуда того же описания — с его снимка; `done` — пройдено.
    resumes: bool
    done: int
    lines: list[str]
    refusal: str | None

    @classmethod
    def of(cls, found: RejudgeEstimate) -> EstimateView:
        return cls(
            sites=found.sites,
            model=found.model,
            per_site=found.per_site,
            tokens=found.tokens,
            tokens_left=found.tokens_left,
            kept_human=found.kept_human,
            with_lead=found.with_lead,
            pending=found.pending,
            digest=found.digest,
            mode=found.mode,
            resumes=found.resumes,
            done=found.done,
            lines=found.lines,
            refusal=found.refusal,
        )


class JobView(BaseModel):
    """Задача пересуда глазами очереди: ждёт повтора — почему и когда; упала — почему."""

    state: str
    error: str | None
    next_try_at: datetime | None


class RejudgeView(BaseModel):
    """Пересуд прогона: смета следующего, ход и итог последнего."""

    run_id: int
    estimate: EstimateView
    #: Последний пересуд: ход, «было → стало» числами, причина остановки. Пусто — не было.
    last: dict[str, Any] | None
    #: Идёт ли задача пересуда: `None` — очередь не ответила.
    going: bool | None
    #: Исход задачи в очереди (`ops/job_outcome`): повтор с причиной, падение. Пусто — не знает.
    job: JobView | None = None


class RejudgeAsk(BaseModel):
    """Запуск пересуда — по смете того описания сегмента, которое видел человек."""

    model_config = ConfigDict(extra="forbid")

    digest: str = Field(min_length=1, max_length=64)


class RejudgeQueued(BaseModel):
    run_id: int
    job_id: str


# --- маршруты -------------------------------------------------------------------------------------


async def _run(session: AsyncSession, run_id: int) -> SalesSegmentRunModel:
    """Прогон — заново из базы: отчёт пересуда пишется SQL-правкой своего ключа, мимо объекта."""
    run = await session.get(SalesSegmentRunModel, run_id, populate_existing=True)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, UNKNOWN_RUN.format(run_id=run_id))
    return run


@router.get(
    "/segment/runs/{run_id}/sites", response_model=SitesView, summary="Все сайты прогона гипотезы"
)
async def read_sites(
    run_id: int,
    lane: SiteLane = Query(default=SiteLane.ALL, description="вкладка: полоса, все, сменили"),
    decider: Decider | None = Query(
        default=None, description="кто решил: rule, model, language, human"
    ),
    page: int = Query(default=1, ge=1, le=100_000),
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SitesView:
    found = await sites_page(session, SitesAsk(run_id, lane, decider, page))
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, UNKNOWN_RUN.format(run_id=run_id))
    return SitesView(
        run_id=run_id,
        lane=lane.value,
        decider=None if decider is None else decider.value,
        page=found.page,
        limit=found.limit,
        total=found.total,
        lanes=found.lanes,
        deciders=found.deciders,
        rows=[SiteView.of(row) for row in found.rows],
    )


async def _alive(job_id: str) -> bool | None:
    """Жива ли задача: клиент Redis синхронный — в пуле потоков, а не в цикле событий."""
    return await asyncio.to_thread(job_alive, job_id)


def _rejudging(jobs: Queue, run_id: int) -> bool | None:
    """Идёт ли пересуд прогона: его задача — в той же очереди, куда встанет продолжение, и жива
    (`queue.job_alive`: «идёт» у умершего воркера — не жива; `None` — не узнать). Redis лежит
    целиком — отказ общим обработчиком (`api/errors.QUEUE_DOWN`), как у постановки продолжения."""
    job_id = rejudge_job_id(run_id)
    if jobs.fetch_job(job_id) is None:
        return False
    return job_alive(job_id, jobs.connection)


async def refuse_while_rejudging(run_id: int, jobs: Queue) -> None:
    """Продолжение прогона («Продолжить», «Найти адреса принятым») — не посреди его пересуда:
    409 словами; очередь не ответила — 503: неизвестность — не «не идёт»."""
    going = await asyncio.to_thread(_rejudging, jobs, run_id)
    if going is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, RESUME_SILENT)
    if going:
        raise HTTPException(status.HTTP_409_CONFLICT, RESUME_REJUDGING)


@router.get(
    "/segment/runs/{run_id}/rejudge",
    response_model=RejudgeView,
    summary="Пересуд прогона: смета, ход и «было → стало»",
)
async def read_rejudge(
    run_id: int, _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> RejudgeView:
    run = await _run(session, run_id)
    last = summary(run.report)
    found = EstimateView.of(await estimate(session, run))
    if last is None:
        return RejudgeView(run_id=run_id, estimate=found, last=None, going=False)
    job_id = rejudge_job_id(run_id)
    outcome = await asyncio.to_thread(job_outcome, job_id)
    job = (
        None
        if outcome is None
        else JobView(state=outcome.state, error=outcome.error, next_try_at=outcome.next_try_at)
    )
    return RejudgeView(
        run_id=run_id, estimate=found, last=last, going=await _alive(job_id), job=job
    )


async def _refusal(run: SalesSegmentRunModel, found: RejudgeEstimate, digest: str) -> str | None:
    """Почему пересуд сейчас не запустить — словами; `None` — можно. Очередь не ответила —
    503: идёт ли прогон, не узнать, а неизвестность — не «не идёт»."""
    run_going = await _alive(segment_job_id(run.id))
    rejudge_going = await _alive(rejudge_job_id(run.id))
    if run_going is None or rejudge_going is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, QUEUE_SILENT)
    checks = (
        (run_going, RUN_GOING),
        (rejudge_going, REJUDGE_GOING),
        (found.refusal is not None, f"Пересуд не запущен: {found.refusal}"),
        (digest != found.digest, DIGEST_STALE),
    )
    return next((words for failed, words in checks if failed), None)


#: Запись журнала действий: новый пересуд — или продолжение прерванного.
ACTIONS = {
    False: "пересуд сбора базы продаж",
    True: "продолжение пересуда сбора базы продаж",
}


def _asked(was: dict[str, Any], resumes: bool, author: str, job_id: str) -> dict[str, Any]:
    """Блок пересуда до постановки задачи: новый — «поставлен», задача снимет снимок полос;
    продолжение — тот же блок со снимком, «идёт» и без причины остановки: задача пойдёт с места."""
    moment = datetime.now(UTC).isoformat()
    if resumes:
        resumed = {"resumed_at": moment, "resumed_by": author}
        return was | {"state": GOING, "error": None, "finished_at": None} | resumed
    return {"state": QUEUED, "job_id": job_id, "by": author, "asked_at": moment}


async def _enqueue(
    session: AsyncSession, run_id: int, jobs: Queue, *, block: dict[str, Any], was: dict[str, Any]
) -> Job | None:
    """Блок пересуда — в отчёт прогона до постановки (задача, взятая раньше коммита, не нашла бы,
    что пересуживать), затем задача — в очередь. Очередь не ответила — прежний блок
    возвращается, как был."""
    await write_block(session, run_id, block)
    await session.commit()
    try:
        return await asyncio.to_thread(
            enqueue_once, jobs, rejudge_job_id(run_id), REJUDGE_JOB, run_id, **with_retries()
        )
    except RedisError:
        await write_block(session, run_id, was)
        await session.commit()
        raise


@router.post(
    "/segment/runs/{run_id}/rejudge",
    response_model=RejudgeQueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Пересудить прогон гипотезы",
)
async def start_rejudge(
    run_id: int,
    body: RejudgeAsk,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> RejudgeQueued:
    """Смета — заново, на сервере; отказ — 409 словами до очереди. Отметка «поставлен» — в
    отчёте прогона до постановки: задача, взятая раньше коммита, не нашла бы, что пересуживать.
    Очередь не ответила — прежний пересуд в отчёте возвращается как был. Незаконченный пересуд
    того же описания — продолжается со своего снимка (смета это знает: `estimate.resumes`)."""
    run = await _run(session, run_id)
    found = await estimate(session, run)
    if (words := await _refusal(run, found, body.digest)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, words)
    jobs, job_id = sales_bulk_queue(), rejudge_job_id(run_id)
    # Воркер умер посреди пересуда: rq держит задачу «идёт» — постановка ответила бы «уже идёт», а
    # обслуживание rq повторило бы её само, сняв снимок полос заново. Убрать до записи блока.
    await asyncio.to_thread(segment_watch.drop_dead_job, jobs, job_id)
    was = block_of(run.report)
    block = _asked(was, found.resumes, author.email, job_id)
    job = await _enqueue(session, run_id, jobs, block=block, was=was)
    if job is None:
        # Номер занят — мёртвая задача убрана выше, значит, это второе нажатие: его задача уже
        # поставлена и ждёт этот блок. Прежний не возвращаем: с ним она увидела бы кончившийся
        # пересуд и молча ничего не сделала.
        raise HTTPException(status.HTTP_409_CONFLICT, REJUDGE_GOING)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details={
            "действие": ACTIONS[found.resumes],
            "прогон": run_id,
            "сайтов": found.sites,
            "смета": " ".join(found.lines[:2]),
        },
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона
    )
    await session.commit()
    return RejudgeQueued(run_id=run_id, job_id=str(job.id))
