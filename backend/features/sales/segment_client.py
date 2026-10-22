"""Вызовы модели судьи сегмента: факты сайта и разбор описания — строгой формой у провайдера.

Модель v4 отдаёт факты, а не полосу (`segment_facts`), описание разбирается один раз
(`segment_scope`); полосу ставит судья кодом (`segment_judge`, `segment_rules`). Здесь — только
вызов: промпт, тело запроса, потолок вывода, токены и сбой словами. Клиент не бросает: отказ
провайдера, обрезанный и пустой ответ — словами в ответе (`Asked.failure`), с токенами, если они
оплачены. У ключа нет кредитов — признак `no_credits` в ответе: проход встаёт, а не судит дальше
вхолостую (`segment_window`, `segment_judging.read_scope`; слово владельца 20.10.2026).

**Второй шанс цитате** (v5): цитата вида сайта не нашлась в тексте — модель спрашивают ещё раз по
тому же тексту с короткой подсказкой: какие слова не нашлись и правило «дословно слова одной строки,
без подписи» (`prompts/segment_again.md`). Один раз на сайт; цитата нового ответа нашлась — в счёт
идут его факты, нет — «на проверку» словами. Токены обоих вызовов — в ответе (`Said.tokens`): в счёт
прохода и в расход; повтор и его исход — признаками ответа (`Said.retried`, `Said.retry_held`).
"""

from __future__ import annotations

import json
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
#: v5 — второй шанс цитате: подсказка второго вопроса (`AGAIN_PATH`); факты помнят, что судья видел
#: только выдачу (`SiteFacts.snippet_only`). Текст `segment.md` — прежний: калибровка «не понять»
#: замерена парно (22.10) и не взята — доля «на проверку» на эталонах не упала.
PROMPT_VERSION = "sales-segment-v5"
PROMPT_PATH = Path(__file__).with_name("prompts") / "segment.md"
#: Подсказка второго вопроса, когда цитата вида сайта не нашлась (`SegmentClient.judge`): файлом
#: рядом с промптом — её правка тоже правка поведения модели и той же версии.
AGAIN_PATH = Path(__file__).with_name("prompts") / "segment_again.md"
#: Сколько знаков ненайденной цитаты уходит в подсказку: цитата — до 30 слов.
AGAIN_QUOTE_CHARS = 300
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


@lru_cache(maxsize=1)
def load_again() -> str:
    """Подсказка второго вопроса файлом рядом с промптом; `{quote}` — место ненайденной цитаты."""
    return AGAIN_PATH.read_text(encoding="utf-8").strip()


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


def again_message(user: str, missing: str) -> str:
    """Второй вопрос: тот же вопрос по тому же тексту и подсказка — какие слова не нашлись и правило
    цитаты (`load_again`). Слова — строкой JSON с погашенными метками данных: они пришли из ответа
    модели о чужом тексте, и закрыть данные раньше времени или сойти за наше правило не могут."""
    words = json.dumps(_quiet(missing[:AGAIN_QUOTE_CHARS]), ensure_ascii=False)
    return f"{user}\n{load_again().replace('{quote}', words)}"


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
    #: Цитата первого ответа не нашлась — модель спросили второй раз (токены обоих — в `tokens`).
    retried: bool = False
    #: И цитата второго ответа нашлась: факты — его.
    retry_held: bool = False


def _both(first: Spent | None, second: Spent | None) -> Spent | None:
    """Токены двух вызовов раздельно; хоть один не назван раздельно — оба «раздельно неизвестно»."""
    if first is None or second is None:
        return None
    return Spent(
        first.prompt + second.prompt,
        first.completion + second.completion,
        first.reasoning + second.reasoning,
    )


def second_chance(first: Said, again: Said, missing_again: str) -> Said:
    """Итог второго вопроса: факты нового ответа — или «на проверку» словами. Цитата нового ответа
    тоже не нашлась — его слова (с его цитатой); новый ответ не дал ничего лучше (сбой провайдера, нет
    цитаты) — слова первого. Токены — обоих вызовов. Нет кредитов на втором — первый ответ остаётся:
    проход встанет на следующем сайте."""
    tokens, spent = first.tokens + again.tokens, _both(first.spent, again.spent)
    if again.facts is not None:
        return Said(again.facts, "", again.model, tokens, spent, retried=True, retry_held=True)
    failure = again.failure if missing_again else first.failure
    return Said(None, failure, first.model, tokens, spent, retried=True)


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
        """Факты одного сайта; цитата вида сайта не нашлась — второй вопрос с подсказкой, один на
        сайт (шапка модуля). Правила до модели — у судьи (`segment_judge.decide`)."""
        user, prompt = user_message(host=host, segment=segment, text=text), system or load_prompt()
        said, missing = await self._facts(user, prompt, text, host)
        if not missing:
            return said
        again, missing_again = await self._facts(again_message(user, missing), prompt, text, host)
        logger.info(
            "судья сегмента: цитата не нашлась — второй вопрос",
            extra={"host": host, "held": again.facts is not None},
        )
        return second_chance(said, again, missing_again)

    async def _facts(self, user: str, system: str, text: SiteText, host: str) -> tuple[Said, str]:
        """Один вызов фактов: ответ — и цитата вида сайта, которой нет в тексте (пусто — нет такой)."""
        asked = await self.ask(user=user, system=system, schema=segment_facts.SCHEMA, host=host)
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
            ), ""
        found = segment_facts.read(asked.content, text)
        return Said(
            found.facts, found.failure, asked.model, asked.tokens, asked.spent
        ), found.missing

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
