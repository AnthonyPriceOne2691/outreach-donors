"""Отказы входа и учёток → коды ответа: часть таблицы `api/errors.STATUSES`.

Своим файлом с 23.11.2026: таблица упёрлась в предел длины файла (500 строк), а отказы входа
растут вместе со входом — второй шаг (вход с кодом) добавил три. Правило то же, что у всей
таблицы: коды знает только веб-слой, ядро остаётся без веба; `errors.install` ставит эти отказы
рядом с остальными.
"""

from __future__ import annotations

from fastapi import status

from backend.features.access.administration import (
    LastAdminError,
    SelfLockoutError,
    UnknownUserError,
    WrongPasswordError,
)
from backend.features.access.challenge import ChallengeError
from backend.features.access.login import LoginFailedError
from backend.features.access.passwords import WeakPasswordError
from backend.features.access.repository import EmailTakenError
from backend.features.access.second_step import CodeRefusedError, TotpStateError
from backend.features.access.tokens import SecretMissingError

ACCESS_STATUSES: dict[type[Exception], int] = {
    # Неверная почта или пароль — 400, как неверный старый пароль при смене (`WrongPasswordError`),
    # а не 401: 401 за прокси стирает у браузера пароль прокси (`access/permissions.SignedOutError`).
    # Пропуск не принят (`TokenError`) — отказ «входа нет»: 403 обработчиком `AccessDeniedError`.
    LoginFailedError: status.HTTP_400_BAD_REQUEST,
    # Второй шаг входа (23.11.2026): вызов устарел или код не подошёл — отказ входа, как у пары;
    # действие не к состоянию кода (включают включённый, выключают обязательный) — состояние.
    ChallengeError: status.HTTP_400_BAD_REQUEST,
    CodeRefusedError: status.HTTP_400_BAD_REQUEST,
    TotpStateError: status.HTTP_409_CONFLICT,
    UnknownUserError: status.HTTP_404_NOT_FOUND,
    EmailTakenError: status.HTTP_409_CONFLICT,
    LastAdminError: status.HTTP_409_CONFLICT,
    SelfLockoutError: status.HTTP_409_CONFLICT,
    WeakPasswordError: status.HTTP_400_BAD_REQUEST,
    WrongPasswordError: status.HTTP_400_BAD_REQUEST,
    # Секрета подписи нет — это поломка развёртывания, а не запроса.
    # Ответ честно говорит об этом пятисоткой и называет переменную:
    # в логах иначе останется «internal error» без единой подсказки.
    SecretMissingError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}
