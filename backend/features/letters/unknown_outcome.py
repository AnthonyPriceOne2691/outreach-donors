"""Исход неизвестен: письмо «отправляется», а ушло ли оно — неизвестно.

Так выглядит обрыв связи посреди передачи письма почте (`Sending._hand_over`):
платформа могла принять письмо до того, как потерялся её ответ, ключа
идемпотентности у неё нет, и повтор вслепую — второе письмо тому же донору.
Поэтому письмо остаётся в «отправляется», и до 07.10.2026 из этого состояния
его не выводило ничто — ни экран, ни кнопка, ни автоматика. С отправкой
очереди пачкой (#191) это стало дырой: зависшее письмо держит своего
адресата навсегда — второе письмо ему не собирается (`attempts.PENDING`),
добивка не идёт.

Выходов три, и ушедшим письмо записывается тем же путём, что обычная
отправка (`settle.py`):

- **событие платформы** по письму (`events.py`) — доказательство, что
  платформа письмо приняла: наш номер письма уезжает в `custom_args`
  и возвращается в каждом её событии, а события по письму, которого она
  не принимала, ей взять неоткуда. Дальше событие делает своё, как
  с любым ушедшим письмом: «доставлено», «не дошло», жалоба;
- **«Ушло»** — человек нашёл письмо в журнале платформы;
- **«Вернуть в очередь»** — не нашёл: письмо снова ждёт отправки. Если оно
  всё же ушло, адресат получит его второй раз — окно подтверждения на экране
  говорит это прямо.

**Время ухода — начало передачи, а не минута решения.** Человек может
разобрать письмо назавтра, а ушло оно тогда, когда его отдали почте: от этого
времени считаются срок добивки и дневной лимит ящика. Начало передачи —
`updated_at` письма в «отправляется»: захват (`Sending._claim`) — последняя
запись в его строку, следующая выводит письмо из этого состояния.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, MessageStatus, Stage, ThreadStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderModel,
    ThreadModel,
)
from backend.features.letters import settle
from backend.features.letters.chain import ANSWER_STEP, CHAINABLE, FIRST_STEP, MAX_STEPS, kind_of
from backend.features.letters.repository import UnknownLetterError
from backend.features.letters.sendgrid import SendGridTransport

logger = logging.getLogger(__name__)

#: Сколько письмо висит в «отправляется», прежде чем его исход решает человек.
#: Обычная передача держит письмо там секунды, худшая — три попытки по 30 с
#: и паузы до 30 с между ними (`sendgrid.py`), две с половиной минуты. Раньше
#: решать нельзя: письмо может быть ещё в пути, и «вернуть в очередь» отправило
#: бы его второй раз рядом с идущей передачей. Пять минут — вдвое больше худшей
#: передачи, и событие платформы за это время обычно успевает решить исход само.
STUCK_AFTER = timedelta(minutes=5)
#: То же в минутах — для слов экрана и остановки пачки.
STUCK_MINUTES = int(STUCK_AFTER.total_seconds() // 60)

#: Потолок списка — как у очереди на экране (`LetterRepository.queued`):
#: десятки зависших писем бывают только при поломке, и тогда смотрят первые.
STUCK_LIMIT = 200

#: Чем письмо уходило. Исход бывает неизвестен только у настоящей платформы:
#: нулевой транспорт никуда не передаёт и не обрывается.
PLATFORM = SendGridTransport.name

#: Состояния словами экрана (`MESSAGE_STATUSES` фронта) — для отказа.
_STATUS_WORDS = {
    MessageStatus.QUEUED: "в очереди",
    MessageStatus.SENT: "принято платформой",
    MessageStatus.DELIVERED: "доставлено",
    MessageStatus.BOUNCED: "отказ доставки",
    MessageStatus.STOPPED: "остановлено",
}


class Outcome(StrEnum):
    """Что человек нашёл в журнале платформы."""

    SENT = "sent"
    QUEUED = "queued"


class ResolveError(RuntimeError):
    """Исход письма решать нельзя: он уже известен или письмо может быть в пути."""


@dataclass(frozen=True, slots=True)
class StuckLetter:
    """Письмо в «отправляется» и то, по чему его ищут в журнале платформы."""

    message: MessageModel
    host: str
    email: str | None
    sender_email: str | None
    campaign: str
    #: Когда началась передача почте.
    since: datetime

    @property
    def what(self) -> str:
        """Какое это письмо — словами: решение по добивке и ответу разное."""
        return kind_of(self.message.step)


@dataclass(frozen=True, slots=True)
class Resolution:
    """Чем кончилось решение человека — состоянием и словами для экрана."""

    letter: StuckLetter
    status: MessageStatus
    said: str


def _letters() -> Select[Any]:
    return (
        select(
            MessageModel,
            DomainModel.host,
            ContactModel.email,
            SenderModel.email,
            CampaignModel.name,
        )
        .join(DomainModel, DomainModel.id == MessageModel.domain_id)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .outerjoin(ContactModel, ContactModel.id == MessageModel.contact_id)
        .outerjoin(SenderModel, SenderModel.id == MessageModel.sender_id)
    )


def _stuck_letter(row: Any) -> StuckLetter:
    message, host, email, sender_email, campaign = row
    return StuckLetter(
        message=message,
        host=host,
        email=email,
        sender_email=sender_email,
        campaign=campaign,
        since=message.updated_at,
    )


async def stuck(
    session: AsyncSession, *, stage: Stage, now: datetime | None = None, limit: int = STUCK_LIMIT
) -> list[StuckLetter]:
    """Письма этапа, висящие в «отправляется» дольше `STUCK_AFTER`, старые первыми."""
    edge = (now or datetime.now(UTC)) - STUCK_AFTER
    rows = await session.execute(
        _letters()
        .where(
            MessageModel.status == MessageStatus.SENDING,
            CampaignModel.stage == stage,
            MessageModel.updated_at <= edge,
        )
        .order_by(MessageModel.updated_at, MessageModel.id)
        .limit(limit)
    )
    return [_stuck_letter(row) for row in rows.all()]


async def _one(session: AsyncSession, letter_id: int) -> StuckLetter:
    found = (await session.execute(_letters().where(MessageModel.id == letter_id))).first()
    if found is None:
        raise UnknownLetterError(f"Письма №{letter_id} нет")
    return _stuck_letter(found)


def _witness(letter: StuckLetter, *, how: str) -> settle.Witness:
    return settle.Witness(
        host=letter.host,
        email=letter.email,
        sender_email=letter.sender_email,
        transport=PLATFORM,
        real=True,
        how=how,
    )


async def settle_by_event(session: AsyncSession, message: MessageModel, *, kind: str) -> bool:
    """Событие платформы по письму в «отправляется»: записать его ушедшим.

    Ложь — его секундой раньше вывел другой путь; тогда письмо перечитывается,
    и событие применяется к тому, что в базе, а не к прочитанному до захвата.
    """
    letter = await _one(session, message.id)
    done = await settle.record_sent(
        session,
        message,
        moment=letter.since,
        provider_id=None,
        author_id=None,
        witness=_witness(letter, how=f"событие платформы «{kind}»"),
    )
    if not done:
        await session.refresh(message)
        return False
    logger.info(
        "письма: письмо №%s — исход выяснен событием платформы «%s»: ушло", message.id, kind
    )
    return True


async def resolve(
    session: AsyncSession,
    letter_id: int,
    outcome: Outcome,
    *,
    author_id: int | None,
    now: datetime | None = None,
) -> Resolution:
    """Решение человека по журналу платформы. Без фиксации — её делает вызывающий."""
    moment = now or datetime.now(UTC)
    letter = await _one(session, letter_id)
    _check(letter, moment)
    if outcome is Outcome.SENT:
        done = await settle.record_sent(
            session,
            letter.message,
            moment=letter.since,
            provider_id=None,
            author_id=author_id,
            witness=_witness(letter, how="человек нашёл письмо в журнале платформы"),
        )
        status: MessageStatus | None = MessageStatus.SENT if done else None
    else:
        status = await _back(session, letter, author_id=author_id, moment=moment)
    if status is None:
        raise ResolveError(
            f"Письмо №{letter_id} секундой раньше вышло из «отправляется» другим путём — "
            "обновите экран: его исход уже записан"
        )
    logger.info("письма: исход письма №%s решён человеком — %s", letter_id, status.value)
    return Resolution(letter=letter, status=status, said=_said(letter, status))


def _check(letter: StuckLetter, moment: datetime) -> None:
    """Решать можно только письмо в «отправляется», которое уже не может быть в пути."""
    message = letter.message
    if message.status is not MessageStatus.SENDING:
        words = _STATUS_WORDS.get(message.status, message.status.value)
        raise ResolveError(
            f"Письмо №{message.id} уже не «отправляется», а «{words}»: его исход известен, "
            "решать нечего"
        )
    if letter.since > moment - STUCK_AFTER:
        raise ResolveError(
            f"Письмо №{message.id} передано почте меньше {STUCK_MINUTES} минут назад — оно "
            "может быть ещё в пути, и платформа может сообщить о нём сама. Решать его исход "
            f"руками — спустя {STUCK_MINUTES} минут после передачи"
        )


async def _back(
    session: AsyncSession, letter: StuckLetter, *, author_id: int | None, moment: datetime
) -> MessageStatus | None:
    """«Вернуть в очередь»: письмо снова ждёт отправки — тем путём, каким уходит его вид.

    Первое письмо ждёт в очереди экрана писем, ответ — в карточке переписки,
    откуда его отправляют повтором. Добивку отправляет проход добивок
    (`followups.py`): с ящика переписки и в её ветку, а в общей очереди её нет
    (`mailbox.py`). Поэтому у добивки назад возвращается и срок — на предыдущем
    письме цепочки, и проход заберёт её в ближайшую минуту. Цепочка кончилась,
    пока письмо висело (адресат ответил, отписался, прежнее письмо не дошло), —
    добивка не пойдёт вовсе.
    """
    message = letter.message
    previous = await _alive_link(session, message) if _is_followup(message) else None
    status = (
        MessageStatus.STOPPED
        if _is_followup(message) and previous is None
        else MessageStatus.QUEUED
    )
    # Идентификатор уходит вместе с ящиком, как при отказе почты
    # (`Sending._hand_over`): повтор может пойти с другого домена.
    if not await settle.leave_sending(
        session, message, status=status, sender_id=None, internet_message_id=None
    ):
        return None
    if previous is not None:
        previous.next_action_at = moment
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author_id,
        target=f"message:{message.id}",
        details={
            "действие": "исход неизвестен — письмо не нашлось в журнале платформы",
            "донор": letter.host,
            "кому": letter.email,
            "письмо": letter.what,
            "стало": _STATUS_WORDS[status],
        },
    )
    return status


def _is_followup(message: MessageModel) -> bool:
    return FIRST_STEP < message.step < MAX_STEPS


async def _alive_link(session: AsyncSession, message: MessageModel) -> MessageModel | None:
    """Предыдущее письмо цепочки, если цепочка жива: ушло, не отбилось, переписка
    не кончилась ответом или отпиской. Проход добивок берёт срок с него."""
    if message.thread_id is None:
        return None
    found: MessageModel | None = await session.scalar(
        select(MessageModel)
        .join(ThreadModel, ThreadModel.id == MessageModel.thread_id)
        .where(
            MessageModel.thread_id == message.thread_id,
            MessageModel.step == message.step - 1,
            MessageModel.status.in_(CHAINABLE),
            ThreadModel.status == ThreadStatus.OPEN,
        )
        .order_by(MessageModel.id)
        .limit(1)
    )
    return found


def _said(letter: StuckLetter, status: MessageStatus) -> str:
    """Что стало с письмом — словами для экрана."""
    if status is MessageStatus.SENT:
        return "отмечено ушедшим — сроки добивок идут от начала передачи"
    if status is MessageStatus.STOPPED:
        return (
            "добивка не пойдёт: цепочка кончилась, пока письмо висело — адресат ответил, "
            "отписался или прежнее письмо не дошло"
        )
    if _is_followup(letter.message):
        return "добивка вернулась в цепочку и уйдёт с ближайшим проходом добивок, с того же ящика"
    if letter.message.step >= ANSWER_STEP:
        return (
            "ответ не ушёл и ждёт: отправить его снова можно из карточки переписки — "
            "с ящика переписки и веткой к письму собеседника"
        )
    return "письмо вернулось в очередь — отправить его можно отсюда же"
