"""С какого ящика уходит письмо: первое — с любого свободного, письмо переписки — только со своего.

**Первое письмо уходит с ящика этапа, у которого больше остаток на сегодня**
(`outreach/senders.pick`). Ящик выбирается в момент отправки, а не при сборке:
очередь общая, и вставший ящик не должен блокировать свою часть.

**Добивка и ответ — письма переписки — уходят только с ящика своей переписки
и только своим путём:** добивка — проходом добивок (`followups.py`), ответ —
из карточки переписки (`answers.py`). Ящик называет сам путь, и он же кладёт
письмо в ветку переписки (`In-Reply-To`). Без названного ящика письмо
переписки не уходит вовсе (находка ревью 07.10.2026): добивка и ответ,
которые отказ почты вернул «в очередь», попадали в общую очередь этапа,
их брала пачка «Отправить очередь», и уходили они с любого свободного
ящика — первым письмом вне своей переписки, с чужого домена и без ветки.

**Ящик переписки не пишет — письмо ждёт его, а не уходит с другого.** Второй
голос в начатом разговоре хуже паузы: для почты это подмена отправителя,
для собеседника — незнакомец, не читавший первого письма.

Отказ здесь — значение, а не исключение: отказы отправки живут в
`sending.py`, который этот модуль и зовёт, — он и превращает их в свои.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import SenderStatus, Stage
from backend.features.core.models.outreach import MessageModel, SenderModel
from backend.features.letters.chain import ANSWER_STEP, FIRST_STEP, kind_of
from backend.features.outreach import senders as sender_rules
from backend.features.outreach.repository import OutreachRepository


@dataclass(frozen=True, slots=True)
class Choice:
    """Ящик письма — или почему его нет, словами для экрана."""

    sender: SenderModel | None
    refusal: str = ""
    #: Отказ пути, а не ящика: письмо переписки пришло без ящика переписки —
    #: из общей очереди, а не своим путём.
    wrong_path: bool = False


async def choose(
    session: AsyncSession,
    message: MessageModel,
    *,
    stage: Stage,
    thread_sender_id: int | None,
    now: datetime,
) -> Choice:
    """Ящик для письма. `thread_sender_id` — ящик переписки, названный её путём."""
    if thread_sender_id is not None:
        return await _thread_box(session, thread_sender_id)
    if message.step == FIRST_STEP:
        return await _free_box(session, stage, now)
    return Choice(sender=None, refusal=_own_path_only(message), wrong_path=True)


async def _free_box(session: AsyncSession, stage: Stage, now: datetime) -> Choice:
    """Ящик этапа с наибольшим остатком на сегодня. Остаток считают первые
    письма: у добивок свой часовой лейн, и класть их в тот же кап значит
    на каждую цепочку недосчитаться нового адресата."""
    rows = await session.execute(select(SenderModel).order_by(SenderModel.id))
    spot = sender_rules.pick(
        rows.scalars().all(),
        sent_today=await OutreachRepository(session).sent_today(now=now, first_only=True),
        stage=stage,
        now=now,
    )
    if spot is None:
        return Choice(
            sender=None,
            refusal=(
                "Сегодня писать некому: все ящики либо выключены, либо выбрали дневной "
                "лимит. Письмо остаётся в очереди — завтра лимит откроется заново"
            ),
        )
    return Choice(sender=spot.sender)


async def _thread_box(session: AsyncSession, sender_id: int) -> Choice:
    """Ящик, который ведёт переписку. Не пишет — письмо ждёт его."""
    sender = await session.get(SenderModel, sender_id)
    if sender is None:
        return Choice(
            sender=None,
            refusal=(
                f"Ящика №{sender_id} нет: им начата переписка, а его удалили. "
                "С другого ящика письмо переписки не уйдёт — оно ждёт"
            ),
        )
    if not sender.enabled or sender.status is SenderStatus.PAUSED:
        return Choice(
            sender=None,
            refusal=(
                f"Ящик {sender.email} сейчас не пишет ({_why_silent(sender)}), а переписку "
                "ведёт он. С другого ящика письмо не уйдёт — оно ждёт, пока ящик не включат "
                "на экране «Домены рассылки»"
            ),
        )
    return Choice(sender=sender)


def _why_silent(sender: SenderModel) -> str:
    state = "выключен" if not sender.enabled else "на паузе"
    return f"{state}: {sender.pause_reason}" if sender.pause_reason else state


def _own_path_only(message: MessageModel) -> str:
    """Чьим путём уходит письмо переписки — словами отказа общей очереди."""
    what = kind_of(message.step)
    if message.step >= ANSWER_STEP:
        path = "из карточки переписки — с её ящика и веткой к письму собеседника"
    else:
        path = "проходом добивок — с ящика своей переписки и в её ветку"
    return (
        f"Письмо №{message.id} — {what}: оно уходит только своим путём, {path}. "
        "Из очереди писем его не отправить: с любого свободного ящика оно ушло бы "
        "первым письмом вне своей переписки"
    )
