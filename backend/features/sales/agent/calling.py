"""Вызов модели агентом продаж: ситуация письма и судья — одним путём.

Писателя черновика зовёт шов (`agent/writer.py`); ситуацию письма и судью
продаж — этот модуль. Здесь только «как спросить и что вернулось»: что
спрашивать — у вызывающего, и что делать с отказом — тоже у него (ситуация
роняет задачу черновика с причиной, судья отдаёт черновик человеку).

**Наружу не уходит ни один адрес.** Запрос маскируется целиком, как у
писателя (`letters/masking.py`); адрес остался после маскирования — запрос
не отправляется вовсе. Промпт — файлом в package-data, тем же чтением, что у
промпта этапа (`writer.load_prompt` через `writer.build_payload`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from backend.config import llm as llm_cfg
from backend.features.agent.writer import build_payload
from backend.features.letters import masking
from backend.shared.llm import (
    Refusal,
    RefusalKind,
    content_of,
    is_reasoning,
    post_chat,
    tokens_of,
)

logger = logging.getLogger(__name__)

#: Потолок вывода: метка или строгая форма, рассуждать почти не о чем.
TOKENS_REASONING = 1500
TOKENS_PLAIN = 600


@dataclass(frozen=True, slots=True)
class Answer:
    """Что вернула модель: текст ответа и сколько он стоил."""

    content: str
    tokens: int
    #: Метки, которыми замаскированы адреса запроса: метка → адрес.
    labels: dict[str, str] = field(default_factory=dict)


def client() -> httpx.AsyncClient:
    """HTTP-клиент одного вызова. Тесты подменяют его подставным транспортом."""
    return httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S)


def payload(
    model: str, *, prompt: Path, user: str, schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Тело запроса: системный промпт файлом, данные — сообщением, ответ — JSON.

    Форма — та же, что у писателя шва (`writer.build_payload`), своё — потолок
    вывода и сэмплинг: метка и строгая форма, а не письмо. Схема (`schema`) —
    строгая форма ответа у провайдера (`json_schema`, `strict`): модель не может
    вернуть объект другой формы. Без схемы — любой JSON-объект.
    """
    body = build_payload(model, user=user, prompt=prompt)
    if schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {**schema, "strict": True}}
    if is_reasoning(model):
        body["max_completion_tokens"] = TOKENS_REASONING
        body["reasoning_effort"] = "minimal"
    else:
        body["max_tokens"] = TOKENS_PLAIN
        body["temperature"] = 0
    return body


async def ask(
    *, prompt: Path, user: str, model: str, topic: str, schema: dict[str, Any] | None = None
) -> Answer | Refusal:
    """Один вызов модели со строгим JSON в ответе. `Refusal` — не вышло, со словами.

    Ключа нет — тоже `Refusal` («чинить»): его называет общий `post_chat`.
    """
    hidden = masking.mask(user)
    if masking.leaked(hidden.text) is not None:
        logger.error("%s: адрес остался после маскирования — запрос не отправлен", topic)
        return Refusal(
            RefusalKind.LOCAL,
            "маскирование не сработало — адрес ушёл бы в модель, запрос не отправлен",
            permanent=True,
        )
    async with client() as http:
        body = await post_chat(
            http,
            api_key=llm_cfg.API_KEY,
            payload=payload(model, prompt=prompt, user=hidden.text, schema=schema),
            topic=topic,
        )
    if isinstance(body, Refusal):
        return body
    return Answer(
        content=content_of(body, topic=topic), tokens=tokens_of(body), labels=hidden.labels
    )
