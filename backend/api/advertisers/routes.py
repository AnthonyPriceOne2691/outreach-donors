"""Экран ручной проверки кандидатов в рекламодатели.

**Без этого экрана допуск в десять процентов ложных не держится.**
Скоринг выносит вердикт по пяти признакам, два из которых выведены
из замера на одной нише; пограничные случаи должен смотреть человек,
а не порог. До экрана ту же работу делала консоль — и делала тем же
кодом, порядок живёт в `features/crawl/review.py`.

**Смотреть — под правом `view`, решать — под `prices`.** Право выбрано
не по названию, а по смыслу: `prices` — это уже право «человек
поправляет то, что решила машина», ровно тот же класс действия. Если
окажется, что решать кандидатов должен кто-то другой, меняется одна
строка здесь.

**Решение пишется в журнал.** «Откуда у нас этот адресат» спросит либо
сам адресат, либо юрист.

**«Перевести в рекламодатели» и поиск им адресов** (06.10.2026) — раньше
только консоль (`outreach advertisers-promote --contacts`). Перевод — тот же
`crawl/promote.py`, внутри запроса: это доли секунды и ни копейки. Поиск
адресов ставится им сам, как у принятых доноров Этапа 1: человек, сказавший
«переводим», собирается им писать. Поиск — задачей: лестница доходит до
платной ступени и идёт минутами. Право на оба — `run`: тратятся деньги.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.advertisers.schemas import (
    CandidateCard,
    CandidatesView,
    DecisionBody,
    NicheCard,
    NicheCollected,
    NicheDecision,
    NicheView,
    PromoteResult,
    PromotionView,
)
from backend.api.contacts.schemas import ContactsQueued, ContactsState, SearchBody
from backend.api.contacts.search_state import search_state
from backend.api.deps import db_session, needs
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission, Stage, Verdict
from backend.features.core.models.access import UserModel
from backend.features.crawl import niche, promote, review
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.shared.queue import (
    ADVERTISER_CONTACTS_JOB_KEY,
    CONTACTS_JOB,
    contacts_job_id,
    job_alive,
    remember_contacts_job,
    runs_queue,
    with_retries,
    workers_alive,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/advertisers", tags=["рекламодатели"])

_viewer = Depends(needs(Permission.VIEW))
_reviewer = Depends(needs(Permission.PRICES))
_runner = Depends(needs(Permission.RUN))

#: Потолок одного прохода — тот же, что у поиска доноров (`SearchBody`).
SEARCH_MAX = 1000


@router.get("", response_model=CandidatesView, summary="Очередь ручной проверки")
async def queue(
    include_decided: bool = Query(
        default=False, description="показывать и те, по которым решение уже принято"
    ),
    verdict: Verdict = Query(
        default=Verdict.PENDING, description="спорные (pending) или «куплено» (bought)"
    ),
    limit: int = Query(default=review.PAGE_SIZE, ge=1, le=200),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> CandidatesView:
    """Кандидаты, которых смотрит человек: спорные — решить, «куплено» —
    проверить перед письмом и снять ложного."""
    if verdict not in review.REVIEWED:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Списком смотрят спорных (pending) и «куплено» (bought): «мимо» и «кому не пишем» "
            "видны числами в шапке",
        )
    rows = await review.queue(
        session, limit=limit, include_decided=include_decided, verdict=verdict
    )
    dates = await review.article_dates(session, rows)
    return CandidatesView(
        rows=[CandidateCard.of(row, published=dates.get(row.id)) for row in rows],
        waiting=await review.waiting(session),
        counts=await review.counts(session),
    )


@router.post("/{candidate_id}/decide", response_model=CandidateCard, summary="Решение человека")
async def decide(
    candidate_id: int,
    body: DecisionBody,
    author: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> CandidateCard:
    """Подтвердить кандидата или отклонить.

    Балл скоринга при этом не меняется: по расхождению между ним
    и решением человека и считается, как часто скоринг ошибается.
    """
    try:
        row = await review.decide(
            session, candidate_id, confirmed=body.confirmed, by=author.email, force=body.force
        )
    except review.UnknownCandidateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except review.AlreadyDecidedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    await AccessRepository(session).record(
        AuditAction.ADVERTISER_REVIEWED,
        author_id=author.id,
        target=f"candidate:{candidate_id}",
        details={
            "донор": row.donor_host,
            "рекламодатель": row.target_root,
            "решение": "пишем" if row.confirmed else "не пишем",
            "балл скоринга": row.points,
        },
    )
    await session.commit()
    return CandidateCard.of(row)


@router.get("/promotion", response_model=PromotionView, summary="Сколько переводить")
async def promotion(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> PromotionView:
    """Сколько доменов к переводу, сколько из них новых и сколько уже с адресом."""
    found = await promote.standing(session)
    return PromotionView(
        ready=found.ready,
        fresh=found.fresh,
        advertisers=found.advertisers,
        with_address=found.with_address,
    )


@router.post("/promote", response_model=PromoteResult, summary="Перевести в рекламодатели")
async def promote_candidates(
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> PromoteResult:
    """Перевести «куплено» и «пишем» в рекламодатели и поставить поиск адресов."""
    report = await promote.promote(session)
    await AccessRepository(session).record(
        AuditAction.ADVERTISER_REVIEWED,
        author_id=author.id,
        target="advertisers:promote",
        details={"действие": "перевод в рекламодатели", **report.as_dict()},
    )
    await session.commit()
    pending = await AdvertiserContactRepository(session).pending_count()
    # Очередь — в пуле потоков: клиент Redis синхронный, а процесс API один (аудит 10.10.2026).
    job_id = await asyncio.to_thread(_search_after_promotion, pending) if pending > 0 else None
    logger.info(
        "рекламодатели: %s перевёл — заведено %s, ждут адреса %s",
        author.email,
        report.promoted,
        pending,
    )
    return PromoteResult(
        report=report.as_dict(), fresh=report.hosts, pending=pending, contacts_job_id=job_id
    )


@router.get("/contacts", response_model=ContactsState, summary="Поиск адресов рекламодателям")
async def contacts_state(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> ContactsState:
    """Сколько рекламодателей ждёт адреса и идёт ли поиск — как у доноров."""
    pending = await AdvertiserContactRepository(session).pending_count()
    return await asyncio.to_thread(_queue_state, pending)


def _queue_state(pending: int) -> ContactsState:
    """Что говорит очередь — в пуле потоков, как постановка поиска."""
    return search_state(pending, contacts_job_id(key=ADVERTISER_CONTACTS_JOB_KEY), workers_alive())


@router.post("/contacts", response_model=ContactsQueued, summary="Найти адреса рекламодателям")
async def search_contacts(
    body: SearchBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> ContactsQueued:
    """Поставить поиск адресов тем рекламодателям, кому он нужен."""
    pending = await AdvertiserContactRepository(session).pending_count()
    job_id = await asyncio.to_thread(_search, body.limit)
    logger.info("рекламодатели: %s поставил поиск адресов, ждут %s", author.email, pending)
    return ContactsQueued(job_id=job_id, pending=pending)


def _search_after_promotion(pending: int) -> str | None:
    """Поиск после перевода. Очередь не ответила — перевод всё равно сделан.

    Ответить отказом значило бы сказать «не перевели» о записанном
    переводе. Строка «ждут адреса · найти» на экране остаётся — поиск
    ставится ею, когда очередь вернётся.
    """
    try:
        return _search(pending)
    except RedisError as exc:
        logger.warning("рекламодатели: переведены, поиск адресов не поставлен — %s", exc)
        return None


def _search(limit: int) -> str:
    """Поиск адресов рекламодателям — задача поиска доноров по своей очереди.

    Идущий поиск не дублируется: второй проход по тем же доменам заплатил
    бы платной ступени дважды (нажали «Перевести» второй раз, пока ищет).
    """
    running = contacts_job_id(key=ADVERTISER_CONTACTS_JOB_KEY)
    if running is not None and job_alive(running) is True:
        logger.info("рекламодатели: поиск адресов уже идёт (%s) — второй не ставим", running)
        return running
    job = runs_queue().enqueue(
        CONTACTS_JOB,
        min(limit, SEARCH_MAX),
        False,  # браузер: секунды на страницу, у рекламодателей не нужен
        False,  # платная ступень — последней, как у доноров
        None,  # не один донор, а вся очередь
        Stage.ADVERTISERS.value,
        **with_retries(),
    )
    job_id = str(job.id)
    remember_contacts_job(job_id, key=ADVERTISER_CONTACTS_JOB_KEY)
    return job_id


# --- бизнесы ниши из выдачи прогона (`crawl/niche.py`) ---


@router.get("/niche", response_model=NicheView, summary="Бизнесы ниши из выдачи, по странице")
async def niche_queue(
    include_decided: bool = Query(
        default=False, description="показывать и те, по которым решение уже принято"
    ),
    page: int = Query(default=1, ge=1, le=1_000_000, description="страница, с единицы"),
    limit: int = Query(
        default=niche.PAGE_SIZE, ge=1, le=niche.MAX_PAGE_SIZE, description="бизнесов на странице"
    ),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> NicheView:
    """Сайты, которые сами продают в нише прогона: кандидаты в рекламодатели, по странице.

    Страница — номером, как у очереди форм: экран держит номер в адресе, а размер
    страницы называет сервер. До 10.10.2026 приходили первые полсотни одним списком —
    простыня «пишем / не пишем», а пятьдесят первый бизнес был недостижим.
    """
    rows = await niche.listed(session, include_decided=include_decided, page=page, size=limit)
    return NicheView(
        rows=[NicheCard.of(row) for row in rows],
        waiting=await niche.waiting(session),
        total=await niche.total(session, include_decided=include_decided),
        page=page,
        limit=limit,
    )


@router.post(
    "/niche/{advertiser_id}/decide", response_model=NicheCard, summary="Решение по бизнесу ниши"
)
async def niche_decide(
    advertiser_id: int,
    body: NicheDecision,
    author: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> NicheCard:
    """«Пишем» открывает поиск адреса и письмо; «не пишем» запоминается и снимает
    собранные ему письма — из очереди и со сроков добивок."""
    decided = await niche.decide(session, advertiser_id, write=body.write, by=author.email)
    row = await niche.row_of(session, advertiser_id)
    await AccessRepository(session).record(
        AuditAction.ADVERTISER_REVIEWED,
        author_id=author.id,
        target=f"advertiser:{advertiser_id}",
        details={
            "рекламодатель": row.host,
            "источник": f"выдача прогона №{row.run_id}",
            "решение": "пишем" if body.write else "не пишем",
            **({"снято писем": decided.stopped} if decided.stopped else {}),
        },
    )
    await session.commit()
    return NicheCard.of(row)


@router.post(
    "/niche/collect", response_model=NicheCollected, summary="Собрать бизнесы ниши из прогона"
)
async def niche_collect(
    run_id: int = Query(ge=1, description="номер прогона"),
    _: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> NicheCollected:
    """Для прогонов, прошедших до сбора: вердикты судьи уже оплачены."""
    report = await niche.collect(session, run_id)
    await session.commit()
    return NicheCollected(run_id=report.run_id, found=report.found, added=report.added)
