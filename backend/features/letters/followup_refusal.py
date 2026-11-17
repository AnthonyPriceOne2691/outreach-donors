"""Отказ почты добивке — след у строки добивки: сторож «ящик молчит» видит и такую тишину.

После отказа почты срок добивки уходит на час вперёд (`followups._deliver`, `POSTPONE`): спрашивать
чаще значит долбить почту. Но добивку со сроком в будущем сторож (`ops/mail_watch._quiet`) считал
«ещё не подошедшей», и ящик, которому почта отказывает, для него не молчал: тревога приходила, только
если подошедших было больше, чем проход перекладывает за десять минут. Поэтому отказ почты оставляет
след у строки добивки — причину словами с меткой `MARK` и минуту отказа (`updated_at`), — а каждая
новая попытка шага начинается без следа (`clear` при захвате срока): след значит «последняя попытка
кончилась отказом почты», а не «когда-то отказывали».

**След — только у отказа почты ящику.** Письмо отдали почте, и она не приняла: ключ, учётка, частота,
сбой или недоступность платформы, предохранитель, транспорт этапа не собран (`TransportError`). Отказ
этому письму — адресату, содержимому, размеру (`LetterRefusalError`) — не о ящике: его другие письма
уходят, и «ящик молчит» было бы неправдой. Отказы правил до почты — окно, стоп-лист, DNS получателя,
ящик выбыл — следа не оставляют: это ожидание по замыслу, о выбывшем ящике тревога своя.

Длина причины — как у прочих причин письма (`messages.failure_reason`, 256 знаков); строки событий
доставки читают причину только у дошедших до почты и отбитых писем (`events.py`, `deferrals.py`), а
у добивки в очереди её не читает никто, кроме сторожа.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.followup_report import PassReport
from backend.features.letters.transport import LetterRefusalError, TransportError
from backend.features.outreach.followup_caps import chain_followup

#: Начало причины у добивки, которой отказала почта, — те же слова, что в итоге пачки (`batch.why`).
MARK = "почта отказала"

#: Сколько знаков причины держит строка письма (`messages.failure_reason`).
REASON_LENGTH = 256


def by_mail(exc: BaseException) -> bool:
    """Отказ пришёл от почты: письмо ей отдали (или собрать транспорт этапа не вышло), и она не
    приняла. Такая добивка — в потолке прохода, как ушедшая (`PassReport.handed`)."""
    return isinstance(exc.__cause__, TransportError)


def of_box(exc: BaseException) -> bool:
    """Отказ почты ящику, а не этому письму: только такой оставляет след (`mark`)."""
    return by_mail(exc) and not isinstance(exc.__cause__, LetterRefusalError)


def refused(letter: MessageModel, exc: BaseException, moment: datetime, report: PassReport) -> None:
    """Почта не приняла добивку `letter`: в итог прохода — и, если отказ ящику, след у строки
    (`mark`). Без фиксации — её делает проход вместе с возвратом срока."""
    report.refused += 1
    if of_box(exc):
        mark(letter, exc, moment)


def mark(letter: MessageModel, exc: BaseException, moment: datetime) -> None:
    """След отказа у строки добивки: причина с меткой и минута отказа. Минута — прохода, а не базы:
    сторож меряет её тем же счётом минут, что и сроки (`next_action_at`)."""
    letter.failure_reason = f"{MARK}: {exc}"[:REASON_LENGTH]
    letter.updated_at = moment


def marked() -> ColumnElement[bool]:
    """Добивка в очереди, чья последняя попытка кончилась отказом почты ящику."""
    return and_(
        MessageModel.status == MessageStatus.QUEUED,
        chain_followup(),
        MessageModel.failure_reason.startswith(MARK, autoescape=True),
    )


async def clear(session: AsyncSession, thread_id: int | None, step: int) -> None:
    """Новая попытка шага `step` в переписке — без следа прежнего отказа: чем она кончится, скажет
    она сама. Без переписки строку добивки не найти — и следа у неё не бывает."""
    if thread_id is None:
        return
    await session.execute(
        update(MessageModel)
        .where(MessageModel.thread_id == thread_id, MessageModel.step == step, marked())
        .values(failure_reason=None)
    )
