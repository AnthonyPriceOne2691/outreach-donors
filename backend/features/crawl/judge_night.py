"""Судья сам, каждую ночь: после круга обходов разбирает новых спорных и применяет включённые полосы.

Слово владельца 10–11.11.2026: «включить этот режим автоматический, чтобы судья это делал» — тысячу
спорных руками никто не разберёт. Кнопка «Применить советы…» (`judge_apply.py`) остаётся ручным
запуском; здесь то же идёт само, раз в сутки и без человека.

**Только если включена хотя бы одна полоса** (`judge_bands.py`). Полосы выключены — шаг не начинается
вовсе: ни строки ночи, ни разбора, ни токена.

**Когда.** Ночной круг обходов этой даты кончился (`crawl_nights`: статус не «идёт») — после него и
появляются новые спорные. Круг выключен или строки у него ещё нет — не раньше `LATEST_START` по
Москве. Один раз за дату держит база (уникальная строка `advertiser_judge_nights`, как у круга);
процесс лежал — шаг догоняет в тот же день. Включили полосу днём, а шаг сегодня не шёл — он
начнётся со следующим проходом: «сегодня» для него ещё не кончилось.

**Шаг — четыре дела по порядку, и каждое переживает выкатку.**

1. Разбор новых спорных — той же задачей и тем же планом, что кнопка «Переобойти спорные»
   (`judge_night_queue.py`). Потолки модели те же: выбран до начала — разбора нет, упёрлись
   посреди — разбор встал словами; в обоих случаях шаг идёт дальше с тем, что рассужено, а остаток
   разберёт следующая ночь. Выкатка обрывает разбор на границе пачки — шаг ставит его снова, и план
   берёт только нерассуженных. Больше `MAX_TRIES` постановок за ночь — шаг встаёт с тревогой.
2. Полный пересчёт советов нынешней таблицей (`judge_store.relabel` без номеров): с таблицы v6
   совет зависит от признака донора-каталога, и только полный пересчёт находит такие строки.
3. Применение включённых полос — тем же `judge_apply.apply`, порциями: каждая фиксируется со своей
   записью журнала действий без учётки (`"кто": "ночной обход"`), партией — она стоит в списке
   партий и откатывается кнопкой, как ручная. Имя партии записано в строке ночи: шаг, оборванный
   посреди применения, продолжает ту же партию, а решённые пары в отбор не входят — дважды ничего
   не применяется.
4. Итог — в строке ночи: что рассужено, что применено и что оставлено человеку.

**Предохранители ночи — строже кнопки**: за шагом никто не смотрит.

- Не больше `NIGHT_CAP` пар на полосу за ночь; остальные — следующей ночью, числом в итоге.
- «Не пишем», которое сняло бы заведённого рекламодателя (`judge_plan.taken_off`), ночью не
  применяется: пара остаётся человеку и названа числом. Кнопка по-прежнему применяет её — после
  показа плана.
- «Пишем» само ничего не переводит и не отправляет: «Перевести» ставит поиск адресов, а это
  отдельное слово владельца. Экран говорит, сколько доменов ждут перевода.
- Шаг упал или не смог начаться за целый день — тревога один раз (образец `night._failed`), и
  строка ночи помнит почему.

**Живёт внутри прохода очереди загрузки** (`backlog.feed`), как ночной круг: свой цикл в
`workers/reaper` пошёл бы живым в чужих тестах. Проход зовёт шаг раз в минуту; когда делать нечего,
шаг — один-два коротких запроса и ни строки в журнале процесса. Выдачу обходов он не роняет: любой
свой сбой шаг ловит сам.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction
from backend.features.core.models.advertiser import JudgeBandModel, JudgeNightModel
from backend.features.core.models.crawl import CrawlNightModel
from backend.features.crawl import judge_apply, judge_bands, judge_estimate, judge_plan
from backend.features.crawl.judge_apply import PORTION, Applied, Band
from backend.features.crawl.judge_batches import WHO_KEY, portion_details
from backend.features.crawl.judge_night_queue import PassQueue, PassSeen, RqPass
from backend.features.crawl.judge_pass import RESTARTING
from backend.features.crawl.judge_store import relabel
from backend.features.crawl.night import MOSCOW, NIGHT_BY, NightStatus
from backend.features.ops.alarms import cause, marked
from backend.shared.alerts import send_alert

logger = logging.getLogger(__name__)

#: Круг обходов выключен или его строки ещё нет — шаг начинается не раньше этого часа по Москве:
#: окно круга — семь часов от полуночи, и раньше новые спорные ещё едут.
LATEST_START = time(7, 0)
#: Не больше стольких пар на полосу за ночь. Ночь приносит десятки спорных; сотни разом — это
#: завал, который человек разбирает кнопкой, глядя на план, или сломанный обход. Сверх потолка —
#: следующей ночью.
NIGHT_CAP = 300
#: Сколько раз за ночь шаг ставит разбор: выкатка и сбой — повод повторить, но не без конца.
MAX_TRIES = 3
#: Ключи строки ночи: сделан ли пересчёт советов и сколько строк он переписал.
RELABELLED = "relabelled"

#: Пометка тревоги о шаге, который не дошёл до конца: сам он сегодня уже не повторится.
NOT_DONE = cause(
    "судья этой ночью не решил спорных сам, и они ждут человека",
    "посмотреть причину на «Проверке»; следующая ночь начнётся сама, а полосу можно применить кнопкой",
)


class Phase(StrEnum):
    """Где шаг ночи."""

    JUDGING = "judging"  # разбор спорных поставлен или идёт
    APPLYING = "applying"  # пересчёт советов и применение полос
    DONE = "done"
    FAILED = "failed"  # упал или не начался — с причиной в `note`


#: Шаг кончился: до завтра делать нечего.
OVER = (Phase.DONE.value, Phase.FAILED.value)


class NightStoppedError(RuntimeError):
    """Шаг ночи не может идти дальше — словами для строки ночи и тревоги."""


@dataclass(slots=True)
class BandNight:
    """Полоса за ночь: партия и числа. Хранится в строке ночи — по ней шаг продолжает с места."""

    batch: str = ""
    applied: int = 0
    #: Пропущены: пару за это время решил человек или строку заменил обход.
    passed: int = 0
    #: Оставлены человеку: «не пишем» сняло бы рекламодателя.
    spared: int = 0
    #: Не вошли в потолок ночи — следующей ночью.
    over_cap: int = 0
    removed: int = 0
    stopped: int = 0
    done: bool = False

    @classmethod
    def of(cls, stored: object) -> BandNight:
        """Полоса из строки ночи; строки нет или она не той формы — с нуля."""
        if not isinstance(stored, dict):
            return cls()
        known = {one.name for one in fields(cls)}
        return cls(**{key: value for key, value in stored.items() if key in known})

    def add(self, portion: Applied) -> None:
        self.applied += portion.applied
        self.passed += portion.decided + portion.gone
        self.spared = portion.spared
        self.removed += portion.removed
        self.stopped += portion.stopped


def _bands_of(row: JudgeNightModel) -> dict[str, Any]:
    return dict(row.applied or {})


async def _round_over(session: AsyncSession, moment: datetime) -> bool:
    """Можно ли начинать: круг обходов этой даты кончился, а нет его строки — час настал."""
    local = moment.astimezone(MOSCOW)
    status = await session.scalar(
        select(CrawlNightModel.status).where(CrawlNightModel.night == local.date())
    )
    if status is None:
        return local.time() >= LATEST_START
    return bool(status != NightStatus.RUNNING.value)


async def _claim(session: AsyncSession, day: date, moment: datetime) -> JudgeNightModel | None:
    """Строка шага на дату. `None` — её уже завёл другой процесс: шаг один."""
    found = await session.execute(
        insert(JudgeNightModel)
        .values(night=day, status=Phase.JUDGING.value, started_at=moment)
        .on_conflict_do_nothing(index_elements=["night"])
        .returning(JudgeNightModel.id)
    )
    claimed = found.scalar_one_or_none()
    await session.commit()
    return None if claimed is None else await session.get(JudgeNightModel, claimed)


async def _to_applying(session: AsyncSession, row: JudgeNightModel, report: dict[str, Any]) -> None:
    row.report, row.status = report, Phase.APPLYING.value
    await session.commit()


async def _ask(
    session: AsyncSession, row: JudgeNightModel, moment: datetime, queue: PassQueue
) -> None:
    """Поставить разбор по свежей смете — или пойти дальше, если разбирать нечего или не на что."""
    found = await judge_estimate.estimate(session, now=moment)
    if found.refusal is not None:
        # Потолок модели выбран до начала: разбора нет, остаток — следующей ночью.
        await _to_applying(session, row, {"lines": found.lines, "stopped": found.refusal})
        return
    if found.cases == 0:
        await _to_applying(session, row, {"lines": found.lines, "done": 0, "total": 0})
        return
    if row.tries >= MAX_TRIES:
        raise NightStoppedError(
            f"разбор спорных ставился {row.tries} раза и не дошёл до конца — спорные ждут человека"
        )
    started = await asyncio.to_thread(queue.start, found.cases, by=NIGHT_BY)
    if started is None:
        return  # очередь не ответила: ничего не записано, следующий проход попробует снова
    row.pass_asked_at = moment
    row.tries += int(started)
    await session.commit()
    logger.info(
        "судья ночью: разбор спорных поставлен",
        extra={"night": str(row.night), "cases": found.cases, "own": started, "tries": row.tries},
    )


def _ours(seen: PassSeen, row: JudgeNightModel) -> bool:
    """Итог разбора — этой ночи, а не прошлой: он кончился после того, как шаг о нём спросил."""
    asked = row.pass_asked_at
    return asked is not None and seen.ended_at is not None and seen.ended_at >= asked


async def _judge(
    session: AsyncSession, row: JudgeNightModel, moment: datetime, queue: PassQueue
) -> None:
    """Разбор спорных: поставить, дождаться, взять итог; оборвался — поставить снова."""
    if row.pass_asked_at is None:
        await _ask(session, row, moment, queue)
        return
    seen = await asyncio.to_thread(queue.seen)
    if seen.state in ("going", "unknown"):
        return
    report = seen.report or {}
    if seen.state == "done" and _ours(seen, row) and report.get("stopped") != RESTARTING:
        await _to_applying(session, row, report)
        return
    # Задачи нет, она упала, итог чужой или обходчик перезапускали: ставим снова — план возьмёт
    # только тех, кого разбор не успел рассудить.
    logger.info(
        "судья ночью: разбор спорных не дошёл до конца — ставим снова",
        extra={"night": str(row.night), "state": seen.state, "error": seen.error},
    )
    await _ask(session, row, moment, queue)


async def _spare(session: AsyncSession, band: Band) -> set[str]:
    """Домены, чьи пары ночь оставляет человеку: «не пишем» сняло бы их рекламодателя."""
    if band is not Band.SKIP:
        return set()
    rows = await judge_apply.said(session)
    return await judge_plan.taken_off(
        session, {one.target_root for one in rows if one.fits(Band.SKIP)}
    )


async def _apply_band(
    session: AsyncSession, row: JudgeNightModel, band: Band, moment: datetime
) -> None:
    """Полоса — порциями до потолка ночи; каждая порция фиксируется с записью журнала и числами в
    строке ночи."""
    state = BandNight.of(_bands_of(row).get(band.value))
    if state.done:
        return
    state.batch = state.batch or judge_apply.new_batch(band, now=moment)
    left = 0
    while (room := NIGHT_CAP - state.applied) > 0:
        portion = await judge_apply.apply(
            session,
            band,
            batch=state.batch,
            limit=min(PORTION, room),
            spare=await _spare(session, band),
        )
        state.add(portion)
        left = portion.left
        if portion.applied:
            await AccessRepository(session).record(
                AuditAction.ADVERTISER_JUDGE_APPLIED,
                author_id=None,
                target=f"judge_batch:{state.batch}",
                details={**portion_details(portion), WHO_KEY: NIGHT_BY},
            )
        row.applied = {**_bands_of(row), band.value: asdict(state)}
        await session.commit()
        if left == 0 or portion.applied + portion.decided + portion.gone == 0:
            break
    state.over_cap, state.done = left, True
    row.applied = {**_bands_of(row), band.value: asdict(state)}
    await session.commit()


async def _apply(
    session: AsyncSession, row: JudgeNightModel, bands: frozenset[Band], moment: datetime
) -> None:
    """Пересчёт советов нынешней таблицей — раз за ночь, затем включённые полосы."""
    if RELABELLED not in _bands_of(row):
        row.applied = {**_bands_of(row), RELABELLED: await relabel(session)}
        await session.commit()
    for band in Band:
        # Полоса выключена — не применяется, даже если шаг начинался при включённой.
        if band in bands:
            await _apply_band(session, row, band, moment)
    row.status, row.finished_at = Phase.DONE.value, moment
    await session.commit()
    logger.info("судья ночью: шаг окончен", extra={"night": str(row.night), "applied": row.applied})


async def _failed(
    session: AsyncSession, row_id: int, day: date, why: str, moment: datetime
) -> None:
    """Шаг не дошёл до конца: причина — в строку ночи, тревога — один раз."""
    row = await session.get(JudgeNightModel, row_id)
    if row is not None:
        row.status, row.note, row.finished_at = Phase.FAILED.value, why, moment
    await session.commit()
    await send_alert(marked(f"Судья ночью {day:%d.%m} не дошёл до конца: {why}", NOT_DONE))


async def _note_missed(session: AsyncSession, day: date, moment: datetime) -> None:
    """Вчера полосы были включены, а шага не было: он не смог начаться за целый день — круг обходов
    так и не кончился или процесс лежал. Записать и сказать один раз."""
    yesterday = day - timedelta(days=1)
    known = await session.scalar(
        select(JudgeNightModel.id).where(JudgeNightModel.night == yesterday)
    )
    if known is not None:
        return
    day_start = datetime.combine(day, time.min, tzinfo=MOSCOW)
    since = await session.scalar(
        select(func.min(JudgeBandModel.set_at)).where(JudgeBandModel.enabled.is_(True))
    )
    # Полосы включили только сегодня (или строки вчера уже есть) — вчера шагу и не полагалось идти.
    if since is None or since >= day_start - timedelta(days=1) + timedelta(hours=LATEST_START.hour):
        return
    why = "шаг не начался за день: круг обходов не кончился или процесс разбора не работал"
    found = await session.execute(
        insert(JudgeNightModel)
        .values(night=yesterday, status=Phase.FAILED.value, note=why, finished_at=moment)
        .on_conflict_do_nothing(index_elements=["night"])
        .returning(JudgeNightModel.id)
    )
    first = found.scalar_one_or_none() is not None
    await session.commit()
    if first:
        await send_alert(marked(f"Судья ночью {yesterday:%d.%m} не начинал: {why}", NOT_DONE))


async def _step(
    session: AsyncSession, moment: datetime, queue: PassQueue
) -> tuple[JudgeNightModel | None, date]:
    """Один проход шага; строка ночи — чтобы сбой было куда записать."""
    day = moment.astimezone(MOSCOW).date()
    row = await session.scalar(select(JudgeNightModel).where(JudgeNightModel.night == day))
    if row is not None and row.status in OVER:
        return row, day
    bands = await judge_bands.enabled(session)
    if row is None:
        if not bands:
            return None, day
        await _note_missed(session, day, moment)
        if not await _round_over(session, moment):
            return None, day
        row = await _claim(session, day, moment)
        if row is None:
            return None, day
    if row.status == Phase.JUDGING.value:
        await _judge(session, row, moment, queue)
    if row.status == Phase.APPLYING.value:
        await _apply(session, row, bands, moment)
    return row, day


async def tick(
    session: AsyncSession, *, now: datetime | None = None, queue: PassQueue | None = None
) -> None:
    """Шаг ночи — из прохода очереди загрузки (шапка модуля). Свой сбой ловит сам: выдача обходов
    идёт дальше, а причина — в строке ночи и тревогой один раз."""
    moment = now or datetime.now(UTC)
    day = moment.astimezone(MOSCOW).date()
    row_id: int | None = None
    try:
        row, day = await _step(session, moment, queue or RqPass())
        row_id = None if row is None else row.id
    except NightStoppedError as exc:
        logger.warning("судья ночью: шаг встал", extra={"night": str(day), "why": str(exc)})
        await session.rollback()
        row_id = await _known(session, day)
        if row_id is not None:
            await _failed(session, row_id, day, str(exc), moment)
    except Exception as exc:
        logger.exception("судья ночью: шаг упал", extra={"night": str(day)})
        await session.rollback()
        row_id = await _known(session, day)
        if row_id is not None:
            await _failed(session, row_id, day, f"шаг упал: {exc}"[:500], moment)


async def _known(session: AsyncSession, day: date) -> int | None:
    found = await session.scalar(select(JudgeNightModel.id).where(JudgeNightModel.night == day))
    return None if found is None else int(found)
