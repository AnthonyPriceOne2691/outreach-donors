"""Подтверждение разбора цены человеком.

Ветка, без которой не держится приёмка: требование говорит, что разбор
с недостаточной уверенностью уходит в ручную очередь, а не в базу.
Здесь эта очередь и заканчивается.

**Подтверждение человека сильнее любой уверенности модели.** Подтверждая,
человек видит исходный текст письма рядом с разобранным — так устроена
карточка диалога, — и его решение кладёт цену в карточку донора
независимо от того, что насчитала модель.

**Уверенность модели при этом не переписывается.** Она осталась тем, что
модель сказала, и стереть её значило бы потерять единственный след,
по которому потом видно, часто ли она ошибается.

**Разбор — именованное действие.** Смотреть цены может каждый (так в ТЗ),
подтверждать — действие `prices`. Названо отдельно не чтобы отобрать,
а чтобы на вопрос «кто подтверждает цены» отвечал список действий,
а не чтение обработчиков.

**Вложение ответа скачивается здесь же, под правом смотреть:** прайс
файлом — то же содержимое переписки, что и текст письма. Отдаётся оно
только на скачивание (`download.py`), никогда — на показ.

**Ответы без письма — тоже здесь и под тем же правом** (28.09.2026). Приём
сохранял их с первого дня, а видеть их было негде: сохранённый и невидимый
ответ — тот же «донор не ответил», только без шанса заметить.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.replies.download import download_headers
from backend.api.replies.schemas import (
    Calibration,
    LeadSent,
    LeadTaken,
    ReviewBody,
    Reviewed,
    UnboundCard,
    UnboundView,
    VersionCalibration,
)
from backend.config import outreach as outreach_cfg
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.replies import lead_handoff, unbound
from backend.features.replies.attachments import ReplyFiles
from backend.features.replies.calibration import calibrate
from backend.features.replies.extract import PLACEMENT_DECLINES, PLACEMENT_SELLS
from backend.features.replies.repository import ReplyRepository
from backend.shared.queue import LEAD_JOB, runs_queue, with_retries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/replies", tags=["ответы"])

_reviewer = Depends(needs(Permission.PRICES))
_viewer = Depends(needs(Permission.VIEW))


@router.get("/leads.csv", summary="Лиды файлом")
async def export_leads(
    taken: bool | None = Query(None, description="только взятые (true) или ждущие (false)"),
    since: datetime | None = Query(None, description="получены не раньше"),
    until: datetime | None = Query(None, description="получены раньше"),
    _: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> Response:
    """Лиды — ответы людей на оффер рекламодателю — файлом CSV, новые первыми.

    Поля те же, что в теле вебхука: одно описание лида на оба пути передачи.
    Право — того, кто ведёт лиды: в файле переписка с адресами (ревью #160).
    Строк больше потолка — файл обрезан, и заголовок это говорит.
    """
    limit = lead_handoff.EXPORT_LIMIT
    cards = await lead_handoff.leads(
        session, taken=taken, since=since, until=until, limit=limit + 1
    )
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return Response(
        content=lead_handoff.to_csv(cards[:limit]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="leads-{stamp}.csv"',
            "X-Export-Rows": str(min(len(cards), limit)),
            "X-Export-Truncated": "1" if len(cards) > limit else "0",
        },
    )


@router.get("/calibration", response_model=Calibration, summary="Калибровка разбора")
async def calibration(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> Calibration:
    """Предложение модели против решения человека — по версиям промпта.

    Считается там, где смотрел человек: автоматически положенная цена
    сверки не имеет и идёт отдельным числом.
    """
    return Calibration(
        versions=[
            VersionCalibration(
                version=score.version,
                reviewed=score.reviewed,
                as_is=score.as_is,
                edited=score.edited,
                wrong=score.wrong,
                auto_stored=score.auto_stored,
                waiting=score.waiting,
            )
            for score in await calibrate(session)
        ]
    )


@router.get("/unbound", response_model=UnboundView, summary="Ответы без письма, по странице")
async def unbound_replies(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
    page: int = Query(default=1, ge=1, le=1_000_000, description="страница, с единицы"),
    limit: int = Query(
        default=unbound.PAGE_SIZE, ge=1, le=unbound.MAX_PAGE_SIZE, description="ответов на странице"
    ),
) -> UnboundView:
    """Ответы, которые не привязались ни к одному нашему письму, — новые первыми.

    У каждого — почему не привязан, словами, и вложения сведениями: сам
    файл скачивается тем же маршрутом, что у ответа в переписке. Страница —
    номером, размер называет сервер, как у очереди форм.
    """
    rows = await unbound.page(session, page=page, size=limit)
    # Вложения — одним запросом на страницу и без самих файлов.
    files = await ReplyFiles(session).listed(reply.id for reply in rows)
    return UnboundView(
        rows=[UnboundCard.of(reply, files.get(reply.id, ())) for reply in rows],
        total=await unbound.total(session),
        page=page,
        limit=limit,
    )


@router.get(
    "/{reply_id}/attachments/{attachment_id}",
    summary="Вложение ответа — файлом на скачивание",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "Файл"}},
)
async def attachment(
    reply_id: int,
    attachment_id: int,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> Response:
    """Файл, присланный донором, — только на скачивание.

    Тип, который назвал отправитель, в ответ не идёт: присланный HTML,
    открытый с нашего адреса, выполнился бы на странице с пропуском
    сотрудника.
    """
    found, data = await ReplyFiles(session).file(reply_id, attachment_id)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers=download_headers(found.name, found.id),
    )


@router.post("/{reply_id}/lead", response_model=LeadTaken, summary="Взять лид в работу")
async def take_lead(
    reply_id: int,
    author: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> LeadTaken:
    """Ответ рекламодателя — в работу. Цену в нём не разбирают, его ведёт человек.

    Право то же, что у разбора цены: ответы, ждущие человека, разбирает
    один и тот же человек, какого бы этапа они ни были.
    """
    repository = ReplyRepository(session)
    reply = await repository.reply(reply_id)
    taken_at = await repository.take_lead(reply, by=author.email)
    await AccessRepository(session).record(
        AuditAction.LEAD_TAKEN,
        author_id=author.id,
        target=f"reply:{reply_id}",
        details={"действие": "лид взят в работу", "от кого ответ": reply.from_email},
    )
    await session.commit()
    logger.info("лиды: ответ №%s взят в работу", reply_id)
    # Передача в CRM — после записи: задача читает лид из базы, и взятым он
    # должен быть уже там. Номер события — номер лида: повтор «взять» отказан
    # выше, второй передачи по кнопке «взять» не бывает.
    handoff = "off"
    if outreach_cfg.LEAD_WEBHOOK_URL:
        _enqueue_lead(reply_id, event_id=f"lead-{reply_id}")
        handoff = "queued"
    return LeadTaken(id=reply_id, reviewed_by=author.email, reviewed_at=taken_at, handoff=handoff)


@router.post("/{reply_id}/lead/send", response_model=LeadSent, summary="Передать лид в CRM ещё раз")
async def send_lead(
    reply_id: int,
    _: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> LeadSent:
    """Повторная передача: вебхук настроили позже, или получатель лежал дольше
    повторов очереди. Номер события новый — получатель отличит ручной повтор
    от повтора очереди."""
    if not outreach_cfg.LEAD_WEBHOOK_URL:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Адрес вебхука лидов не задан — заполнить OUTREACH_LEAD_WEBHOOK_URL",
        )
    card = await lead_handoff.lead_card(session, reply_id)
    if card is None or not card.taken_at:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Ответ №{reply_id} не взятый лид — передают лид, который кто-то ведёт",
        )
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    job_id = _enqueue_lead(reply_id, event_id=f"lead-{reply_id}-{stamp}")
    return LeadSent(id=reply_id, job_id=job_id)


def _enqueue_lead(reply_id: int, *, event_id: str) -> str:
    job = runs_queue().enqueue(LEAD_JOB, reply_id, event_id, job_id=event_id, **with_retries())
    logger.info("лиды: передача лида №%s поставлена (%s)", reply_id, event_id)
    return str(job.id)


@router.patch("/{reply_id}", response_model=Reviewed, summary="Подтвердить разбор цены")
async def review(
    reply_id: int,
    body: ReviewBody,
    author: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> Reviewed:
    """Принять цену такой, какой её увидел человек."""
    repository = ReplyRepository(session)
    reply = await repository.reply(reply_id)

    await repository.confirm(
        reply,
        by=author.email,
        price_white=body.price_white,
        price_grey=body.price_grey,
        currency=body.currency,
        payment_methods=body.payment_methods,
    )

    price = body.price_white if body.price_white is not None else body.price_grey
    domain_id = await repository.domain_of(reply)
    stored = False
    answer: str | None = None
    if price is not None and domain_id is not None:
        await repository.store_price(domain_id=domain_id, price=price, currency=body.currency)
        stored = True
        answer = PLACEMENT_SELLS
    elif body.declines:
        reply.placement = PLACEMENT_DECLINES
        answer = PLACEMENT_DECLINES
    if answer is not None and domain_id is not None:
        await repository.record_seller_answer(domain_id=domain_id, answer=answer, reply_id=reply.id)

    await AccessRepository(session).record(
        AuditAction.PRICE_REVIEWED,
        author_id=author.id,
        target=f"reply:{reply_id}",
        details={
            "действие": "донор не продаёт размещения"
            if body.declines
            else "разбор цены подтверждён",
            "белая": str(body.price_white) if body.price_white is not None else None,
            "серая": str(body.price_grey) if body.price_grey is not None else None,
            "валюта": body.currency,
            "уверенность модели": reply.confidence,
        },
    )
    await session.commit()

    logger.info("разбор: ответ №%s подтверждён, цена в базу — %s", reply_id, stored)
    return Reviewed(
        id=reply_id, reviewed_by=author.email, stored_price=stored, seller_answer=answer
    )
