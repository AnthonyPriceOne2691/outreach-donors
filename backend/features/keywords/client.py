"""Вызов модели для генерации ключей.

Здесь одна тема: попросить модель и понять ответ. Что именно просить —
в `angles.py`, что делать с ответом — в `hygiene.py`. Сам запрос и разбор
отказов — в `backend/shared/llm.py`: та же работа понадобилась
уникализации письма, а два экземпляра разбора отказов разъезжаются.

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
from backend.shared.llm import Refusal, content_of, is_reasoning, post_chat, tokens_of

logger = logging.getLogger(__name__)

#: Тема для логов: по ней видно, что именно осталось несделанным.
TOPIC = "ключи"

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


def build_payload(model: str, ask: Ask) -> dict[str, Any]:
    """Тело запроса с поправкой на семейство модели."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": ask.system},
            {"role": "user", "content": ask.user},
        ],
    }
    if is_reasoning(model):
        payload["max_completion_tokens"] = max(1200, ask.max_phrases * TOKENS_PER_PHRASE_REASONING)
        # Минимальный режим: нам нужен список фраз, а не размышление о нём.
        payload["reasoning_effort"] = "minimal"
    else:
        payload["max_tokens"] = max(400, ask.max_phrases * TOKENS_PER_PHRASE_PLAIN)
        payload["temperature"] = 0
    return payload


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
        #: Отказы модели, по одному на неудавшийся вызов. Считаются
        #: отдельно от пустых ответов: доля «не знаю» обязана быть
        #: числом в отчёте, иначе поломка ступени выглядит как её работа.
        self.refusals: list[str] = []
        #: Отказ, который повтором не лечится. Живой прогон с протухшим
        #: ключом сделал двенадцать обречённых вызовов подряд — по углу
        #: на каждый круг добора. Повторять то, что уже названо
        #: неисправимым, значит ждать втрое дольше ради того же ответа.
        self._hopeless: str | None = None

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        if self._own_client:
            await self._http.aclose()

    def _skip_hopeless(self) -> bool:
        """Отказ уже назван неисправимым — вызов не делаем.

        Считаем его всё равно: в отчёте должно быть видно, скольким углам
        он стоил фраз, иначе пул выглядит просто маленьким.
        """
        if self._hopeless is None:
            return False
        self.refusals.append(self._hopeless)
        return True

    def _remember(self, refusal: Refusal) -> None:
        """Записать отказ и, если он неисправим, перестать звонить."""
        self.refusals.append(str(refusal))
        if refusal.permanent:
            self._hopeless = str(refusal)

    async def ask(self, ask: Ask) -> list[str]:
        """Попросить фраз. Пустой список — законный исход, не исключение.

        Отказ модели тоже даёт пустой список, но попадает в `refusals`:
        без этого «модель ничего не придумала» и «модель не ответила»
        неразличимы, и отчёт называет вторым первое.
        """
        if not self._api_key:
            raise LlmError(
                "LLM_API_KEY не задан — генерация ключей работать не может. "
                "Заполнить в окружении или брать ключи списком"
            )

        if self._skip_hopeless():
            return []

        self.calls += 1
        body = await post_chat(
            self._http,
            api_key=self._api_key,
            payload=build_payload(self._model, ask),
            topic=TOPIC,
        )
        if isinstance(body, Refusal):
            self._remember(body)
            return []

        self.tokens_spent += tokens_of(body)

        phrases = parse_phrases(content_of(body, topic=TOPIC))
        if not phrases:
            logger.warning("ключи: модель вернула пустой набор (модель %s)", self._model)
        return phrases
