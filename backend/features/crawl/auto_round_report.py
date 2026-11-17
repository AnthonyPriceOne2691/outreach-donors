"""Итог и строка автокруга рекламодателей (`auto_round.py`): где круг, что он сделал, какими словами
это сказать и какие даты остались без круга.

Своим модулем: круг упёрся бы в потолок длины файла, а итог и расписание читают трое — сам круг,
который пишет итог в строку даты по шагам, экран (`auto_round_view.py`) и сторож
(`auto_round_alarm.py`). Сторож живёт в процессе разбора с тесным пределом памяти: модуль не тянет
ни перевода, ни почты, ни очереди задач — только модели.

**Адреса считаются по базе, а не по отчёту задачи** (`found`): поиск, которого круг ждал, мог
поставить и человек — «ждут адреса · найти» или «пишем» бизнесу ниши, — и отчёт его задачи сказал бы
о чужом проходе. Найденные с начала круга — найденные за круг.

**Итог сборки — как его вернула задача** (`workers/jobs._build_letters`): собрано, воронка, почему
встала. Задача старше кода вернёт меньше ключей — итог берёт, что есть, а не падает.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import ContactSource, Stage
from backend.features.core.llm_caps import MOSCOW
from backend.features.core.models.advertiser import AutoRoundModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.crawl.auto_round_switch import Switch
from backend.features.letters.chain import FIRST_STEP

logger = logging.getLogger(__name__)

#: Не раньше этого часа по Москве: ночной круг обходов идёт с полуночи до семи (`night.py`).
START = time(7, 0)
#: Шаг судьи не кончился к этому часу — круг идёт без него.
JUDGE_WAIT_UNTIL = time(12, 0)
#: Позже круг этой даты не начинается: дата пропущена, остальное сделает следующий.
CATCH_UP_UNTIL = time(20, 0)
#: Сколько круг ждёт поиска адресов: лестница с платной ступенью идёт минутами на сотню доменов.
SEARCH_WAIT = timedelta(hours=3)

#: Что стало с режимом «уходит сама» — словами итога.
SENDING = "очередь уходит сама"
ALREADY = "очередь уже уходила сама — новые письма она возьмёт"
EMPTY = "очередь пуста — «уходит сама» не включалась"
SEND_OFF = "письма ждут «Отправить очередь»: автоотправка выключена настройкой сервиса"
#: Почему круг кончился раньше или не начался — словами строки круга.
OFF_MIDWAY = (
    "выключатель выключили посреди круга — новых задач круг не ставил, начатые доходят сами"
)
MISSED_WHY = "круг не начался за день: процесс разбора не работал или обходчики стояли"
ABANDONED = "круг не дошёл до конца: процесс разбора не работал больше суток"
SEARCH_SILENT = "очередь задач не ответила — поиск адресов не ставился"


class Phase(StrEnum):
    """Где круг."""

    PROMOTING = "promoting"  # строка заведена, перевод ещё не записан
    SEARCHING = "searching"  # переведены — ждём поиск адресов
    BUILDING = "building"  # сборка поставлена или ждёт, пока кончится сборка человека
    DONE = "done"
    STOPPED = "stopped"  # выключатель выключили посреди круга
    FAILED = "failed"  # упал — причина в `note`
    MISSED = "missed"  # не начался за день


#: Круг кончился: до завтра делать нечего.
OVER = (Phase.DONE.value, Phase.STOPPED.value, Phase.FAILED.value, Phase.MISSED.value)

#: Что круг делает сейчас — словами строки на экране и тревоги, пока он не кончился.
GOING = {
    Phase.PROMOTING.value: "переводит в рекламодатели",
    Phase.SEARCHING.value: "ждёт поиск адресов",
    Phase.BUILDING.value: "собирает очередь",
}


@dataclass(slots=True)
class RoundReport:
    """Итог круга по шагам. Хранится в строке круга — по нему экран говорит, что круг сделал."""

    #: Перевод: заведено новых, обновлено, отсеяно стоп-листами, ждут DR.
    promoted: int = 0
    updated: int = 0
    dropped: int = 0
    waiting_dr: int = 0
    #: Поиск адресов: ставился или шёл, не дождались ли, почему не дошёл.
    searched: bool = False
    search_cut: bool = False
    search_error: str | None = None
    #: Адресов найдено рекламодателям за круг и сколько из них — платной ступенью.
    found: int = 0
    paid: int = 0
    #: Сборка: рассылка, потолок писем, собрано и из скольких ждавших письма.
    campaign: str = ""
    letters: int = 0
    prepared: int = 0
    waiting: int = 0
    #: Писем рассылки круга до последней постановки сборки: повтор после сбоя собирает только
    #: остаток потолка, а итог считает письма прошлых попыток.
    earlier: int = 0
    #: Почему сборки не было или она встала раньше потолка — потолок модели словами.
    stopped: str | None = None
    #: Почему сборка не дошла до конца в прошлый раз — круг ставит её снова.
    build_error: str | None = None
    #: Почему части адресов письмо не собрано — проверка адреса не состоялась.
    unchecked: str | None = None
    #: Незаполненные настройки: собрано, а отправить нельзя.
    blocked_by: list[str] = field(default_factory=list)
    #: Что с режимом «уходит сама».
    queue: str = ""

    @classmethod
    def of(cls, stored: object) -> RoundReport:
        """Итог из строки круга: известные поля — как записаны, незнакомые ключи (их писал код
        другой версии) — мимо; строки нет или она не той формы — с нуля."""
        report = cls()
        if not isinstance(stored, dict):
            return report
        for one in fields(cls):
            if one.name in stored:
                setattr(report, one.name, stored[one.name])
        return report


def campaign_name(day: date) -> str:
    """Рассылка круга: «Автокруг 17.11 — рекламодатели по ссылке»."""
    return f"Автокруг {day:%d.%m} — рекламодатели по ссылке"


def keep(row: AutoRoundModel, report: RoundReport) -> None:
    """Итог — в строку круга."""
    row.report = asdict(report)


async def found(session: AsyncSession, since: datetime) -> tuple[int, int]:
    """Рекламодателей, которым с начала круга нашёлся адрес, и сколько из них — платной ступенью."""
    fresh = (
        select(
            ContactModel.domain_id,
            func.bool_or(ContactModel.source == ContactSource.PROVIDER).label("paid"),
        )
        .join(AdvertiserModel, AdvertiserModel.domain_id == ContactModel.domain_id)
        .where(ContactModel.created_at >= since)
        .group_by(ContactModel.domain_id)
        .subquery()
    )
    total, paid = (
        await session.execute(select(func.count(), func.count().filter(fresh.c.paid)))
    ).one()
    return int(total), int(paid)


def _count(value: object, fallback: int = 0) -> int:
    return value if isinstance(value, int) else fallback


async def campaign_letters(session: AsyncSession, name: str) -> int:
    """Первых писем в рассылке круга — собранных всеми его попытками."""
    counted = (
        select(func.count())
        .select_from(MessageModel)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(
            CampaignModel.name == name,
            CampaignModel.stage == Stage.ADVERTISERS,
            MessageModel.step == FIRST_STEP,
        )
    )
    return int(await session.scalar(counted) or 0)


def built(report: RoundReport, said: dict[str, Any] | None) -> None:
    """Итог задачи сборки — в итог круга: собрано, из скольких ждавших письма, почему встала.
    Письма прошлых попыток (`earlier`) — в обоих числах: воронка повтора их уже не видит."""
    body = said or {}
    funnel = body.get("funnel")
    prepared = _count(body.get("prepared"))
    waiting = funnel.get("ещё не писали") if isinstance(funnel, dict) else None
    report.prepared = report.earlier + prepared
    report.waiting = report.earlier + _count(waiting, prepared)
    stopped, unchecked = body.get("stopped"), body.get("unchecked")
    report.stopped = stopped if isinstance(stopped, str) else report.stopped
    report.unchecked = unchecked if isinstance(unchecked, str) else None
    blocked = body.get("blocked_by")
    report.blocked_by = [str(one) for one in blocked] if isinstance(blocked, list) else []


async def note_missed(session: AsyncSession, day: date, moment: datetime, switch: Switch) -> None:
    """Даты без круга, хотя выключатель стоял включённым с их утра: «пропущен» строкой даты — один
    раз. Вчера — всегда, сегодня — после `CATCH_UP_UNTIL`. Включили позже утра даты — круг ей не
    должен. Тревогу говорит сторож (`auto_round_alarm.py`)."""
    owed = [day - timedelta(days=1)]
    if moment.astimezone(MOSCOW).time() >= CATCH_UP_UNTIL:
        owed.append(day)
    since = switch.on_since
    owed = [one for one in owed if since and since < datetime.combine(one, START, tzinfo=MOSCOW)]
    if not owed:
        return
    known = set(
        (
            await session.scalars(select(AutoRoundModel.day).where(AutoRoundModel.day.in_(owed)))
        ).all()
    )
    for one in sorted(set(owed) - known):
        await session.execute(
            insert(AutoRoundModel)
            .values(day=one, status=Phase.MISSED.value, note=MISSED_WHY, finished_at=moment)
            .on_conflict_do_nothing(index_elements=["day"])
        )
        logger.warning("автокруг: дата пропущена", extra={"day": str(one)})
    await session.commit()


async def abandon(session: AsyncSession, day: date, moment: datetime) -> None:
    """Круг старше вчерашнего, который так и не кончился, — не дошёл: процесс лежал дольше суток."""
    stale = await session.execute(
        update(AutoRoundModel)
        .where(AutoRoundModel.day < day - timedelta(days=1), AutoRoundModel.status.not_in(OVER))
        .values(status=Phase.FAILED.value, note=ABANDONED, finished_at=moment)
        .returning(AutoRoundModel.day)
    )
    if stale.first() is not None:
        await session.commit()
