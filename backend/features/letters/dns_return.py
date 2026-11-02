"""Письмо, отбитое молчащим DNS получателя, возвращается в работу (02.11.2026).

Отказ гасит цепочку (`events._bounced`): первое письмо кончается, у добивки срок погашен, а лид
остаётся жив — и молчит, потому что написать ему больше некому. Проверка перед отправкой
(`recipient_dns.py`) такие отказы почти убирает, но не все: у платформы свои резолверы, и домен,
ответивший нам, ей мог не ответить, а письма, отбитые до неё, уже лежат отказами. Чужой сбой не
должен стоить разговора: проход возвращает такие письма туда, откуда они ушли.

**Тем же путём, что «Вернуть в очередь»** (`unknown_outcome._back`): первое письмо — в очередь, его
заберёт сама очередь; добивке возвращается срок на предыдущем письме, и её заберёт проход добивок
с ящика переписки; цепочка кончилась (адресат ответил, отписался) — добивка не возвращается. Ответ
и перезапрос цены не возвращаются вовсе: их отправил человек, и решать снова — ему. Дальше письма
судит обычная отправка: DNS молчит — ждёт, домен в стоп-листе — стоит.

**Не сразу.** Письмо в очереди с событием платформы считается ушедшим (`events._settle_if_pending`:
событие доказывает приём), и запоздавшая отсрочка той же попытки сняла бы его с очереди. События
одной попытки приходят минутами: `SETTLE` их пережидает.

**Второе письмо получателю не уходит.** Отказ «писали» не считается (`attempts.py`): пока письмо
лежит отказом, сборка могла написать сайту на следующий адрес — и в другой рассылке: отбор судит
сайт, а не рассылку. Возврат поставил бы в очередь второе первое письмо, ровно то, от чего правило
«одно письмо за раз». Поэтому условие то же, что у сборки: живое письмо сайта или его ответ
(`attempts.live_letter`, `answered`). В переписке — живое письмо этого шага или дальше: добивка
могла уйти, пока первое письмо откладывалось, и повтор пришёл бы после неё. У продаж получатель —
переписка, а не сайт: два лида одной компании делят домен и законны оба, а второе письмо тому же
лиду сборка не заводит (`sales/queue._leads`). Такие письма остаются отказом и считаются отдельным
числом в журнале прохода: условие проверяется на каждом проходе, пока письмо в пределах срока.

**Лиду, которого закрыли, письмо не возвращается.** Стоп-лист и очистка снимают письма очереди
(`sales/off_queue`), а отказ не трогают. Вернувшееся письмо стояло бы в очереди до конца: проверка
перед отправкой его не пускает, а снять его некому, и одно в окне оно звало бы ложную тревогу.
Поэтому продажи спрашиваются той же проверкой (`stages.check_sales`). «Не готово» письму не мешает:
собранное до правки лида пересоберёт сборка.

**Отметка письма стоит на месте** (`updated_at`). Правку лида продажи узнают сравнением с ней
(`sales/lead_edit.edited`): двинь её возврат, и письмо, собранное со старыми данными, выглядело бы
собранным после правки и ушло бы как есть.

**Один раз и недолго.** Тот же текст отказа дают и домены со сломанной записью MX: DNS им отвечает,
проверка письмо пропускает, платформа отказывает снова. Вторая попытка отличает одно от другого,
третья — письмо по кругу. Что письмо уже возвращали, видно без отдельной колонки: его первая
отсрочка (`deferred_since`) остаётся и старше нынешней отправки. Поэтому она при возврате не
очищается, а если отсрочек не было — ставится. Дольше `WINDOW` от начала беды письмо не возвращается.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.features.access.repository import AccessRepository
from backend.features.core import stages
from backend.features.core.domain import AuditAction, MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import attempts, settle
from backend.features.letters.chain import FIRST_STEP, MAX_STEPS
from backend.features.letters.delivery_trouble import Trouble, refused_kind
from backend.features.letters.send_refusals import SuppressedError
from backend.features.letters.unknown_outcome import alive_link, is_followup

logger = logging.getLogger(__name__)

#: Сколько ждать после отправки: события одной попытки приходят минутами (шапка модуля).
SETTLE = timedelta(minutes=30)

#: Дольше этого от начала беды письмо не возвращается.
WINDOW = timedelta(days=3)

#: Первая отсрочка старше отправки на столько — письмо уже возвращали. Часы платформы и наши
#: расходятся на секунды, а повтор уходит не раньше `SETTLE` после прошлой попытки.
RETURNED_BEFORE = timedelta(minutes=10)


@dataclass(slots=True)
class Returned:
    """Итог прохода: вернулось писем и осталось отказом: писать нельзя или у получателя уже есть
    живое письмо."""

    back: int = 0
    taken: int = 0


def _returnable(message: MessageModel) -> bool:
    """Отказ — молчание DNS получателя, и это первая попытка письма."""
    kind = refused_kind(
        message.failure_reason,
        deferred_kind=message.deferred_kind,
        deferred_reason=message.deferred_reason,
    )
    if kind is not Trouble.RECIPIENT_DNS or message.sent_at is None:
        return False
    since = message.deferred_since
    return since is None or message.sent_at - since < RETURNED_BEFORE


async def _closed(session: AsyncSession, message: MessageModel) -> bool:
    """Лиду больше нельзя писать (шапка модуля). Другой отказ проверки письму не мешает: «не готово»
    пересоберёт сборка, а молчащие продажи отправка назовёт сама."""
    try:
        await stages.check_sales(session, message)
    except SuppressedError:
        return True
    except stages.MailRefusalError:
        return False
    return False


async def _taken(session: AsyncSession, message: MessageModel, *, site: bool) -> bool:
    """Возврат был бы вторым письмом получателю (шапка модуля): в переписке есть живое письмо этого
    шага или дальше, а по сайту (`site`) — ещё и живое письмо сайта или ответ с него. Само письмо
    лежит отказом и живым себя не считает."""
    found: list[ColumnElement[bool]] = []
    if message.thread_id is not None:  # `== None` стал бы `IS NULL` — чужие письма
        later = aliased(MessageModel)
        found.append(
            select(later.id)
            .where(
                later.thread_id == message.thread_id,
                later.step >= message.step,
                ~attempts.dead(later),
            )
            .exists()
        )
    if site:
        found.append(attempts.live_letter(message.domain_id))
        found.append(attempts.answered(message.domain_id))
    return bool(found) and bool(await session.scalar(select(or_(*found))))


async def _kept(session: AsyncSession, message: MessageModel) -> bool:
    """Письмо остаётся отказом: лида закрыли или получателю уже идёт живое письмо. Получатель у
    продаж — переписка, у первого письма остальных этапов — сайт."""
    sales = await settle.stage_of(session, message) is Stage.SALES
    if sales and await _closed(session, message):
        return True
    return await _taken(session, message, site=not sales and message.step == FIRST_STEP)


async def _return(session: AsyncSession, message: MessageModel, now: datetime) -> bool:
    """Вернуть письмо в работу; `False` — цепочка кончилась или его вывел другой путь."""
    previous = await alive_link(session, message) if is_followup(message) else None
    if is_followup(message) and previous is None:
        return False
    returned = await settle.leave(
        session,
        message,
        was=MessageStatus.BOUNCED,
        status=MessageStatus.QUEUED,
        sent_at=None,
        delivered_at=None,
        failure_reason=None,
        deferred_at=None,
        deferred_attempts=None,
        deferred_reason=None,
        deferred_kind=None,
        # Остаётся: по ней видно, что письмо уже возвращали (шапка модуля).
        deferred_since=message.deferred_since or message.sent_at,
        # Иначе возврат спрятал бы правку лида (шапка модуля).
        updated_at=message.sent_at,
    )
    if not returned:
        return False
    if previous is not None:
        previous.next_action_at = now
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=None,
        target=f"message:{message.id}",
        details={"действие": "письмо вернулось в работу: отказ из-за молчания DNS получателя"},
        stage=await settle.stage_of(session, message),
    )
    return True


async def return_letters(session: AsyncSession, *, now: datetime) -> Returned:
    """Вернуть в работу письма, отбитые молчащим DNS получателя. Письма идут по одному: два отказа
    одного сайта вернут одно, второе увидит вернувшееся живым. Без фиксации: её делает вызывающий."""
    rows = await session.scalars(
        select(MessageModel)
        .where(
            MessageModel.status == MessageStatus.BOUNCED,
            MessageModel.step < MAX_STEPS,
            MessageModel.sent_at <= now - SETTLE,
            func.coalesce(MessageModel.deferred_since, MessageModel.sent_at) > now - WINDOW,
        )
        .order_by(MessageModel.id)
    )
    report = Returned()
    for message in [found for found in rows.all() if _returnable(found)]:
        if await _kept(session, message):
            report.taken += 1
        elif await _return(session, message, now):
            report.back += 1
    if report.back or report.taken:
        logger.info(
            "письма: отказы из-за молчания DNS получателя — вернулось в работу %s, "
            "осталось отказом %s: писать нельзя или у получателя уже есть живое письмо",
            report.back,
            report.taken,
        )
    return report
