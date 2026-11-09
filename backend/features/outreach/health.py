"""Мягкие сигналы ящика и окно его последних писем (Ф4, срез 4.5b) — у всех этапов.

`deferred` («приду позже») и мягкий отказ (`blocked`) говорят, что ящику пора сбавить ход,
задолго до отказов. Каждый — строка журнала здоровья; набралось `events` за сутки — лимит
ящика на сутки снижен (`cut`, фильтр `limits.screen`), тоже строкой журнала.

Пауза — по окну последних писем ящика (`window`), а не за всю жизнь: доля отказов или жалоб
не ниже порога от окна целиком — пауза с причиной словами, одна на ящик и с тревогой человеку
(`autopause.py`).

**Правило одно у всех этапов** (02.11.2026). У доноров и рекламодателей стояла доля отказов за всю
жизнь ящика от 50 писем: при их объёме до 50 не дошёл ни один из двадцати ящиков, и защиты не было
при десяти процентах отказов. Доля за жизнь к тому же размывается: ящик с тысячей хороших писем не
встал бы и после сотни отказов подряд. Окно этого лишено, а порог — число отказов в нём: три отказа
ставят паузу и новому ящику. Продажи, пока модуль не ответил о политике (договор моста), не судятся:
окно считается заново на каждом отказе, и следующий отказ осудит и этот. Снижение лимита пришло к
донорам и рекламодателям вместе с окном: сигналов о ящике у них единицы на сотню писем, и порог в
три за сутки срабатывает только на настоящей беде.

**Окно — с последней автопаузы** (слово Anthony 03.11.2026). Ящик встал, человек разобрал и
снял паузу — прежние отказы и жалобы больше не в счёт: до этого окно их помнило, и первый же
отказ после включения, даже по старому письму, снова ставил ящик на паузу, — вторая тревога о
том, что человек уже разобрал. Пока ящик стоит, он не пишет, поэтому граница — строка паузы в
журнале (`paused`), а не минута включения: так же и для включения кнопкой (разгон заново), и
для снятия из консоли (разгон на месте), и для ящиков, включённых до этой правки. Паузу снова
ставят только новые: три отказа писем, ушедших после неё, или жалоба после неё.

**Против ящика — только то, что о ящике** (02.11.2026). Письмо, не дошедшее до сервера получателя
(`delivery_trouble.NOT_THE_BOX`: его DNS молчит или сервер не отвечает), о нашей репутации не
говорит: его никто не видел. С 09.11.2026 туда же — сервер, который почту извне не берёт и просит
вход: это его настройка, а не наш адрес. Сбой хостинга получателей дал одному письму три
отсрочки и отказ — лимит ящика падал вдвое от одного чужого домена, а три таких отказа в окне
ставили ящик на паузу.
Такая отсрочка в журнал не пишется, такой отказ в окне не считается. Вид — по тексту письма,
а не по записанному: письма, ушедшие до этой правки, судятся так же, без правки данных.

Повтор события — не второй сигнал: платформа доставляет пачку «хотя бы один раз» и шлёт её
снова на любой не-2xx. Строка самого события несёт его номер у платформы (`event_id`):
событие, уже лежащее в журнале, слышано — ни второй строки, ни второго вердикта. Гонку двух
доставок одной пачки держит уникальный индекс: вставка проигравшей падает в своей точке
сохранения, а не роняет вебхук — иначе платформа повторила бы всю пачку, с событиями доноров.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import stages
from backend.features.core.box_rules import SoftSignals
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel, SenderHealthModel, SenderModel
from backend.features.letters.delivery_trouble import NOT_THE_BOX, reason_of, refused_kind
from backend.features.outreach.autopause import AutoPause, pause_once, window_advice
from backend.features.outreach.senders import warmup_state

if TYPE_CHECKING:
    from backend.features.letters.events import DeliveryEvent

logger = logging.getLogger(__name__)

__all__ = ["JUDGED", "SOFT_KINDS", "SoftSignals", "cuts", "listen"]

#: Мягкие сигналы: «приду позже» и мягкий отказ принимающего сервера.
SOFT_KINDS = ("deferred", "blocked")
#: События, после которых ящик судится по окну последних писем.
JUDGED = frozenset({"bounce", "dropped", "blocked", "spamreport"})
DAY = timedelta(days=1)
#: Уникальный индекс номера события (`SenderHealthModel`): его отказ — «событие уже записано».
EVENT_INDEX = "uq_sender_health_event"
#: Строка журнала автопаузы: с неё окно ящика начинается заново (`_window_start`).
PAUSED = "paused"


def _note(
    session: AsyncSession,
    sender: SenderModel,
    kind: str,
    detail: str,
    at: datetime,
    event_id: str | None = None,
) -> None:
    session.add(
        SenderHealthModel(
            sender_id=sender.id, kind=kind, detail=detail[:256], at=at, event_id=event_id
        )
    )


async def listen(
    session: AsyncSession, box_id: int, event: DeliveryEvent, moment: datetime
) -> AutoPause | None:
    """Событие доставки письма с ящика `box_id` — глазами политики его этапа: пауза, которую оно
    поставило (ящик, этап и почему), или `None`.

    Событие, которое журнал уже знает по номеру, слышано сразу, до записи: повтор пачки не судит
    ящик дважды."""
    sender = await session.get(SenderModel, box_id)
    rules = None if sender is None else await _rules(session, sender)
    if sender is None or rules is None:
        return None
    if await _known(session, event.event_id):
        return None
    at = event.at or moment
    if not await _noted(session, sender, rules, event, at):
        return None
    judged = event.kind in JUDGED and sender.enabled
    why = await _verdict(session, sender, rules, at) if judged else None
    return await _paused(session, sender, rules, why, at)


async def _paused(
    session: AsyncSession, sender: SenderModel, rules: SoftSignals, why: str | None, at: datetime
) -> AutoPause | None:
    """Пауза по вердикту — одна на ящик (`autopause.pause_once`) и строкой журнала, совет — по
    окну правила. `None` — вердикта нет или ящик уже остановили рядом."""
    if why is None or not await pause_once(session, sender, why, now=at):
        return None
    _note(session, sender, PAUSED, why, at)
    return AutoPause(sender.email, sender.stage, why, window_advice(rules.window))


async def _known(session: AsyncSession, event_id: str | None) -> bool:
    """Журнал уже знает событие с этим номером: платформа повторила пачку."""
    if event_id is None:
        return False
    found = await session.scalar(
        select(SenderHealthModel.id).where(SenderHealthModel.event_id == event_id)
    )
    return found is not None


async def _noted(
    session: AsyncSession,
    sender: SenderModel,
    rules: SoftSignals,
    event: DeliveryEvent,
    at: datetime,
) -> bool:
    """Строки самого события: мягкий сигнал (и снижение лимита на сутки) или жалоба.
    `False` — строку события только что записала другая доставка той же пачки."""
    row = _row(event)
    if row is None:
        return True
    kind, detail = row
    if not await _written(session, sender, kind, detail, at, event.event_id):
        return False
    if kind in SOFT_KINDS and await _soft_today(session, sender.id, at) == rules.events:
        cut = f"лимит снижен на сутки: {rules.events} мягких отказа"
        _note(session, sender, "limit_cut", cut, at)
    return True


def _row(event: DeliveryEvent) -> tuple[str, str] | None:
    """Строка журнала самого события — вид и подробность: мягкий сигнал или жалоба. Сигнал о
    стороне получателя (`NOT_THE_BOX`) — не о ящике, строки нет; жалоба — всегда о нём."""
    signal = _signal(event.kind, event.soft)
    if signal is not None:
        if reason_of(event.reason, status=event.status).kind in NOT_THE_BOX:
            return None
        return signal, event.reason or signal
    return ("complaint", "жалоба на спам") if event.kind == "spamreport" else None


async def _written(
    session: AsyncSession,
    sender: SenderModel,
    kind: str,
    detail: str,
    at: datetime,
    event_id: str | None,
) -> bool:
    """Записать строку события. С номером — в своей точке сохранения: вставку, которую
    отверг уникальный индекс (ту же пачку только что записала другая доставка), откатывает
    она одна — транзакция вебхука жива, остальные события пачки идут дальше. Такой отказ —
    `False`; любой другой отказ базы — как был, исключением."""
    if event_id is None:
        _note(session, sender, kind, detail, at)
        return True
    try:
        async with session.begin_nested():
            _note(session, sender, kind, detail, at, event_id)
            await session.flush()
    except IntegrityError as exc:
        if EVENT_INDEX not in str(exc.orig):
            raise
        logger.info("сигналы ящика: событие %s уже записала другая доставка пачки", event_id)
        return False
    return True


async def _rules(session: AsyncSession, sender: SenderModel) -> SoftSignals | None:
    """Пороги этапа ящика; модуль продаж не ответил — ящик не судится (мост сказал почему)."""
    try:
        return (await stages.mail_policy(session, sender.stage, f"Ящик {sender.email}")).soft
    except stages.SalesNotConnectedError as exc:
        logger.info("сигналы ящика: ящик не судится — %s", exc)
        return None


def _signal(kind: str, soft: bool) -> str | None:
    """Мягкий ли это сигнал: `deferred` или мягкий отказ (`blocked`, отказ с `type: blocked`)."""
    if kind == "deferred":
        return "deferred"
    return "blocked" if kind == "blocked" or (kind == "bounce" and soft) else None


def _soft_day(at: datetime) -> tuple[ColumnElement[bool], ...]:
    """Мягкий сигнал за сутки до `at` — одно условие на снижение и на счёт."""
    return SenderHealthModel.kind.in_(SOFT_KINDS), SenderHealthModel.at > at - DAY


async def _soft_today(session: AsyncSession, sender_id: int, at: datetime) -> int:
    found = await session.scalar(
        select(func.count()).where(SenderHealthModel.sender_id == sender_id, *_soft_day(at))
    )
    return int(found or 0)


async def _verdict(
    session: AsyncSession, sender: SenderModel, rules: SoftSignals, at: datetime
) -> str | None:
    """Пауза по окну последних писем: причина словами или `None`. Отказы — только те, что о
    ящике: вид судит Python по тексту письма, в окне не больше `window` строк, и только письма,
    ушедшие после последней автопаузы (`_window_start`). Жалобы — с первого письма окна, то есть
    тоже после неё."""
    letters = select(
        MessageModel.status,
        MessageModel.sent_at,
        MessageModel.failure_reason,
        MessageModel.deferred_kind,
        MessageModel.deferred_reason,
    ).where(MessageModel.sender_id == sender.id, MessageModel.sent_at.is_not(None))
    start = await _window_start(session, sender.id)
    if start is not None:
        letters = letters.where(MessageModel.sent_at > start)
    rows = await session.execute(letters.order_by(MessageModel.sent_at.desc()).limit(rules.window))
    last = rows.tuples().all()
    bounced = sum(
        1
        for status, _, reason, kind, said in last
        if status is MessageStatus.BOUNCED
        and refused_kind(reason, deferred_kind=kind, deferred_reason=said) not in NOT_THE_BOX
    )
    since = min((sent_at for _, sent_at, _, _, _ in last if sent_at is not None), default=None)
    complaints = await session.scalar(
        select(func.count()).where(
            SenderHealthModel.sender_id == sender.id,
            SenderHealthModel.kind == "complaint",
            SenderHealthModel.at >= (since or at),
        )
    )
    for count, rate, what in (
        (bounced, rules.bounces, "отказов"),
        (complaints, rules.complaints, "жалоб"),
    ):
        if count and count >= math.ceil(round(rate * rules.window, 9)):
            return f"{what} {count} в окне {rules.window} писем — порог {rate:.1%}: пауза"
    return None


async def _window_start(session: AsyncSession, sender_id: int) -> datetime | None:
    """С какой минуты окно ящика: его последняя автопауза — строка `paused` журнала. Пока ящик
    стоит, он не пишет: всё, что ушло после неё, ушло уже после того, как паузу снял человек.
    Паузы не было — `None`, окно от первого письма."""
    found: datetime | None = await session.scalar(
        select(func.max(SenderHealthModel.at)).where(
            SenderHealthModel.sender_id == sender_id, SenderHealthModel.kind == PAUSED
        )
    )
    return found


async def cuts(session: AsyncSession, stage: Stage, now: datetime) -> dict[int, int]:
    """Сниженные на сегодня лимиты ящиков этапа: `events` мягких отказов за сутки."""
    rules = (await stages.mail_policy(session, stage, f"Выбор ящика «{stage.value}»")).soft
    if rules is None:
        return {}
    rows = await session.execute(
        select(SenderModel, func.count(SenderHealthModel.id))
        .join(SenderHealthModel, SenderHealthModel.sender_id == SenderModel.id)
        .where(SenderModel.stage == stage, *_soft_day(now))
        .group_by(SenderModel.id)
    )
    return {
        sender.id: int(warmup_state(sender, now=now).allowance * rules.cut)
        for sender, count in rows.all()
        if count >= rules.events
    }
