"""Строки экрана «Очередь писем» об автоотправке и автосборке — тем же снимком, что у проходов.

Экран не решает сам: включена ли автоотправка, как часто уходят письма, сколько писем сейчас
в окне получателя и почему проход стоит — называет сервер одной строкой (`GET /sales/queue`),
тем же правилом, каким судит проход (`autosend.facts`, `autobuild.plan`). Строка о падении
прохода здесь не живёт: проход идёт в процессе разбора, а не в сервере, — его падение видно в
журнале и тревогой (`autosend_jobs.py`).

Выключено — так и сказано, с именем настройки: включает её администратор (решение владельца).
Тумблер включён, а предохранитель почты продаж не снят — автоотправка не включается
(`autosend.guard`): строка говорит это тем же правилом, что у прохода, значок — «выключена».
Включена — значит, список разрешённых адресатов пуст, и строка говорит это прямо: письма уходят
всем лидам очереди, а не только своим адресам.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from redis.exceptions import RedisError
from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.features.core.domain import Stage
from backend.features.core.window import WEEKDAYS
from backend.features.letters import mailbox
from backend.features.sales import autobuild, autobuild_alarm, autosend
from backend.features.sales.autosend import SPREAD, Stop

logger = logging.getLogger(__name__)

#: Включена — предохранитель снят: так и сказано, кому уходят письма.
SEND_ON = (
    "Автоотправка включена, список разрешённых адресатов почты продаж пуст — письма уходят всем "
    "лидам очереди"
)
SEND_OFF = (
    "Автоотправка выключена — первые письма уходят кнопкой «Отправить очередь» или по одному; "
    "включается настройкой SALES_AUTOSEND."
)
#: Включена, а продажи к почте не подключены: окно не считано, а предохранитель мог и не
#: сниматься (строку удалили вместо пустой) — «список пуст» и «в окне 0 писем» тут врали бы. Так
#: же — лимит первых писем продаж не прочитан: «письма уходят всем лидам» врало бы, и строка
#: говорит, какую настройку и как исправить (`autosend.Facts.broken`).
SEND_STOPPED = "Автоотправка включена, а не шлёт ничего"
BUILD_OFF = (
    "Автосборка выключена — очередь собирают кнопкой «Собрать очередь»; включается настройкой "
    "SALES_AUTOBUILD."
)
#: Письма в окне есть и ящик свободен, а как есть не уйдёт ни одно — проход стоит, отказывая
#: каждому (`autosend.Stop.REFUSED_ALL`). Правило — счёт очереди автосборки (`autobuild.queued`).
STALE = (
    "ни одно письмо в окне получателя не уйдёт как есть — они собраны с прежними подписью и "
    "адресом «Отправителя» или до правки лида"
)
BY_AUTOBUILD = "их пересоберёт ближайший проход автосборки"
BY_BUTTON = "соберите очередь заново кнопкой «Собрать очередь»"
CLOSED = (
    "ни одно письмо в окне получателя не уйдёт — их лидов сняли (стоп-лист продаж, отказ) или "
    "лиды в «кому не пишем»; причина каждого — в журнале разбора"
)


@dataclass(frozen=True, slots=True)
class AutoLine:
    """Включено ли и что сейчас — одной строкой словами."""

    enabled: bool
    line: str


def letters(count: int) -> str:
    """«1 письмо», «3 письма», «5 писем», «21 письмо»."""
    if count % 100 in range(11, 15) or count % 10 in (0, *range(5, 10)):
        return f"{count} писем"
    return f"{count} письмо" if count % 10 == 1 else f"{count} письма"


def pace() -> str:
    """Как часто уходят письма — из настроек прохода."""
    gap = sales_cfg.AUTOSEND_GAP_MIN
    shortest, longest = round(gap * (1 - SPREAD)), round(gap * (1 + SPREAD))
    # Число и единица — неразрывным пробелом: на телефоне «60» и «с» расходились по строкам.
    return (
        f"проход раз в {sales_cfg.AUTOSEND_PASS_SEC}\u00a0с, с ящика — не больше письма за проход "
        f"и пауза {shortest}–{longest}\u00a0мин между его первыми письмами"
    )


async def autosend_line(session: AsyncSession, now: datetime) -> AutoLine:
    """Автоотправка одной строкой: включена ли, как часто, сколько в окне, почему стоит."""
    if not sales_cfg.AUTOSEND:
        return AutoLine(enabled=False, line=SEND_OFF)
    guarded = autosend.guard()
    if guarded is not None:
        return AutoLine(enabled=False, line=f"Автоотправка не включается: {guarded}.")
    seen = await autosend.facts(session, now)
    broken = seen.broken()
    if broken is not None:
        return AutoLine(enabled=True, line=f"{SEND_STOPPED}: {broken[1]}.")
    stop = seen.stop()
    head = f"{SEND_ON}: {pace()}. В окне получателя сейчас {letters(len(seen.waiting.ids))}"
    if stop is not None:
        return AutoLine(enabled=True, line=f"{head}. Стоит: {stop[1]}.")
    stands = await none_goes(session, seen)
    if stands is not None:
        return AutoLine(enabled=True, line=f"{head}. Стоит: {stands}.")
    return AutoLine(enabled=True, line=f"{head}. {await _going(session, seen, now)}.")


def batch_line() -> str | None:
    """Строка вместо кнопки пачки, пока включена автоотправка (`autosend.switched_on`, решение
    владельца 13.10): письма уходят сами, а отправить вручную — выключить её. `None` — кнопка
    есть. Тем же правилом пачке откажет сервер (`mail.batch`)."""
    if not autosend.switched_on():
        return None
    return f"{autosend.BATCH_OFF.capitalize()}: {autosend.BY_ITSELF}."


async def none_goes(session: AsyncSession, seen: autosend.Facts) -> str | None:
    """Ни одно письмо в окне получателя не уйдёт как есть — почему и что делать; `None` — хоть
    одно уйдёт. Без этого строка писала «Письма уходят», пока проход отказывал каждому письму
    окна (подпись «Отправителя» поправили — замер стенда, ревью стыков отправки). Те же слова —
    у прогноза рассылки, почему автоотправка стоит (`forecast_answers.autosend_stop`)."""
    found = await autobuild.queued(session)
    window = set(seen.waiting.ids)
    if window & found.ready:
        return None
    if window & found.redo:
        return f"{STALE}: {BY_AUTOBUILD if sales_cfg.AUTOBUILD else BY_BUTTON}"
    # Письма, которые держит адрес лида, в окно не идут (`autosend_window.py`): очередь из одних
    # них проход называет сам (`Stop.HELD`), тем же правилом, что `Queued.held`.
    return CLOSED


async def _going(session: AsyncSession, seen: autosend.Facts, now: datetime) -> str:
    """Слать есть что и кому: уходят — или ждут паузы ящиков, до какой минуты."""
    resting = await autosend.rests(session, now)
    if seen.waiting.first is None or not resting:
        return "Письма уходят"
    with mailbox.resting(resting.keys()):
        found = await mailbox.choose(
            session, seen.waiting.first, stage=Stage.SALES, thread_sender_id=None, now=now
        )
    if found.sender is not None:
        return "Письма уходят"
    return f"{Stop.RESTING.value.capitalize()} — ближайший освободится в {min(resting.values()):%H:%M} UTC"


async def autobuild_line(session: AsyncSession, jobs: Queue, now: datetime) -> AutoLine:
    """Автосборка одной строкой: хватает ли очереди на ближайший день отправки (мощность ящиков в
    него — та же, что у прогноза рассылки на этот день), кого соберёт или почему не собирает, когда
    и сколько собрано само."""
    if not sales_cfg.AUTOBUILD:
        return AutoLine(enabled=False, line=BUILD_OFF)
    found = await autobuild.plan(session, jobs, now=now)
    day = f"{WEEKDAYS[found.day.weekday()]} {found.day:%d.%m}" if found.day else "—"
    parts = [
        f"Автосборка включена: очередь — на ближайший день отправки, {day}: мощность ящиков "
        f"продаж — {letters(found.need)}, в очереди готовых к отправке — {letters(found.have)}"
    ]
    if not found.short:
        parts.append("хватает")
    elif found.hypothesis_id is not None:
        parts.append(
            f"не хватает {letters(found.short)} — ближайший проход соберёт гипотезу "
            f"«{found.name}», до {letters(found.limit)}"
        )
    else:
        parts.append(f"не хватает {letters(found.short)}, но не собирается: {found.why}")
    last = await _last(session, jobs)
    if last is not None:
        parts.append(last)
    return AutoLine(enabled=True, line="; ".join(parts) + ".")


async def _last(session: AsyncSession, jobs: Queue) -> str | None:
    """Последняя автосборка: когда, какая гипотеза, до скольких писем и сколько собрано."""
    row = await autobuild.last_started(session)
    if row is None or row.details is None or row.target is None:
        return None
    said = (
        f"последняя автосборка — {row.created_at:%d.%m %H:%M} UTC, гипотеза "
        f"«{row.details.get('гипотеза')}», до {letters(int(row.details.get('писем', 0)))}"
    )
    return said + await asyncio.to_thread(_collected, jobs, row.target.removeprefix("job:"))


def _collected(jobs: Queue, job_id: str) -> str:
    """Сколько собрала сборка, поставленная автосборкой, — пока её итог не сменила кнопка."""
    try:
        found = autobuild_alarm.outcome_of(jobs, job_id)
    except RedisError as exc:
        logger.warning("продажи: итог автосборки не прочитан", extra={"error": str(exc)})
        return ""
    if found is None:
        return ""
    return ": идёт" if found.running else _outcome(found.report)


def _outcome(report: object) -> str:
    """Итог сборки словами: сколько собрано и почему встала — или что не выполнена."""
    if not isinstance(report, dict):
        return ": упала — причина в журнале задач"
    if report.get("error"):
        return f": не выполнена — {report['error']}"
    collected = int(report.get("prepared", 0)) + int(report.get("refreshed", 0))
    stopped = f", встала: {report['stopped']}" if report.get("stopped") else ""
    return f": собрано {letters(collected)}{stopped}"
