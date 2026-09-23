"""Перевод отказов ядра в ответы HTTP.

Зачем отдельно: обработчику иначе пришлось бы ловить каждое исключение
руками, и однажды один из них забудут — маршрут ответит пятисоткой там,
где отказ был предусмотрен. Ядро при этом остаётся без веба: коды
знает только этот файл.

**Текст отказа доходит до человека целиком.** Сообщения ядра написаны
так, чтобы говорить, что делать; заменять их на «Bad Request» значит
выбрасывать ровно ту часть, ради которой они писались.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from backend.features.access.administration import (
    LastAdminError,
    SelfLockoutError,
    UnknownUserError,
    WrongPasswordError,
)
from backend.features.access.attempts import TooManyAttemptsError
from backend.features.access.login import LoginFailedError
from backend.features.access.passwords import WeakPasswordError
from backend.features.access.permissions import AccessDeniedError
from backend.features.access.repository import EmailTakenError
from backend.features.access.tokens import SecretMissingError, TokenError
from backend.features.contacts.forms import UnknownFormError
from backend.features.donors.browse import UnknownDonorError
from backend.features.letters.building import LetterScopeError
from backend.features.letters.compose import ComposeError
from backend.features.letters.draft import LetterConflictError
from backend.features.letters.guards import ForbiddenContentError
from backend.features.letters.repository import UnknownLetterError
from backend.features.letters.review import NotEditableError
from backend.features.letters.sending import SendError
from backend.features.letters.stoplist import StopListError
from backend.features.letters.template import TemplateError
from backend.features.letters.transport import TransportError
from backend.features.outreach.repository import UnknownSenderError, UnknownThreadError
from backend.features.replies.repository import UnknownReplyError
from backend.features.review.candidates import NotInRunError
from backend.features.review.candidates import UnknownRunError as ReviewUnknownRunError
from backend.features.runs.browse import UnknownRunError

#: Отказ → код ответа. Порядок в словаре значения не имеет: FastAPI
#: выбирает обработчик по точному типу и его предкам.
STATUSES: dict[type[Exception], int] = {
    LoginFailedError: status.HTTP_401_UNAUTHORIZED,
    TokenError: status.HTTP_401_UNAUTHORIZED,
    AccessDeniedError: status.HTTP_403_FORBIDDEN,
    UnknownUserError: status.HTTP_404_NOT_FOUND,
    UnknownSenderError: status.HTTP_404_NOT_FOUND,
    UnknownDonorError: status.HTTP_404_NOT_FOUND,
    UnknownRunError: status.HTTP_404_NOT_FOUND,
    UnknownThreadError: status.HTTP_404_NOT_FOUND,
    UnknownLetterError: status.HTTP_404_NOT_FOUND,
    UnknownReplyError: status.HTTP_404_NOT_FOUND,
    # Письмо не отправлено: стоп-лист, незаполненная настройка письма,
    # некому писать сегодня, письмо уже ушло. Все четыре — про состояние,
    # а не про запрос, и все четыре человек чинит сам.
    SendError: status.HTTP_409_CONFLICT,
    NotEditableError: status.HTTP_409_CONFLICT,
    # Транспорта нет или он не тот. Это тоже состояние развёртывания,
    # и текст отказа называет, чего не хватает.
    TransportError: status.HTTP_409_CONFLICT,
    # Метрики Ahrefs в письме: правка человека, которую нельзя принять.
    ForbiddenContentError: status.HTTP_400_BAD_REQUEST,
    # Текст письма с экрана не разобрался как шаблон: нет зоны, подписи,
    # коридор недостижим, неизвестная подстановка. Сообщение говорит,
    # что поправить.
    TemplateError: status.HTTP_400_BAD_REQUEST,
    ComposeError: status.HTTP_400_BAD_REQUEST,
    # У рассылки уже свой текст письма — это состояние, а не запрос.
    LetterConflictError: status.HTTP_409_CONFLICT,
    # Прогоны рассылки из разных стран или с неоконченным поиском контактов —
    # состояние, которое человек чинит сам.
    LetterScopeError: status.HTTP_409_CONFLICT,
    ReviewUnknownRunError: status.HTTP_404_NOT_FOUND,
    NotInRunError: status.HTTP_409_CONFLICT,
    # Ручная очередь форм: донора в ней уже нет — либо адрес нашёлся,
    # либо очередь разобрал кто-то другой. Это состояние, а не запрос.
    UnknownFormError: status.HTTP_409_CONFLICT,
    # Стоп-лист: уже там, такого нет, снятие отписки без причины.
    # Всё это про состояние списка и про то, что человек чинит сам.
    StopListError: status.HTTP_409_CONFLICT,
    EmailTakenError: status.HTTP_409_CONFLICT,
    LastAdminError: status.HTTP_409_CONFLICT,
    SelfLockoutError: status.HTTP_409_CONFLICT,
    WeakPasswordError: status.HTTP_400_BAD_REQUEST,
    WrongPasswordError: status.HTTP_400_BAD_REQUEST,
    TooManyAttemptsError: status.HTTP_429_TOO_MANY_REQUESTS,
    # Секрета подписи нет — это поломка развёртывания, а не запроса.
    # Ответ честно говорит об этом пятисоткой и называет переменную:
    # в логах иначе останется «internal error» без единой подсказки.
    SecretMissingError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _handler(code: int):  # type: ignore[no-untyped-def]
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        headers = {}
        if isinstance(exc, TooManyAttemptsError):
            headers["Retry-After"] = str(exc.retry_after_seconds)
        if code == status.HTTP_401_UNAUTHORIZED:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse({"detail": str(exc)}, status_code=code, headers=headers)

    return handle


def install(app: FastAPI) -> None:
    for error, code in STATUSES.items():
        app.add_exception_handler(error, _handler(code))
