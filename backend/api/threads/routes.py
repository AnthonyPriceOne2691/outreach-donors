"""Диалоги: список и переписка целиком.

Смотреть переписку может каждый, у кого есть доступ к базе: цена,
полученная в письме, — это то же содержимое базы, что и метрики донора.
Ответ в переписке — письмо наружу, и право у него то же, что у отправки
письма из очереди (`send`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.letters.schemas import SendResult
from backend.api.threads.schemas import AnswerBody, ThreadCard, ThreadView
from backend.features.agent.drafts import Decider, settle_sent
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.letters.answers import answer_reply
from backend.features.letters.mailbox import thread_mail
from backend.features.letters.sending import Sending
from backend.features.letters.transport_factory import Transports, in_use
from backend.features.outreach.repository import OutreachRepository
from backend.features.replies.attachments import ReplyFiles

router = APIRouter(prefix="/threads", tags=["диалоги"])

_viewer = Depends(needs(Permission.VIEW))
_sender = Depends(needs(Permission.SEND))


@router.get("", response_model=list[ThreadCard], summary="Список диалогов")
async def all_threads(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> list[ThreadCard]:
    rows = await OutreachRepository(session).threads()
    return [ThreadCard.of(row) for row in rows]


@router.get("/{thread_id}", response_model=ThreadView, summary="Переписка целиком")
async def one_thread(
    thread_id: int,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> ThreadView:
    detail = await OutreachRepository(session).thread(thread_id)
    # Вложения — одним запросом на всю переписку и без самих файлов:
    # карточке нужны имена и размеры, а не мегабайты прайсов.
    files = await ReplyFiles(session).listed(reply.id for reply in detail.replies)
    return ThreadView.of(detail, files, await thread_mail(session, detail.messages))


@router.post("/{thread_id}/answer", response_model=SendResult, summary="Ответить в переписке")
async def answer(
    thread_id: int,
    body: AnswerBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> SendResult:
    """Наш ответ на ответ собеседника — и отправка сразу, тем ящиком, что начал
    переписку, на адрес, с которого ответили, веткой к его письму.

    Право — то же, что у отправки письма из очереди: это письмо наружу.
    """
    async with in_use(Transports()) as transports:
        outcome = await answer_reply(
            session,
            Sending(session, transports),
            thread_id=thread_id,
            reply_id=body.reply_id,
            body=body.body,
            author_id=author.id,
        )
    # Ответ ушёл мимо кнопки черновика — черновик к нему всё равно закрыт:
    # иначе он висел бы «ждёт человека» над уже отвеченным письмом.
    await settle_sent(
        session,
        body.reply_id,
        message_id=outcome.message_id,
        text=body.body,
        by=Decider(name=author.email, user_id=author.id),
    )
    await session.commit()
    return SendResult(id=outcome.message_id, sender_email=outcome.sender_email, real=outcome.real)
