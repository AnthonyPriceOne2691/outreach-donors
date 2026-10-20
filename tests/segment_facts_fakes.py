"""Подставная модель судьи сегмента v4 для тестов: ответы фактами и разбор описания, без сети.

Судья v4 спрашивает модель о фактах сайта (`segment_facts`) и разбирает описание сегмента один раз на
проход (`segment_scope`). Подделки тестов отвечают по строгой форме запроса: запрос разбора описания
узнаётся по имени формы (`segment_scope`), остальные — запросы фактов сайта. Ответ фактами строится
по намерению (`facts`): «yes» — своё дело того вида, что ищет сегмент; «no» — другое дело; «unsure» —
по тексту не понять.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from backend.features.sales import segment_judge as sj
from backend.features.sales.hypothesis_countries import CountryPolicy
from backend.features.sales.segment_scope import Scope

#: Сегмент без места и своё дело: так судят unit-тесты, которым место не важно.
ANYWHERE = Scope(frozenset(), frozenset({"own"}))
#: Поле гипотезы по умолчанию: чужая география — человеку, «не пишем» пуст.
POLICY = CountryPolicy()
#: Ответ разбора описания по умолчанию — сегмент без места, своё дело.
SCOPE_BODY: dict[str, Any] = {"countries": [], "kinds": ["own"]}
#: Намерение → вид сайта и дело.
INTENTS = {"yes": ("own", "yes"), "no": ("own", "no"), "unsure": ("unclear", "unsure")}

Handler = Callable[[httpx.Request], httpx.Response]


def facts(fit: str, quote: str = "", why: str = "по тексту сайта", **over: Any) -> dict[str, Any]:
    """Ответ модели фактами по намерению; `over` — свои поля (места, страна бизнеса, вид)."""
    kind, core = INTENTS[fit]
    body: dict[str, Any] = {
        "kind": kind,
        "home": "",
        "home_quote": "",
        "served": [],
        "only": "",
        "network": False,
        "local": False,
        "core": core,
        "quote": quote,
        "why": why,
    }
    return body | over


def is_scope(request: httpx.Request) -> bool:
    """Запрос разбора описания — по имени строгой формы ответа."""
    payload = json.loads(request.content)
    form = (payload.get("response_format") or {}).get("json_schema") or {}
    return form.get("name") == "segment_scope"


def reply(request: httpx.Request, body: Any, *, tokens: int = 0) -> httpx.Response:
    """Ответ модели телом `body` (объект — JSON строкой) и итогом токенов."""
    content = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    answer = {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": tokens}}
    return httpx.Response(200, json=answer, request=request)


def scoped(handler: Handler, body: Any = None, *, tokens: int = 0) -> Handler:
    """Транспорт подделки: разбор описания — `body` (по умолчанию «без места, своё дело») за
    `tokens`, остальные запросы — `handler`."""
    said = SCOPE_BODY if body is None else body

    def route(request: httpx.Request) -> httpx.Response:
        return reply(request, said, tokens=tokens) if is_scope(request) else handler(request)

    return route


async def decide_any(client: sj.SegmentClient, **asked: Any) -> sj.SegmentVerdict:
    """`segment_judge.decide` с разбором «сегмент без места» и полем по умолчанию, если тест их не
    задал: место сайта там, где тест его не проверяет, не важно."""
    asked.setdefault("scope", ANYWHERE)
    asked.setdefault("policy", POLICY)
    return await sj.decide(client, **asked)
