"""Существует ли адрес — вопрос платному сервису (Hunter email-verifier), общий клиент аутрича.

Зовёт его проверка адреса перед первым письмом донору и рекламодателю (`address_check.py`,
11.11.2026). MX говорит только о домене; жив ли сам ящик, знает сервис, который поговорит с
почтовым сервером от своего имени: разговор SMTP со своего адреса бьёт по репутации рассылки.

**Клиент свой, а не продаж.** У продаж проверка адреса лида живёт в их модуле
(`sales/verifier.py`), а почта и адреса модуль продаж не импортируют (`.importlinter`). Общее
у двух клиентов уже вынесено и здесь переиспользуется: адрес сервиса, разбор маркеров отказа,
правило «что повторять» и исключения — `provider.py`; повторы и частота — `net/retry`. Своё —
только запрос, разбор вердикта и переспрос «ещё проверяет».

**Вердикт и отказ сервиса — разные вещи** (`okf/provider-refusals.md`). Вердикт — слово Hunter
об адресе, в том числе «не знаю» (`unknown`); что с ним делать, решает вызывающий. Отказ — сеть,
5xx, непонятный ответ, квота, закрытая учётка, частота — исключение `ProviderError`: вердикта
нет, единица не списана, и молча считать адрес проверенным нельзя.

**202 — «ещё проверяет»**: Hunter не дождался почтового сервера; тот же запрос, пока ответа нет,
новым не считается. Переспрашиваем тут же, не больше `STILL_CHECKING_ASKS` раз; не дождались —
`StillCheckingError`: вердикта пока нет, адрес спросит следующая сборка.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from backend.config import contacts as cfg
from backend.features.contacts.provider import (
    BASE_URL,
    ProviderError,
    ProviderKeyRefusedError,
    ProviderRateLimitError,
    _raise_refusal,
    transient,
)
from backend.shared.net.retry import RateLimiter, reason_of, unsent, with_retries

logger = logging.getLogger(__name__)

#: Слова Hunter об адресе.
VALID = "valid"
INVALID = "invalid"
ACCEPT_ALL = "accept_all"
WEBMAIL = "webmail"
DISPOSABLE = "disposable"
UNKNOWN = "unknown"
STATUSES = frozenset({VALID, INVALID, ACCEPT_ALL, WEBMAIL, DISPOSABLE, UNKNOWN})

#: Попыток на адрес: 429, 5xx и обрыв связи бывают временными (`net/retry.with_retries`).
ATTEMPTS = 3
#: Запросов в минуту — с запасом ниже предела частоты Hunter: сборка спрашивает пачкой разом.
RATE_PER_MINUTE = 150
#: Переспросов «ещё проверяет» сверх первого запроса.
STILL_CHECKING_ASKS = 2

NO_KEY = "ключ проверки адресов не задан — его задаёт администратор (CONTACTS_HUNTER_API_KEY)"
KEY_REFUSED = "ключ проверки адресов не принят провайдером (HTTP 401) — ключ меняет администратор"
TOO_OFTEN = f"частота превышена (HTTP 429) и после {ATTEMPTS} попыток с паузой"
STILL_CHECKING = (
    f"провайдер ещё проверяет адрес и после {STILL_CHECKING_ASKS} переспросов — вердикта пока нет"
)


class NoKeyError(ProviderError):
    """Ключа нет: спросить нечем ни этот адрес, ни следующий."""


class StillCheckingError(ProviderError):
    """202 и после переспросов: вердикта ещё нет. Не отказ сервиса — адрес спросят позже."""


@dataclass(frozen=True, slots=True)
class Verdict:
    """Что сервис сказал об адресе."""

    #: Слово Hunter — одно из `STATUSES`.
    status: str
    #: Уверенность 0–100, если сервис её дал.
    score: int | None = None
    #: Сколько проверок списано: вердикт Hunter — одна (полкредита общего счёта учётки).
    units: int = 1


@runtime_checkable
class EmailVerifier(Protocol):
    """Проверяльщик адреса — за интерфейсом: тесты подставляют свой, без сети."""

    async def verify(self, email: str) -> Verdict:
        """Вердикт по адресу. Отказ сервиса — `ProviderError` и наследники."""
        ...


class HunterVerifier:
    """Hunter Email Verifier. Разбор ответа защитный: чужой формат однажды поменяется, и
    молчать об этом нельзя."""

    def __init__(self, http: httpx.AsyncClient, *, api_key: str | None = None) -> None:
        self._http = http
        self._api_key = cfg.HUNTER_API_KEY if api_key is None else api_key
        # Свой на сборку: проверяльщик собирается на каждую задачу.
        self._limiter = RateLimiter(per_minute=RATE_PER_MINUTE)

    async def verify(self, email: str) -> Verdict:
        if not self._api_key:
            raise NoKeyError(NO_KEY)
        response = await with_retries(  # 202 — переспросить тут же: это бесплатно
            lambda: self._get(email),
            attempts=1 + STILL_CHECKING_ASKS,
            topic="адрес ещё проверяется",
            retry_when=_still_checking,
        )
        code = response.status_code
        body = _json(response)
        if code == httpx.codes.UNAUTHORIZED:
            # Имя настройки — строкой журнала, человеку — слова: ключ меняет администратор.
            logger.warning("проверка адреса: ключ не принят — проверить CONTACTS_HUNTER_API_KEY")
            raise ProviderKeyRefusedError(KEY_REFUSED)
        if body is not None and body.get("errors"):
            _raise_refusal(body, code)  # маркер раньше кода: 429 бывает закрытой учёткой
        if code == httpx.codes.TOO_MANY_REQUESTS:
            raise ProviderRateLimitError(TOO_OFTEN)  # повторы с паузой уже позади (`_get`)
        if code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise ProviderError(f"провайдер не ответил (HTTP {code})")
        if code == httpx.codes.ACCEPTED:
            raise StillCheckingError(STILL_CHECKING)
        if body is None:
            raise ProviderError(
                f"ответ не разобран как JSON (HTTP {code}): {response.text[:120]!r}"
            )
        return _verdict(body, code)

    async def _get(self, email: str) -> httpx.Response:
        """Ответ Hunter с повторами временных отказов — правилом поиска адресов
        (`provider.transient`): квота и закрытая учётка приезжают тем же 429 и не повторяются."""
        try:
            # Ключ идёт ЗАГОЛОВКОМ, а не параметром адреса: в адресе он утекал бы в журналы
            # прокси и в текст ошибки провайдера.
            return await with_retries(
                lambda: self._http.get(
                    f"{BASE_URL}/email-verifier",
                    params={"email": email},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=cfg.HUNTER_TIMEOUT_SEC,
                ),
                attempts=ATTEMPTS,
                topic="проверка адреса перед письмом",
                limiter=self._limiter,
                retry_when=transient,
            )
        except httpx.HTTPError as exc:
            # Несобранный запрос — без цепочки причин: в тексте такого исключения ключ.
            error = ProviderError(f"сеть до провайдера не дошла: {reason_of(exc)}")
            raise error from None if unsent(exc) else exc


def _still_checking(response: httpx.Response) -> bool:
    return response.status_code == httpx.codes.ACCEPTED


def _json(response: httpx.Response) -> dict[str, Any] | None:
    """Тело как объект; не JSON или не объект — `None`, судит вызывающий по коду."""
    try:
        body = response.json()
    except ValueError:
        logger.debug("проверка адреса: ответ не JSON", extra={"status": response.status_code})
        return None
    return body if isinstance(body, dict) else None


def _verdict(body: dict[str, Any], code: int) -> Verdict:
    data = body.get("data")
    if not isinstance(data, dict):
        raise ProviderError(f"в ответе нет объекта data (HTTP {code}): {str(body)[:120]!r}")
    status = data.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        # «Мы не поняли» — громко: молча назвать это «провайдер не знает» — и смена формата
        # выглядела бы базой сплошь из адресов, которые нельзя проверить.
        raise ProviderError(
            f"формат ответа поменялся: status={status!r} (HTTP {code}) — читать лог, не гадать"
        )
    score = data.get("score")
    return Verdict(
        status, score if isinstance(score, int) and not isinstance(score, bool) else None
    )
