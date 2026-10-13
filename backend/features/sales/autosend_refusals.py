"""Письмо, которому отказывает платформа, — с очереди после `REFUSALS` отказов (решение владельца
13.10, ревью стыков отправки).

Отказ платформы этому письму (`LetterRefusalError`: 400 и другие 4xx о письме — адресат,
содержимое, размер) возвращает его в очередь, как любой отказ, и проход автоотправки брал его
снова: старшее письмо окна — на каждом проходе, раз в минуту, с запросом к платформе на каждый.
Теперь проход считает такие отказы письму и на `REFUSALS`-м снимает его с очереди: «остановлено»
с причиной словами (`failure_reason`) и строкой журнала разбора (`autosend_jobs.Watch`).

**Считается отказ в проходе, где ушли другие письма.** Отказ, который получает каждое письмо
окна, — не о письме, а о платформе, ящике или сборке письма: о нём тревога через 15 минут
(`autosend.Stop.REFUSED_ALL`), а снять за три минуты всё окно значило бы потерять его лидов. Не
считается и проход, вставший на отказе платформы любому письму или на неизвестном исходе.

**Память — процесса** (`autosend_jobs.Watch.bounced`), как у тревоги: выкатка начинает счёт
заново, и письмо получит до `REFUSALS` отказов ещё раз. Кнопка одного письма и пачка отказы не
считают: человек видит отказ сразу и решает сам.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import settle

#: Сколько отказов платформы письму проход терпит: на этом письмо снимается с очереди.
REFUSALS = 3
#: Длина причины у письма (`messages.failure_reason`).
REASON_LENGTH = 256

#: Отказы платформы письмам до этого прохода: номер → сколько (`autosend_jobs.Watch.bounced`).
_BEFORE: ContextVar[Mapping[int, int]] = ContextVar(
    "autosend_refusals", default=MappingProxyType({})
)


@contextmanager
def before(counts: Mapping[int, int]) -> Iterator[None]:
    """Внутри блока проход знает, сколько раз платформа уже отказала письмам (`counts`)."""
    token = _BEFORE.set(MappingProxyType(dict(counts)))
    try:
        yield
    finally:
        _BEFORE.reset(token)


def words(count: int, detail: str) -> str:
    """Почему письмо снято с очереди — словами, с отказом платформы целиком."""
    return (
        f"платформа отказала письму в {count}-й раз — снято с очереди, проход его больше не "
        f"берёт: {detail}"
    )


async def counted(
    session: AsyncSession, bounced: Collection[int], details: Mapping[int, str], *, went: bool
) -> tuple[dict[int, int], dict[int, str]]:
    """Отказы платформы письмам `bounced` в проходе, где ушли другие письма (`went`): сколько
    их теперь у каждого и какие письма сняты с очереди — номер → слова. Не ушло ни одно —
    не счёт: отказ не о письме."""
    if not went or not bounced:
        return {}, {}
    known = _BEFORE.get()
    counts = {letter_id: known.get(letter_id, 0) + 1 for letter_id in bounced}
    dropped: dict[int, str] = {}
    for letter_id, count in counts.items():
        said = words(count, details[letter_id])
        if count >= REFUSALS and await _drop(session, letter_id, said):
            dropped[letter_id] = said
    await session.commit()
    return counts, dropped


async def _drop(session: AsyncSession, letter_id: int, said: str) -> bool:
    """Письмо — из очереди в «остановлено» с причиной: условием (`settle.leave`), письмо, которое
    секундой раньше взяла другая отправка, не трогается."""
    letter = await session.get(MessageModel, letter_id)
    if letter is None:
        return False
    return await settle.leave(
        session,
        letter,
        was=MessageStatus.QUEUED,
        status=MessageStatus.STOPPED,
        next_action_at=None,
        failure_reason=said[:REASON_LENGTH],
    )
