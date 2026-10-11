"""Автоотправка первых писем продаж: по расписанию, в окне получателя, по одному с ящика.

**Путь письма — тот же, что у пачки и кнопки** (`Sending.send`): стоп-листы, предохранитель
списка разрешённых, окно получателя, выбор ящика, разгон, дневной кап, лимиты и выдержка домена
работают так же. Своей отправки у прохода нет: он решает, какое письмо и когда, а уходит письмо
общей отправкой. Гонку с кнопкой закрывает тот же захват письма (`Sending._claim`): письмо,
взятое пачкой секундой раньше, проход получает отказом «уже не в очереди».

**Какое письмо.** Первое письмо продаж в очереди, у получателя которого сейчас окно, — то же
правило, что у отправки: окно — из политики продаж, пояса — лида (`policy.py`). Порядок —
старшее в очереди первым (номер письма — порядок сборки), как у пачки и экрана писем: почта судит
о рассылке по темпу ящика, а не по тому, кому письмо, — случайный порядок доставляемости не
добавляет, а предсказуемость у человека и у тестов отнимает.

**Разнесение по окну.** За проход — не больше одного первого письма с ящика: ящик, отдавший
письмо, до конца прохода на паузе. Между первыми письмами ящика — пауза `SALES_AUTOSEND_GAP_MIN`
с разбросом ±50% (`pause_of`) — поверх капа, разгона и лимитов домена, а не вместо них: их
по-прежнему судит выбор ящика, а пауза только убирает ящик из выбора (`mailbox.resting`). Пауза
считается от последнего первого письма ящика любым путём — пачкой, кнопкой или проходом: ящик,
выпустивший пачку, отдыхает так же. Разброс свой у каждого письма, но один между проходами: зерно
— ящик и его последнее письмо. Новое число на каждом проходе кончало бы паузу на первом коротком
— у нижнего края.

**Исход неизвестен — проход стоит.** Письмо продаж «отправляется» (`unknown_outcome.py`): ушло
ли оно, неизвестно, и слать дальше вслепую нельзя — как и пачке. Отказ по одному письму
(стоп-лист, предохранитель, письмо не готово) проход не останавливает: письмо ждёт в очереди
с причиной, проход идёт к следующему. Писать некому (кап, разгон, лимиты домена, паузы ящиков)
— проход кончается: следующему письму ответ был бы тем же.

Тревога, если проход стоит не по делу дольше 15 минут, и строки журнала по смене — в
`autosend_jobs.py`; строка экрана — `auto_lines.py`.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.features.core import window
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from backend.features.core.stages import SalesNotConnectedError
from backend.features.letters import batch, mailbox
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.sending import NoSenderError, SendError, Sending
from backend.features.letters.transport import Mail
from backend.features.letters.unknown_outcome import STUCK_MINUTES
from backend.features.sales import connection
from backend.features.sales.models import SalesLeadModel, SalesThreadModel
from backend.features.sales.policy import sales_policy, zones_of

logger = logging.getLogger(__name__)

#: Разброс паузы ящика — доля `SALES_AUTOSEND_GAP_MIN` в обе стороны.
SPREAD = 0.5


class Stop(StrEnum):
    """Почему проход не шлёт — видом; слова — в `Pass.stopped`."""

    NOT_CONNECTED = "продажи не подключены"
    UNKNOWN = "исход неизвестен"
    EMPTY = "очередь пуста"
    OUTSIDE = "вне окна получателя"
    CAP = "писать некому"
    RESTING = "ящики на паузе"


#: Стоит не по делу — о таком тревога (`autosend_jobs.py`); остальное — проход по делу ждёт.
STALLS = frozenset({Stop.UNKNOWN})


def gap() -> timedelta:
    """Пауза между первыми письмами ящика до разброса."""
    return timedelta(minutes=sales_cfg.AUTOSEND_GAP_MIN)


def pause_of(box_id: int, letter_id: int) -> timedelta:
    """Пауза ящика `box_id` после его первого письма `letter_id`: `gap()` с разбросом ±`SPREAD`.

    Свой разброс у каждого письма, один и тот же на всех проходах: зерно — ящик и письмо."""
    draw = random.Random(f"{box_id}:{letter_id}").uniform(-1.0, 1.0)  # noqa: S311 — не секрет
    return gap() * (1 + SPREAD * draw)


@dataclass(frozen=True, slots=True)
class Waiting:
    """Первые письма продаж в очереди: сколько всего и какие в окне получателя сейчас."""

    queued: int
    #: Письма в окне получателя — по порядку очереди.
    ids: tuple[int, ...]
    #: Первое из них — им спрашивается выбор ящика (`facts`).
    first: MessageModel | None = None
    #: Без пояса получателя — окна не посчитать, письмо ждёт (`window.NO_ZONE`).
    zoneless: int = 0


@dataclass(frozen=True, slots=True)
class Unsettled:
    """Письмо продаж в «отправляется»: исход неизвестен."""

    message_id: int
    since: datetime

    @property
    def words(self) -> str:
        return (
            f"исход письма №{self.message_id} неизвестен: оно «отправляется» с "
            f"{self.since:%d.%m %H:%M} UTC, и слать дальше вслепую нельзя. Исход решит событие "
            f"платформы, а через {STUCK_MINUTES} минут — человек на экране «Письма», в блоке "
            "«Исход неизвестен»"
        )


@dataclass(frozen=True, slots=True)
class Facts:
    """Что видит автоотправка сейчас — один снимок у прохода и у строки экрана."""

    missing: list[str]
    waiting: Waiting = field(default_factory=lambda: Waiting(0, ()))
    #: Ответ выбора ящика без пауз автоотправки; `None` — писем в окне нет, не спрашивали.
    free: mailbox.Choice | None = None
    unsettled: Unsettled | None = None

    @property
    def due(self) -> bool:
        """Есть письма в окне получателя и свободный кап — проход обязан слать."""
        return bool(self.waiting.ids) and self.free is not None and self.free.sender is not None

    def stop(self) -> tuple[Stop, str] | None:
        """Почему проход не шлёт ни одного письма — видом и словами; `None` — слать есть что."""
        if self.missing:
            return Stop.NOT_CONNECTED, f"продажи к почте не подключены — {'; '.join(self.missing)}"
        if self.unsettled is not None:
            return Stop.UNKNOWN, self.unsettled.words
        if not self.waiting.queued:
            return Stop.EMPTY, "в очереди продаж писем нет — их собирает сборка очереди"
        if not self.waiting.ids:
            return Stop.OUTSIDE, _outside(self.waiting)
        if self.free is None or self.free.sender is None:
            refusal = self.free.refusal if self.free is not None else ""
            return Stop.CAP, f"кап набран — {refusal}"
        return None


def _outside(waiting: Waiting) -> str:
    zoneless = (
        f", у {waiting.zoneless} из них пояс получателя неизвестен — окна не посчитать"
        if waiting.zoneless
        else ""
    )
    return f"вне окна получателя у всех писем очереди ({waiting.queued}){zoneless}"


async def facts(session: AsyncSession, now: datetime) -> Facts:
    """Снимок без записи: подключены ли продажи, исход неизвестен, письма в окне, место у ящиков."""
    missing = await connection.missing(session)
    if missing:
        return Facts(missing)
    waiting = await in_window(session, now)
    free = None
    if waiting.first is not None:
        free = await mailbox.choose(
            session, waiting.first, stage=Stage.SALES, thread_sender_id=None, now=now
        )
    return Facts(missing, waiting, free, await unsettled(session))


async def in_window(session: AsyncSession, now: datetime) -> Waiting:
    """Первые письма продаж в очереди и те из них, у чьих получателей сейчас окно, — тем же
    правилом, что у отправки: окно политики продаж, пояса лида."""
    rows = await session.execute(
        select(MessageModel, SalesLeadModel)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(
            CampaignModel.stage == Stage.SALES,
            MessageModel.status == MessageStatus.QUEUED,
            MessageModel.step == FIRST_STEP,
        )
        .order_by(MessageModel.id)
    )
    frame = sales_policy().window
    found: list[MessageModel] = []
    queued = zoneless = 0
    for letter, lead in rows.tuples().all():
        queued += 1
        late = window.check(frame, zones_of(lead), now)
        if late is None:
            found.append(letter)
        elif late.delay is None:
            zoneless += 1
    first = found[0] if found else None
    return Waiting(queued, tuple(letter.id for letter in found), first, zoneless)


async def unsettled(session: AsyncSession) -> Unsettled | None:
    """Письмо продаж в «отправляется» — старшее; `None` — таких нет."""
    row = (
        await session.execute(
            select(MessageModel.id, MessageModel.updated_at)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .where(
                CampaignModel.stage == Stage.SALES,
                MessageModel.status == MessageStatus.SENDING,
            )
            .order_by(MessageModel.updated_at, MessageModel.id)
            .limit(1)
        )
    ).first()
    return None if row is None else Unsettled(row[0], row[1])


async def rests(session: AsyncSession, now: datetime) -> dict[int, datetime]:
    """Ящики продаж на паузе после последнего первого письма: номер ящика → до какой минуты."""
    longest = gap() * (1 + SPREAD)
    rows = await session.execute(
        select(MessageModel.sender_id, MessageModel.id, MessageModel.sent_at)
        .join(SenderModel, SenderModel.id == MessageModel.sender_id)
        .where(
            SenderModel.stage == Stage.SALES,
            MessageModel.step == FIRST_STEP,
            MessageModel.sent_at > now - longest,
        )
        .order_by(MessageModel.sender_id, MessageModel.sent_at.desc(), MessageModel.id.desc())
        .distinct(MessageModel.sender_id)
    )
    found: dict[int, datetime] = {}
    for box, letter, sent_at in rows.tuples().all():
        until = sent_at + pause_of(box, letter) if box is not None and sent_at is not None else now
        if until > now and box is not None:
            found[box] = until
    return found


@dataclass(slots=True)
class Pass:
    """Итог прохода — для журнала, тревоги и тестов."""

    #: Есть письма в окне получателя и свободный кап (`Facts.due`).
    due: bool = False
    #: Ушедшие письма по порядку: номер письма → ящик.
    sent: dict[int, str] = field(default_factory=dict)
    #: Не ушедшие письма: номер → причина словами, одинаковыми для похожих (`batch.why`).
    refused: dict[int, str] = field(default_factory=dict)
    #: Почему проход не шлёт или кончился раньше писем в окне — видом и словами.
    stop: Stop | None = None
    stopped: str | None = None


async def run(session: AsyncSession, transports: Mail, *, now: datetime) -> Pass:
    """Один проход: снимок, затем письма в окне по одному с ящика, пока есть кому писать."""
    seen = await facts(session, now)
    found = Pass(due=seen.due)
    stop = seen.stop()
    if stop is not None:
        found.stop, found.stopped = stop
        return found
    resting = await rests(session, now)
    used: dict[int, datetime] = {}
    sending = Sending(session, transports, now=now)
    for letter_id in seen.waiting.ids:
        with mailbox.resting(resting.keys() | used.keys()):
            refusal = await _one(sending, letter_id, found)
        if refusal is not None:
            found.stop, found.stopped = _why_stopped(refusal, resting, used, now)
            return found
        box = await _box_of(session, letter_id) if letter_id in found.sent else None
        if box is not None:
            used[box] = now + pause_of(box, letter_id)
    return found


async def _one(sending: Sending, letter_id: int, found: Pass) -> str | None:
    """Одно письмо прохода. Ушло или отказ по письму — `None`; писать некому — слова отказа:
    дальше проход не идёт. Остальное (обрыв связи, база) — исключением: проход упал, и письмо,
    оставшееся «отправляется», следующий проход увидит исходом неизвестным."""
    try:
        outcome = await sending.send(letter_id)
    except (NoSenderError, SalesNotConnectedError) as exc:
        logger.debug("продажи: автоотправка — на письме №%s писать некому: %s", letter_id, exc)
        return str(exc)
    except SendError as exc:
        # Строка журнала об отказе — по смене причины у письма (`autosend_jobs.py`): письмо
        # со стоп-листом ждёт в очереди днями, а проход заглядывает в него каждую минуту.
        found.refused[letter_id] = batch.why(exc)
        logger.debug("продажи: автоотправка — письмо №%s не ушло: %s", letter_id, batch.why(exc))
        return None
    found.sent[letter_id] = outcome.sender_email
    logger.info(
        "продажи: автоотправка — письмо №%s ушло с ящика %s",
        letter_id,
        outcome.sender_email,
        extra={"letter": letter_id, "box": outcome.sender_email},
    )
    return None


async def _box_of(session: AsyncSession, letter_id: int) -> int | None:
    """Ящик ушедшего письма — его записал захват (`Sending._claim`)."""
    letter = await session.get(MessageModel, letter_id)
    return None if letter is None else letter.sender_id


def _why_stopped(
    refusal: str, resting: dict[int, datetime], used: dict[int, datetime], now: datetime
) -> tuple[Stop, str]:
    """Почему проход кончился раньше писем в окне: писать некому — из-за пауз или по капу."""
    if mailbox.RESTING not in refusal:
        return Stop.CAP, refusal
    until = min([*resting.values(), *used.values()], default=now)
    return Stop.RESTING, (
        f"ящики на паузе между письмами — ближайший освободится в {until:%H:%M} UTC"
    )
