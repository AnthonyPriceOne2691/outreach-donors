"""Очередь писем продаж — под правом `sales`: что ждёт и подключены ли продажи, сборка задачей.

`GET /sales/queue?hypothesis=N` — без записи: подключены ли продажи и чего не хватает
(тем же правилом, каким откажет отправка), цепочка гипотезы на каждом языке, сколько лидов
ещё без письма и сколько писем ждёт отправки — у гипотезы и у продаж целиком (пачка берёт
очередь этапа, а не гипотезы). `POST /sales/queue` — поставить сборку в
очередь задач (`sales/queue_jobs.py`): модель на каждое письмо — минуты, а не запрос. Очередь —
продаж (`worker-sales`), а не общая: минуты модели не держат воркер доноров, а сборка не ждёт за прогоном.
Отказ подключения — до очереди задач, 409 словами: человек видит его у кнопки, а не в итоге
задачи через минуты. Сборка гипотезы — одна за раз: второе «Собрать», пока первая идёт или ждёт
повтора, — 409 словами (вторая собрала бы те же письма и потратила модель дважды).

Отправляет очередь не этот раздел: письма продаж уходят общей отправкой — пачкой
(`POST /letters/send-queue` с этапом `sales`) или по одному, под правом отправки.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from rq import Queue
from rq.exceptions import DuplicateJobError
from rq.job import Job, JobStatus
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales.chain_schemas import ChainState
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.letters import batch
from backend.features.sales import chain, connection, queue
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.queue_jobs import QUEUE_JOB
from backend.shared.queue import sales_queue, with_retries

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`) — как цепочка.
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
#: Писем за одну сборку: больше — модель часами, а очередь пачкой всё равно идёт днями.
LIMIT_MAX = 200
#: Сборка ещё идёт или ждёт повтора — вторую не ставим.
_RUNNING = frozenset({JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED})
BUILD_RUNNING = (
    "Сборка очереди этой гипотезы уже идёт — дождитесь её итога: вторая собрала бы те же письма "
    "и потратила модель дважды"
)


def build_job_id(hypothesis_id: int) -> str:
    """Номер задачи сборки — от гипотезы: одна сборка гипотезы за раз."""
    return f"sales-queue-{hypothesis_id}"


def _build_once(jobs: Queue, hypothesis_id: int, limit: int) -> Job:
    """Поставить сборку гипотезы, если её сборка не идёт.

    Готовая задача лежит в Redis весь `result_ttl` (неделю), поэтому один `unique=True` с постоянным
    номером отказывал бы и после неё: закончившуюся или упавшую сборку убираем и ставим заново.
    `unique=True` остаётся на гонку двух нажатий между проверкой и постановкой.
    """
    job_id = build_job_id(hypothesis_id)
    previous = jobs.fetch_job(job_id)
    if previous is not None:
        if previous.get_status() in _RUNNING:
            raise DuplicateJobError(job_id)
        previous.delete()
    return jobs.enqueue(
        QUEUE_JOB, hypothesis_id, limit, job_id=job_id, unique=True, **with_retries()
    )


class SalesQueueView(BaseModel):
    """Очередь гипотезы: подключены ли продажи, цепочки, сколько лидов и писем ждёт."""

    hypothesis_id: int
    connected: bool
    #: Чего не хватает продажам — словами отказа; пусто — подключены.
    missing: list[str]
    #: Цепочка гипотезы на каждом языке — той же формой, что у экрана цепочки.
    chains: list[ChainState]
    #: Лидов `ready` без письма — их возьмёт следующая сборка.
    unwritten: int
    #: Писем гипотезы в очереди — ждут отправки.
    queued: int
    #: Писем продаж в очереди — всех гипотез: столько возьмёт пачка этапа продаж.
    stage_queued: int
    #: Больше писем за одну сборку сервер не примет — граница поля на экране.
    limit_max: int
    #: Больше писем одна пачка не возьмёт — окно подтверждения называет этот потолок, а не всю
    #: очередь этапа (тот же `batch_max`, что у экрана писем).
    batch_max: int


class SalesQueueBody(BaseModel):
    """Собрать очередь гипотезы: сколько писем за раз."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: int = Field(ge=1)
    limit: int = Field(default=50, ge=1, le=LIMIT_MAX)


class SalesQueueQueued(BaseModel):
    """Сборка ушла в очередь задач — итог скажет строка задачи."""

    job_id: str


@router.get("/queue", response_model=SalesQueueView, summary="Очередь писем продаж гипотезы")
async def read_queue(
    hypothesis: int = Query(ge=1, description="номер гипотезы"),
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SalesQueueView:
    await chain.known(session, hypothesis)
    found = await queue.state(session, hypothesis)
    return SalesQueueView(
        hypothesis_id=hypothesis,
        connected=not found.missing,
        missing=found.missing,
        chains=[ChainState.of(item) for item in found.chains],
        unwritten=found.unwritten,
        queued=found.queued,
        stage_queued=found.stage_queued,
        limit_max=LIMIT_MAX,
        batch_max=batch.BATCH_MAX,
    )


@router.post("/queue", response_model=SalesQueueQueued, summary="Собрать очередь продаж")
async def build_queue(
    body: SalesQueueBody, author: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> SalesQueueQueued:
    """Поставить сборку в очередь задач. Ничего не отправляет."""
    await chain.known(session, body.hypothesis_id)
    await connection.check(session, queue.WHAT)
    try:
        job = _build_once(sales_queue(), body.hypothesis_id, body.limit)
    except DuplicateJobError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, BUILD_RUNNING) from exc
    hypothesis = await session.get(SalesHypothesisModel, body.hypothesis_id)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details={
            "действие": "сборка очереди продаж",
            "гипотеза": hypothesis.name if hypothesis else body.hypothesis_id,
            "писем": body.limit,
        },
    )
    await session.commit()
    return SalesQueueQueued(job_id=str(job.id))
