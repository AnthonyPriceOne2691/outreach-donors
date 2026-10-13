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
считается от последнего первого письма ящика, отданного почте любым путём — пачкой, кнопкой или
проходом: ящик, выпустивший пачку, отдыхает так же. Отдано и письмо «отправляется» — с минуты
захвата (`updated_at`, `unknown_outcome.py`): почта могла его принять, а её ответ потеряться, и
ящик, чей ответ не пришёл, не шлёт следующее через минуту (дневной счёт такого письма не видит —
`sent_at` пуст). Разброс свой у каждого письма, но один между проходами: зерно — ящик и его
последнее письмо. Новое число на каждом проходе кончало бы паузу на первом коротком — у нижнего
края.

**Исход неизвестен — проход стоит.** Письмо продаж в блоке «Исход неизвестен» — «отправляется»
дольше `STUCK_AFTER` (`unknown_outcome.py`): ушло ли оно, неизвестно, и слать дальше вслепую
нельзя — как и пачке. Письмо моложе может быть ещё в пути: его прямо сейчас передаёт почте другая
отправка — добивка, ответ, кнопка, пачка (худшая передача — две с половиной минуты). Стоять на нём
значило бы стоять на каждой чужой отправке. **Своё неизвестное — сразу:** почта не ответила на
письмо самого прохода (`MaybeSentError`) — проход стоит с этой минуты, а не с блока: в пути это
письмо уже не будет, а ответ платформы следующему был бы тем же. Такие письма помнит проход по
расписанию (`unsure`, `autosend_jobs.Watch`) до `STUCK_AFTER`; решилось раньше (событие платформы)
— проход идёт, дальше письмо держит блок.

Отказ по одному письму (стоп-лист, письмо не готово, почта отказала этому адресату или
содержимому) проход не останавливает: письмо ждёт в очереди с причиной, проход идёт к следующему.
**Отказ получило каждое письмо окна, а не ушло ни одно** — письма очереди собраны с прежними
подписью и адресом «Отправителя», лидов снял стоп-лист, — проход стоит не по делу (`REFUSED_ALL`):
тревога через 15 минут, как у неизвестного исхода. Иначе он перебирал бы окно каждую минуту, а
поток стоял бы молча. Писать некому (кап, разгон, лимиты домена, паузы ящиков) — проход
кончается: следующему письму ответ был бы тем же.

**Платформа отказывает любому письму — проход стоит** (`PlatformRefusalError`: ключ не принят,
учётке отказано, частота или сбой после повторов, недоступна). Письмо, на котором это выяснилось,
вернулось в очередь, а следующие проход не берёт: иначе раз в минуту он захватывал бы и возвращал
каждое письмо окна. Это остановка не по делу — тревога через 15 минут, как у неизвестного исхода.

**Предохранитель почты не снят — автоотправка не включается** (`guard`). Пока список разрешённых
адресатов почты продаж не пуст, письма уходят только на свои адреса, а отказ предохранителя почта
даёт уже после захвата письма (`sendgrid.py`): проход раз в минуту брал бы каждое письмо окна в
«отправляется» и возвращал в очередь — две записи на письмо в общую базу, сотни в минуту на полной
очереди. Проверка на своих адресах — кнопкой; автоотправка — для боевой отправки.

