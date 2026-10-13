"""Письмо продаж — с очереди: одно место снятия у правил модуля, и когда снятое пишется заново.

Письмо, которому ждать нечего, не висит в очереди с отказом: проход автоотправки брал бы его раз
в минуту, а очередь из одних таких писем поднимала бы тревогу «отказ каждому письму окна»
(`autosend.Stop.REFUSED_ALL`). Снимает такое письмо одно место (`take_off`): «остановлено» с
причиной словами (`messages.failure_reason`) — условием (`settle.leave`): письмо, которое секундой
раньше взяла отправка, не трогается, и «остановлено» не врёт о письме, которое ушло. Снимают так:

- проход автоотправки — письмо, которому платформа отказала в третий раз (`autosend_refusals.py`,
  решение владельца 13.10); насовсем: сборка такое письмо не пересобирает;
- очистка и стоп-лист продаж — письмо лида, которого сняли после сборки письма: очистка отклонила
  (перепроверка адреса, второй `unknown`, другое направление) или закрыл стоп-лист продаж
  (`stoplist_leads.close`). В момент отклонения (`of_leads`), с причиной `LEAD_CLOSED` и словами
  очистки — до этого письмо висело с отказом «стоп-лист» на каждом проходе.

**Снятое до отправки пишется заново на том же месте** (`again`). Письмо снятого лида и письмо,
которое общая отправка сняла, потому что сайт в работе у другого направления
(`letters/site_role.py`, `attempts.elsewhere_reason`), не уходили. Лид снова «готов» (запись
стоп-листа сняли, и очистка его пропустила) и сайт свободен (`elsewhere.py`) — сборка пишет ему
первое письмо заново (`queue._write`): тот же диалог и тот же ключ письма, так что второго письма
тому же человеку не будет. Письмо с нашим `Message-ID` прежней попытки могло уйти — его сборка не
трогает. Письмо, снятое после отказов платформы, — тоже: его снял не лид, а платформа.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.orm.util import AliasedClass

from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import attempts, settle
from backend.features.letters.chain import FIRST_STEP
from backend.features.sales.models import LeadStatus, SalesLeadModel, SalesThreadModel

logger = logging.getLogger(__name__)

#: Длина причины у письма (`messages.failure_reason`).
REASON_LENGTH = 256
#: Начало причины у письма лида, которого сняли после сборки письма: по нему сборка узнаёт
#: письмо, которое пишет заново, когда лид снова «готов». Текст не менять.
LEAD_CLOSED = "лида сняли после сборки письма — снято с очереди"
#: Причины письма, которое общая отправка сняла: сайт в работе у другого направления.
ELSEWHERE = tuple(attempts.elsewhere_reason(stage) for stage in Stage)

#: Письмо — или его псевдоним в подзапросе.
_Letter = type[MessageModel] | AliasedClass[MessageModel]


async def take_off(session: AsyncSession, letter: MessageModel, said: str) -> bool:
    """Письмо из очереди — в «остановлено» с причиной `said`; ложь — его секундой раньше взяла
    другая отправка, и оно не тронуто."""
    return await settle.leave(
        session,
        letter,
        was=MessageStatus.QUEUED,
        status=MessageStatus.STOPPED,
        next_action_at=None,
        failure_reason=said[:REASON_LENGTH],
    )


def closed(note: str | None) -> str:
    """Причина у письма снятого лида: `LEAD_CLOSED` и слова, почему лида сняли."""
    return f"{LEAD_CLOSED}: {note}" if note else LEAD_CLOSED


async def of_leads(session: AsyncSession, notes: Mapping[int, str | None]) -> int:
    """Первые письма лидов `notes` (номер лида → почему сняли, словами очистки), ждущие в
    очереди, — с очереди с причиной `closed`. Сколько снято. Коммит — за вызывающим."""
    if not notes:
        return 0
    rows = await session.execute(
        select(MessageModel, SalesThreadModel.lead_id)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .where(
            SalesThreadModel.lead_id.in_(sorted(notes)),
            MessageModel.step == FIRST_STEP,
            MessageModel.status == MessageStatus.QUEUED,
        )
        .order_by(MessageModel.id)
    )
    taken = 0
    for letter, lead_id in rows.tuples().all():
        taken += await take_off(session, letter, closed(notes[lead_id]))
    if taken:
        logger.info("продажи: письма снятых лидов — с очереди", extra={"taken": taken})
    return taken


def again(letter: _Letter) -> ColumnElement[bool]:
    """Первое письмо снято до отправки — лида сняли или сайт был в работе у другого
    направления, — не уходило (нашего `Message-ID` нет), а лид его диалога снова «готов»: сборка
    пишет его заново на том же месте (сайт свободен — это судит сборка, `elsewhere.py`). Лид —
    здесь же, условием: сборку, начатую до снятия лида, письмо снятого лида не воскресит."""
    ready = (
        select(SalesThreadModel.lead_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(SalesThreadModel.thread_id == letter.thread_id)
        .where(SalesLeadModel.status == LeadStatus.READY)
        .exists()
    )
    return and_(
        letter.step == FIRST_STEP,
        letter.status == MessageStatus.STOPPED,
        letter.internet_message_id.is_(None),
        or_(
            letter.failure_reason.in_(ELSEWHERE),
            letter.failure_reason.startswith(LEAD_CLOSED, autoescape=True),
        ),
        ready,
    )


def unwritten(link: type[SalesThreadModel]) -> ColumnElement[bool]:
    """Лиду собирается первое письмо: диалога нет (`link` — связь, присоединённая внешним
    соединением) — или первое письмо диалога снято до отправки (`again`)."""
    first = aliased(MessageModel)
    returned = select(first.id).where(first.thread_id == link.thread_id, again(first)).exists()
    return or_(link.thread_id.is_(None), returned)
