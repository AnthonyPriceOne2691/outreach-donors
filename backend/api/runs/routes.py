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
from backend.api.runs.schemas import Forecast, RunCard, RunQueued, RunRequestBody, RunsView
from backend.config import ahrefs as ahrefs_cfg
from backend.config import filters
from backend.config.startup_checks import check_collect
from backend.features.access.repository import AccessRepository
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.runs.browse import RunBrowser
from backend.features.runs.budget import units_left
from backend.features.runs.estimate import UNIQUE_SHARE, forecast
from backend.features.runs.repository import RunRepository
from backend.features.runs.spending import ahrefs_spent_this_month, cap_left
from backend.features.runs.thresholds import defaults
from backend.features.serp.dataforseo import COUNTRY_CODES
from backend.shared.queue import RUN_JOB, runs_queue, workers_alive

router = APIRouter(prefix="/runs", tags=["прогоны"])

_runner = Depends(needs(Permission.RUN))
_viewer = Depends(needs(Permission.VIEW))


async def effective_cap(session: AsyncSession, *, asked: int | None) -> int:
    """Потолок этого прогона: остаток по месячному капу, а если человек
    назвал свой — меньшее из двух.

    Своё число не может быть больше общего: иначе поле «попробовать
    дёшево» превращалось бы в способ обойти кап.
    """
    left = await cap_left(session, cap=ahrefs_cfg.UNITS_CAP)
    return min(asked, left) if asked is not None else left


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
async def estimate(
    body: RunRequestBody,
    _: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> Forecast:
    """Сколько будет стоить. Не тратит ничего: остаток провайдера — это
    бесплатный запрос, число доменов — арифметика и замеренная доля."""
    spent = await ahrefs_spent_this_month(session)

    client = AhrefsClient()
    try:
        # Остаток берётся сырой, без капа: всю арифметику потолков
        # делает смета в одном месте. Применить кап и здесь, и там
        # значило вычесть месячную трату дважды — поймано живой
        # проверкой на потолке в пять тысяч.
        left = await units_left(client)
    finally:
        await client.aclose()

    return Forecast.of(
        forecast(
            keywords=len(body.keywords),
            depth_pages=body.depth_pages,
            units_left=left,
            units_cap=ahrefs_cfg.UNITS_CAP,
            units_spent_this_month=spent,
            run_ceiling=body.cap,
            country_share=await RunRepository(session).country_call_share(body.country),
            unique_share=await RunRepository(session).unique_share(UNIQUE_SHARE),
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

    # Строка прогона заводится здесь, а не в задаче. Между нажатием
    # и первой тратой идут выдача и смета — минуты, за которые экран
    # не показывал ничего; а если задачу никто не возьмёт, не покажет
    # никогда. Теперь прогон виден сразу и со своим состоянием.
    runs = RunRepository(session)
    # Кап прогона: остаток по месячному капу, а если человек задал свой
    # потолок — меньшее из двух. Дальше он едет в настройках прогона,
    # и задача берёт его оттуда: между нажатием и тратой проходят минуты,
    # за которые остаток мог измениться, — но обещанное человеку число
    # меняться не должно.
    settings = await runs.create_settings(
        defaults(),
        geo_top_n=filters.GEO_TOP_N,
        geo_min_share=filters.GEO_MIN_SHARE,
        metrics_ttl_days=filters.METRICS_TTL_DAYS,
        price_ttl_days=filters.PRICE_TTL_DAYS,
        units_cap=await effective_cap(session, asked=body.cap),
    )
    run = await runs.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=body.keywords,
        country=body.country,
        depth_pages=body.depth_pages,
    )
    job = runs_queue().enqueue(RUN_JOB, run.id)
    await runs.bind_job(run, str(job.id))
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"run:{run.id}",
        details={
            "ключей": len(body.keywords),
            "страна": body.country,
            "потолок юнитов": settings.units_cap,
            "задача": str(job.id),
        },
    )
    await session.commit()
    return RunQueued(run_id=run.id, job_id=str(job.id))


@router.get("", response_model=RunsView, summary="История прогонов")
async def all_runs(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> RunsView:
    """Список прогонов и то, есть ли кому их выполнять.

    Про воркеров спрашивается здесь, а не отдельным маршрутом: экран,
    на котором нажимают «Запустить», — единственное место, где ответ
    «задачу некому взять» приходит вовремя.
    """
    rows = await RunBrowser(session).recent()
    return RunsView(runs=[RunCard.of(row) for row in rows], workers=workers_alive())


@router.get("/{run_id}", response_model=RunCard, summary="Один прогон")
async def one_run(
    run_id: int,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> RunCard:
    return RunCard.of(await RunBrowser(session).one(run_id))
