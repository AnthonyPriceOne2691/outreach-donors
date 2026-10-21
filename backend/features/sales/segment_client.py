"""Вызовы модели судьи сегмента: факты сайта и разбор описания — строгой формой у провайдера.

Модель v4 отдаёт факты, а не полосу (`segment_facts`), описание разбирается один раз
(`segment_scope`); полосу ставит судья кодом (`segment_judge`, `segment_rules`). Здесь — только
вызов: промпт, тело запроса, потолок вывода, токены и сбой словами. Клиент не бросает: отказ
провайдера, обрезанный и пустой ответ — словами в ответе (`Asked.failure`), с токенами, если они
оплачены. У ключа нет кредитов — признак `no_credits` в ответе: проход встаёт, а не судит дальше
вхолостую (`segment_window`, `segment_judging.read_scope`; слово владельца 20.10.2026).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.config import llm as llm_cfg
from backend.features.sales import segment_facts, segment_scope
from backend.features.sales.segment_facts import SiteFacts
from backend.features.sales.segment_scope import Scope
from backend.features.sales.segment_text import SiteText
from backend.shared.llm import ModelClient, Refusal, content_of, is_reasoning, post_chat, tokens_of

logger = logging.getLogger(__name__)

TOPIC = "судья сегмента продаж"
#: Версия промпта. Меняется при КАЖДОЙ правке файла промпта: вердикт хранит её, и точность
#: против человека считается по версии; кэш вердиктов отдаёт только вердикты своей версии.
#: v2 — сайт о бизнесах вида — не бизнес вида. v3 — признак сегмента, которого нет в тексте, —
#: «не видно», а не «нет»; посредники — по сигналам текста; язык сайта — кодом.
#: v4 — модель отдаёт факты (вид сайта, дело, страна бизнеса, места работы — с цитатами), полосу
#: ставит код по таблице правил и полю гипотезы «страны компаний» (`segment_rules`).
PROMPT_VERSION = "sales-segment-v4"
PROMPT_PATH = Path(__file__).with_name("prompts") / "segment.md"
#: Усилие рассуждения — как у судьи площадки: на `minimal` модель читает только буквы.
REASONING_EFFORT = "low"
#: Потолки вывода: рассуждение входит в потолок, и обрезанный ответ пришёл бы пустым. Факты v4
#: длиннее вердикта v3 (места строками с цитатами): замер 20.10 — вывод до ≈ 2700 токенов.
TOKENS_REASONING = 4000
TOKENS_PLAIN = 900
#: Ответ обрезан потолком вывода (`finish_reason == "length"`): у модели с рассуждением потолок
#: `max_completion_tokens` включает рассуждение — оно съело ответ. Это не вердикт: следующий проход
#: спросит снова, а доля таких за прогон — в его итоге (`JudgeTally.cut_off`).
CUT_OFF = (
    "модель упёрлась в потолок вывода — рассуждение съело ответ; следующий проход спросит снова"
)
#: Метки данных. Сайт может написать их сам, чтобы «закрыть» текст раньше времени:
#: в его тексте они заменяются похожими знаками.
SEGMENT_OPEN, SEGMENT_CLOSE = "<<<SEGMENT", "SEGMENT>>>"
SITE_OPEN, SITE_CLOSE = "<<<SITE", "SITE>>>"


@lru_cache(maxsize=1)
def load_prompt() -> str:
    """Промпт файлом рядом с модулем: читается один раз на процесс."""
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


@dataclass(frozen=True, slots=True)
class Spent:
    """Токены вызова раздельно: ввод и вывод — у вывода своя цена, и рассуждение модели идёт в
    счёт вывода (`completion_tokens` его включает); `reasoning` — сколько из вывода рассуждение.
    Провайдер не назвал ввод или вывод — раздельно неизвестно, а не ноль (`spent_of`)."""

    prompt: int
    completion: int
    reasoning: int = 0


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def spent_of(body: dict[str, Any]) -> Spent | None:
    """Ввод, вывод и рассуждение из `usage` ответа; `None` — провайдер не назвал ввод или вывод
    или они не сходятся с его итогом (`tokens_of`: по итогу пишется расход): такие токены —
    «раздельно неизвестно», а не ноль ни там, ни там."""
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt, completion = _count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens"))
    if prompt is None or completion is None or prompt + completion != tokens_of(body):
        return None
    details = usage.get("completion_tokens_details")
    reasoning = _count(details.get("reasoning_tokens")) if isinstance(details, dict) else None
    return Spent(prompt, completion, reasoning or 0)


def _quiet(words: str) -> str:
    """Метки данных в чужом тексте — похожими знаками: закрыть текст раньше он не сможет."""
    return words.replace("<<<", "‹‹‹").replace(">>>", "›››")


def user_message(*, host: str, segment: str, text: SiteText) -> str:
    """Сегмент и текст сайта — данными между метками; домен — строкой над ними."""
    return (
        f"Domain: {_quiet(host)}\n{SEGMENT_OPEN}\n{_quiet(segment.strip())}\n{SEGMENT_CLOSE}\n"
        f"{SITE_OPEN}\n{_quiet(text.for_model())}\n{SITE_CLOSE}"
    )


def scope_message(segment: str) -> str:
    """Описание сегмента — данными между метками: разбор описания (`segment_scope`)."""
    return f"{SEGMENT_OPEN}\n{_quiet(segment.strip())}\n{SEGMENT_CLOSE}"


def build_payload(
    model: str,
    *,
    user: str,
    system: str | None = None,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Тело запроса: строгая форма ответа у провайдера (`json_schema`, `strict`; по умолчанию —
    факты сайта), потолок вывода — по семейству модели."""
    limits: dict[str, Any] = (
        {"max_completion_tokens": TOKENS_REASONING, "reasoning_effort": REASONING_EFFORT}
        if is_reasoning(model)
        else {"max_tokens": TOKENS_PLAIN, "temperature": 0}
    )
    form = schema or segment_facts.SCHEMA
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system or load_prompt()},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_schema", "json_schema": {**form, "strict": True}},
        **limits,
    }


