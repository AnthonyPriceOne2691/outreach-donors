"""Очередь, которая уходит сама: «Отправить очередь» один раз — и остаток уходит каждый день.

Замечание тест-группы 29.10.2026: «нужна автоматическая ежедневная отправка очереди, если её
запустил, без ручного захода каждый день». Пачка выбирала дневной лимит ящиков, а остальное ждало
следующего нажатия: назавтра человек заходил и жал снова. Теперь «Отправить очередь» с отметкой
«дальше — сама» включает режим очереди этапа и аудитории (строка `queue_autosend`), и проход
процесса разбора (`workers/reaper.py`) шлёт остаток сам — пока очередь не кончится или человек не
нажмёт «Остановить». Слово владельца: включить сразу после выкатки.

**Включает только человек.** Сам режим не включается ни у какой очереди — только кнопкой, с тем,
кто нажал. Письма, которые сборка положит после того, как очередь кончилась, ждут нового нажатия:
шаг «проверяем письма и нажимаем «Отправить очередь»» у человека остаётся. Поэтому пустая очередь
режим кончает, а не держит.

**Путь письма — тот же** (`batch.send_queue` → `Sending.send`): стоп-листы, решение по адресату,
предохранитель, кап, разгон и лимиты домена; в журнал письмо пишется с тем, кто включил режим.
Своей отправки у прохода нет: проход — пачка с окном получателя и паузами ящиков (`batch.Paced`).

**Окно получателя — только у прохода** (`autosend_window.py`): письмо уходит в рабочие часы
получателя по его часам (по умолчанию пн–пт 09:00–18:00), пояс — по его стране. Кнопка окна не
ждёт: когда слать, решил человек. Вне окна у всех — проход ждёт и говорит, когда откроется
ближайшее.

**Темп — по ящику с паузой, а не всплеском.** Кнопка по-прежнему шлёт сразу, сколько позволяет кап.
Проход — нет: с ящика — не больше одного первого письма за проход, и между первыми письмами ящика —
пауза `OUTREACH_AUTOSEND_GAP_MIN` с разбросом ±50%, как у автоотправки продаж. Почта судит
рассылку по темпу ящика: десятки писем с ящика за минуту каждое утро — тот же всплеск, что у
кнопки, только без человека, который его видит. Доставляемость дороже скорости: дневной кап в
20 писем при паузе 10 минут ящик выбирает за три с небольшим часа. Пауза считается от последнего
первого письма ящика, отданного почте любым путём — кнопкой, пачкой, проходом: ящик, выпустивший
пачку, отдыхает так же. Отдано и письмо «отправляется» — с минуты захвата (`updated_at`).

**Проход стоит, режим идёт.** Писать некому (кап, разгон, лимиты, паузы ящиков) — проход ждёт
следующего; завтра лимит откроется, и проход продолжит сам. Почта отказала любому письму
(`PlatformRefusalError`) — проход встаёт, причина — строкой под кнопкой пачки, следующий пробует
снова. Почта не ответила — письмо осталось «отправляется», и ушло ли оно, неизвестно: проход стоит,
пока оно «отправляется» (своё — сразу, `unsure_id`; чужое — с блока «Исход неизвестен»,
`unknown_outcome.py`). Отказ одному письму (стоп-лист, «кому не пишем», ждёт решения) проход не
останавливает: письмо ждёт в очереди с причиной, проход идёт к следующему.

**Стоит не по делу — тревога** (`autosend_alarm.py`): почта отказала любому письму, исход
неизвестен или каждое письмо окна получило отказ — проход отдаёт это видом (`Passed.stall`), а
тревогу через 15 минут говорит общий канал тревог.

**Рубильник** (`OUTREACH_AUTOSEND`, включён по умолчанию): выключенный — проходы не идут, отметки
«дальше — сама» на экране нет, идущие запуски ждут включения. Режим без человека не включается и
при включённом. Продаж здесь нет: у них своя автоотправка (`sales/autosend.py`), и режим этапа
продаж не включается (`available`).
"""

from __future__ import annotations

import logging
import random
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    QueueAutosendModel,
    SenderModel,
)
from backend.features.core.window import Pace
from backend.features.crawl.niche import LINKS
from backend.features.letters import autosend_window, batch, mailbox, unknown_outcome
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.repository import LetterRepository
from backend.features.letters.transport import Mail

