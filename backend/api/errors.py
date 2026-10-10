"""Перевод отказов ядра в ответы HTTP.

Зачем отдельно: обработчику иначе пришлось бы ловить каждое исключение
руками, и однажды один из них забудут — маршрут ответит пятисоткой там,
где отказ был предусмотрен. Ядро при этом остаётся без веба: коды
знает только этот файл.

**Текст отказа доходит до человека целиком.** Сообщения ядра написаны
так, чтобы говорить, что делать; заменять их на «Bad Request» значит
выбрасывать ровно ту часть, ради которой они писались.

**Отказ разбора запроса (422) — тоже по-русски.** Экран показывает первую
строку списка как есть, и умолчание FastAPI давало «Input should be a valid
decimal» на любом экране (`said_in_russian`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from backend.config.startup_checks import ConfigError
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
from backend.features.agent.drafts import DraftDecisionError, DraftReasonError, UnknownDraftError
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
from backend.features.crawl.niche import UnknownNicheAdvertiserError, UnknownNicheRunError
from backend.features.donors.export import PickRefusedError
from backend.features.donors.manual_price import DonorRefusedError, ManualPriceError
from backend.features.donors.standing import UnknownDonorError
from backend.features.letters.answers import UnknownAnswerTargetError
from backend.features.letters.building import LetterScopeError
from backend.features.letters.compose import ComposeError
from backend.features.letters.draft import LetterConflictError
from backend.features.letters.guards import ForbiddenContentError
from backend.features.letters.outgoing_files import OutgoingFileError
from backend.features.letters.outgoing_store import OutgoingFileTakenError, UnknownOutgoingFileError
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
from backend.features.runs.budget import QuotaUnavailableError
from backend.features.sales.chain import ChainNotReadyError
from backend.features.sales.intake import IntakeError, UnknownHypothesisError
from backend.features.sales.kb import KbError, KbKeyTakenError, UnknownKbEntryError
from backend.features.sales.sender import SenderSettingsError
from backend.features.sales.sheet import SheetError, SheetUnavailableError

logger = logging.getLogger(__name__)

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
    # Бизнес ниши или прогон, из которого собирать, — не найдены.
    UnknownNicheAdvertiserError: status.HTTP_404_NOT_FOUND,
    UnknownNicheRunError: status.HTTP_404_NOT_FOUND,
    UnknownLetterError: status.HTTP_404_NOT_FOUND,
    UnknownReplyError: status.HTTP_404_NOT_FOUND,
    # Вложение ответа: нет такого у этого ответа — или есть, но файл не
    # сохранён (опасный, лишний, слишком большой). Отдавать нечего в обоих
    # случаях, и текст отказа говорит, какой из двух.
    UnknownAttachmentError: status.HTTP_404_NOT_FOUND,
    AttachmentNotKeptError: status.HTTP_404_NOT_FOUND,
    # Файл к нашему ответу: не годится (тип, содержимое, размер, число, имя) —
    # это запрос, и текст называет, что не так и какой предел; такого файла
    # нет в переписке или у письма — 404; уже приложен к письму — состояние.
    OutgoingFileError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnknownOutgoingFileError: status.HTTP_404_NOT_FOUND,
    OutgoingFileTakenError: status.HTTP_409_CONFLICT,
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
    # Продажи не подключены к почте (чего не хватает — в тексте отказа) или путь почты,
    # которого у продаж нет: цена и лид из ответа, сборка очереди и шаблоны доноров.
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
    # Причины нет, её нет в перечислении этапа, «другое» без текста — это запрос.
    DraftReasonError: status.HTTP_422_UNPROCESSABLE_CONTENT,
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
    # Настройка не даёт работать: песочница выдачи, нет ключа. Состояние развёртывания,
    # и текст называет, что поправить; до аудита 10.10.2026 «Запустить» отвечал на это
    # пятисоткой, и написанный человеку отказ до экрана не доходил.
    ConfigError: status.HTTP_409_CONFLICT,
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


#: Очередь задач (Redis) не ответила маршруту: постановка (прогон, пачка писем, поиск адресов,
#: рассмотрение, лид из ответа, сборка очереди продаж) или чтение. Без утверждения о задаче:
#: при чтении она уже стоит, а при таймауте постановки Redis мог выполнить команду, не
#: успев ответить, — «не поставлена» звала бы запустить второй раз.
QUEUE_DOWN = "Очередь задач недоступна — повторите позже"


async def _queue_down(_: Request, exc: Exception) -> JSONResponse:
    """503 словами вместо «голой» пятисотки. Текст redis-py (адрес и номер ошибки) —
    только в журнал полем: человеку он не поможет, а адрес очереди наружу не нужен."""
    logger.warning("очередь задач не ответила маршруту — 503", extra={"error": str(exc)})
    return JSONResponse({"detail": QUEUE_DOWN}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


async def _quota_unknown(_: Request, exc: Exception) -> JSONResponse:
    """Остаток у Ahrefs не узнать — код по причине (`runs/budget.py`): ключ отозван или прав
    нет (`permanent`) — 409, повтор не поможет; сеть или 5xx — 503, поможет. Текст — как
    написан, с причиной: до аудита 10.10.2026 смета отвечала «Internal Server Error»."""
    permanent = getattr(exc, "permanent", False)
    code = status.HTTP_409_CONFLICT if permanent else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse({"detail": str(exc)}, status_code=code)


#: Отказ разбора запроса по типу pydantic — что не так, словами (аудит 10.10.2026, QA №5).
#: Подстановки — из `ctx` отказа. Число стоит в конце фразы: «элементов — не больше 500»
#: склоняется одинаково при любом числе, «не больше 500 элементов» — нет.
_SAID: dict[str, str] = {
    "missing": "обязательно, а в запросе его нет",
    "extra_forbidden": "такого поля нет",
    "string_type": "нужна строка",
    "string_too_short": "знаков — не меньше {min_length}",
    "string_too_long": "знаков — не больше {max_length}",
    "string_pattern_mismatch": "не того вида",
    "too_short": "элементов — не меньше {min_length}",
    "too_long": "элементов — не больше {max_length}, пришло {actual_length}",
    "int_type": "нужно целое число",
    "int_parsing": "нужно целое число",
    "int_from_float": "нужно целое число, без дробной части",
    "int_parsing_size": "число слишком большое",
    "float_type": "нужно число",
    "float_parsing": "нужно число",
    "finite_number": "нужно конечное число",
    "decimal_type": "нужно число",
    "decimal_parsing": "нужно число",
    "decimal_max_digits": "цифр — не больше {max_digits}",
    "decimal_max_places": "знаков после запятой — не больше {decimal_places}",
    "decimal_whole_digits": "цифр до запятой — не больше {whole_digits}",
    "bool_type": "нужно «да» или «нет» (true или false)",
    "bool_parsing": "нужно «да» или «нет» (true или false)",
    "greater_than": "нужно больше {gt}",
    "greater_than_equal": "нужно не меньше {ge}",
    "less_than": "нужно меньше {lt}",
    "less_than_equal": "нужно не больше {le}",
    "multiple_of": "нужно кратное {multiple_of}",
    "literal_error": "нужно одно из: {expected}",
    "enum": "нужно одно из: {expected}",
    "list_type": "нужен список",
    "dict_type": "нужен объект",
    "model_type": "нужен объект",
    "model_attributes_type": "нужен объект",
    "json_type": "нужен JSON",
    "none_required": "должно быть пусто",
    "url_type": "нужна ссылка",
    "url_parsing": "нужна ссылка",
    "date_type": "нужна дата",
    "date_parsing": "нужна дата",
    "datetime_type": "нужны дата и время",
    "datetime_parsing": "нужны дата и время",
    "timezone_aware": "нужно время с часовым поясом",
    "uuid_type": "нужен UUID",
    "uuid_parsing": "нужен UUID",
}

#: Чего нет в `_SAID` или чему не хватило подстановки: общие слова, поле — по имени.
_UNSAID = "значение не подходит"

#: Приставки pydantic к нашему же тексту: `ValueError` и `assert` в проверках схем.
_PREFIXES = ("Value error, ", "Assertion failed, ")

#: Откуда поле — тело, адрес, заголовок: человеку нужно имя поля, а не это.
_SOURCES = frozenset({"body", "query", "path", "header", "cookie"})

#: Свой текст отказа — по-русски, как и всё, что пишет сервис (тот же признак —
#: язык — у причин прогона, `runs/reasons.py`).
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def said_in_russian(error: Mapping[str, Any]) -> str:
    """Один отказ разбора — для экрана. Свой текст (кириллица) — как написан, без
    «Value error, »; знакомый тип — фразой с именем поля; незнакомый или чужой английский
    текст — общими словами, тоже с именем поля: экран показывает одну строку."""
    said = str(error.get("msg", ""))
    for prefix in _PREFIXES:
        said = said.removeprefix(prefix)
    if _CYRILLIC.search(said):
        return said
    kind = str(error.get("type", ""))
    if kind == "json_invalid":
        return "Тело запроса — не JSON"
    phrase = _phrase(_SAID.get(kind, _UNSAID), error.get("ctx") or {})
    field = _field(error.get("loc") or ())
    return f"Поле «{field}»: {phrase}" if field else f"Тело запроса: {phrase}"


def _phrase(template: str, ctx: Mapping[str, Any]) -> str:
    """Фраза с подстановками из `ctx`. «'a' or 'b'» pydantic — «'a' или 'b'»."""
    shown = {key: str(value).replace(" or ", " или ") for key, value in ctx.items()}
    try:
        return template.format(**shown)
    except (KeyError, IndexError):
        # Подстановки нет: у типа другая форма `ctx` в этой версии pydantic.
        return _UNSAID


def _field(loc: Sequence[object]) -> str:
    """Имя поля из `loc`: без источника, вложенное — через точку, номер в списке — [n]."""
    parts = list(loc)[1:] if loc and loc[0] in _SOURCES else list(loc)
    name = ""
    for part in parts:
        name += f"[{part}]" if isinstance(part, int) else f"{'.' if name else ''}{part}"
    return name


async def _unreadable(_: Request, exc: Exception) -> JSONResponse:
    """422 разбора запроса: тот же список, что у FastAPI (`msg`, `loc`, `type`…), но `msg`
    по-русски. Экран и тесты читают форму ответа — она не меняется, меняются слова."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    detail = [{**error, "msg": said_in_russian(error)} for error in errors]
    return JSONResponse(
        {"detail": jsonable_encoder(detail)},
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


def install(app: FastAPI) -> None:
    for error, code in STATUSES.items():
        app.add_exception_handler(error, _handler(code))
    app.add_exception_handler(QuotaUnavailableError, _quota_unknown)
    app.add_exception_handler(RequestValidationError, _unreadable)
    # Маршруты, которые отвечают о недоступной очереди сами (вебхук ответов — 503 со своей
    # причиной, поиск после перевода рекламодателей — успех перевода, исход задачи —
    # «очередь не отвечает»), ловят её до этого.
    app.add_exception_handler(RedisError, _queue_down)