def finish_of(body: dict[str, Any]) -> str | None:
    """Почему модель закончила ответ (`finish_reason` первой альтернативы); не названо — `None`."""
    choices = body.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    reason = first.get("finish_reason") if isinstance(first, dict) else None
    return reason if isinstance(reason, str) else None


@dataclass(frozen=True, slots=True)
class Asked:
    """Один вызов модели: текст ответа — или почему его нет (`failure`), и чего он стоил."""

    content: str = ""
    failure: str | None = None
    model: str | None = None
    tokens: int = 0
    spent: Spent | None = None
    cut_off: bool = False
    #: У ключа модели нет кредитов (`Refusal.no_credits`): дальше модель не зовут.
    no_credits: bool = False


@dataclass(frozen=True, slots=True)
class Said:
    """Что модель сказала о сайте: факты — или почему их нет; токены вызова (у фактов кэша — 0)."""

    facts: SiteFacts | None
    failure: str = ""
    model: str | None = None
    tokens: int = 0
    spent: Spent | None = None
    #: Ответа нет вовсе (отказ, пустой или обрезанный ответ): следующий проход спросит снова.
    unanswered: bool = False
    cut_off: bool = False
    #: У ключа модели нет кредитов: сайт не судили — проход встаёт, сайт ждёт «Продолжить».
    no_credits: bool = False


class SegmentClient(ModelClient):
    """Вызовы модели судьи сегмента. Не бросает: любой сбой — словами в ответе."""

    def _default_model(self) -> str:
        return llm_cfg.SALES_SEGMENT_MODEL

    async def ask(self, *, user: str, system: str, schema: dict[str, Any], host: str = "") -> Asked:
        """Один вызов строгой формы; отказ, обрезанный и пустой ответ — словами, с токенами."""
        payload = build_payload(self._model, user=user, system=system, schema=schema)
        body = await post_chat(self._http, api_key=self._api_key, payload=payload, topic=TOPIC)
        if isinstance(body, Refusal):
            logger.warning("%s: %s", TOPIC, body, extra={"host": host})
            return Asked(failure=f"модель не ответила: {body}", no_credits=body.no_credits)
        tokens, spent = tokens_of(body), spent_of(body)
        content = content_of(body, topic=TOPIC)
        if finish_of(body) == "length":
            # Пустой или обрезанный посередине JSON — одно и то же: ответа нет, оплачен весь вывод.
            logger.warning(
                "%s: ответ обрезан потолком вывода",
                TOPIC,
                extra={"host": host, "tokens": tokens, "empty": not content},
            )
            return Asked("", CUT_OFF, self._model, tokens, spent, cut_off=True)
        if not content:
            # Оплачен: рассуждение съело потолок и до ответа не дошло.
            return Asked("", "пустой ответ модели", self._model, tokens, spent)
        return Asked(content, None, self._model, tokens, spent)

    async def judge(
        self, *, host: str, segment: str, text: SiteText, system: str | None = None
    ) -> Said:
        """Факты одного сайта. Правила до модели — у судьи (`segment_judge.decide`)."""
        asked = await self.ask(
            user=user_message(host=host, segment=segment, text=text),
            system=system or load_prompt(),
            schema=segment_facts.SCHEMA,
            host=host,
        )
        if asked.failure is not None:
            return Said(
                None,
                asked.failure,
                asked.model,
                asked.tokens,
                asked.spent,
                True,
                asked.cut_off,
                asked.no_credits,
            )
        found = segment_facts.read_facts(asked.content, text)
        if isinstance(found, str):
            return Said(None, found, asked.model, asked.tokens, asked.spent)
        return Said(found, "", asked.model, asked.tokens, asked.spent)

    async def scope(self, segment: str) -> tuple[Scope | str, Asked]:
        """Разбор описания сегмента (`segment_scope`): итог или почему его нет — и сам вызов."""
        asked = await self.ask(
            user=scope_message(segment),
            system=segment_scope.load_prompt(),
            schema=segment_scope.SCHEMA,
        )
        if asked.failure is not None:
            return asked.failure, asked
        return segment_scope.read_scope(asked.content), asked
