"""Один запрос к модели и разбор её отказов.

Здесь нет ни одного решения про то, что просить, — только про то, как
пережить ответ. Вынесено из генерации ключей, когда та же работа
понадобилась уникализации письма: два экземпляра разбора отказов
разъезжаются на первой же правке, и тише всех расходится тот, который
реже вызывают.

**Отказ не рушит вызывающего.** Возвращается `Refusal`, а решение
о повторе принимает вызывающий: у пула ключей и у одного письма оно разное.

**Причина едет вместе со значением, а не только в лог.** Раньше здесь
возвращался голый `None`, и по нему нельзя было отличить протухший ключ
от честно пустого ответа. Генерация ключей на этом и погорела: `None`
превращался в `[]`, добор крутил углы вхолостую и записывал в отчёт
«модель исчерпала уникальные фразы» — то есть называла неработающий ключ
исчерпанной фантазией. Лог при этом был полон, но читают отчёт.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from backend.shared.net.retry import with_retries

logger = logging.getLogger(__name__)

#: Сколько раз пробуем один вызов модели. Перегрузка у провайдера
#: проходит сама, а вызов стоит денег только когда он удался.
ATTEMPTS = 3

API_URL = "https://api.openai.com/v1/chat/completions"

#: Семейства, которые считают токены рассуждений отдельно и не принимают
#: температуру. Перепутать нельзя: провайдер отвечает отказом, а не догадкой.
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def is_reasoning(model: str) -> bool:
    return model.startswith(REASONING_PREFIXES)


#: Статусы, на которых повтор бессмысленен: ключ, права или сама форма
#: запроса. Разделение то же, что у клиента Ahrefs, и по той же причине —
#: «повторить позже» на протухшем ключе это вечный цикл с бодрым текстом.
PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 422})


class RefusalKind(StrEnum):
    NETWORK = "сеть"  # до провайдера не дошли
    REFUSED = "отказ"  # провайдер ответил и отказал
    FORMAT = "формат"  # ответил, но разобрать нечего


@dataclass(frozen=True, slots=True)
class Refusal:
    """Почему вызов не удался. Отдаётся вызывающему наравне с ответом.

    `permanent` — это не «серьёзность», а указание, что делать: повтор
    не поможет, чинить надо ключ, права или запрос.
    """

    kind: RefusalKind
    detail: str
    permanent: bool

    def __str__(self) -> str:
        what = "чинить" if self.permanent else "можно повторить"
        return f"модель, {self.kind} ({what}): {self.detail}"


def _message_of(response: httpx.Response) -> str:
    """Внятная фраза провайдера, если она есть, иначе сырое тело."""
    try:
        body = response.json()
    except ValueError:
        # Не JSON — законный случай для страницы прокси или шлюза.
        # Тело всё равно уедет в ноту целиком, но молчать о разборе нельзя.
        logger.debug("отказ провайдера пришёл не в JSON, берём тело как есть")
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:300]
    return response.text[:300]


async def post_chat(
    http: httpx.AsyncClient,
    *,
    api_key: str,
    payload: dict[str, Any],
    topic: str,
) -> dict[str, Any] | Refusal:
    """Запрос к модели. `Refusal` — не получилось, и он говорит почему."""
    # Повторы общие на все внешние сервисы: до них один обрыв связи
    # означал письмо, оставшееся шаблонным, или ответ, оставшийся
    # неразобранным, — и оба случая выглядели как «модель отказала».
    if not api_key.isascii():
        # Ключ едет в HTTP-заголовке, а заголовки только ASCII. Без этой
        # проверки httpx роняет UnicodeEncodeError мимо всей обработки
        # отказов: вызывающий получает трассировку, которая не называет
        # ни ключ, ни что делать. Тот же класс, что у секрета приёма —
        # он тоже едет заголовком и тоже обязан быть латиницей.
        detail = "ключ модели содержит не-ASCII символы — заголовок с ним не собрать"
        logger.error("%s: %s", topic, detail)
        return Refusal(RefusalKind.REFUSED, detail, permanent=True)

    try:
        response = await with_retries(
            lambda: http.post(
                API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            ),
            attempts=ATTEMPTS,
            topic=topic,
        )
    except httpx.HTTPError as exc:
        logger.exception("%s: модель недоступна (%r)", topic, exc)
        return Refusal(RefusalKind.NETWORK, repr(exc), permanent=False)

    if response.status_code >= 400:
        # Отказ называется целиком: «плохой запрос» без текста провайдера
        # отлаживается вслепую. Но сырое тело — это JSON, и в ноте оператору
        # оно обрывается на первой скобке; провайдер кладёт внятную фразу
        # в `error.message`, и она прямо говорит, что чинить.
        detail = f"HTTP {response.status_code}: {_message_of(response)}"
        logger.error("%s: модель отказала (%s)", topic, detail)
        return Refusal(
            RefusalKind.REFUSED,
            detail,
            permanent=response.status_code in PERMANENT_STATUSES,
        )

    try:
        body = response.json()
    except ValueError:
        logger.exception("%s: ответ модели не разобран как JSON", topic)
        return Refusal(RefusalKind.FORMAT, "ответ не разобран как JSON", permanent=False)

    if not isinstance(body, dict):
        logger.error("%s: ждали объект, пришло %s", topic, type(body).__name__)
        return Refusal(
            RefusalKind.FORMAT, f"ждали объект, пришло {type(body).__name__}", permanent=False
        )
    return body


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
