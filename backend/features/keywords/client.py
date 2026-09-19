"""Вызов модели для генерации ключей.

Здесь одна тема: попросить модель и понять ответ. Что именно просить —
в `angles.py`, что делать с ответом — в `hygiene.py`.

**Рассуждающие модели требуют других параметров.** У них свой потолок
вывода и режим рассуждений вместо температуры. Перепутать нельзя:
провайдер отвечает отказом, а не догадкой.

**Отказ модели не рушит прогон.** Пустой ответ — это пустой список
и запись в лог; решение о повторе принимает вызывающий. Иначе один
сбой сети посреди набора пула отменяет всю работу.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from backend.config import llm as cfg
from backend.features.keywords.hygiene import parse_phrases

logger = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1/chat/completions"

#: Семейства, которые считают токены рассуждений отдельно и не принимают
#: температуру.
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

#: Запас токенов на фразу. Рассуждающая модель тратит их и на размышление,
#: поэтому ей нужно кратно больше — при нехватке ответ обрывается.
TOKENS_PER_PHRASE_REASONING = 60
TOKENS_PER_PHRASE_PLAIN = 25


class LlmError(RuntimeError):
    """Модель недоступна или ответила непонятным."""


@dataclass(frozen=True, slots=True)
class Ask:
    """Одна просьба к модели."""

    system: str
    user: str
    max_phrases: int


def _is_reasoning(model: str) -> bool:
    return model.startswith(REASONING_PREFIXES)


def build_payload(model: str, ask: Ask) -> dict[str, Any]:
    """Тело запроса с поправкой на семейство модели."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": ask.system},
            {"role": "user", "content": ask.user},
        ],
    }
    if _is_reasoning(model):
        payload["max_completion_tokens"] = max(1200, ask.max_phrases * TOKENS_PER_PHRASE_REASONING)
        # Минимальный режим: нам нужен список фраз, а не размышление о нём.
        payload["reasoning_effort"] = "minimal"
    else:
        payload["max_tokens"] = max(400, ask.max_phrases * TOKENS_PER_PHRASE_PLAIN)
        payload["temperature"] = 0
    return payload


def _content_of(body: dict[str, Any]) -> str:
    """Текст ответа из первой альтернативы. Пусто — значит, разбирать нечего."""
    choices = body.get("choices") or []
    if not choices:
        logger.error("ключи: модель вернула ответ без вариантов")
        return ""
    return str(((choices[0] or {}).get("message") or {}).get("content") or "")


class KeygenClient:
    """Клиент модели. Считает потраченные токены — это единственный
    честный измеритель расхода на генерацию."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model or cfg.KEYGEN_MODEL
        self._api_key = api_key if api_key is not None else cfg.API_KEY
        self._own_client = client is None
        self._http = client or httpx.AsyncClient(timeout=cfg.TIMEOUT_S)
        self.tokens_spent = 0
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        if self._own_client:
            await self._http.aclose()

    async def ask(self, ask: Ask) -> list[str]:
        """Попросить фраз. Пустой список — законный исход, не исключение."""
        if not self._api_key:
            raise LlmError(
                "LLM_API_KEY не задан — генерация ключей работать не может. "
                "Заполнить в окружении или брать ключи списком"
            )

        self.calls += 1
        body = await self._post(ask)
        if body is None:
            return []

        usage = body.get("usage") or {}
        self.tokens_spent += int(usage.get("total_tokens") or 0)

        phrases = parse_phrases(_content_of(body))
        if not phrases:
            logger.warning("ключи: модель вернула пустой набор (модель %s)", self._model)
        return phrases

    async def _post(self, ask: Ask) -> dict[str, Any] | None:
        """Запрос к модели. `None` — не получилось, причина уже в логе.

        Вынесено из просьбы: там собирались и отправка, и разбор отказов,
        и учёт токенов — три темы в одной функции.
        """
        try:
            response = await self._http.post(
                API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=build_payload(self._model, ask),
            )
        except httpx.HTTPError as exc:
            logger.exception("ключи: модель недоступна (%r) — угол остался без фраз", exc)
            return None

        if response.status_code >= 400:
            # Отказ называется целиком: «плохой запрос» без текста
            # провайдера отлаживается вслепую.
            logger.error(
                "ключи: модель отказала (%s): %s", response.status_code, response.text[:300]
            )
            return None

        try:
            body = response.json()
        except ValueError:
            logger.exception("ключи: ответ модели не разобран как JSON")
            return None

        return body if isinstance(body, dict) else None
