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
from backend.features.agent.drafting import DraftRefusedError, UnknownDraftReplyError
from backend.features.agent.drafts import DraftDecisionError, UnknownDraftError
from backend.features.agent.settings import (
    AgentSettingsConflictError,
    AutopilotOffError,
    UnknownAgentStageError,
)
from backend.features.agent.writer import DraftUnavailableError
from backend.features.contacts.forms import UnknownFormError
from backend.features.contacts.manual import (
    AddressConflictError,
    AddressInvalidError,
    UnknownAddressError,
)
from backend.features.contacts.repository import SearchRefusedError
from backend.features.core.stages import SalesNotConnectedError
from backend.features.core.usage import LlmCapExceededError
from backend.features.donors.export import PickRefusedError
from backend.features.donors.manual_price import DonorRefusedError, ManualPriceError
from backend.features.donors.standing import UnknownDonorError
from backend.features.letters.answers import UnknownAnswerTargetError
from backend.features.letters.building import LetterScopeError
from backend.features.letters.compose import ComposeError
from backend.features.letters.draft import LetterConflictError
from backend.features.letters.guards import ForbiddenContentError
from backend.features.letters.repository import UnknownLetterError
from backend.features.letters.review import NotEditableError
from backend.features.letters.sending import SendError
from backend.features.letters.stoplist import StopListError
from backend.features.letters.template import TemplateError
from backend.features.letters.transport import MaybeSentError, TransportError
from backend.features.letters.unknown_outcome import ResolveError
from backend.features.outreach.repository import UnknownSenderError, UnknownThreadError
from backend.features.replies.attachments import AttachmentNotKeptError, UnknownAttachmentError
from backend.features.replies.repository import LeadError, NotAPriceError, UnknownReplyError
from backend.features.review.candidates import NotInRunError
from backend.features.review.candidates import UnknownRunError as ReviewUnknownRunError
from backend.features.runs.browse import UnknownRunError
from backend.features.sales.chain import ChainNotReadyError
from backend.features.sales.intake import IntakeError, UnknownHypothesisError
from backend.features.sales.kb import KbError, KbKeyTakenError, UnknownKbEntryError
from backend.features.sales.sender import SenderSettingsError
from backend.features.sales.sheet import SheetError, SheetUnavailableError

