"""Сторож входов и прав: пороги тревоги «всплеск неудачных входов» (`ops/access_watch.py`).

Аудит 20.11.2026: подбор пароля был виден только на экране «Журнал действий» — то есть тому, кто
туда зашёл и прокрутил. Счётчик попыток у входа (`ACCESS_LOGIN_ATTEMPTS_PER_MINUTE`) перебор
тормозит, но никому о нём не говорит. Сторож считает неудачные входы журнала за окно и говорит
в Telegram, когда их больше порога.

**Два порога, одна тревога.** Всего неудачных входов за окно — перебор по списку почт, где каждая
почта получает свою первую попытку; в одну набранную почту — подбор пароля одной учётки, ему хватает
и меньшего числа. Сработал любой — тревога «Всплеск неудачных входов», одна на всплеск.

**Окно — минуты, оно же срок тишины ленты:** всплеск кончился, когда целое окно прошло без него.

Свой модуль, а не `config/access.py`: тот грузят сервер и вход, а этот — только процесс сторожа.
Опечатка в пороге (`ACCESS_ALARM_WINDOW_MIN=десять`) не роняет ни вход, ни процесс разбора, где
живёт и сторож тишины (`_base.spared`): берётся умолчание, при старте — громкая строка журнала.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings, Fallback, spared

#: Неудачных входов за окно всего, в одну набранную почту и минут окна — пока не задано иначе.
DEFAULT_FAILED_LOGINS = 10
DEFAULT_PER_EMAIL = 5
DEFAULT_WINDOW_MIN = 10


class _AccessAlarms(DomainSettings):
    failed_logins: int = Field(
        default=DEFAULT_FAILED_LOGINS, gt=0, validation_alias="ACCESS_ALARM_FAILED_LOGINS"
    )
    per_email: int = Field(
        default=DEFAULT_PER_EMAIL, gt=0, validation_alias="ACCESS_ALARM_FAILED_LOGINS_PER_EMAIL"
    )
    window_min: int = Field(
        default=DEFAULT_WINDOW_MIN, gt=0, validation_alias="ACCESS_ALARM_WINDOW_MIN"
    )


_s, _unread = spared(
    _AccessAlarms,
    {
        "ACCESS_ALARM_FAILED_LOGINS": Fallback(
            DEFAULT_FAILED_LOGINS,
            wants="целое число больше нуля",
            means=f"тревога от {DEFAULT_FAILED_LOGINS} неудачных входов за окно",
        ),
        "ACCESS_ALARM_FAILED_LOGINS_PER_EMAIL": Fallback(
            DEFAULT_PER_EMAIL,
            wants="целое число больше нуля",
            means=f"тревога от {DEFAULT_PER_EMAIL} неудачных входов в одну почту за окно",
        ),
        "ACCESS_ALARM_WINDOW_MIN": Fallback(
            DEFAULT_WINDOW_MIN,
            wants="целое число больше нуля",
            means=f"окно всплеска неудачных входов — {DEFAULT_WINDOW_MIN} минут",
        ),
    },
)

FAILED_LOGINS: int = _s.failed_logins
PER_EMAIL: int = _s.per_email
WINDOW_MIN: int = _s.window_min
