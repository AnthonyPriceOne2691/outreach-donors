"""Диалоги: список и переписка целиком.

Смотреть переписку может каждый, у кого есть доступ к базе: цена,
полученная в письме, — это то же содержимое базы, что и метрики донора.
Ответ в переписке — письмо наружу, и право у него то же, что у отправки
письма из очереди (`send`). Черновик агента — под тем же правом: его пишут,
чтобы отправить, и он стоит денег модели. Файлы к ответу — своим модулем
(`files.py`), под теми же правами.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.agent.schemas import DraftCard
from backend.api.deps import db_session, needs
from backend.api.letters.schemas import SendResult
from backend.api.threads.schemas import AnswerBody, ThreadCard, ThreadView
from backend.features.agent.drafting import (
    DraftRefusedError,
    UnknownDraftReplyError,
    announce,
    draft_answer,
)
from backend.features.agent.drafts import (
    Decider,
    agent_writes,
    drafts_of,
    reject_reasons,
    settle_sent,
)
from backend.features.agent.writer import AgentWriter
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.letters.answers import answer_reply
from backend.features.letters.mailbox import thread_mail
from backend.features.letters.outgoing_store import OutgoingFiles
from backend.features.letters.sending import Sending
from backend.features.letters.transport_factory import Transports, in_use
from backend.features.outreach.repository import OutreachRepository
from backend.features.replies.attachments import ReplyFiles

logger = logging.getLogger(__name__)

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
    drafts = await drafts_of(session, (reply.id for reply in detail.replies))
    return ThreadView.of(
        detail,
        files,
        await thread_mail(session, detail.messages),
        drafts=drafts,
        agent_writes=await agent_writes(session, detail.row.stage),
        agent_reasons=reject_reasons(detail.row.stage),
        # Файлы наших писем — так же, одним запросом и без тел.
        letter_files=await OutgoingFiles(session).listed(m.id for m in detail.messages),
    )


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
            file_ids=body.file_ids,
        )
    # Ответ ушёл мимо кнопки черновика — черновик к нему всё равно закрыт:
    # иначе он висел бы «ждёт человека» над уже отвеченным письмом. Письмо к
    # этому месту уже ушло и записано (`answer_reply`), поэтому сбой закрытия
    # черновика не превращает ответ в «не отправили»: черновик остаётся ждать
    # человека, причина — в журнале.
    try:
        async with session.begin_nested():
            await settle_sent(
                session,
                body.reply_id,
                message_id=outcome.message_id,
                text=body.body,
                by=Decider.of(author),
            )
        await session.commit()
    except Exception as exc:
        # Письмо ушло — сбой черновика его не отменяет. Точка сохранения уже
        # откатила своё; сессию закроет зависимость.
        logger.warning(
            "ответ в переписке №%s ушёл (письмо №%s), а черновик к нему не закрыт",
            thread_id,
            outcome.message_id,
            exc_info=exc,
        )
    return SendResult(id=outcome.message_id, sender_email=outcome.sender_email, real=outcome.real)


@router.post(
    "/{thread_id}/replies/{reply_id}/draft",
    response_model=DraftCard,
    summary="Черновик агента — написать заново",
)
async def redraft(
    thread_id: int,
    reply_id: int,
    force: bool = False,
    _: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> DraftCard:
    """Агент пишет черновик ответа сейчас, переписывая прежний нерешённый.

    Сразу, а не задачей: человек нажал и ждёт текст. Задача очереди пишет
    черновик сама после разбора ответа — кнопка нужна, когда тот не устроил
    или его ещё нет. `force` — «всё же написать», когда бриф этапа решил,
    что отвечать не нужно: решение брифа остаётся в `meta`.
    """
    reply = await session.get(ReplyModel, reply_id)
    if reply is None or reply.thread_id != thread_id:
        raise UnknownDraftReplyError(
            f"В переписке №{thread_id} нет ответа №{reply_id} — черновик писать не к чему"
        )
    writer = AgentWriter()
    try:
        outcome = await draft_answer(session, writer, reply_id, again=True, force=force)
    finally:
        await writer.aclose()
    if outcome.skipped is not None:
        raise DraftRefusedError(f"Черновик не написан: {outcome.skipped}")
    await session.commit()
    await announce(outcome)
    [shown] = await drafts_of(session, [reply_id])
    return DraftCard.of(shown)
