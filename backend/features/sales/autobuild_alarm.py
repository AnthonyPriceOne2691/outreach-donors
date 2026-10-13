"""Тревога автосборки (F7, решение владельца 13.10): очередь пуста, а собрать её некому.

Автоотправка включена, её проход стоит «очередь пуста» (`autosend.Facts.stop`, `Stop.EMPTY`), а
автосборка сама не соберёт: потолок модели продаж набран или сборки гипотез встали на серии писем
вне коридора (`autobuild.Plan.stuck`), или последняя сборка, поставленная самой, упала. До решения
поток стоял молча: автоотправка ждала по делу, строка экрана говорила причину, а тревоги не было.

**Одна тревога при переходе — и «прошло» при выходе**, в общий канал тем же путём, что у
автоотправки (`autosend_jobs.say`): Telegram или громкой строкой журнала, если бот не задан.
Без 15 минут ожидания, как у автоотправки: проход автосборки идёт раз в десять минут, и
состояние, которое он видит, само за минуту не пройдёт — потолок, серия вне коридора и упавшая
сборка ждут человека или следующих суток. Проход раз в десять минут не повторяет сказанного; не
принял Telegram — скажет следующий.

**Упала — значит, задача кончилась без отчёта** (исключение, повторы очереди кончились; отказ
словами — «не выполнена» — не падение: о неподключённых продажах своя тревога автоотправки).
Это помнит проход (`Watch.failed`) и держит, пока следующая его сборка не кончится без падения:
новая сборка той же гипотезы занимает номер задачи прежней, и итог упавшей иначе пропал бы на
первом же проходе, а тревога шла бы через проход — «упала», «идёт», «упала».

**Память — процесса**, как у тревоги автоотправки: выкатка начинает её заново, и тревога о том,
что ещё стоит, приходит после неё ещё раз.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales import autosend, autosend_jobs, queue_start
from backend.features.sales.autosend import Stop

if TYPE_CHECKING:
    from backend.features.sales.autobuild import Plan

#: Тревога и её отбой — о чём речь, без открытия журнала.
STUCK = (
    "тревога: автоотправка писем продаж включена, очередь пуста, а автосборка не может её собрать"
)
UNSTUCK = "прошло: автосборка снова может собрать очередь писем продаж"
FAILED = "последняя автосборка (гипотеза «{name}») упала — причина в журнале задач"


@dataclass(frozen=True, slots=True)
class Outcome:
    """Сборка, поставленная автосборкой: идёт ли и её отчёт — `None` у кончившейся: упала."""

    running: bool = False
    report: dict[str, Any] | None = None


def outcome_of(jobs: Queue, job_id: str) -> Outcome | None:
    """Сборка `job_id`, поставленная автосборкой; `None` — задачи нет, или её итог сменила
    кнопка (мета без пометки автосборки). Очередь не ответила — исключение вызывающему."""
    job = jobs.fetch_job(job_id)
    if job is None or not (job.meta or {}).get(queue_start.AUTO):
        return None
    if job.get_status() in queue_start.RUNNING:
        return Outcome(running=True)
    result = job.latest_result()
    report = result.return_value if result is not None else None
    return Outcome(report=report if isinstance(report, dict) else None)


@dataclass
class Watch:
    """Память прохода автосборки в процессе разбора: что сказано тревогой и упала ли последняя
    сборка, поставленная самой."""

    #: Почему стояла, когда сказана тревога; `None` — тревоги нет.
    told: str | None = None
    #: Последняя автосборка упала — словами; держится, пока следующая не кончится без падения.
    failed: str | None = None

    def saw(self, found: Outcome | None, name: str) -> None:
        """Исход последней автосборки: идёт, не найдена или сменена кнопкой — память прежняя."""
        if found is not None and not found.running:
            self.failed = None if found.report is not None else FAILED.format(name=name)

    async def see(self, stall: str | None) -> None:
        """Тревога — один раз, при переходе в «собрать нельзя»; отбой — один раз, при выходе.
        Telegram не принял — скажет следующий проход."""
        if stall is None:
            if self.told is not None and await autosend_jobs.say(
                f"{UNSTUCK} (стояла: {self.told})"
            ):
                self.told = None
            return
        if self.told is None and await autosend_jobs.say(f"{STUCK}: {stall}"):
            self.told = stall


#: Память прохода — одна на процесс разбора.
WATCH = Watch()


async def stalled(
    session: AsyncSession, found: Plan, failed: str | None, now: datetime
) -> str | None:
    """Почему поток писем встал, словами: очередь пуста — как её видит проход автоотправки, — а
    автосборка сама не соберёт (`Plan.stuck`) или её последняя сборка упала (`failed`); `None` —
    не встал."""
    stop = (await autosend.facts(session, now)).stop()
    if stop is None or stop[0] is not Stop.EMPTY:
        return None
    return found.why if found.stuck else failed