#: Отказ → код ответа. Порядок в словаре значения не имеет: FastAPI
#: выбирает обработчик по точному типу и его предкам.
STATUSES: dict[type[Exception], int] = {
    # Потолок расхода на модель: не ошибка запроса и не наша поломка —
    # предел, который человек поднимает настройкой или ждёт следующего дня.
    LlmCapExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    UnknownAnswerTargetError: status.HTTP_404_NOT_FOUND,
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
    # Вложение ответа: нет такого у этого ответа — или есть, но файл не
    # сохранён (опасный, лишний, слишком большой). Отдавать нечего в обоих
    # случаях, и текст отказа говорит, какой из двух.
    UnknownAttachmentError: status.HTTP_404_NOT_FOUND,
    AttachmentNotKeptError: status.HTTP_404_NOT_FOUND,
    # Письмо не отправлено: стоп-лист, незаполненная настройка письма,
    # некому писать сегодня, письмо уже ушло. Все четыре — про состояние,
    # а не про запрос, и все четыре человек чинит сам.
    SendError: status.HTTP_409_CONFLICT,
    NotEditableError: status.HTTP_409_CONFLICT,
    # Подтверждать цену в ответе рекламодателя: его расход не цена площадки.
    NotAPriceError: status.HTTP_409_CONFLICT,
    # Брать лидом ответ донора или лид, который уже ведёт другой.
    LeadError: status.HTTP_409_CONFLICT,
    # Транспорта нет или он не тот. Это тоже состояние развёртывания,
    # и текст отказа называет, чего не хватает.
    TransportError: status.HTTP_409_CONFLICT,
    # Почта не ответила после отправки — письмо «отправляется», повтор только
    # после проверки в кабинете платформы: словами, а не «сервер упал».
    MaybeSentError: status.HTTP_409_CONFLICT,
    # Исход письма уже известен или письмо может быть ещё в пути — состояние.
    ResolveError: status.HTTP_409_CONFLICT,
    # Письмо, добивка, разбор или лид этапа продаж: почта его ещё не ведёт.
    # Состояние продукта, а не ошибка запроса и не наша поломка.
    SalesNotConnectedError: status.HTTP_409_CONFLICT,
    # Цепочка писем продаж неполна: письмо не собрать — состояние, которое чинят на экране.
    ChainNotReadyError: status.HTTP_409_CONFLICT,
    # Метрики Ahrefs в письме: правка человека, которую нельзя принять.
    ForbiddenContentError: status.HTTP_400_BAD_REQUEST,
    # Текст письма с экрана не разобрался как шаблон: нет зоны, подписи,
    # коридор недостижим, неизвестная подстановка. Сообщение говорит,
    # что поправить.
    TemplateError: status.HTTP_400_BAD_REQUEST,
    ComposeError: status.HTTP_400_BAD_REQUEST,
    # Черновик агента: ответа нет в переписке; модель недоступна или ключа нет —
    # сообщение говорит, что из двух и что чинить; черновик не положен — 409.
    UnknownDraftReplyError: status.HTTP_404_NOT_FOUND,
    DraftUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    DraftRefusedError: status.HTTP_409_CONFLICT,
    # Решение по черновику агента: уже решён, «как есть» у отданного человеку — 409.
    UnknownDraftError: status.HTTP_404_NOT_FOUND,
    DraftDecisionError: status.HTTP_409_CONFLICT,
    # Две правки настроек агента одного этапа разом: вторая не ложится молча.
    AgentSettingsConflictError: status.HTTP_409_CONFLICT,
    # Автопилот этапу не разрешён (код этапа, сервер): состояние, а не запрос.
    AutopilotOffError: status.HTTP_409_CONFLICT,
    UnknownAgentStageError: status.HTTP_404_NOT_FOUND,
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
    # Поиск адреса одному донору: правило «кому искать» его не берёт —
    # не принят, не подходит, искали недавно. Состояние, а не запрос.
    SearchRefusedError: status.HTTP_409_CONFLICT,
    # Адрес, вписанный руками: не адрес или адрес, которым не пользуются, —
    # запрос; уже есть у донора или по нему шла переписка — состояние.
    AddressInvalidError: status.HTTP_400_BAD_REQUEST,
    AddressConflictError: status.HTTP_409_CONFLICT,
    UnknownAddressError: status.HTTP_404_NOT_FOUND,
    # Выгрузка отмеченных: ни одного или больше потолка — это запрос.
    PickRefusedError: status.HTTP_400_BAD_REQUEST,
    # Цена руками: не цена, не валюта, не домен — запрос; домен в стоп-листе,
    # поставщик, отклонён, ждёт решения в очереди, не прошёл пороги — состояние.
    ManualPriceError: status.HTTP_400_BAD_REQUEST,
    DonorRefusedError: status.HTTP_409_CONFLICT,
    # Стоп-лист: уже там, такого нет, снятие отписки без причины.
    # Всё это про состояние списка и про то, что человек чинит сам.
    StopListError: status.HTTP_409_CONFLICT,
    # Загрузка базы продаж: источник не читается, ссылка закрыта, сопоставление
    # не годится — это запрос; гипотезы нет — 404; Google не ответил — 502:
    # тут поможет повтор, а не правка запроса.
    IntakeError: status.HTTP_400_BAD_REQUEST,
    SheetError: status.HTTP_400_BAD_REQUEST,
    UnknownHypothesisError: status.HTTP_404_NOT_FOUND,
    SheetUnavailableError: status.HTTP_502_BAD_GATEWAY,
    # База знаний и отправитель продаж: поле не годится — запрос; такая запись
    # уже есть — состояние; записи нет — 404.
    KbError: status.HTTP_400_BAD_REQUEST,
    KbKeyTakenError: status.HTTP_409_CONFLICT,
    UnknownKbEntryError: status.HTTP_404_NOT_FOUND,
    SenderSettingsError: status.HTTP_400_BAD_REQUEST,
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
