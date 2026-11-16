"""Письмо на адрес из ответа уходит само: в рабочие часы получателя и в лимитах ящика (16.11.2026).

Письмо уходит без человека — значит, так же, как первые письма очереди, которая уходит сама
(`autosend.py`): окно получателя (`autosend_window.in_window` — пояс по той же стране, что у первого
письма рекламодателю), ящик переписки и его лимиты (`limits.screen`: домен, направление, сниженный
на сутки лимит; разгон — `senders.pick`; пауза между первыми письмами ящика — `autosend.rests`),
молчащий DNS получателя (`recipient_dns.holds`). Отправка — та же (`Sending.send`) со всеми её
проверками: стоп-лист, решение по рекламодателю, подпись, ящик переписки. Своего механизма отправки
нет: здесь только «можно ли сейчас» и что вышло.

**Не сейчас — письмо ждёт, а не теряется.** Вне окна, ящик выбрал лимит или отдыхает, DNS молчит —
письмо остаётся в очереди переписки, причина — строкой решения (её показывает «Диалоги»), следующий
проход пробует снова. В общей очереди писем его нет: пачка и проход очереди берут только первые
письма (`LetterRepository.queued`), а с любого свободного ящика оно ушло бы незнакомцем.

**Человек взял лид, пока письмо ждало, — письмо снимается** с причиной (`lead_redirect.held`):
лид ведёт он, и письмо новому адресату поверх его разговора спутало бы обоих.

Проход (`run`): сначала решения по ответам, которые ждут (`redirect_letter.decide`), потом письма,
которые ждут отправки. Сбой одной строки — откат её сессии и строка журнала; проход идёт дальше.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Select, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.features.contacts.verifier import EmailVerifier
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel, SenderModel
from backend.features.core.models.redirect import LeadRedirectModel
from backend.features.letters import autosend, autosend_window, mailbox, recipient_dns, settle
from backend.features.letters.answers import AnswerRefusedError, thread_sender
from backend.features.letters.redirect_letter import RETRY_AFTER, Asker, decide
from backend.features.letters.repository import LetterRepository
from backend.features.letters.sending import SendError, Sending, SuppressedError
from backend.features.letters.transport import Mail
from backend.features.outreach import health, limits
from backend.features.outreach import senders as sender_rules
from backend.features.outreach.repository import OutreachRepository
from backend.features.replies.lead_redirect import State, held

logger = logging.getLogger(__name__)

STAGE = Stage.ADVERTISERS

#: Сколько строк проход берёт за раз — решений и писем отдельно. Ответов «пишите на …» в день
#: единицы; потолок — чтобы сбой не держал проход минутами.
PASS_LIMIT = 10

#: Чем кончилась попытка отправки — словами журнала и команды.
SENT = "ушло"


async def box_refusal(session: AsyncSession, box: SenderModel | None, now: datetime) -> str | None:
    """Почему ящик переписки сейчас не пишет первое письмо — словами; `None` — пишет.

    Те же правила, что у выбора ящика первого письма (`mailbox._free_box`), — только для одного
    ящика: ящик и его домен не на паузе, лимиты домена и направления, сниженный лимит, разгон
    и пауза между первыми письмами ящика у автоотправки. Ящики этапа читаются все: счёт домена
    и направления — по всем его ящикам, а не по одному."""
    silence = await mailbox.silence_of(session, box, gone="первого письма")
    if silence is not None or box is None:
        return silence or mailbox.SILENT
    sent = await OutreachRepository(session).sent_today(now=now, first_only=True)
    screened = limits.screen(
        (await session.scalars(select(SenderModel).order_by(SenderModel.id))).all(),
        stage=STAGE,
        sent_today=sent,
        domains=await limits.sending_domains(session),
        direction_limit=outreach_cfg.direction_limit(STAGE.value),
        now=now,
        cuts=await health.cuts(session, STAGE, now),
        direction_unread=outreach_cfg.direction_unread(STAGE.value),
    )
    fit = [one for one in screened.fit if one.id == box.id]
    if not fit:
        return screened.refused.get(box.id) or mailbox.SILENT
    if sender_rules.pick(fit, sent_today=sent, stage=STAGE, now=now) is None:
        return mailbox.spent_words(fit, now) or f"ящик {box.email} выбрал дневной лимит"
    rest = (await autosend.rests(session, STAGE, now)).get(box.id)
    if rest is not None:
        return f"ящик {box.email} отдыхает между первыми письмами — до {rest:%H:%M} UTC"
    return None


async def _not_now(session: AsyncSession, message: MessageModel, now: datetime) -> str | None:
    """Почему письмо не уходит в эту минуту — словами; `None` — уходит: окно получателя, ящик
    переписки и его лимиты, молчащий DNS домена получателя."""
    letter = await LetterRepository(session).letter(message.id)
    opened = await autosend_window.in_window(session, [letter], now)
    if message.id not in opened.ids:
        return opened.words
    box_id = await thread_sender(session, message.thread_id or 0)
    refusal = await box_refusal(session, await session.get(SenderModel, box_id), now)
    if refusal is not None:
        return refusal
    if letter.email and await recipient_dns.holds(letter.email, now=now):
        return f"DNS домена {recipient_dns.domain_of(letter.email)} не отвечает — письмо ждёт"
    return None


async def _stop(session: AsyncSession, message: MessageModel, why: str) -> None:
    """Снять письмо из очереди с причиной — только если оно всё ещё там (`settle.leave`)."""
    await settle.leave(
        session,
        message,
        was=MessageStatus.QUEUED,
        status=MessageStatus.STOPPED,
        failure_reason=why[:256],
        next_action_at=None,
    )


async def deliver(
    session: AsyncSession, sending: Sending, row: LeadRedirectModel, *, now: datetime
) -> str:
    """Отправить письмо строки, если сейчас можно. С фиксацией. Возвращает, что вышло, словами."""
    message = await session.get(MessageModel, row.message_id) if row.message_id else None
    if message is None or message.status is not MessageStatus.QUEUED:
        return "письма в очереди нет"
    row.tried_at = now
    taken = await held(session, row.thread_id, letting=message.id)
    if taken is not None:
        await _stop(session, message, taken)
        row.reason = taken
        await session.commit()
        return taken
    try:
        waiting = await _not_now(session, message, now)
        if waiting is None:
            box_id = await thread_sender(session, row.thread_id)
            await sending.send(message.id, from_sender_id=box_id)
    except (SuppressedError, AnswerRefusedError) as exc:
        # Стоп-лист после решения или ящик переписки удалили — ждать письму нечего.
        waiting = str(exc)
        await _stop(session, message, waiting)
    except SendError as exc:
        waiting = str(exc)
    row.reason = waiting
    await session.commit()
    return SENT if waiting is None else waiting


async def _tried(session: AsyncSession, row_id: int, now: datetime) -> None:
    """Строка, на которой решение упало, ждёт паузу `RETRY_AFTER`, а не каждого прохода: откат
    снял и отметку попытки. Не записалась отметка — строка журнала, проход идёт дальше."""
    try:
        await session.execute(
            update(LeadRedirectModel).where(LeadRedirectModel.id == row_id).values(tried_at=now)
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("переадресация: отметка попытки строки №%s не записалась", row_id)


def _due_pending(now: datetime) -> Select[tuple[LeadRedirectModel]]:
    """Решения, которые ждут прохода: ещё не брались или брались раньше `RETRY_AFTER`."""
    return select(LeadRedirectModel).where(
        LeadRedirectModel.state == State.PENDING,
        or_(LeadRedirectModel.tried_at.is_(None), LeadRedirectModel.tried_at <= now - RETRY_AFTER),
    )


def _queued_letters() -> Select[tuple[LeadRedirectModel]]:
    """Решения с письмом, которое ждёт в очереди."""
    return (
        select(LeadRedirectModel)
        .join(MessageModel, MessageModel.id == LeadRedirectModel.message_id)
        .where(LeadRedirectModel.state == State.LETTER, MessageModel.status == MessageStatus.QUEUED)
    )


@dataclass(slots=True)
class Passed:
    """Итог прохода — для журнала и команды: номер переписки → что вышло словами."""

    decided: dict[int, str] = field(default_factory=dict)
    delivered: dict[int, str] = field(default_factory=dict)

    @property
    def anything(self) -> bool:
        return bool(self.decided or self.delivered)


async def _ids(
    session: AsyncSession,
    statement: Select[tuple[LeadRedirectModel]],
    thread_ids: Collection[int] | None,
) -> list[int]:
    if thread_ids is not None:
        statement = statement.where(LeadRedirectModel.thread_id.in_(list(thread_ids)))
    rows = await session.scalars(statement.order_by(LeadRedirectModel.id).limit(PASS_LIMIT))
    return [row.id for row in rows.all()]


async def run(
    session: AsyncSession,
    asker: Asker,
    transports: Mail,
    *,
    now: datetime,
    thread_ids: Collection[int] | None = None,
    verifier: EmailVerifier | None = None,
) -> Passed:
    """Один проход: решения по ответам, которые ждут, потом письма, которые ждут отправки.
    `thread_ids` — только эти переписки (разовый проход командой); пусто — все."""
    passed = Passed()
    for row_id in await _ids(session, _due_pending(now), thread_ids):
        row = await session.get(LeadRedirectModel, row_id)
        if row is None:
            continue
        thread = row.thread_id
        try:
            state = await decide(session, asker, row, now=now, verifier=verifier)
            passed.decided[thread] = row.reason or state.value
        except Exception:
            await session.rollback()
            logger.exception("переадресация: решение по ответу строки №%s упало", row_id)
            await _tried(session, row_id, now)
    sending = Sending(session, transports, now=now)
    for row_id in await _ids(session, _queued_letters(), thread_ids):
        row = await session.get(LeadRedirectModel, row_id)
        if row is None:
            continue
        thread = row.thread_id
        try:
            passed.delivered[thread] = await deliver(session, sending, row, now=now)
        except Exception:
            await session.rollback()
            logger.exception("переадресация: письмо строки №%s не отправилось", row_id)
    return passed
