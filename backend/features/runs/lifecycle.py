"""Жизнь прогона между нажатием и итогом: признак живости и разбор мёртвых.

Зачем это вообще. Прогон уходит в очередь, и до сегодняшнего дня на этом
его видимая жизнь заканчивалась: строка в базе появлялась только перед
первой тратой, а если задачу никто не брал — не появлялась никогда.
Проверка 21.09.2026 нашла в очереди задачу, провисевшую сутки: сервер
ответил «поставлено», экран показал успех, воркера не было.

Три правила, и каждое стоит замера — своего или соседней системы.

**Живой прогон отмечается сам.** Между нажатием и первой записью идут
выдача и метрики, это минуты; по одному «когда последний раз менялась
строка» медленный прогон неотличим от мёртвого. В соседней системе
прогон на 1050 ссылках из 2000 похоронили именно так.

**Смерть определяет воркер, а не статус задачи.** Задача умершего
воркера остаётся «выполняется» до своего таймаута — у нас это час.

**«Не знаю» — не «мертва».** Redis недоступен, и приняв это за смерть,
мы поставим вторую задачу на тот же прогон. Прогон платный, и вторая
задача — это второй счёт.

Продолжение здесь дешёвое только потому, что выдача сохраняется в строке
прогона (`runs.candidates`): без этого каждая попытка покупала бы её
заново, и лечение было бы дороже болезни.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.features.core.domain import RunStatus
from backend.features.core.models.run import RunModel
from backend.features.runs.repository import REASON_KEY, RESUMES_KEY, RunRepository

logger = logging.getLogger(__name__)

#: Как часто прогон отмечается живым. Кратно меньше порогов ниже, чтобы
#: один несостоявшийся удар не выглядел смертью.
HEARTBEAT_INTERVAL_SEC = 30.0

#: Сколько молчания считать подозрительным. Заметно больше интервала
#: ударов, но кратно меньше похорон: сначала пытаемся довести прогон
#: до конца и только потом закрываем.
RESUME_AFTER_SEC = 180.0

#: Сколько молчания считать смертью, которую уже не вылечить.
STALE_AFTER_SEC = 900.0

#: Сколько раз продолжаем один прогон. Без потолка задача, падающая
#: сразу (негодная настройка, недоступная база), воскресала бы вечно,
#: и каждый круг стоил бы метрик Ahrefs по уже собранным доменам.
MAX_RESUMES = 2

#: Ключ счётчика в отчёте прогона. Лежит там же, где остальной отчёт:
#: человек, разбирающий «почему прогон странный», читает одно место.

#: Жива ли задача: `True` / `False` / `None` (выяснить не удалось).
AliveCheck = Callable[[str | None], bool | None]

#: Почему задача упала: строка исключения или `None` — не падала / не узнать.
FailureCheck = Callable[[str | None], str | None]


def _no_failure(_job_id: str | None) -> str | None:
    return None


def _cause(failure: FailureCheck, job_id: str | None) -> str:
    """Упавшая задача и умерший воркер для разбора одинаково «мертвы», но
    человеку нужна причина: у первой она есть, и подменять её нельзя."""
    fell = failure(job_id)
    return f"задача упала: {fell}" if fell else "воркер умер"


#: Поставить задачу прогону заново. Возвращает номер новой задачи.
Enqueue = Callable[[int], str | None]


async def heartbeat(
    repository: RunRepository, run_id: int, *, interval: float | None = None
) -> None:
    """Отмечать прогон живым, пока задачу не отменят.

    Отмена пропускается наружу — это штатный способ остановки. Сбой
    записи только логируется: удар не должен ронять прогон, ради
    которого он бьётся.
    """
    every = HEARTBEAT_INTERVAL_SEC if interval is None else interval
    while True:
        await asyncio.sleep(every)
        try:
            await repository.touch(run_id)
        except Exception:
            logger.exception("Прогон %s: отметка о жизни не записалась", run_id)


@dataclass(frozen=True, slots=True)
class Recovery:
    """Что сделал один проход разбора мёртвых."""

    resumed: list[int] = field(default_factory=list)
    stopped: list[int] = field(default_factory=list)
    unknown: list[int] = field(default_factory=list)
    """Прогоны, про которые не удалось выяснить, живы ли они. Не ошибка,
    а состояние: следующий проход спросит ещё раз."""

    @property
    def as_report(self) -> str:
        return (
            f"продолжено {len(self.resumed)}, закрыто {len(self.stopped)}, "
            f"не выяснено {len(self.unknown)}"
        )


def resumes_done(run: RunModel) -> int:
    """Сколько раз этот прогон уже продолжали."""
    stats = run.stats or {}
    try:
        return int(stats.get(RESUMES_KEY, 0))
    except (TypeError, ValueError):
        # Отчёт правили руками. Считаем, что продолжений не было, но
        # говорим об этом: молчаливый ноль здесь означает вечную петлю.
        logger.warning("Прогон %s: счётчик продолжений испорчен — считаю нулём", run.id)
        return 0


def _with_note(run: RunModel, **fields: Any) -> dict[str, Any]:
    """Отчёт прогона плюс пометки разбора. Старое не затирается: в нём
    лежит то, что прогон успел сделать до смерти."""
    stats = dict(run.stats or {})
    stats.update(fields)
    return stats


async def recover(
    repository: RunRepository,
    *,
    alive: AliveCheck,
    enqueue: Enqueue,
    failure: FailureCheck = _no_failure,
    now: datetime | None = None,
) -> Recovery:
    """Один проход: продолжить осиротевших, закрыть безнадёжных.

    Порядок важен. Сначала продолжение (через `RESUME_AFTER_SEC`), потом
    похороны (через `STALE_AFTER_SEC`): сначала пытаемся довести прогон
    до конца, и только если не вышло — признаём его остановленным.
    """
    moment = now or datetime.now(UTC)
    resumed: list[int] = []
    stopped: list[int] = []
    unknown: list[int] = []

    # «В очереди» проверяется наравне с «идёт», и это отличие от соседней
    # системы: там задачу, которую никто не взял, не ищет никто, и она
    # живёт вечно. Ровно это и нашлось 21.09.2026.
    for status in (RunStatus.QUEUED, RunStatus.RUNNING):
        for run in await repository.stale(
            status=status, older_than=moment - timedelta(seconds=RESUME_AFTER_SEC)
        ):
            verdict = alive(run.job_id)
            if verdict is None:
                unknown.append(run.id)
                logger.warning(
                    "Прогон %s: жива ли задача %s — выяснить не удалось, не трогаю",
                    run.id,
                    run.job_id,
                )
                continue
            if verdict:
                continue

            silent_for = (moment - _updated_at(run)).total_seconds()
            attempts = resumes_done(run)
            cause = _cause(failure, run.job_id)
            if attempts < MAX_RESUMES:
                job_id = enqueue(run.id)
                if job_id is None:
                    unknown.append(run.id)
                    continue
                await repository.bind_job(run, job_id)
                await repository.save_stats(
                    run,
                    _with_note(
                        run,
                        **{
                            RESUMES_KEY: attempts + 1,
                            REASON_KEY: f"{cause}; прогон продолжен по сохранённой выдаче",
                        },
                    ),
                )
                resumed.append(run.id)
                logger.warning(
                    "Прогон %s молчит %.0f с — %s, поставлена новая задача (%s, попытка %s)",
                    run.id,
                    silent_for,
                    cause,
                    job_id,
                    attempts + 1,
                )
                continue

            if silent_for < STALE_AFTER_SEC:
                # Продолжать больше нечем, но и хоронить рано: последняя
                # задача могла успеть начать работу.
                continue

            await repository.stop_run(
                run,
                stats=_with_note(
                    run,
                    **{
                        REASON_KEY: (
                            f"остановлен разбором: {cause}, продолжений {attempts} "
                            f"из {MAX_RESUMES}, молчание {silent_for:.0f} с"
                        )
                    },
                ),
            )
            stopped.append(run.id)
            logger.error(
                "Прогон %s закрыт как мёртвый: молчит %.0f с, продолжения исчерпаны (%s)",
                run.id,
                silent_for,
                cause,
            )

    await repository.session_commit()
    return Recovery(resumed=resumed, stopped=stopped, unknown=unknown)


def _updated_at(run: RunModel) -> datetime:
    """Время последней записи с часовым поясом. База отдаёт его с поясом,
    но в тестах модель бывает собрана руками — а naive и aware даты
    не вычитаются, и падает это далеко от причины."""
    moment = run.updated_at
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
