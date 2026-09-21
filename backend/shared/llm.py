"""Один запрос к модели и разбор её отказов.

Здесь нет ни одного решения про то, что просить, — только про то, как
пережить ответ. Вынесено из генерации ключей, когда та же работа
понадобилась уникализации письма: два экземпляра разбора отказов
разъезжаются на первой же правке, и тише всех расходится тот, который
реже вызывают.

**Отказ не рушит вызывающего.** Возвращается `None`, причина уже в логе
с названием темы — «ключи» или «письма», — иначе по логу непонятно, что
именно осталось несделанным. Решение о повторе принимает вызывающий:
у пула ключей и у одного письма оно разное.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1/chat/completions"

#: Семейства, которые считают токены рассуждений отдельно и не принимают
#: температуру. Перепутать нельзя: провайдер отвечает отказом, а не догадкой.
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def is_reasoning(model: str) -> bool:
    return model.startswith(REASONING_PREFIXES)


async def post_chat(
    http: httpx.AsyncClient,
    *,
    api_key: str,
    payload: dict[str, Any],
    topic: str,
) -> dict[str, Any] | None:
    """Запрос к модели. `None` — не получилось, причина уже в логе."""
    try:
        response = await http.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    except httpx.HTTPError as exc:
        logger.exception("%s: модель недоступна (%r)", topic, exc)
        return None

    if response.status_code >= 400:
        # Отказ называется целиком: «плохой запрос» без текста провайдера
        # отлаживается вслепую.
        logger.error(
            "%s: модель отказала (%s): %s", topic, response.status_code, response.text[:300]
        )
        return None

    try:
        body = response.json()
    except ValueError:
        logger.exception("%s: ответ модели не разобран как JSON", topic)
        return None

    return body if isinstance(body, dict) else None


def content_of(body: dict[str, Any], *, topic: str) -> str:
    """Текст ответа из первой альтернативы. Пусто — значит, разбирать нечего."""
    choices = body.get("choices") or []
    if not choices:
        logger.error("%s: модель вернула ответ без вариантов", topic)
        return ""
    return str(((choices[0] or {}).get("message") or {}).get("content") or "")


def tokens_of(body: dict[str, Any]) -> int:
    """Сколько токенов стоил вызов. Единственный честный измеритель расхода."""
    usage = body.get("usage") or {}
    return int(usage.get("total_tokens") or 0)