logger = logging.getLogger(__name__)

#: Разброс паузы ящика — доля `OUTREACH_AUTOSEND_GAP_MIN` в обе стороны.
SPREAD = 0.5
#: Этапы, чью очередь проход шлёт сам: у продаж своя автоотправка.
STAGES = frozenset({Stage.DONORS, Stage.ADVERTISERS})

#: Почему запуск кончился — словами строки под кнопкой пачки.
EMPTY = "очередь кончилась — новые письма уйдут, когда очередь снова отправят"
BY_HUMAN = "остановлено кнопкой «Остановить»"
#: Почему проход не идёт при идущем запуске: рубильник выключен.
SWITCHED_OFF = (
    "проходы стоят: автоотправка очереди выключена настройкой сервиса (OUTREACH_AUTOSEND) — "
    "письма уходят только кнопкой"
)


def available(stage: Stage) -> bool:
    """Можно ли включить режим очереди этапа: рубильник включён, этап — доноры или рекламодатели."""
    return cfg.AUTOSEND and stage in STAGES


def gap() -> timedelta:
    """Пауза между первыми письмами ящика до разброса."""
    return timedelta(minutes=cfg.AUTOSEND_GAP_MIN)


def pace() -> Pace:
    """Темп прохода для прогноза рассылки (`forecast.py`): пауза между первыми письмами ящика и
    её разброс."""
    return Pace(gap(), SPREAD)


def pause_of(box_id: int, letter_id: int) -> timedelta:
    """Пауза ящика `box_id` после первого письма `letter_id`: `gap()` с разбросом ±`SPREAD`. Зерно —
    ящик и письмо: разброс у каждого письма свой, но один и тот же на всех проходах."""
    draw = random.Random(f"{box_id}:{letter_id}").uniform(-1.0, 1.0)  # noqa: S311 — не секрет
    return gap() * (1 + SPREAD * draw)


@dataclass(slots=True)
class Rest:
    """Проход для пачки (`batch.Paced`): письма в окне получателя и паузы ящиков — номер ящика →
    до какой минуты."""

    until: dict[int, datetime]
    now: datetime
    only: list[int] = field(default_factory=list)

    def boxes(self) -> Collection[int]:
        return self.until.keys()

    def took(self, box_id: int, letter_id: int) -> None:
        self.until[box_id] = self.now + pause_of(box_id, letter_id)

    def free_at(self) -> datetime | None:
        """Когда освободится первый ящик на паузе; `None` — пауз нет."""
        return min(self.until.values(), default=None)


async def rests(session: AsyncSession, stage: Stage, now: datetime) -> dict[int, datetime]:
    """Ящики этапа на паузе после своего последнего первого письма: номер → до какой минуты.

    Письмо отдано почте, если ушло (`sent_at`) или «отправляется» (с минуты захвата): ящик, чей
    ответ почты потерялся, не шлёт следующее через минуту. Раньше края `gap · (1 + SPREAD)` пауза
    любого письма уже кончилась — их запрос не берёт."""
    handed = func.coalesce(MessageModel.sent_at, MessageModel.updated_at)
    went = or_(MessageModel.sent_at.is_not(None), MessageModel.status == MessageStatus.SENDING)
    rows = await session.execute(
        select(MessageModel.sender_id, MessageModel.id, handed)
        .join(SenderModel, SenderModel.id == MessageModel.sender_id)
        .where(SenderModel.stage == stage, MessageModel.step == FIRST_STEP, went)
        .where(handed > now - gap() * (1 + SPREAD))
    )
    until: dict[int, datetime] = {}
    for box, letter, at in rows.tuples().all():
        if box is None:  # ящик удалён — паузы нет; соединение по ящику таких не даёт
            continue
        ends = at + pause_of(box, letter)
        if ends > max(now, until.get(box, now)):
            until[box] = ends
    return until


async def running(
    session: AsyncSession, stage: Stage, audience: str = LINKS
) -> QueueAutosendModel | None:
    """Идущий запуск очереди этапа и аудитории; `None` — очередь сама не уходит."""
    found: QueueAutosendModel | None = await session.scalar(
        select(QueueAutosendModel).where(
            QueueAutosendModel.stage == stage,
            QueueAutosendModel.audience == audience,
            QueueAutosendModel.stopped_at.is_(None),
        )
    )
    return found


