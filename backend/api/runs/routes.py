"""Прогон: смета, запуск, история.

Единственный экран, где человек тратит деньги, и единственный, где
кнопка блокируется расчётом. Поэтому смета — отдельный маршрут, который
ничего не тратит: остаток у Ahrefs спрашивается бесплатным запросом,
а число доменов оценивается по замеренной доле уникальных.

**Запуск кладёт задачу в очередь, а не выполняет её.** Прогон идёт
минутами; выполнить его внутри запроса значит потерять оплаченную
работу, если человек закрыл вкладку.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.runs.schemas import Forecast, RunCard, RunQueued, RunRequestBody
from backend.config import ahrefs as ahrefs_cfg
from backend.config.startup_checks import check_collect
from backend.features.access.repository import AccessRepository
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.runs.browse import RunBrowser
from backend.features.runs.estimate import forecast
from backend.features.runs.pipeline import units_left
from backend.features.serp.dataforseo import COUNTRY_CODES
from backend.shared.queue import runs_queue

router = APIRouter(prefix="/runs", tags=["прогоны"])

_runner = Depends(needs(Permission.RUN))
_viewer = Depends(needs(Permission.VIEW))

#: Путь к задаче строкой: воркеру не нужен тот же объект в памяти, что
#: и серверу, а проверка импорта происходит у него при первом запуске.
RUN_JOB = "backend.workers.jobs.run_donor_search"


@router.get("/countries", response_model=list[str], summary="Страны, доступные источнику")
async def countries(_: UserModel = _runner) -> list[str]:
    """Список стран отдаёт сервер, а не хранит фронт.

    Промахнуться страной хуже, чем не начать: выдача по чужому региону
    выглядит нормальной, и ошибка всплывает уже после оплаченного
    прогона. Второй список на фронте разъехался бы с этим при первом же
    добавлении рынка.
    """
    return sorted(COUNTRY_CODES)


@router.post("/estimate", response_model=Forecast, summary="Смета до запуска")
async def estimate(body: RunRequestBody, _: UserModel = _runner) -> Forecast:
    """Сколько будет стоить. Не тратит ничего: остаток провайдера — это
    бесплатный запрос, число доменов — арифметика и замеренная доля."""
    client = AhrefsClient()
    try:
        left = await units_left(client, cap=ahrefs_cfg.UNITS_CAP)
    finally:
        await client.aclose()

    return Forecast.of(
        forecast(
            keywords=len(body.keywords),
            depth_pages=body.depth_pages,
            units_left=left,
            units_cap=ahrefs_cfg.UNITS_CAP,
        )
    )


@router.post("", response_model=RunQueued, summary="Запустить прогон")
async def start_run(
    body: RunRequestBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> RunQueued:
    """Поставить прогон в очередь.

    Настройки проверяются здесь, хотя их проверяет и сама задача: отказ
    из очереди человек увидит нескоро и не поймёт, почему прогон «не
    случился». Отказ на нажатии он видит сразу и с текстом.
    """
    check_collect()

    job = runs_queue().enqueue(
        RUN_JOB,
        body.keywords,
        body.country,
        depth_pages=body.depth_pages,
    )
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details={"ключей": len(body.keywords), "страна": body.country},
    )
    await session.commit()
    return RunQueued(job_id=str(job.id))


@router.get("", response_model=list[RunCard], summary="История прогонов")
async def all_runs(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> list[RunCard]:
    rows = await RunBrowser(session).recent()
    return [RunCard.of(row) for row in rows]


@router.get("/{run_id}", response_model=RunCard, summary="Один прогон")
async def one_run(
    run_id: int,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> RunCard:
    return RunCard.of(await RunBrowser(session).one(run_id))
