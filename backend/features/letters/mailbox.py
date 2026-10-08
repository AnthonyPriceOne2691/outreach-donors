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

**Карточка переписки говорит то же, что отказ** (`thread_mail`): ящик
переписки, пишет ли он и когда следующая добивка. До 07.10.2026 причина
ожидания жила только в журнале прохода добивок: человек видел, что добивка
не ушла, но не видел почему, пока не нажимал «Ответить» и не получал отказ.

**Домен ящика на паузе — письма переписки тоже ждут** (`sending_domains`,
решение 08.10.2026). Пауза домена ставится за репутацию — жалоба, блок-лист, —
а добивка с такого домена — то же холодное письмо и тот же удар по нему.
До 08.10.2026 пауза держала только первые письма (`limits.screen`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core.domain import MessageStatus, SenderStatus, Stage
from backend.features.core.models.outreach import (
    MessageModel,
    SenderModel,
    SendingDomainModel,
)
from backend.features.letters.chain import ANSWER_STEP, CHAINABLE, FIRST_STEP, MAX_STEPS, kind_of
from backend.features.outreach import health, limits
from backend.features.outreach import senders as sender_rules
from backend.features.outreach.repository import OutreachRepository

#: Письмо ушло к почте — и получило ящик (`Sending._claim` пишет его вместе
#: с «отправляется»). Пустой ящик у такого письма значит, что ящик удалили.
_WENT = (MessageStatus.SENDING, MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED)


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
    на каждую цепочку недосчитаться нового адресата.

    До выбора — лимиты домена и направления (`outreach/limits.py`): отсеянный
    ящик в выбор не идёт, а причина отсева становится словами отказа."""
    rows = await session.execute(select(SenderModel).order_by(SenderModel.id))
    sent = await OutreachRepository(session).sent_today(now=now, first_only=True)
    screened = limits.screen(
        rows.scalars().all(),
        stage=stage,
        sent_today=sent,
        domains=await limits.sending_domains(session),
        direction_limit=cfg.direction_limit(stage.value),
        now=now,
        cuts=await health.cuts(session, stage, now),
    )
    spot = sender_rules.pick(screened.fit, sent_today=sent, stage=stage, now=now)
    if spot is None:
        why = screened.why()
        return Choice(
            sender=None,
            refusal=(
                f"Сегодня писать некому: {why}. Письмо остаётся в очереди"
                if why
                else "Сегодня писать некому: все ящики либо выключены, либо выбрали дневной "
                "лимит. Письмо остаётся в очереди — завтра лимит откроется заново"
            ),
        )
    return Choice(sender=spot.sender)


async def _thread_box(session: AsyncSession, sender_id: int) -> Choice:
    """Ящик, который ведёт переписку. Не пишет — письмо ждёт его."""
    sender = await session.get(SenderModel, sender_id)
    silence = await _silence(session, sender, gone=f"№{sender_id}")
    if silence is not None:
        return Choice(sender=None, refusal=silence)
    return Choice(sender=sender)


async def _silence(session: AsyncSession, sender: SenderModel | None, *, gone: str) -> str | None:
    """Почему ящик переписки не пишет — словами отказа; `None` — пишет.
    `gone` — как назвать ящик, которого нет: номером или «первого письма».
    Ящик пишет, а его домен на паузе — тоже не пишет (`_domain_paused`).

    Одни слова на отправку и на карточку переписки (`thread_mail`): в карточке
    человек читает ровно то, что скажет отказ, если нажать «Ответить».
    """
    if sender is None:
        return (
            f"Ящика {gone} нет: им начата переписка, а его удалили. "
            "С другого ящика письмо переписки не уйдёт — оно ждёт"
        )
    if not sender.enabled or sender.status is SenderStatus.PAUSED:
        return (
            f"Ящик {sender.email} сейчас не пишет ({_why_silent(sender)}), а переписку "
            "ведёт он. С другого ящика письмо не уйдёт — оно ждёт, пока ящик не включат "
            "на экране «Домены рассылки»"
        )
    return await _domain_paused(session, sender)


async def _domain_paused(session: AsyncSession, sender: SenderModel) -> str | None:
    """Домен ящика переписки на паузе — словами отказа; `None` — домен пишет.

    Только пауза, а не выдержка и не чужое направление (их судит фильтр первых писем,
    `limits.screen`): выдержка — у домена без писем, переписок на нём нет, а запись
    домена за другим этапом не отбирает у начатых разговоров их ящик.
    """
    row = await session.scalar(
        select(SendingDomainModel).where(SendingDomainModel.domain == sender.domain)
    )
    if row is None or row.paused_at is None:
        return None
    return (
        f"Домен {row.domain} на паузе ({row.pause_reason or 'без причины'}), а переписку "
        f"ведёт ящик {sender.email} на нём. С другого ящика письмо не уйдёт — оно ждёт, "
        "пока домен не снимут с паузы (outreach sending-domain --resume)"
    )


@dataclass(frozen=True, slots=True)
class ThreadMail:
    """Чем переписка пишет дальше — для её карточки."""

    #: Адрес ящика переписки; пусто — ящик удалили.
    mailbox: str | None
    #: Почему письма переписки ждут — словами отказа; пусто — ящик пишет.
    waiting: str | None
    #: Шаг и срок следующей добивки. Пусто — добивки не будет: цепочка
    #: кончилась, собеседник ответил или исход письма ещё неизвестен.
    next_step: int | None = None
    next_at: datetime | None = None


async def thread_mail(session: AsyncSession, messages: Sequence[MessageModel]) -> ThreadMail | None:
    """Ящик переписки и её следующая добивка. `None` — первое письмо ещё
    не уходило: ящик выберется в момент отправки (`_free_box`).

    Ящик переписки — тот, с которого ушло первое письмо: им же уходят ответ
    (`answers._thread_sender`) и добивки (`followups.py`).
    """
    first = _first_gone(messages)
    if first is None:
        return None
    sender = await _box_of(session, first)
    next_step, next_at = _next_followup(messages)
    return ThreadMail(
        mailbox=sender.email if sender is not None else None,
        waiting=await _silence(session, sender, gone="первого письма"),
        next_step=next_step,
        next_at=next_at,
    )


def _first_gone(messages: Sequence[MessageModel]) -> MessageModel | None:
    """Первое письмо переписки, ушедшее к почте; `None` — ещё не уходило."""
    return next((m for m in messages if m.step == FIRST_STEP and m.status in _WENT), None)


async def _box_of(session: AsyncSession, letter: MessageModel) -> SenderModel | None:
    """Ящик, с которого ушло письмо; `None` — ящик удалили, и номер ушёл из письма."""
    if letter.sender_id is None:
        return None
    return await session.get(SenderModel, letter.sender_id)


def _next_followup(messages: Sequence[MessageModel]) -> tuple[int | None, datetime | None]:
    """Шаг и срок следующей добивки — по тем же условиям, что у захвата
    прохода (`followups.Chain.claim`): иначе карточка обещала бы добивку,
    которую проход не возьмёт."""
    due = [m for m in messages if _claimable(m)]
    if not due:
        return None, None
    last = max(due, key=lambda m: m.step)
    return last.step + 1, last.next_action_at


def _claimable(message: MessageModel) -> bool:
    """Срок письма возьмёт проход добивок: условия захвата `Chain.claim`."""
    return (
        message.next_action_at is not None
        and message.status in CHAINABLE
        and message.step < MAX_STEPS - 1
    )


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
