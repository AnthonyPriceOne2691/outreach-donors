"""Очередь писем продаж — под правом `sales`: что ждёт и подключены ли продажи, сборка задачей.

`GET /sales/queue?hypothesis=N` — без записи: подключены ли продажи и чего не хватает
(тем же правилом, каким откажет отправка), цепочка гипотезы на каждом языке, сколько лидов
ещё без письма и сколько писем ждёт отправки — у гипотезы и у продаж целиком (пачка берёт
очередь этапа, а не гипотезы). `POST /sales/queue` — поставить сборку в
очередь задач (`sales/queue_jobs.py`): модель на каждое письмо — минуты, а не запрос.
Отказ подключения — до очереди задач, 409 словами: человек видит его у кнопки, а не в итоге
задачи через минуты.

Отправляет очередь не этот раздел: письма продаж уходят общей отправкой — пачкой
(`POST /letters/send-queue` с этапом `sales`) или по одному, под правом отправки.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales.chain_schemas import ChainState
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import chain, connection, queue
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.queue_jobs import QUEUE_JOB
from backend.shared.queue import runs_queue, with_retries

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`) — как цепочка.
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
#: Писем за одну сборку: больше — модель часами, а очередь пачкой всё равно идёт днями.
LIMIT_MAX = 200


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
    )


@router.post("/queue", response_model=SalesQueueQueued, summary="Собрать очередь продаж")
async def build_queue(
    body: SalesQueueBody, author: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> SalesQueueQueued:
    """Поставить сборку в очередь задач. Ничего не отправляет."""
    await chain.known(session, body.hypothesis_id)
    await connection.check(session, queue.WHAT)
    job = runs_queue().enqueue(QUEUE_JOB, body.hypothesis_id, body.limit, **with_retries())
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
