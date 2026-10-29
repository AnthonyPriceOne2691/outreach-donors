"""Отсрочка доставки — у письма, а не в никуда (29.10.2026).

Платформа присылает отсрочку (`deferred`), когда сервер получателя не принял письмо: она
повторяет попытки до 72 часов, и исход придёт доставкой или отказом. До этого модуля отсрочки
принимались и отбрасывались: статус «ушло» они не меняют, у письма не оставалось ничего, и
письмо №404 шесть часов было «отправлено» без исхода, а сторож велел проверять вебхук.

**У письма — последняя отсрочка** (`note`): когда, какая попытка, что ответил сервер
(обрезанным) и вид причины (`delivery_trouble.py`), а ещё — с какого часа платформа повторяет.
События приходят не по порядку и повторяются: последней считается отсрочка с самым поздним
временем платформы, запоздавшая её не откатывает, а повтор не прибавляет попытку. Номер попытки —
платформы (`attempt`); нет его — счёт отсрочек, о которых она сообщила.

**Статус не трогается.** Доставка или отказ после отсрочки — исход, как раньше (`events.py`),
а отсрочка остаётся у письма историей: «до доставки откладывалась», «платформа перестала
повторять после 14 попыток».

**Что видит человек** — `deferral_of` и `refusal_of`: вид и слова одной функцией с отказом
(`delivery_trouble.reason_of`), у отказа, где платформа сдалась, — словами последней отсрочки.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters.delivery_trouble import (
    Trouble,
    kind_of,
    reason_of,
    words_of,
)
from backend.features.letters.draft import split_audience

if TYPE_CHECKING:
    from backend.features.letters.events import DeliveryEvent

#: Часы отсрочки в тревоге — по Москве, как сброс потолка модели (`core/llm_caps.py`): тревогу
#: читают в Telegram, а там нет часов браузера.
MOSCOW = ZoneInfo("Europe/Moscow")

#: «Попытка» при числе — по последней цифре; 11–14 и остальное — «попыток».
ATTEMPT_FORMS = {1: "попытка", 2: "попытки", 3: "попытки", 4: "попытки"}

#: Сколько откладываемых писем показать на экране писем — остальные числом.
WAITING_LIMIT = 50

#: Отказ, в словах которого причины нет, — у письма с отсрочками его называет последняя отсрочка:
#: платформа сдалась, а почему — сказала раньше.
GAVE_UP = frozenset({Trouble.UNKNOWN, Trouble.EXPIRED})


@dataclass(frozen=True, slots=True)
class Deferral:
    """Последняя отсрочка письма словами: вид, слова, сколько попыток, с какого часа и до какого."""

    kind: Trouble
    words: str
    attempts: int
    since: datetime
    last: datetime


@dataclass(frozen=True, slots=True)
class DeferredLetter:
    """Письмо, доставка которого откладывается, — с тем, по чему его узнают: сайт, кому, рассылка."""

    message: MessageModel
    host: str
    email: str | None
    campaign: str


@dataclass(frozen=True, slots=True)
class Waiting:
    """Откладываемые письма этапа: первые `WAITING_LIMIT` и сколько их всего."""

    letters: list[DeferredLetter]
    total: int


def note(message: MessageModel, event: DeliveryEvent, moment: datetime) -> bool:
    """Записать отсрочку `event` у письма. `False` — это не новая последняя отсрочка: повтор
    того же события или запоздавшая (её номер попытки всё равно засчитан)."""
    at = event.at or moment
    since = message.deferred_since
    message.deferred_since = at if since is None else min(since, at)
    counted = message.deferred_attempts or 0
    later = _later(message.deferred_at, at, event.attempt, counted)
    message.deferred_attempts = _attempts(counted, event.attempt, later=later)
    if not later:
        return False
    found = reason_of(event.reason, status=event.status)
    message.deferred_at = at
    message.deferred_reason = found.text or None
    message.deferred_kind = found.kind.value
    return True


def _attempts(counted: int, attempt: int | None, *, later: bool) -> int:
    """Попыток после события: номер платформы — не меньше записанного; без номера новая
    отсрочка — ещё одна попытка, повтор и запоздавшая — ни одной."""
    if attempt is not None:
        return max(counted, attempt)
    return counted + 1 if later else counted


def _later(last: datetime | None, at: datetime, attempt: int | None, counted: int) -> bool:
    """Новее ли отсрочка записанной: позже по времени платформы — или в ту же секунду, но с
    бóльшим номером попытки. Повтор того же события новее не бывает."""
    if last is None or at > last:
        return True
    return at == last and attempt is not None and attempt > counted


def deferral_of(message: MessageModel) -> Deferral | None:
    """Последняя отсрочка письма словами; не откладывалось — `None`."""
    last = message.deferred_at
    if last is None:
        return None
    kind = _stored_kind(message)
    return Deferral(
        kind=kind,
        words=words_of(kind, message.deferred_reason),
        attempts=message.deferred_attempts or 1,
        since=message.deferred_since or last,
        last=last,
    )


def refusal_of(message: MessageModel) -> str | None:
    """Почему не доставлено — словами; у письма не отказ — `None`. Отказ без причины в словах
    (срок попыток вышел, ответ не узнан) у письма с отсрочками — словами последней из них."""
    if message.status is not MessageStatus.BOUNCED:
        return None
    reason = reason_of(message.failure_reason)
    deferral = deferral_of(message)
    if reason.kind in GAVE_UP and deferral is not None:
        return deferral.words
    return reason.words


def said(message: MessageModel, now: datetime) -> str | None:
    """Последняя отсрочка строкой тревоги: «сервер получателя не отвечает (порт 25, тайм-аут),
    14 попыток с 10:26 по Москве». Не откладывалось — `None`."""
    deferral = deferral_of(message)
    return None if deferral is None else f"{deferral.words}, {tries(deferral, now)}"


def tries(deferral: Deferral, now: datetime) -> str:
    """Сколько попыток и с какого часа: «14 попыток с 10:26 по Москве», одна — «попытка
    в 10:26 по Москве»; не сегодня — с днём: «с 28.10 10:26»."""
    since = _clock(deferral.since, now)
    if deferral.attempts == 1:
        return f"попытка в {since} по Москве"
    return f"{attempts_word(deferral.attempts)} с {since} по Москве"


def attempts_word(count: int) -> str:
    """«14 попыток», «2 попытки», «21 попытка»."""
    form = "попыток" if 11 <= count % 100 <= 14 else ATTEMPT_FORMS.get(count % 10, "попыток")
    return f"{count} {form}"


def _clock(moment: datetime, now: datetime) -> str:
    """Часы по Москве; не сегодняшний по Москве день — с числом и месяцем."""
    local = moment.astimezone(MOSCOW)
    today = local.date() == now.astimezone(MOSCOW).date()
    return f"{local:%H:%M}" if today else f"{local:%d.%m %H:%M}"


def _stored_kind(message: MessageModel) -> Trouble:
    """Вид, записанный с отсрочкой; незнакомый (вид переименовали) — заново по её тексту."""
    stored = message.deferred_kind
    known = next((kind for kind in Trouble if kind.value == stored), None)
    return known if known is not None else kind_of(message.deferred_reason or "")


async def waiting(
    session: AsyncSession, *, stage: Stage, audience: str | None = None, limit: int = WAITING_LIMIT
) -> Waiting:
    """Письма этапа, доставка которых откладывается сейчас: ушли, исхода нет, отсрочка была.
    Первыми — те, что откладываются дольше. `audience` — только рассылок этой аудитории Этапа 2,
    как у блока «Исход неизвестен» (`draft.split_audience`)."""
    statement = _waiting(stage, audience)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = await session.execute(
        statement.order_by(MessageModel.deferred_since, MessageModel.id).limit(limit)
    )
    letters = [
        DeferredLetter(message=message, host=host, email=email, campaign=campaign)
        for message, host, email, campaign in rows.tuples().all()
    ]
    return Waiting(letters=letters, total=int(total or 0))


def _waiting(stage: Stage, audience: str | None) -> Select[Any]:
    """Отбор откладываемых писем этапа: ушло, исхода нет, отсрочка была; адрес — у контакта."""
    statement = (
        select(MessageModel, DomainModel.host, ContactModel.email, CampaignModel.name)
        .join(DomainModel, DomainModel.id == MessageModel.domain_id)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .outerjoin(ContactModel, ContactModel.id == MessageModel.contact_id)
        .where(
            CampaignModel.stage == stage,
            MessageModel.status == MessageStatus.SENT,
            MessageModel.deferred_at.is_not(None),
        )
    )
    split = split_audience(stage, audience)
    return statement if split is None else statement.where(CampaignModel.audience == split)
