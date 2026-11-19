"""Почему автосборка обходит гипотезу: прошлая сборка встала на серии писем вне коридора — или
была пустой и ещё не отдохнула.

Итог сборки — отчёт её задачи (`queue_jobs.build_sales_queue` → `QueueReport.as_dict`): rq держит
его неделю (`queue_start.start`), и новая сборка той же гипотезы его снимает.

**Серия вне коридора** — до сборки кнопкой: шаблон первого письма не даёт писем в коридоре
отличия, и без правки цепочки каждый проход тратил бы модель на те же лиды впустую.

**Пустая сборка** — ни нового письма, ни пересборки, без ошибки и останова, а лиды ждут с
причинами: та же гипотеза снова — не раньше `EMPTY_REST`. 19.11.2026: письмо лида не пускала
проверка готового письма (`letter.problem`), а сборка ставилась каждые десять минут, переписывала
его моделью и упиралась в ту же проверку — сутки за сутками; «сначала письма» плана работ
(`plan_gates.letters_first`) держал конвейер на каждой такой сборке. Это передышка, а не
остановка: через час автосборка попробует сама, поэтому слова — не `Stuck`, и тревога «поток писем
встал» по ним не звучит. Итог без времени конца или с ошибкой судить нечем — гипотеза собирается
как прежде.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rq import Queue

from backend.features.sales import corridor
from backend.features.sales.job_holder import holder
from backend.features.sales.queue_start import build_job_id

EMPTY_REST = timedelta(hours=1)
EMPTY = (
    "прошлые сборки гипотез не собрали ни одного письма — почему лиды ждут, видно в итоге сборки "
    "на вкладке «Рассылка»; автосборка попробует снова через час или соберите очередь кнопкой"
)


def _last(jobs: Queue, hypothesis_id: int) -> tuple[dict[str, object] | None, datetime | None]:
    """Отчёт прошлой сборки гипотезы и момент её конца с поясом; чего нет — `None`."""
    job = holder(jobs, build_job_id(hypothesis_id))
    result = job.latest_result() if job is not None else None
    if result is None:
        return None, None
    report = result.return_value if isinstance(result.return_value, dict) else None
    ended = getattr(result, "created_at", None) or getattr(job, "ended_at", None)
    if not isinstance(ended, datetime):
        return report, None
    return report, ended if ended.tzinfo is not None else ended.replace(tzinfo=UTC)


def streak_stopped(jobs: Queue, hypothesis_id: int) -> bool:
    """Прошлая сборка гипотезы встала на серии писем вне коридора — и человек её ещё не пересобрал."""
    report, _ = _last(jobs, hypothesis_id)
    return report is not None and report.get("stopped") == corridor.streak_stop()


def _empty(report: dict[str, object]) -> bool:
    """Ни нового письма, ни пересборки, без ошибки и останова — а лиды ждут с причинами."""
    if report.get("prepared") or report.get("refreshed") or report.get("error"):
        return False
    return report.get("stopped") is None and bool(report.get("waiting"))


def rested_empty(jobs: Queue, hypothesis_id: int, now: datetime) -> bool:
    """Прошлая сборка гипотезы была пустой и кончилась меньше `EMPTY_REST` назад."""
    report, ended = _last(jobs, hypothesis_id)
    if report is None or ended is None:
        return False
    return _empty(report) and now - ended < EMPTY_REST
