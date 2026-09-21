"""Скользящее окно: сколько событий с одним ключом случилось за минуту.

Механика одна на две разные защиты — счёт неудачных входов и потолок
частоты у вебхука приёма, — а поводы у них разные: там перебор пароля,
здесь поток входящих. Вынесено до того, как появился второй экземпляр:
две копии оконного счётчика разъезжаются на первой же правке окна,
и тише всех расходится та, которую реже зовут.

**Счёт живёт в памяти процесса.** При нескольких рабочих процессах
предел умножается на их число — это осознанное упрощение, то же самое
и по той же причине, что у попыток входа: настоящий предел ставится
на периметре (`docs/SECURITY.md`), а этот нужен, чтобы поток не был
бесплатным и был виден в журнале.

**Записи укорачиваются при каждом обращении.** Без этого словарь растёт
на каждый новый ключ и живёт столько же, сколько процесс.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

MINUTE = timedelta(minutes=1)


class SlidingWindow:
    """События по ключу за последнее окно."""

    def __init__(self, *, window: timedelta = MINUTE) -> None:
        self._window = window
        self._events: defaultdict[str, list[datetime]] = defaultdict(list)

    @property
    def window(self) -> timedelta:
        return self._window

    def fresh(self, key: str, now: datetime | None = None) -> list[datetime]:
        """Что попало в окно. Старое выбрасывается прямо здесь."""
        moment = now or datetime.now(UTC)
        kept = [at for at in self._events[key] if moment - at < self._window]
        if kept:
            self._events[key] = kept
        else:
            self._events.pop(key, None)
        return kept

    def record(self, key: str, now: datetime | None = None) -> None:
        self._events[key].append(now or datetime.now(UTC))

    def count(self, key: str, now: datetime | None = None) -> int:
        return len(self.fresh(key, now))

    def retry_after(self, key: str, now: datetime | None = None) -> int:
        """Через сколько секунд освободится место. Ноль — уже свободно."""
        moment = now or datetime.now(UTC)
        events = self.fresh(key, moment)
        if not events:
            return 0
        left = self._window - (moment - events[0])
        return max(1, int(left.total_seconds()) + 1)

    def clear(self, key: str) -> None:
        self._events.pop(key, None)

    @property
    def tracked(self) -> int:
        """Сколько ключей держим в памяти.

        Наблюдаемое, а не внутреннее: рост этого числа при постоянном
        потоке означает, что уборка перестала работать, и заметить это
        можно только по нему.
        """
        return len(self._events)