async def stage_running(session: AsyncSession, stage: Stage) -> bool:
    """Уходит ли сама хоть одна очередь этапа — прогнозу рассылки: он считает этап целиком."""
    found = await session.scalar(
        select(QueueAutosendModel.id)
        .where(QueueAutosendModel.stage == stage, QueueAutosendModel.stopped_at.is_(None))
        .limit(1)
    )
    return found is not None


async def every_running(session: AsyncSession) -> list[QueueAutosendModel]:
    """Все идущие запуски — проходу, старые первыми."""
    found = await session.scalars(
        select(QueueAutosendModel)
        .where(QueueAutosendModel.stopped_at.is_(None))
        .order_by(QueueAutosendModel.id)
    )
    return list(found.all())


async def start(
    session: AsyncSession, stage: Stage, audience: str, *, by: int | None, now: datetime
) -> tuple[QueueAutosendModel, bool]:
    """Включить режим очереди; уже идёт — прежний запуск. Второй — новый ли запуск. Без коммита."""
    found = await running(session, stage, audience)
    if found is not None:
        return found, False
    mode = QueueAutosendModel(stage=stage, audience=audience, started_at=now, started_by_id=by)
    session.add(mode)
    await session.flush()
    logger.info(
        "письма: очередь этапа %s (%s) отправляется сама — запуск №%s",
        stage.value,
        audience,
        mode.id,
    )
    return mode, True


def finish(mode: QueueAutosendModel, *, by: int | None, reason: str, now: datetime) -> None:
    """Остановить запуск: кто (`None` — проход), когда и почему. Без коммита."""
    mode.stopped_at, mode.stopped_by_id, mode.stop_reason = now, by, reason
    logger.info(
        "письма: запуск №%s очереди этапа %s остановлен — %s", mode.id, mode.stage.value, reason
    )


@dataclass(frozen=True, slots=True)
class Passed:
    """Итог прохода запуска: строка под кнопкой пачки и — если проход стоит не по делу — почему
    (тревоге, `autosend_alarm.py`). Стоит по делу (кап, паузы ящиков, вне окна у всех, очередь
    пуста) — `stall` пуст: так проход и должен ждать."""

    state: str
    stall: str | None = None


async def run(
    session: AsyncSession, transports: Mail, mode: QueueAutosendModel, *, now: datetime
) -> Passed:
    """Один проход запуска `mode`: письма очереди в окне получателя по одному с ящика, пока есть
    кому писать. Итог — и в `mode.state`; очередь кончилась — запуск остановлен. Без коммита:
    проход фиксирует вызывающий."""
    queued = await LetterRepository(session).queued(stage=mode.stage, audience=mode.audience)
    if not queued:
        finish(mode, by=None, reason=EMPTY, now=now)
        return _noted(mode, now, Passed(EMPTY))
    held = await _held(session, mode, now)
    if held is not None:
        return _noted(mode, now, Passed(held, stall=held))
    found = await autosend_window.in_window(session, queued, now)
    if found.first is None:
        return _noted(mode, now, Passed(found.words))
    rest = Rest(await rests(session, mode.stage, now), now, found.ids)
    # Свободного ящика нет — очередь не перебирается: проход раз в минуту, а ящик после письма
    # отдыхает минуты, и каждый проход иначе судил бы все письма очереди ради «писать некому».
    with mailbox.resting(rest.boxes()):
        free = await mailbox.choose(
            session, found.first.message, stage=mode.stage, thread_sender_id=None, now=now
        )
    if free.sender is None:
        said = _nobody(batch.nobody_words(free.refusal, automatic=True), rest)
        return _noted(mode, now, Passed(said))
    report = await batch.send_queue(
        session,
        transports,
        stage=mode.stage,
        author_id=mode.started_by_id,
        audience=mode.audience,
        paced=rest,
    )
    mode.unsure_id = report.unsure
    said = _words(report, rest)
    return _noted(mode, now, Passed(said, stall=said if _stalled(report) else None))


