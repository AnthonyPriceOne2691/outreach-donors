"""Переадресация словами — для «Диалогов» и команды разового прохода (16.11.2026).

Что решено по ответу «пишите на …» и что с письмом, говорит сервер одной строкой (`Shown.words`)
и словом состояния (`Shown.state`): экран показывает их как есть и своих правил не держит. Слова —
из строки решения (`lead_redirects`) и письма переадресации: его статус, причина отказа почты,
ответ нового адресата (ответ, привязанный меткой к этому письму).

Главное решение переписки (`_pick`) — письмо, если оно заведено: ради него всё и затевалось; иначе
то, что ждёт прохода, иначе последний отказ. «Не просят писать на другой адрес» не показывается:
говорить там не о чем, диалог остаётся обычным лидом.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus, ReplyKind
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.core.models.redirect import LeadRedirectModel
from backend.features.replies.lead_redirect import State, rows_of
from backend.shared.net.idn import shown_address

#: Состояние словом: проверяем ответ, письмо ждёт, переслали, не дошло, снято, не пересылаем.
Kind = Literal["checking", "waiting", "sent", "bounced", "stopped", "refused"]

_ORDER = {State.LETTER: 0, State.PENDING: 1, State.REFUSED: 2}


@dataclass(frozen=True, slots=True)
class Shown:
    """Переадресация в переписке — как её видит человек."""

    state: Kind
    words: str
    #: Адрес, который назвал ответ (выбрала модель), дословно. Пусто — ещё не выбран.
    address: str | None
    #: Ответ, который назвал адрес.
    reply_id: int
    #: Письмо на этот адрес. Пусто — письма нет.
    letter_id: int | None = None
    #: Пометка: адрес не на домене рекламодателя.
    note: str | None = None


def _pick(rows: Sequence[LeadRedirectModel]) -> LeadRedirectModel | None:
    """Главное решение переписки: письмо, потом ждущее, потом отказ — у равных новее."""
    shown = [row for row in rows if row.state in _ORDER]
    return min(shown, key=lambda row: (_ORDER[State(row.state)], -row.id), default=None)


def _day(moment: datetime | None) -> str:
    return f" по ответу {moment:%d.%m.%Y}" if moment is not None else ""


def _undecided(row: LeadRedirectModel) -> tuple[Kind, str]:
    if row.state == State.REFUSED:
        target = f" на {shown_address(row.address)}" if row.address else ""
        return "refused", f"Не переслали{target}: {row.reason or 'причина не записана'}"
    target = f"на {shown_address(row.address)}" if row.address else "туда"
    words = f"Ответ называет другой адрес — проверяем, просят ли писать {target}"
    return "checking", f"{words} ({row.reason})" if row.reason else words


def _letter_words(
    row: LeadRedirectModel,
    letter: MessageModel,
    asked_at: datetime | None,
    answered_at: datetime | None,
) -> tuple[Kind, str]:
    # Слова — для людей: домен адреса письменностью сайта; поле `address` — как в базе.
    address, on = shown_address(row.address or ""), _day(asked_at)
    if letter.status is MessageStatus.QUEUED:
        return "waiting", f"Перешлём на {address}{on}: {row.reason or 'уйдёт ближайшим проходом'}"
    if letter.status is MessageStatus.SENDING:
        return "waiting", f"Письмо на {address} отправляется — почта ещё не сказала, ушло ли"
    if letter.status is MessageStatus.BOUNCED:
        why = f": {letter.failure_reason}" if letter.failure_reason else ""
        return "bounced", f"Переслали на {address}{on}, но письмо не дошло{why}"
    if letter.status is MessageStatus.STOPPED:
        why = letter.failure_reason or row.reason or "снято"
        return "stopped", f"Письмо на {address} не ушло: {why}"
    if answered_at is not None:
        return "sent", f"Переслали на {address}{on}; новый адресат ответил {answered_at:%d.%m.%Y}"
    return "sent", f"Переслали на {address}{on} — ждём ответа нового адресата"


def words_of(
    row: LeadRedirectModel,
    letter: MessageModel | None,
    *,
    asked_at: datetime | None = None,
    answered_at: datetime | None = None,
) -> Shown:
    """Решение строкой для экрана — по строке решения и письму переадресации."""
    if row.state != State.LETTER:
        state, words = _undecided(row)
    elif letter is None:
        state, words = (
            "stopped",
            f"Письмо на {shown_address(row.address or '')} удалено — переадресации нет",
        )
    else:
        state, words = _letter_words(row, letter, asked_at, answered_at)
    return Shown(
        state=state,
        words=words,
        address=row.address,
        reply_id=row.reply_id,
        letter_id=row.message_id,
        note=row.note,
    )


async def shown(
    session: AsyncSession, thread_ids: Collection[int] | None = None
) -> dict[int, Shown]:
    """Переадресация переписок `thread_ids` (или всех, где она есть): номер переписки → слова.
    Четыре запроса на любой список: решения, письма, время ответов и ответы новых адресатов."""
    by_thread: defaultdict[int, list[LeadRedirectModel]] = defaultdict(list)
    for one in await rows_of(session, thread_ids):
        by_thread[one.thread_id].append(one)
    chosen = {thread: _pick(rows) for thread, rows in by_thread.items()}
    picked = {thread: row for thread, row in chosen.items() if row is not None}
    if not picked:
        return {}
    letter_ids = [row.message_id for row in picked.values() if row.message_id is not None]
    letters = {
        letter.id: letter
        for letter in (
            await session.scalars(select(MessageModel).where(MessageModel.id.in_(letter_ids)))
        ).all()
    }
    asked = dict(
        (
            await session.execute(
                select(ReplyModel.id, ReplyModel.created_at).where(
                    ReplyModel.id.in_([row.reply_id for row in picked.values()])
                )
            )
        )
        .tuples()
        .all()
    )
    answered = dict(
        (
            await session.execute(
                select(ReplyModel.message_id, func.min(ReplyModel.created_at))
                .where(ReplyModel.message_id.in_(letter_ids), ReplyModel.kind == ReplyKind.HUMAN)
                .group_by(ReplyModel.message_id)
            )
        )
        .tuples()
        .all()
    )
    return {
        thread: words_of(
            row,
            letters.get(row.message_id) if row.message_id is not None else None,
            asked_at=asked.get(row.reply_id),
            answered_at=answered.get(row.message_id) if row.message_id is not None else None,
        )
        for thread, row in picked.items()
    }
