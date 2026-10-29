"""Очередь, которая уходит сама (`letters/autosend.py`), — в ответах экрана писем."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.features.letters import autosend


class QueueAutosendView(BaseModel):
    """Режим очереди этапа и аудитории — для отметки в окне пачки и строки под кнопкой."""

    #: Можно ли включить: рубильник сервиса включён и этап — доноры или рекламодатели. Нет —
    #: отметки «дальше — сама» в окне пачки нет.
    available: bool
    #: Идёт ли запуск. Поля ниже — идущего, а у кончившегося — последнего запуска.
    running: bool = False
    since: datetime | None = None
    started_by: str | None = None
    #: Первых писем очереди ушло с запуска — любым путём: кнопкой, проходом, по одному.
    sent: int = 0
    #: Чем кончился последний проход — словами сервера; пусто — проходов ещё не было.
    state: str | None = None
    passed_at: datetime | None = None
    #: Почему проходы стоят при идущем запуске: рубильник выключен.
    off: str | None = None
    stopped_at: datetime | None = None
    stopped_by: str | None = None
    stop_reason: str | None = None

    @classmethod
    def of(cls, found: autosend.State) -> QueueAutosendView:
        mode = found.mode
        if mode is None:
            return cls(available=found.available)
        return cls(
            available=found.available,
            running=mode.stopped_at is None,
            since=mode.started_at,
            started_by=found.started_by,
            sent=found.sent,
            state=mode.state,
            passed_at=mode.passed_at,
            off=found.off,
            stopped_at=mode.stopped_at,
            stopped_by=found.stopped_by,
            stop_reason=mode.stop_reason,
        )