def _stalled(report: batch.BatchReport) -> bool:
    """Проход стоит не по делу: почта отказала любому письму, не ответила (исход неизвестен) или
    каждое письмо окна получило отказ, и ни одно не ушло."""
    if report.unsure is not None:
        return True
    if report.stopped is not None:
        return batch.EVERYONE_REFUSED in report.stopped
    return report.sent == 0 and bool(report.refused)


async def _held(session: AsyncSession, mode: QueueAutosendModel, now: datetime) -> str | None:
    """Почему проход стоит, не начав: исход письма неизвестен — своего (почта не ответила на письмо
    прохода, и оно всё ещё «отправляется») или из блока «Исход неизвестен» этой очереди."""
    if mode.unsure_id is not None:
        letter = await session.get(MessageModel, mode.unsure_id)
        if letter is not None and letter.status is MessageStatus.SENDING:
            return _unknown(mode.unsure_id)
        mode.unsure_id = None
    stuck = await unknown_outcome.stuck(
        session, stage=mode.stage, audience=mode.audience, now=now, limit=1
    )
    return _unknown(stuck[0].message.id) if stuck else None


def _unknown(letter_id: int) -> str:
    return (
        f"проход стоит: исход письма №{letter_id} неизвестен — ушло ли оно, не сказала почта. "
        "Исход решит событие платформы или человек в блоке «Исход неизвестен» выше"
    )


def _words(report: batch.BatchReport, rest: Rest) -> str:
    """Итог прохода словами: сколько ушло, что не ушло и почему, почему остановился."""
    parts = [f"ушло {report.sent}"]
    parts += [f"{why} — {count}" for why, count in report.refused.items()]
    said = ", ".join(parts)
    stopped = report.stopped
    return said if stopped is None else f"{said}. {_nobody(stopped, rest)}"


def _nobody(words: str, rest: Rest) -> str:
    """Почему писать некому — словами выбора ящика, а паузы ящиков — с минутой, когда первый
    освободится: «ящики на паузе» без часа читалось бы как поломка."""
    free = rest.free_at()
    if mailbox.RESTING in words and free is not None:
        return f"ящики на паузе между письмами — ближайший освободится в {free:%H:%M} UTC"
    return words


def _noted(mode: QueueAutosendModel, now: datetime, passed: Passed) -> Passed:
    mode.passed_at, mode.state = now, passed.state
    return passed


@dataclass(frozen=True, slots=True)
class State:
    """Режим очереди этапа и аудитории для экрана: можно ли, идёт ли, с какого времени и кем,
    сколько первых писем очереди ушло с запуска любым путём и чем кончился последний проход.
    Последний кончившийся запуск — когда очередь сама не уходит: кто, когда и почему остановил."""

    available: bool
    mode: QueueAutosendModel | None
    started_by: str | None = None
    stopped_by: str | None = None
    sent: int = 0
    #: Рубильник выключен при идущем запуске — почему проходы стоят.
    off: str | None = None


async def state(session: AsyncSession, stage: Stage, audience: str = LINKS) -> State:
    """Что говорить о режиме очереди под кнопкой пачки."""
    mode = await running(session, stage, audience) or await session.scalar(
        select(QueueAutosendModel)
        .where(QueueAutosendModel.stage == stage, QueueAutosendModel.audience == audience)
        .order_by(QueueAutosendModel.id.desc())
        .limit(1)
    )
    if mode is None:
        return State(available(stage), None)
    sent = 0
    if mode.stopped_at is None:
        sent = await _sent_since(session, mode)
    off = SWITCHED_OFF if mode.stopped_at is None and not cfg.AUTOSEND else None
    return State(
        available(stage),
        mode,
        started_by=await _email(session, mode.started_by_id),
        stopped_by=await _email(session, mode.stopped_by_id),
        sent=sent,
        off=off,
    )


async def _sent_since(session: AsyncSession, mode: QueueAutosendModel) -> int:
    """Первых писем очереди ушло с запуска — любым путём: кнопкой, проходом, по одному."""
    counted = (
        select(func.count())
        .select_from(MessageModel)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(
            CampaignModel.stage == mode.stage,
            CampaignModel.audience == mode.audience,
            MessageModel.step == FIRST_STEP,
            MessageModel.sent_at >= mode.started_at,
        )
    )
    return int(await session.scalar(counted) or 0)


async def _email(session: AsyncSession, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    user = await session.get(UserModel, user_id)
    return None if user is None else user.email
