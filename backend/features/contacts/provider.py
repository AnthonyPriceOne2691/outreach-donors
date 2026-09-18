"""Ступень 3: платный сервис поиска адресов.

Единственная ступень, которая стоит денег, поэтому она последняя и видит
только остаток доменов. Замер: свой парсинг снимает с неё примерно
половину обращений (okf/contact-ladder.md).

Ключ может быть общим с соседней системой — так же, как ключ Ahrefs.
Поэтому **остаток спрашивается у провайдера бесплатным эндпоинтом, а не
считается по своей таблице**: своя таблица знает только наши траты.

Исходы отказа различаются намеренно. «Квота кончилась» и «адреса нет» —
разные вещи: слив первое во второе, мы навсегда похоронили бы домены,
по которым просто не дошли руки заплатить.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from backend.config import contacts as cfg
from backend.features.contacts.quality import Candidate
from backend.features.core.domain import ContactSource

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hunter.io/v2"


class ProviderError(RuntimeError):
    """Сервис не ответил или ответил непонятным. Домен можно повторить."""


class ProviderQuotaError(ProviderError):
    """Платная квота исчерпана. Домен ждёт следующего месяца, а не отказа."""


class ProviderRateLimitError(ProviderError):
    """Частота превышена. Повторить позже."""


@dataclass(frozen=True, slots=True)
class Quota:
    """Остаток платных запросов по данным самого провайдера."""

    used: int
    available: int

    @property
    def left(self) -> int:
        return max(self.available - self.used, 0)


@runtime_checkable
class ContactProvider(Protocol):
    """Платный источник адресов. За интерфейсом, чтобы смена тарифа или
    провайдера не переписывала лестницу."""

    name: str

    async def quota(self) -> Quota:
        """Остаток. Бесплатный вызов — спрашивается перед прогоном."""
        ...

    async def find_emails(self, host: str) -> list[Candidate]:
        """Адреса по домену. Пустой список — законный исход."""
        ...


def _error_id(body: dict[str, Any]) -> tuple[str, str]:
    """Маркер и текст ошибки провайдера. Маркер стабилен, текст — для лога."""
    errors = body.get("errors")
    first = errors[0] if isinstance(errors, list) and errors else {}
    if not isinstance(first, dict):
        return "", str(first)
    return str(first.get("id") or "").strip(), str(first.get("details") or "")


class HunterProvider:
    """Hunter.io. Разбор ответа защитный: чужой формат однажды поменяется,
    и молчать об этом нельзя."""

    name = "hunter"

    def __init__(self, client: httpx.AsyncClient, *, api_key: str | None = None) -> None:
        self._client = client
        self._api_key = api_key if api_key is not None else cfg.HUNTER_API_KEY

    async def _call(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise ProviderError(
                "ключ платного сервиса не задан — заполнить CONTACTS_HUNTER_API_KEY "
                "или убрать ступень 3 из лестницы"
            )
        try:
            response = await self._client.get(
                f"{BASE_URL}{path}",
                params={**params, "api_key": self._api_key},
                timeout=cfg.HUNTER_TIMEOUT_SEC,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"сеть до провайдера не дошла: {exc!r}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"ответ не разобран как JSON (HTTP {response.status_code}): {response.text[:120]!r}"
            ) from exc

        if not isinstance(body, dict):
            raise ProviderError(f"ждали объект, пришло {type(body).__name__}")

        if body.get("errors"):
            marker, details = _error_id(body)
            if marker == "usage_exceeded" or response.status_code == 403:
                raise ProviderQuotaError(f"квота исчерпана: {details}")
            if response.status_code == 429:
                raise ProviderRateLimitError(f"частота превышена: {details}")
            raise ProviderError(f"провайдер отказал ({response.status_code}, {marker}): {details}")

        data = body.get("data")
        if not isinstance(data, dict):
            raise ProviderError(f"в ответе нет объекта data: {str(body)[:120]!r}")
        return data

    async def quota(self) -> Quota:
        data = await self._call("/account", {})
        searches = (data.get("requests") or {}).get("searches")
        if not isinstance(searches, dict):
            raise ProviderError("в ответе нет блока с остатком поисков — формат поменялся")
        used, available = searches.get("used"), searches.get("available")
        if not isinstance(used, int) or not isinstance(available, int):
            raise ProviderError(f"остаток пришёл не числами: {searches!r}")
        return Quota(used=used, available=available)

    async def find_emails(self, host: str) -> list[Candidate]:
        data = await self._call("/domain-search", {"domain": host, "limit": 10})

        rows = data.get("emails")
        if not isinstance(rows, list):
            raise ProviderError(f"в ответе нет списка адресов: {str(data)[:120]!r}")

        out: list[Candidate] = []
        skipped = 0
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("value"), str):
                skipped += 1
                continue
            confidence = row.get("confidence")
            confidence = confidence if isinstance(confidence, int) else 0
            if confidence < cfg.HUNTER_MIN_CONFIDENCE:
                skipped += 1
                continue
            out.append(
                Candidate(
                    email=row["value"].strip().lower(),
                    source=ContactSource.PROVIDER,
                    confidence=confidence,
                )
            )

        if skipped:
            # Пропущенная строка считается: иначе смена формата выглядит
            # как «провайдер перестал находить адреса».
            logger.info(
                "платный сервис по %s: принято %d, пропущено %d (низкая уверенность или формат)",
                host,
                len(out),
                skipped,
            )
        return out
