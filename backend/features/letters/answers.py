"""Наш ответ в переписке: письмо собеседнику на его ответ.

До 04.10.2026 из переписки уходили только первое письмо и добивки — ответить
донору, назвавшему цену, или рекламодателю, заинтересовавшемуся офером, было
нельзя ничем, кроме своей почты мимо системы. Ответ — то же письмо в
`messages` и та же отправка (`sending.Sending`), со всеми её проверками:
стоп-лист, решение по донору, готовность настроек, лимиты ящика.

Что отличает ответ от добивки:

- **Шаг вне цепочки** (`chain.ANSWER_STEP`): после ответа добивок нет —
  разговор ведёт человек, и напоминание поверх его письма выглядело бы спамом.
- **Ящик — тот, что начал переписку.** Сменить его посреди разговора значит
  попасть в спам и запутать собеседника. Ящика нет (удалён) или он не пишет —
  ответ не уходит с другого, а отказ говорит почему; в общей очереди писем
  ответа нет, и пачка его не берёт (`mailbox.py`).
- **Адрес — тот, с которого ответили** (приём запоминает его контактом):
  ответить могли с другого ящика, чем тот, на который ушло первое письмо.
- **Ветка — к письму собеседника** (`In-Reply-To` его `Message-ID`): у него
  ответ ляжет под его же письмом, а не отдельным разговором.

Метрик Ahrefs в ответе быть не может, как в любом нашем письме
(`guards.assert_no_metrics`): текст пишет человек, и «у сайта DR 45» —
ровно то, что он написал бы, отвечая на вопрос о площадке.

**Ответ лиду продаж** — тот же путь, а о лиде почта спрашивает мост к модулю
продаж (`core/stages.answer_text`) до заведения письма: продажи не подключены —
отказ словами, чего не хватает, и ответ в очереди не остаётся; текст уходит с
подписью и физическим адресом из настроек отправителя продаж, адрес — лида.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.core.stages import answer_text
from backend.features.letters import guards
from backend.features.letters.chain import ANSWER_STEP, FIRST_STEP
from backend.features.letters.sending import SendError, Sending, SendOutcome

#: Длина ответа. Не про письмо человеку — про ошибку вставки: ответ на десятки
#: тысяч знаков почти наверняка вставлен по ошибке целым документом.
MAX_BODY = 20_000


class UnknownAnswerTargetError(LookupError):
    """Ответа с таким номером в этой переписке нет."""


class AnswerRefusedError(SendError):
    """Отвечать на это нельзя: не ответ человека, пустой текст, ответ уже ушёл."""


@dataclass(frozen=True, slots=True)
class _Context:
    reply: ReplyModel
    thread: ThreadModel
    stage: Stage
    host: str


def answer_key(stage: Stage, host: str, reply_id: int) -> str:
    """Ключ письма-ответа: один ответ на один входящий ответ.

    Повторный щелчок «Отправить» упирается в тот же ключ и второго письма
    собеседнику не даёт.
    """
    return f"{stage.value}:{host}:answer:{reply_id}"


def subject_for(incoming: str | None) -> str:
    """Тема ответа — тема его письма с «Re:», без второго «Re:»."""
    subject = (incoming or "").strip()
    if not subject:
        return "Re:"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


async def answer_reply(
    session: AsyncSession,
    sending: Sending,
    *,
    thread_id: int,
    reply_id: int,
    body: str,
    author_id: int | None,
) -> SendOutcome:
    """Ответить собеседнику на его ответ — и отправить сразу.

    Сразу, а не в очередь: текст написал или принял человек, это и есть
    согласование, которое у первого письма делает экран очереди.
    """
    text = body.strip()
    if not text:
        raise AnswerRefusedError("Текст ответа пустой — отправлять нечего")
    if len(text) > MAX_BODY:
        raise AnswerRefusedError(
            f"Ответ длиннее {MAX_BODY} знаков — похоже, вставлен целый документ"
        )
    # Тот же запрет, что у первого письма и у правки в очереди: метрики
    # Ahrefs наружу не уходят ни в каком письме, и ответ — не исключение.
    guards.assert_no_metrics(text)
    found = await _context(session, thread_id=thread_id, reply_id=reply_id)
    # До заведения письма: отказ продажам после него оставил бы ответ в очереди.
    what = f"Ответ в переписке №{thread_id}"
    text = await answer_text(session, found.stage, thread_id, text, what)
    sender_id = await _thread_sender(session, thread_id)
    message = await _materialize(session, found, text)
    await session.commit()
    return await sending.send(
        message.id,
        author_id=author_id,
        from_sender_id=sender_id,
        in_reply_to=found.reply.inbound_message_id,
    )


async def _context(session: AsyncSession, *, thread_id: int, reply_id: int) -> _Context:
    row = (
        await session.execute(
            select(ReplyModel, ThreadModel, CampaignModel.stage, DomainModel.host)
            .join(ThreadModel, ThreadModel.id == ReplyModel.thread_id)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .join(DomainModel, DomainModel.id == ThreadModel.domain_id)
            .where(ReplyModel.id == reply_id, ThreadModel.id == thread_id)
        )
    ).first()
    if row is None:
        raise UnknownAnswerTargetError(
            f"В переписке №{thread_id} нет ответа №{reply_id} — отвечать не на что"
        )
    reply, thread, stage, host = row
    if reply.kind is not ReplyKind.HUMAN:
        raise AnswerRefusedError(
            f"Ответ №{reply_id} — не письмо человека ({reply.kind.value}): автоответчику, "
            "отказу доставки и отписке не отвечают"
        )
    return _Context(reply=reply, thread=thread, stage=stage, host=host)


async def _materialize(session: AsyncSession, found: _Context, text: str) -> MessageModel:
    key = answer_key(found.stage, found.host, found.reply.id)
    existing = await session.scalar(select(MessageModel).where(MessageModel.idempotency_key == key))
    if existing is not None and existing.status is not MessageStatus.QUEUED:
        raise AnswerRefusedError(
            f"На ответ №{found.reply.id} уже ответили — письмо №{existing.id} "
            f"в состоянии «{existing.status.value}»"
        )
    message = existing or MessageModel(
        campaign_id=found.thread.campaign_id,
        thread_id=found.thread.id,
        domain_id=found.thread.domain_id,
        step=ANSWER_STEP,
        status=MessageStatus.QUEUED,
        idempotency_key=key,
        answers_reply_id=found.reply.id,
    )
    message.contact_id = await _answering_contact(session, found)
    message.subject = subject_for(found.reply.subject)
    message.body = text
    session.add(message)
    await session.flush()
    return message


async def _answering_contact(session: AsyncSession, found: _Context) -> int | None:
    """Контакт, с которого ответили; нет его — контакт переписки.

    Приём запоминает адрес ответившего контактом домена (`remember_answering_
    address`), если это не робот; иначе отвечаем туда, куда писали.
    """
    if found.reply.from_email:
        contact_id = await session.scalar(
            select(ContactModel.id).where(
                ContactModel.domain_id == found.thread.domain_id,
                func.lower(ContactModel.email) == found.reply.from_email.strip().lower(),
            )
        )
        if contact_id is not None:
            return int(contact_id)
    return found.thread.contact_id


async def _thread_sender(session: AsyncSession, thread_id: int) -> int:
    """Ящик, с которого ушло первое письмо переписки.

    Нет его — ответ не уходит вовсе: с любого свободного ящика он ушёл бы
    первым письмом от незнакомца посреди разговора (находка ревью 07.10.2026).
    """
    sender_id = await session.scalar(
        select(MessageModel.sender_id)
        .where(
            MessageModel.thread_id == thread_id,
            MessageModel.step == FIRST_STEP,
            MessageModel.sender_id.is_not(None),
        )
        .order_by(MessageModel.id)
        .limit(1)
    )
    if sender_id is None:
        raise AnswerRefusedError(
            f"У переписки №{thread_id} нет ящика, с которого ушло первое письмо: его "
            "удалили, и писать в эту переписку нечем. С другого ящика ответ не уйдёт — "
            "для собеседника это был бы незнакомец посреди разговора"
        )
    return int(sender_id)
