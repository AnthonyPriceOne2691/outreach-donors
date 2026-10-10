"""Очистка лидов продаж с экрана — задачей очереди продаж, под правами `sales` и `run`.

`GET /sales/clean?hypothesis=N` — без записи: сколько лидов гипотезы ждут очистки (`new` —
их возьмёт проход) и платная ли проверка адресов. Экран спрашивает здесь по нажатию
«Очистить», а не берёт числа из списка гипотез: окно подтверждения называет потолок
расхода — до N запросов Hunter, по одной проверке на лид, — и число нужно на момент
решения, а список на экране мог устареть. Проверка выдуманная (`fixture`) — окна нет.

`POST /sales/clean {hypothesis_id}` — поставить очистку задачей (`sales/clean_jobs.py`):
тот же путь, что `outreach sales-clean --hypothesis` — проверяльщик по настройке, проход
партиями с коммитом каждой и строкой расхода за платные проверки. Очередь — продаж
(`worker-sales`), как у сборки очереди писем (`queue.py`).

**Право — `sales` и `run`.** Живой проверяльщик — платный Hunter общим ключом, а платный
поиск адресов в ядре — под `run` (`api/contacts/routes.py`).

**Отказы — до очереди задач, словами.** Гипотезы нет — 404. Ждущих лидов нет, проверяльщик
не настроен (`live` без ключа) — 409: человек видит причину у кнопки, а не в итоге задачи.
Очистка гипотезы идёт или ждёт повтора — 409: вторая проверила бы те же адреса и заплатила
бы дважды (та же проверка, что у сборки очереди, `queue._build_once`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from rq import Queue
from rq.exceptions import DuplicateJobError
from rq.job import Job, JobStatus
from rq.results import Result
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.config.startup_checks import ConfigError
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import clean_jobs, verifier
from backend.shared.queue import sales_queue, with_retries

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`) — как очередь писем.
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
_runner = Depends(needs(Permission.RUN))
#: Очистка ещё идёт или ждёт повтора — вторую не ставим.
_LIVE = frozenset({JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED})
CLEAN_RUNNING = (
    "Очистка лидов этой гипотезы уже идёт — дождитесь её итога: вторая проверила бы те же "
    "адреса и заплатила бы дважды"
)
NOTHING_WAITS = "Лидов, ждущих очистки, у гипотезы нет — очищать нечего"


def clean_job_id(hypothesis_id: int) -> str:
    """Номер задачи очистки — от гипотезы: одна очистка гипотезы за раз."""
    return f"sales-clean-{hypothesis_id}"


def _clean_once(jobs: Queue, hypothesis_id: int) -> Job:
    """Поставить очистку гипотезы, если её очистка не идёт. След кончившейся лежит в Redis
    неделю (`result_ttl`): постоянный номер с `unique=True` отказывал бы и после неё, поэтому
    он убирается — вместе с итогом; `unique=True` остаётся на гонку двух нажатий между
    проверкой и постановкой."""
    job_id = clean_job_id(hypothesis_id)
    if (earlier := jobs.fetch_job(job_id)) is not None:
        if earlier.get_status() in _LIVE:
            raise DuplicateJobError(job_id)
        # Итог прежней rq 2.12 хранит отдельно от задачи, и `Job.delete()` его не трогает:
        # новая очистка под тем же номером, пока стоит в очереди, показала бы на экране отчёт
        # прежней. Тот же вызов — у писем (`api/letters/once.enqueue_once`); когда он переедет
        # в `shared/queue.py`, очистка перейдёт на него.
        Result.delete_all(earlier)
        earlier.delete()
    return jobs.enqueue(
        clean_jobs.CLEAN_JOB, hypothesis_id, job_id=job_id, unique=True, **with_retries()
    )


def _paid() -> bool:
    """Платная ли проверка адресов. Настройка негодна — 409 её словами, до очереди задач."""
    try:
        return verifier.configured() == verifier.LIVE
    except ConfigError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Очистка не запустится: {exc}") from exc


class SalesCleanView(BaseModel):
    """Перед очисткой: сколько лидов гипотезы её ждут и платная ли проверка адресов."""

    hypothesis_id: int
    #: Лидов `new` — их возьмёт очистка; это и потолок платных проверок.
    waiting: int
    #: Проверка адресов живая (Hunter общим ключом) — перед запуском окно подтверждения.
    paid: bool


class SalesCleanBody(BaseModel):
    """Очистить лидов гипотезы, которые ждут очистки."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: int = Field(ge=1)


class SalesCleanQueued(BaseModel):
    """Очистка ушла в очередь задач — итог скажет строка задачи."""

    job_id: str


@router.get("/clean", response_model=SalesCleanView, summary="Перед очисткой: сколько и платно ли")
async def read_clean(
    hypothesis: int = Query(ge=1, description="номер гипотезы"),
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SalesCleanView:
    found = await clean_jobs.waiting(session, hypothesis)
    return SalesCleanView(hypothesis_id=hypothesis, waiting=found.count, paid=_paid())


@router.post(
    "/clean",
    response_model=SalesCleanQueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Очистить лидов гипотезы",
)
async def start_clean(
    body: SalesCleanBody,
    author: UserModel = _seller,
    _: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> SalesCleanQueued:
    """Поставить очистку в очередь задач. Писем не пишет и не отправляет."""
    found = await clean_jobs.waiting(session, body.hypothesis_id)
    if found.count == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, NOTHING_WAITS)
    paid = _paid()
    try:
        job = _clean_once(sales_queue(), body.hypothesis_id)
    except DuplicateJobError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, CLEAN_RUNNING) from exc
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details={
            "действие": "очистка лидов продаж",
            "гипотеза": found.name,
            "лидов": found.count,
            "проверка адресов": "платная" if paid else "выдуманная",
        },
    )
    await session.commit()
    return SalesCleanQueued(job_id=str(job.id))