Тревога, если проход стоит не по делу дольше 15 минут, и строки журнала по смене — в
`autosend_jobs.py`; строка экрана — `auto_lines.py`.
"""

from __future__ import annotations

import logging
import random
from collections import Counter
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.core import window
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from backend.features.core.stages import SalesNotConnectedError
from backend.features.letters import batch, mailbox, unknown_outcome
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.sending import NoSenderError, SendError, Sending
from backend.features.letters.transport import Mail, MaybeSentError, PlatformRefusalError
from backend.features.sales import connection
from backend.features.sales.models import SalesLeadModel, SalesThreadModel
from backend.features.sales.policy import sales_policy, zones_of

logger = logging.getLogger(__name__)

#: Разброс паузы ящика — доля `SALES_AUTOSEND_GAP_MIN` в обе стороны.
SPREAD = 0.5


class Stop(StrEnum):
    """Почему проход не шлёт — видом; слова — в `Pass.stopped`."""

    NOT_CONNECTED = "продажи не подключены"
    GUARDED = "предохранитель почты не снят"
    UNKNOWN = "исход неизвестен"
    REFUSED = "платформа отказывает"
    REFUSED_ALL = "отказ каждому письму окна"
    EMPTY = "очередь пуста"
    OUTSIDE = "вне окна получателя"
    CAP = "писать некому"
    RESTING = "ящики на паузе"


#: Стоит не по делу — о таком тревога (`autosend_jobs.py`); остальное — проход по делу ждёт.
STALLS = frozenset({Stop.UNKNOWN, Stop.REFUSED, Stop.REFUSED_ALL})

#: Письма, исход которых проход по расписанию сам оставил неизвестным (`unsure`): на них он стоит
#: сразу, а не с блока «Исход неизвестен». Вне `unsure` множество пусто.
_UNSURE: ContextVar[frozenset[int]] = ContextVar("autosend_unsure", default=frozenset())


@contextmanager
def unsure(letters: Collection[int]) -> Iterator[None]:
    """Внутри блока проход стоит на письмах `letters`, пока они «отправляются»: их исход проход
    по расписанию оставил неизвестным сам (`autosend_jobs.Watch`)."""
    token = _UNSURE.set(frozenset(letters))
    try:
        yield
    finally:
        _UNSURE.reset(token)


def guard() -> str | None:
    """Почему автоотправка не включается при включённом тумблере — словами; `None` — включается.

    Список разрешённых адресатов почты продаж не пуст — см. «Предохранитель» выше. Учётка
    читается на каждом проходе, как у транспорта (`config.outreach.mail_account`)."""
    account = outreach_cfg.mail_account(Stage.SALES.value)
    if not account.allowed_recipients:
        return None
    return (
        f"список разрешённых адресатов почты продаж не пуст ({account.allowlist_setting}) — "
        "письма уходят только на свои адреса, кнопкой «Отправить очередь» или по одному"
    )


#: Пачки продаж нет, пока автоотправка включена (решение владельца 13.10): пачка выбрала бы
#: дневной лимит ящиков за секунды, мимо паузы между первыми письмами ящика. Слова — отказу
#: пачки (`mail.batch`) и строке экрана вместо кнопки (`auto_lines.batch_line`).
BATCH_OFF = "пачкой письма продаж не уходят, пока включена автоотправка"
BY_ITSELF = (
    "письма уходят сами, по одному с ящика за проход и с паузой между письмами ящика; отправить "
    "очередь вручную — выключить автоотправку (SALES_AUTOSEND)"
)


def switched_on() -> bool:
    """Включена ли автоотправка — тем же правилом, что у прохода (`autosend_jobs.tick`):
    продажи и тумблер включены, предохранитель снят (`guard`). Не снят — проход не идёт, и
    пачка нужна: ею проверяют отправку на своих адресах."""
    return sales_cfg.ENABLED and sales_cfg.AUTOSEND and guard() is None


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
    """Письмо продаж в блоке «Исход неизвестен»: «отправляется» дольше `STUCK_AFTER`."""

    message_id: int
    since: datetime

    @property
    def words(self) -> str:
        return (
            f"исход письма №{self.message_id} неизвестен: оно «отправляется» с "
            f"{self.since:%d.%m %H:%M} UTC, и слать дальше вслепую нельзя. Исход решит событие "
            "платформы или человек на экране «Письма», в блоке «Исход неизвестен»"
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
            # Слова выбора ящика как есть: «кап набран» врал бы, когда ящики на паузе или домен
            # на выдержке, — отказ выбора называет причину сам.
            return Stop.CAP, self.free.refusal if self.free is not None else Stop.CAP.value
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
    return Facts(missing, waiting, free, await unsettled(session, now))


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


async def unsettled(session: AsyncSession, now: datetime) -> Unsettled | None:
    """Старшее письмо продаж в блоке «Исход неизвестен» — тем же правилом, что у блока на экране
    писем (`unknown_outcome.stuck`) — или своё неизвестное (`unsure`), всё ещё «отправляется»;
    `None` — таких нет. Чужое письмо моложе `STUCK_AFTER` может быть ещё в пути у другой
    отправки — на нём проход не стоит."""
    found = await unknown_outcome.stuck(session, stage=Stage.SALES, now=now, limit=1)
    if found:
        return Unsettled(found[0].message.id, found[0].since)
    own = _UNSURE.get()
    if not own:
        return None
    row = (
        await session.execute(
            select(MessageModel.id, MessageModel.updated_at)
            .where(MessageModel.id.in_(sorted(own)), MessageModel.status == MessageStatus.SENDING)
            .order_by(MessageModel.id)
            .limit(1)
        )
    ).first()
    return None if row is None else Unsettled(row[0], row[1])


async def rests(session: AsyncSession, now: datetime) -> dict[int, datetime]:
    """Ящики продаж на паузе после последнего первого письма, отданного почте: номер ящика → до
    какой минуты. Отдано — ушло (`sent_at`) или «отправляется» (с минуты захвата, `updated_at`)."""
    edge = now - gap() * (1 + SPREAD)
    handed = func.coalesce(MessageModel.sent_at, MessageModel.updated_at)
    rows = await session.execute(
        select(MessageModel.sender_id, MessageModel.id, handed)
        .join(SenderModel, SenderModel.id == MessageModel.sender_id)
        .where(
            SenderModel.stage == Stage.SALES,
            MessageModel.step == FIRST_STEP,
            or_(
                MessageModel.sent_at > edge,
                (MessageModel.status == MessageStatus.SENDING) & (MessageModel.updated_at > edge),
            ),
        )
        .order_by(MessageModel.sender_id, handed.desc(), MessageModel.id.desc())
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
    #: Те же письма: номер → отказ отправки целиком — для журнала, по письму один раз.
    details: dict[int, str] = field(default_factory=dict)
    #: Письмо, исход которого проход оставил неизвестным: почта не ответила (`unsure`).
    unsure: int | None = None
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
            halt = await _one(sending, letter_id, found, now)
        if halt is not None:
            found.stop, found.stopped = _why_stopped(halt, resting, used, now)
            return found
        box = await _box_of(session, letter_id) if letter_id in found.sent else None
        if box is not None:
            used[box] = now + pause_of(box, letter_id)
    return _to_the_end(found)


def _to_the_end(found: Pass) -> Pass:
    """Проход прошёл всё окно: не ушло ни одно письмо, а отказ получило каждое — стоит не по
    делу (`REFUSED_ALL`); иначе — как прошёл."""
    if found.due and found.refused and not found.sent:
        found.stop, found.stopped = Stop.REFUSED_ALL, _all_refused(found.refused)
    return found


async def _one(
    sending: Sending, letter_id: int, found: Pass, now: datetime
) -> tuple[Stop, str] | None:
    """Одно письмо прохода. Ушло или отказ по письму — `None`; дальше проход не идёт — вид и
    слова: писать некому (паузы ящиков судит `_why_stopped`), платформа отказывает любому
    письму или не ответила на это (своё неизвестное, `unsure`). Остальное (обрыв связи вне
    платформы, база) — исключением: проход упал, и письмо, оставшееся «отправляется», с
    `STUCK_AFTER` встанет в блок «Исход неизвестен»."""
    try:
        outcome = await sending.send(letter_id)
    except (NoSenderError, SalesNotConnectedError) as exc:
        logger.debug("продажи: автоотправка — на письме №%s писать некому: %s", letter_id, exc)
        return Stop.CAP, str(exc)
    except SendError as exc:
        # Отказ транспорта `Sending._hand_over` отдаёт причиной исключения: отказ не по письму
        # узнаётся по ней, а письмо уже вернулось в очередь.
        if isinstance(exc.__cause__, PlatformRefusalError):
            return Stop.REFUSED, _refused(letter_id, exc.__cause__)
        # Строка журнала об отказе — по смене причины у письма (`autosend_jobs.py`): письмо
        # со стоп-листом ждёт в очереди днями, а проход заглядывает в него каждую минуту.
        found.refused[letter_id] = batch.why(exc)
        found.details[letter_id] = str(exc)
        logger.debug("продажи: автоотправка — письмо №%s не ушло: %s", letter_id, batch.why(exc))
        return None
    except MaybeSentError as exc:
        # Письмо «отправляется»; проход на нём стоит сразу — с этой минуты, а не с блока
        # «Исход неизвестен». Строка — с причиной почты: в словах остановки её нет.
        logger.info("продажи: автоотправка — почта не ответила на письмо №%s: %s", letter_id, exc)
        found.unsure = letter_id
        return Stop.UNKNOWN, Unsettled(letter_id, now).words
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


def _all_refused(refused: dict[int, str]) -> str:
    """Каждое письмо окна получило отказ — словами без адресов: причины и сколько писем."""
    counted = ", ".join(f"{why}: {count}" for why, count in Counter(refused.values()).items())
    return (
        f"ни одно письмо в окне получателя не ушло — {counted}; причина каждого — в журнале разбора"
    )


def _refused(letter_id: int, refusal: PlatformRefusalError) -> str:
    """Платформа отказывает любому письму — словами без ключа и адреса: тревога и журнал."""
    return (
        f"платформа отказывает: {refusal.reason} — письмо №{letter_id} вернулось в очередь, "
        "следующие письма проход не берёт: ответ был бы тем же"
    )


def _why_stopped(
    halt: tuple[Stop, str], resting: dict[int, datetime], used: dict[int, datetime], now: datetime
) -> tuple[Stop, str]:
    """Почему проход кончился раньше писем в окне: платформа отказывает — как есть; писать
    некому — из-за пауз ящиков (до какой минуты) или по капу."""
    stop, words = halt
    if stop is not Stop.CAP or mailbox.RESTING not in words:
        return halt
    until = min([*resting.values(), *used.values()], default=now)
    return Stop.RESTING, (
        f"ящики на паузе между письмами — ближайший освободится в {until:%H:%M} UTC"
    )
