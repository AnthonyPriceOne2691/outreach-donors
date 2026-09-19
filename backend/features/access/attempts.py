"""Ограничение попыток входа.

Подбор пароля не должен быть бесплатным. Десяти попыток в минуту хватает
человеку, который ошибся раскладкой, и не хватает перебору: словарь
в тысячу паролей растягивается со стольких же секунд до полутора часов.

**Считаются только неудачи, и только они.** Удачный вход обнуляет счёт:
иначе человек, который работает с двух устройств и раз ошибся, получает
отказ на ровном месте.

**Ключей два — почта и адрес.** По одной почте перебор ловится сразу,
но перебор по списку почт с одного адреса шёл бы мимо счётчика: там
каждая почта получает свою первую попытку.

**Счёт живёт в памяти процесса.** При нескольких рабочих процессах
предел умножается на их число — это осознанное упрощение: настоящий
предел ставится на периметре (`docs/SECURITY.md`), а этот нужен, чтобы
перебор не был бесплатным и был виден в журнале. Переезд в общее
хранилище — когда процессов станет больше одного.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from backend.config import access as cfg

WINDOW = timedelta(minutes=1)


class TooManyAttemptsError(RuntimeError):
    """Попыток входа слишком много. Сообщение говорит, сколько ждать."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"Слишком много попыток входа. Повторите через {retry_after_seconds} с.")
        self.retry_after_seconds = retry_after_seconds


class LoginAttempts:
    """Счётчик неудачных попыток со скользящим окном в минуту."""

    def __init__(self, limit: int | None = None) -> None:
        self._limit = limit if limit is not None else cfg.LOGIN_ATTEMPTS_PER_MINUTE
        self._failures: defaultdict[str, list[datetime]] = defaultdict(list)

    def _fresh(self, key: str, now: datetime) -> list[datetime]:
        kept = [moment for moment in self._failures[key] if now - moment < WINDOW]
        # Записи не только фильтруются, но и укорачиваются: без этого
        # словарь растёт на каждую новую почту и живёт столько же, сколько
        # процесс.
        if kept:
            self._failures[key] = kept
        else:
            self._failures.pop(key, None)
        return kept

    def check(self, *keys: str, now: datetime | None = None) -> None:
        """Пустить или отказать. Отказ называет, через сколько повторить."""
        moment = now or datetime.now(UTC)
        for key in keys:
            attempts = self._fresh(key, moment)
            if len(attempts) < self._limit:
                continue
            wait = WINDOW - (moment - attempts[0])
            raise TooManyAttemptsError(max(1, int(wait.total_seconds()) + 1))

    def failed(self, *keys: str, now: datetime | None = None) -> None:
        """Отметить неудачу по каждому ключу."""
        moment = now or datetime.now(UTC)
        for key in keys:
            self._failures[key].append(moment)

    def succeeded(self, *keys: str) -> None:
        """Удачный вход снимает накопленное."""
        for key in keys:
            self._failures.pop(key, None)
