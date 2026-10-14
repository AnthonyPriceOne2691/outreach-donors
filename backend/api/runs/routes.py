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

import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.runs.schemas import (
    Forecast,
    RunCard,
    RunQueued,
    RunReportView,
    RunRequestBody,
    RunsView,
)
from backend.config import ahrefs as ahrefs_cfg
from backend.config.startup_checks import check_collect
from backend.features.access.permissions import visible_stages
from backend.features.access.repository import AccessRepository
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.donors import standing
from backend.features.donors.geo import assert_settings_allow_limited_fetch
from backend.features.runs.browse import MAX_PAGE_SIZE, PAGE_SIZE, RunBrowser
from backend.features.runs.budget import units_left
from backend.features.runs.duplicates import refuse_duplicate
from backend.features.runs.estimate import UNIQUE_SHARE, forecast
from backend.features.runs.outcome import outcome
from backend.features.runs.repository import RunRepository
from backend.features.runs.spending import ahrefs_spent_this_month, cap_left
from backend.features.runs.thresholds import rules_in_force
from backend.features.serp.dataforseo import COUNTRY_CODES
from backend.features.serp.modes import mode_of
from backend.shared.queue import RUN_JOB, RUN_JOB_TIMEOUT, runs_queue, workers_alive

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
            # Ключи уже сведены схемой правилом выдачи: смета считает то, что она купит.
            keywords=len(body.keywords),
            depth_pages=body.depth_pages,
            units_left=left,
            units_cap=ahrefs_cfg.UNITS_CAP,
            units_spent_this_month=spent,
            run_ceiling=body.cap,
            country_share=await RunRepository(session).country_call_share(body.country),
            unique_share=await RunRepository(session).unique_share(UNIQUE_SHARE),
        ),
        serp_mode=body.serp_mode or mode_of(None),
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
    # Версия настроек, которой прогон будет судить, — действующая: пороги, правило
    # региона и срок метрик, а не окружение (сверка ТЗ 11.10.2026). Несогласованное
    # правило региона — отказ словами здесь, а не остановка из очереди (`geo.py`).
    rules = await rules_in_force(session)
    assert_settings_allow_limited_fetch(rules.geo.top_n, rules.geo.min_share)
    # Такой же прогон, пока первый в очереди или идёт, — отказ: двойной щелчок покупал
    # выдачу дважды (аудит 10.10.2026). Под замком до фиксации (`runs/duplicates.py`).
    await refuse_duplicate(
        session, keywords=body.keywords, country=body.country, depth_pages=body.depth_pages
    )

    # Строка прогона заводится здесь, а не в задаче. Между нажатием
    # и первой тратой идут выдача и смета — минуты, за которые экран
    # не показывал ничего; а если задачу никто не возьмёт, не покажет
    # никогда. Теперь прогон виден сразу и со своим состоянием.
    runs = RunRepository(session)
    # Кап прогона: остаток по месячному капу, а если человек задал свой
    # потолок — меньшее из двух. Дальше он едет в настройках прогона:
    # это обещанное человеку число, и выше него прогон не потратит. Но
    # и не больше, чем останется по месячному капу к старту задачи, —
    # между нажатием и стартом очередь может пройти другие прогоны
    # (`budget.ceiling_at_start`, аудит 10.10.2026).
    settings = await runs.create_settings(
        # Пороги — действующие, с экрана «Пороги»: умолчания конфига здесь
        # молча отменяли сохранённую версию (аудит 10.10.2026). Правило региона
        # и сроки — той же версии: окружение их тоже молча отменяло.
        rules.thresholds,
        **rules.columns(),
        units_cap=await effective_cap(session, asked=body.cap),
    )
    run = await runs.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=body.keywords,
        country=body.country,
        depth_pages=body.depth_pages,
        # Режим — какой человек видел в смете, а не умолчание на момент старта задачи.
        serp_mode=body.serp_mode or mode_of(None),
    )
    # Клиент очереди синхронный: в пуле потоков, а не в цикле событий единственного
    # процесса API — зависший Redis иначе держал бы всех (аудит 10.10.2026).
    job = await asyncio.to_thread(
        runs_queue().enqueue, RUN_JOB, run.id, job_timeout=RUN_JOB_TIMEOUT
    )
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


@router.get("", response_model=RunsView, summary="История прогонов, по странице")
async def all_runs(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
    page: int = Query(default=1, ge=1, description="страница истории, с единицы"),
    limit: int = Query(
        default=PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="прогонов на странице"
    ),
) -> RunsView:
    """Страница истории прогонов и то, есть ли кому их выполнять.

    Страница — номером, а не сдвигом: экран держит в адресе номер
    (`?page=2`), а размер страницы знает только сервер и называет его
    в ответе. Со сдвигом экрану пришлось бы хранить свою копию размера.

    Про воркеров спрашивается здесь, а не отдельным маршрутом: экран,
    на котором нажимают «Запустить», — единственное место, где ответ
    «задачу некому взять» приходит вовремя.

    Рядом со строками — число у «Прогона» в меню и сколько доменов ждут в
    нескольких очередях (`standing.waiting`): сумма «Рассмотреть» по строкам
    больше числа меню, и экран говорит почему (проверка прода 10.10.2026).
    """
    found = await RunBrowser(session).page(page, size=limit)
    decisions = await standing.waiting(session)
    return RunsView(
        runs=[RunCard.of(row) for row in found.rows],
        total=found.total,
        page=page,
        limit=limit,
        workers=await asyncio.to_thread(workers_alive),
        queued=found.queued,
        review_waiting=decisions.domains,
        review_shared=decisions.shared,
    )


@router.get("/with-accepted", response_model=list[RunCard], summary="Прогоны с принятыми донорами")
async def runs_with_accepted(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> list[RunCard]:
    """Из каких прогонов можно собрать рассылку — все, без страниц.

    Отдельным маршрутом, а не страницей истории: выбор прогонов на экране
    писем брал общий список и оставлял в нём прогоны с принятыми, и срез
    по странице (раньше — по тридцати последним) молча прятал бы старый
    прогон, из которого ещё есть что собрать.
    """
    return [RunCard.of(row) for row in await RunBrowser(session).with_accepted()]


@router.get("/{run_id}", response_model=RunCard, summary="Один прогон")
async def one_run(
    run_id: int,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> RunCard:
    return RunCard.of(await RunBrowser(session).one(run_id))


@router.get("/{run_id}/report", response_model=RunReportView, summary="Отчёт прогона")
async def run_report(
    run_id: int,
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> RunReportView:
    """Что прогон проверил, что прошло фильтр, по какому порогу отсеяно и во что
    обошлось — требование ТЗ к отчёту прогона (`runs/outcome.py`).

    Отдельным маршрутом, а не полем строки истории: расход читается из журнала
    по прогону, и считать его для десяти строк страницы ради одной открытой незачем.
    """
    return RunReportView.of(await outcome(session, run_id, stages=visible_stages(actor(user))))
