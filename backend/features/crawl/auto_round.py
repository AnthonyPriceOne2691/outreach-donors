"""Автокруг рекламодателей: после ночи сервис сам переводит «пишем» в рекламодатели, ищет им адреса,
собирает очередь и отпускает её уходить саму — без трёх кнопок.

Слово владельца 17.11.2026 («Да, делай»; замысел — `delivery/BACKLOG.md`, 11.11.2026). После ночного
шага судьи (`judge_night.py`) человек жал «Перевести в рекламодатели», «Собрать очередь» и «Отправить
очередь» с «дальше — сама». Теперь это круг раз в сутки — **только если человек включил выключатель**
(`auto_round_switch.py`, по умолчанию выключен) и рубильник сервиса не выключен (`config/auto_round`).

**Когда.** Не раньше `START` по Москве, после ночного шага судьи этой даты; шага не будет (полосы
выключены) — после ночного круга обходов. Шаг судьи не кончился к `JUDGE_WAIT_UNTIL` — круг идёт без
него: остальное «пишем» переведёт следующий. Один раз в сутки держит база (уникальная строка
`advertiser_auto_rounds`, как у шага судьи); процесс лежал — круг догоняет в тот же день, но не позже
`CATCH_UP_UNTIL`, дальше дата записывается пропущенной. Включили выключатель днём, а круга сегодня не
было — он начнётся со следующим проходом. Дата круга — московская дата его начала: круг, начатый
вечером, доходит под своей датой, а круг новой даты начинается после него.

**Круг — четыре дела по порядку, и каждое переживает выкатку** (шаг — в строке круга):

1. Перевод — тем же `promote.promote`, что у кнопки, с записью журнала от имени автокруга.
2. Поиск адресов — той же задачей, что ставит кнопка (`auto_round_queue.py`), но не больше
   суточного потолка писем: платная ступень тратится на тех, кому круг успеет написать. Круг ждёт
   конца поиска не дольше `SEARCH_WAIT` — дальше собирает тем, у кого адрес уже есть.
3. Сборка — задачей кнопки «Собрать очередь» под её номером: рассылка «Автокруг ДД.ММ — рекламодатели
   по ссылке», текст и сроки добивок по умолчанию, писем — не больше суточного потолка выключателя,
   и повтор после сбоя собирает только остаток потолка. Проверка адреса перед письмом и потолок
   модели — внутри сборки, как у кнопки.
4. Очередь «По найденной ссылке» уходит сама (`letters/autosend.py`). «Включает только человек» —
   правило режима, которое автокруг меняет осознанно, по слову владельца: режим включает круг, от
   имени того, кто включил выключатель. Режим уже идёт — новые письма он подхватит сам; очередь пуста
   — режим не включается. **Пачку круг не шлёт никогда**: «Отправить очередь» без отметки отдаёт почте
   всю очередь разом (`letters/batch.py::send_queue`), а размеренно уходит только проход режима.

**Потолки — не сбой.** Потолок модели выбран до сборки — сборки нет; упёрлись посреди — собранное
остаётся в очереди; месячные потолки платного поиска и проверок адреса держат свои задачи. Круг
делает, что может, и говорит об этом в итоге, а не падает.

**Человеку остаются** «не понять», пары, чьё «не пишем» сняло бы рекламодателя, ответы и лиды — их
круг не трогает. Выключатель выключили посреди круга — новые задачи не ставятся, круг кончается тем,
что сделано.

**Сбой — строкой круга, тревогу говорит сторож** (`auto_round_alarm.py`): круг упал, не начался за
день или стоит на шаге слишком долго. Сборка ставится не больше `MAX_TRIES` раз за круг.

**Живёт внутри прохода очереди загрузки** (`backlog.feed`), после шага судьи, — как он: свой цикл в
`workers/reaper` шёл бы живым в чужих тестах. Делать нечего — один-два коротких запроса. Свой сбой
круг ловит сам: выдачу обходов он не роняет.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import auto_round as cfg
from backend.features.access.repository import AccessRepository
from backend.features.core import usage
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.advertiser import AutoRoundModel, JudgeNightModel
from backend.features.core.models.crawl import CrawlNightModel
from backend.features.crawl import auto_round_switch, judge_bands, promote
from backend.features.crawl.auto_round_queue import BUILD_JOB_ID, RoundQueue, RqRound
from backend.features.crawl.auto_round_report import (
    ALREADY,
    CATCH_UP_UNTIL,
    EMPTY,
    JUDGE_WAIT_UNTIL,
    OFF_MIDWAY,
    OVER,
    SEARCH_SILENT,
    SEARCH_WAIT,
    SEND_OFF,
    SENDING,
    START,
    Phase,
    RoundReport,
    abandon,
    built,
    campaign_letters,
    campaign_name,
    found,
    keep,
    note_missed,
)
from backend.features.crawl.auto_round_switch import Switch
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.crawl.judge_batches import WHO_KEY
from backend.features.crawl.judge_night import OVER as JUDGE_OVER
from backend.features.crawl.niche import LINKS, WHOM
from backend.features.crawl.night import MOSCOW, NightStatus
from backend.features.letters import autosend
from backend.features.letters.repository import LetterRepository

logger = logging.getLogger(__name__)

#: Кто делал — в журнале действий (учётки у круга нет) и в словах экрана.
BY = "автокруг"
#: Ключ журнала: кто разрешил — учётка, включившая выключатель.
ALLOWED_KEY = "выключатель включил"
#: Сколько раз за круг ставится сборка: выкатка и сбой — повод повторить, но не без конца.
MAX_TRIES = 3


class RoundStoppedError(RuntimeError):
    """Круг не может идти дальше — словами для строки круга."""


def _since(row: AutoRoundModel) -> datetime:
    """Начало круга; у строки без него — минута её записи."""
    return row.started_at or row.created_at


def _who(switch: Switch) -> dict[str, str]:
    """Подробности журнала: делал автокруг — с разрешения того, кто включил выключатель."""
    who = {WHO_KEY: BY}
    if switch.set_by is not None:
        who[ALLOWED_KEY] = switch.set_by
    return who


async def ready(session: AsyncSession, moment: datetime) -> bool:
    """Можно ли начинать круг даты: окно дня открыто, а ночной шаг судьи кончился — или его не
    будет, или ждать его дольше незачем."""
    local = moment.astimezone(MOSCOW)
    if not START <= local.time() < CATCH_UP_UNTIL:
        return False
    if local.time() >= JUDGE_WAIT_UNTIL:
        return True
    judge = await session.scalar(
        select(JudgeNightModel.status).where(JudgeNightModel.night == local.date())
    )
    if judge is not None:
        return judge in JUDGE_OVER
    if await judge_bands.enabled(session):
        return False  # шаг судьи будет: «пишем» этой ночи ещё впереди
    crawl = await session.scalar(
        select(CrawlNightModel.status).where(CrawlNightModel.night == local.date())
    )
    return crawl != NightStatus.RUNNING.value


async def _claim(session: AsyncSession, day: date, moment: datetime) -> AutoRoundModel | None:
    """Строка круга на дату. `None` — её уже завёл другой процесс: круг один."""
    inserted = await session.execute(
        insert(AutoRoundModel)
        .values(day=day, status=Phase.PROMOTING.value, started_at=moment)
        .on_conflict_do_nothing(index_elements=["day"])
        .returning(AutoRoundModel.id)
    )
    claimed = inserted.scalar_one_or_none()
    await session.commit()
    return None if claimed is None else await session.get(AutoRoundModel, claimed)


async def _promote(session: AsyncSession, row: AutoRoundModel, switch: Switch) -> None:
    """Перевод «куплено» и «пишем» — тем же путём, что у кнопки, с записью журнала."""
    done = await promote.promote(session)
    await AccessRepository(session).record(
        AuditAction.ADVERTISER_REVIEWED,
        author_id=None,
        target="advertisers:promote",
        details={"действие": "перевод в рекламодатели", **done.as_dict(), **_who(switch)},
    )
    report = RoundReport.of(row.report)
    report.promoted, report.updated, report.waiting_dr = (
        done.promoted,
        done.updated,
        done.waiting_dr,
    )
    report.dropped = done.skipped_supplier + done.skipped_suppressed
    keep(row, report)
    row.status = Phase.SEARCHING.value
    await session.commit()
    logger.info("автокруг: перевод записан", extra={"day": str(row.day), **done.as_dict()})


async def _to_building(session: AsyncSession, row: AutoRoundModel, report: RoundReport) -> None:
    report.found, report.paid = await found(session, _since(row))
    keep(row, report)
    row.status, row.asked_at = Phase.BUILDING.value, None
    await session.commit()


async def _search_over(
    row: AutoRoundModel, moment: datetime, queue: RoundQueue, report: RoundReport
) -> bool:
    """Поиск, которого ждёт круг, кончился — или ждать его дольше нельзя (`SEARCH_WAIT`)."""
    seen = await asyncio.to_thread(queue.search_seen)
    if seen.state in ("going", "unknown"):
        asked = row.asked_at or _since(row)
        report.search_cut = moment >= asked + SEARCH_WAIT
        return report.search_cut
    if seen.state == "failed":
        report.search_error = seen.error or "задача поиска упала"
    return True


async def _search(
    session: AsyncSession,
    row: AutoRoundModel,
    moment: datetime,
    queue: RoundQueue,
    switch: Switch,
) -> None:
    """Поиск адресов: поставить, дождаться, перейти к сборке. Ждущих нет — сразу к сборке; очередь
    не отвечает весь срок ожидания — к сборке тем, у кого адрес уже есть. Ищется не больше
    суточного потолка писем: платная ступень тратится на тех, кому круг успеет написать, остальные
    ждут следующего круга — или «найти» человека."""
    pending = await AdvertiserContactRepository(session).pending_count()
    report = RoundReport.of(row.report)
    if pending and row.asked_at is None:
        if moment < _since(row) + SEARCH_WAIT:
            limit = min(pending, switch.letters)
            if await asyncio.to_thread(queue.search, limit) is None:
                return  # очередь не ответила: ничего не записано, следующий проход попробует снова
            row.asked_at, report.searched = moment, True
            keep(row, report)
            await session.commit()
            logger.info("автокруг: поиск адресов поставлен", extra={"pending": pending})
            return
        report.search_error = SEARCH_SILENT
    elif pending and not await _search_over(row, moment, queue, report):
        return
    await _to_building(session, row, report)


async def _model_refusal(session: AsyncSession) -> str | None:
    """Потолок расхода на модель выбран — словами; иначе `None`. Тем же правилом сборка проверяет
    его перед каждой пачкой: выбран до начала — сборку ставить незачем."""
    try:
        await usage.ensure_llm_within_cap(session)
    except usage.LlmCapExceededError as exc:
        logger.warning("автокруг: потолок модели выбран — сборки не будет", extra={"why": str(exc)})
        return str(exc)
    return None


async def _send(session: AsyncSession, switch: Switch, moment: datetime) -> str:
    """Очередь «По найденной ссылке» уходит сама — режим от имени включившего выключатель. Уже
    уходит, пуста или автоотправка выключена — режим не трогается. Пачки нет никогда."""
    waiting = await LetterRepository(session).queued_count(stage=Stage.ADVERTISERS, audience=LINKS)
    if waiting == 0:
        return EMPTY
    if not autosend.available(Stage.ADVERTISERS):
        return SEND_OFF
    try:
        async with session.begin_nested():
            mode, fresh = await autosend.start(
                session, Stage.ADVERTISERS, LINKS, by=switch.by_id, now=moment
            )
    except IntegrityError:
        # Человек включил режим в ту же секунду: запуск у очереди один (`uq_queue_autosend_running`).
        logger.info("автокруг: очередь включили уходить саму в ту же секунду — она уже уходит")
        return ALREADY
    if not fresh:
        return ALREADY
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=None,
        target=f"queue_autosend:{mode.id}",
        details={
            "действие": "очередь уходит сама",
            "этап": "рекламодатели",
            "кому": WHOM[LINKS],
            **_who(switch),
        },
    )
    return SENDING


async def _finish(
    session: AsyncSession, row: AutoRoundModel, report: RoundReport, moment: datetime
) -> None:
    keep(row, report)
    row.status, row.finished_at = Phase.DONE.value, moment
    await session.commit()
    logger.info("автокруг: круг окончен", extra={"day": str(row.day), "report": row.report})


async def _ask_build(
    session: AsyncSession,
    row: AutoRoundModel,
    moment: datetime,
    queue: RoundQueue,
    switch: Switch,
) -> None:
    """Поставить сборку круга. Потолок модели выбран — сборки нет, круг кончается режимом очереди
    и словами; идёт сборка человека или очередь молчит — следующий проход."""
    report = RoundReport.of(row.report)
    refusal = await _model_refusal(session)
    if refusal is not None:
        report.stopped = refusal
        report.queue = await _send(session, switch, moment)
        await _finish(session, row, report, moment)
        return
    if row.tries >= MAX_TRIES:
        last = f": {report.build_error}" if report.build_error else ""
        raise RoundStoppedError(
            f"сборка ставилась {row.tries} раза и не дошла до конца{last} — собранное в очереди, "
            "остальное соберёт кнопка или следующий круг"
        )
    report.campaign, report.letters = campaign_name(row.day), switch.letters
    report.earlier = await campaign_letters(session, report.campaign)
    left = switch.letters - report.earlier
    if left <= 0:
        # Прошлые попытки уже собрали потолок суток: повтор сверх него собрал бы лишнее.
        report.prepared = report.waiting = report.earlier
        report.queue = await _send(session, switch, moment)
        await _finish(session, row, report, moment)
        return
    asked = await asyncio.to_thread(queue.build, report.campaign, left)
    if asked != "asked":
        return  # собирает человек — ждём его сборку; очередь не ответила — следующий проход
    row.asked_at, row.tries = moment, row.tries + 1
    keep(row, report)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=None,
        target=f"job:{BUILD_JOB_ID}",
        details={
            "действие": "сборка писем",
            "этап": "рекламодатели",
            "кампания": report.campaign,
            "писем": left,
            "добивки, дней": "по умолчанию",
            "текст письма": "по умолчанию",
            "кому": WHOM[LINKS],
            **_who(switch),
        },
    )
    await session.commit()
    logger.info("автокруг: сборка поставлена", extra={"day": str(row.day), "tries": row.tries})


async def _build(
    session: AsyncSession,
    row: AutoRoundModel,
    moment: datetime,
    queue: RoundQueue,
    switch: Switch,
) -> None:
    """Сборка: поставить, дождаться, взять итог и отпустить очередь; оборвалась — поставить снова,
    отказала — круг встал."""
    if row.asked_at is None:
        await _ask_build(session, row, moment, queue, switch)
        return
    seen = await asyncio.to_thread(queue.build_seen)
    if seen.state in ("going", "unknown"):
        return
    if seen.state == "done" and seen.ended_at is not None and seen.ended_at >= row.asked_at:
        report = RoundReport.of(row.report)
        built(report, seen.report)
        report.queue = await _send(session, switch, moment)
        await _finish(session, row, report, moment)
        return
    # Задачи нет, она упала, отказала или её воркер умер: ставим снова — сборка продолжит с
    # ненаписанных, а причина остаётся для итога, если повторы кончатся.
    logger.info(
        "автокруг: сборка не дошла до конца — ставим снова",
        extra={"day": str(row.day), "state": seen.state, "error": seen.error},
    )
    report = RoundReport.of(row.report)
    report.build_error = seen.error or report.build_error
    keep(row, report)
    row.asked_at = None
    await session.commit()


async def _stop(session: AsyncSession, row: AutoRoundModel, moment: datetime) -> None:
    """Выключатель выключили посреди круга: круг кончается тем, что сделано."""
    row.status, row.note, row.finished_at = Phase.STOPPED.value, OFF_MIDWAY, moment
    await session.commit()
    logger.info("автокруг: выключен посреди круга", extra={"day": str(row.day)})


async def _open(session: AsyncSession, day: date) -> AutoRoundModel | None:
    """Строка, с которой круг работает: незаконченная вчерашняя — иначе сегодняшняя."""
    opened: AutoRoundModel | None = await session.scalar(
        select(AutoRoundModel)
        .where(
            AutoRoundModel.day.in_((day - timedelta(days=1), day)),
            or_(AutoRoundModel.day == day, AutoRoundModel.status.not_in(OVER)),
        )
        .order_by(AutoRoundModel.day)
        .limit(1)
    )
    return opened


async def _start(
    session: AsyncSession, day: date, moment: datetime, switch: Switch
) -> AutoRoundModel | None:
    """Начать круг даты, если пора; пропущенные и брошенные даты — записать."""
    await abandon(session, day, moment)
    await note_missed(session, day, moment, switch)
    if not await ready(session, moment):
        return None
    row = await _claim(session, day, moment)
    if row is not None:
        logger.info("автокруг: круг начат", extra={"day": str(day), "letters": switch.letters})
    return row


async def _step(session: AsyncSession, moment: datetime, queue: RoundQueue) -> None:
    """Один проход круга."""
    day = moment.astimezone(MOSCOW).date()
    row = await _open(session, day)
    if row is not None and row.status in OVER:
        return
    switch = await auto_round_switch.state(session)
    if row is None:
        if not switch.enabled:
            return
        row = await _start(session, day, moment, switch)
        if row is None:
            return
    if not switch.enabled:
        await _stop(session, row, moment)
        return
    await _advance(session, row, moment, queue, switch)


async def _advance(
    session: AsyncSession,
    row: AutoRoundModel,
    moment: datetime,
    queue: RoundQueue,
    switch: Switch,
) -> None:
    """Шаги круга по порядку — сколько успеет этот проход: кончившийся шаг сразу передаёт дальше."""
    if row.status == Phase.PROMOTING.value:
        await _promote(session, row, switch)
    if row.status == Phase.SEARCHING.value:
        await _search(session, row, moment, queue, switch)
    if row.status == Phase.BUILDING.value:
        await _build(session, row, moment, queue, switch)


async def _fail_open(session: AsyncSession, day: date, why: str, moment: datetime) -> None:
    """Сбой — в строку круга, который шёл: причина словами, тревогу скажет сторож. Круг ещё не
    начинался или уже кончился — помечать нечего: о сбое говорит строка журнала процесса."""
    await session.rollback()
    row = await _open(session, day)
    if row is not None and row.status not in OVER:
        row.status, row.note, row.finished_at = Phase.FAILED.value, why, moment
        await session.commit()


async def tick(
    session: AsyncSession, *, now: datetime | None = None, queue: RoundQueue | None = None
) -> None:
    """Круг — из прохода очереди загрузки (шапка модуля). Рубильник выключен — ничего. Свой сбой
    ловит сам: выдача обходов идёт дальше, а причина — в строке круга."""
    if not cfg.ENABLED:
        return
    moment = now or datetime.now(UTC)
    day = moment.astimezone(MOSCOW).date()
    try:
        await _step(session, moment, queue or RqRound())
    except RoundStoppedError as exc:
        logger.warning("автокруг: встал", extra={"day": str(day), "why": str(exc)})
        await _fail_open(session, day, str(exc), moment)
    except Exception as exc:
        logger.exception("автокруг: упал", extra={"day": str(day)})
        await _fail_open(session, day, f"круг упал: {exc}"[:500], moment)
