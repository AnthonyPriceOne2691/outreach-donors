"""Пауза входа учётки после неудач подряд (аудит 20.11.2026).

Счётчик попыток в памяти (`attempts.py`) держит минуту и живёт до перезапуска: выкатка его
обнуляет, а подбор, растянутый на сутки, проходит под ним — десять попыток в минуту дают
четырнадцать тысяч в сутки на одну учётку. Здесь счёт ведёт сама учётка, в базе: с
`LOCKOUT_AFTER_FAILURES`-й неудачи подряд вход закрыт на `LOCKOUT_FIRST_MINUTES`, с каждой
следующей — вдвое дольше, до `LOCKOUT_MAX_MINUTES`. Счётчик в памяти остаётся как был: он
ловит перебор по многим почтам с одного адреса, этот — долгий подбор одной учётки.

**Пауза, а не замок.** Замок навсегда отдал бы любому, кто знает почту, кнопку «выключить
сотрудника», а пауза растягивает подбор до пары попыток в час и сходит сама.

**Во время паузы пароль не сверяется, и верный — тоже отказ.** Иначе подбирающий узнал бы
верный пароль по ответу «вошёл», а пауза держала бы только неверные. Отказ во время паузы в
журнал действий не пишется — только её начало (`AuditAction.LOGIN_PAUSED`): иначе подбор
писал бы в базу строку на каждый запрос.

**Незнакомая почта строк не заводит** и паузы не получает: учётки нет — нечему и вставать на
паузу, а строка на каждую набранную почту выросла бы в таблицу мусора.

**Отказ называет минуты — и этим говорит, что учётка с такой почтой есть.** Принято
сознательно: сервис за общим паролем прокси, до входа доходят только свои, а сотруднику,
ошибившемуся пять раз, «неверная почта или пароль» на верный пароль стоило бы звонка админу.

**Удачный вход и любая смена пароля обнуляют счёт**: неудачи подбирали прежний пароль. Сброс
админом поэтому снимает и паузу — человек с новым разовым входит сразу.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from backend.config import access as cfg
from backend.features.access.attempts import TooManyAttemptsError

#: Удвоений не больше стольких: дальше потолок всё равно меньше, а степень двойки от счёта
#: неудач за годы подбора считалась бы зря.
_MAX_DOUBLINGS = 16


class AccountPausedError(TooManyAttemptsError):
    """Вход учётки на паузе. Ответ 429 с `Retry-After`, как у счётчика в памяти
    (`api/errors.py`): экран входа показывает его «Подождите», а не «Не вошли»."""

    def __init__(self, until: datetime, now: datetime) -> None:
        seconds = max(1, math.ceil((until - now).total_seconds()))
        super().__init__(seconds, paused_words(math.ceil(seconds / 60)))


def paused_words(minutes: int) -> str:
    """Отказ во время паузы — сколько ждать, в минутах вверх до целой."""
    return f"Слишком много неудачных попыток — попробуйте через {minutes} мин"


def pause_after(failures: int) -> timedelta | None:
    """Пауза после стольких неудач подряд: до порога — нет, на пороге — первая, с каждой
    следующей неудачей — вдвое, но не дольше потолка."""
    if failures < cfg.LOCKOUT_AFTER_FAILURES:
        return None
    doublings = min(failures - cfg.LOCKOUT_AFTER_FAILURES, _MAX_DOUBLINGS)
    minutes = min(cfg.LOCKOUT_FIRST_MINUTES * 2**doublings, cfg.LOCKOUT_MAX_MINUTES)
    return timedelta(minutes=minutes)


def refuse_while_paused(locked_until: datetime | None, now: datetime) -> None:
    """Пауза ещё идёт — отказ со сроком; кончилась или не было — пропустить к сверке пароля."""
    if locked_until is not None and locked_until > now:
        raise AccountPausedError(locked_until, now)
