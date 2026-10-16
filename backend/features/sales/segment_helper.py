"""Помощник описания сегмента: черновик человека → описание, по которому судит судья сегмента.

Решение владельца 16.10.2026 — «нужно делать его, чтобы он работал… обязательно»: от описания
сегмента зависит конечный результат сбора. Замер 15.10 на первом живом наборе: под прежним
расплывчатым описанием гипотезы ложных «принять» у судьи 4–6 из 33, под конкретным — 1 из 33.
Судья читает описание буквально (`prompts/segment.md`), и помощник переписывает черновик так,
как его надо читать судье (`prompts/segment_helper.md`):

- **кто подходит** — бизнес, который сам делает и продаёт своё, на своём сайте, от своего имени;
- **где** — только место, которое назвал сам черновик. Язык сайта — никогда, если черновик сам
  его не требует: язык наших писем — не свойство сайта, а каждое такое слово в описании судья
  режет (опыт 15.10 приписал сайту язык наших писем);
- **кто не подходит** — те же виды сайтов в любой нише, что у правила судьи v2;
- услуг и соседних видов бизнеса сверх черновика не добавляет (опыт 15.10 дописал услугу, которой в черновике не было) —
  спрашивает человека вопросом (`unclear`).

**«Черновик расплывчатый» — не мнение модели одним словом, а её список** (`problems`): что в
черновике собьёт судью, словами черновика — тема вместо вида бизнеса, цель или контекст человека
вместо сайта, ни слова о том, что бизнес делает сам. Список не пуст — черновик расплывчатый. Так
вывод держится на названных словах (одним флагом модель от прогона к прогону судила по-разному —
замер 16.10), и человек видит, что именно править.

**Человек правит и сохраняет сам**: ответ — предложение на экране, в гипотезу оно не пишется.
Зовут помощника кнопка «Помочь с описанием» у поля описания и проверка описания при смете сбора.

**Кэш** (`SalesSegmentHelpModel`): тот же черновик — с точностью до пробелов, тем же отпечатком,
что у описания в вердикте судьи (`segment_digest`), — под той же версией промпта и моделью второй
раз не оплачивается: смета пересчитывается часто, а описание меняется редко. Отказ модели, пустой
или негодный ответ строкой кэша не становится — следующий вызов спросит модель снова.

**Потолок** — дневной продаж (`usage_cap.sales_cap`): его передаёт вызывающий — `usage_cap`
считает операцию этого модуля и импортирует её отсюда. Ответ из кэша потолка не ждёт: он ничего
не стоит. Токены пишутся в журнал расхода и тогда, когда ответ негоден: строка журнала отвечает
на «во что обошлось», а не на «что вышло».
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.features.core import usage
from backend.features.sales.segment_judge import segment_digest
from backend.features.sales.segment_models import SalesSegmentHelpModel
from backend.shared.llm import ModelClient, Refusal, content_of, is_reasoning, post_chat, tokens_of

logger = logging.getLogger(__name__)

TOPIC = "помощник описания сегмента"
#: Версия промпта. Меняется при КАЖДОЙ правке файла промпта: кэш отдаёт только ответы своей версии.
PROMPT_VERSION = "sales-segment-helper-v1"
PROMPT_PATH = Path(__file__).with_name("prompts") / "segment_helper.md"
#: Операция журнала расхода (`core/usage.OPERATION_PROVIDERS`); её считает потолок продаж.
OPERATION = "sales_segment_helper"
#: Усилие рассуждения — как у судьи сегмента; опыт 15.10 на нём — 1 234 токена ≈ $0,0017 за вызов.
REASONING_EFFORT = "low"
#: Потолки вывода: рассуждение входит в потолок, и обрезанный ответ пришёл бы пустым.
TOKENS_REASONING = 4000
TOKENS_PLAIN = 1200
#: Описание длиннее — негодный ответ: промпт просит до 900 знаков, а судья читает описание целиком
#: в каждом вызове по каждому сайту.
MAX_DESCRIPTION = 1500
#: Вопросов человеку — не больше трёх, бед черновика — не больше пяти; каждая строка — короткая.
MAX_QUESTIONS = 3
MAX_PROBLEMS = 5
MAX_LINE = 300

#: Метки данных: черновик пишет человек, но и он не закроет данные раньше времени.
DRAFT_OPEN, DRAFT_CLOSE = "<<<DRAFT", "DRAFT>>>"

#: Строгая форма ответа у провайдера (`json_schema`, `strict`): другой формы модель не вернёт.
SCHEMA: dict[str, Any] = {
    "name": "segment_help",
    "schema": {
        "type": "object",
        "properties": {
            "problems": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
            "unclear": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["problems", "description", "unclear"],
        "additionalProperties": False,
    },
}

EMPTY = "Напишите черновик описания — помощнику нечего переписывать"
FAILED = (
    "Помощник не ответил ({reason}) — попробуйте ещё раз чуть позже; описание можно написать "
    "и самому, подсказка «Как описать сегмент» — под полем"
)


class EmptyDraftError(ValueError):
    """Черновика нет — переписывать нечего."""


class HelperFailedError(Exception):
    """Помощник не ответил — словами. Токены ответа, если он был, уже в журнале расхода."""


@lru_cache(maxsize=1)
def load_prompt() -> str:
    """Промпт файлом рядом с модулем: читается один раз на процесс."""
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def _quiet(words: str) -> str:
    """Метки данных в черновике — похожими знаками: закрыть данные раньше он не сможет."""
    return words.replace("<<<", "‹‹‹").replace(">>>", "›››")


def user_message(draft: str) -> str:
    """Черновик — данными между метками."""
    return f"{DRAFT_OPEN}\n{_quiet(draft.strip())}\n{DRAFT_CLOSE}"


def build_payload(model: str, *, draft: str, system: str | None = None) -> dict[str, Any]:
    """Тело запроса: промпт файлом, черновик сообщением, строгая форма ответа; потолок вывода —
    по семейству модели. `system` — промпт вместо файла (eval гоняет испорченный)."""
    limits: dict[str, Any] = (
        {"max_completion_tokens": TOKENS_REASONING, "reasoning_effort": REASONING_EFFORT}
        if is_reasoning(model)
        else {"max_tokens": TOKENS_PLAIN, "temperature": 0}
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system or load_prompt()},
            {"role": "user", "content": user_message(draft)},
        ],
        "response_format": {"type": "json_schema", "json_schema": {**SCHEMA, "strict": True}},
        **limits,
    }


@dataclass(frozen=True, slots=True)
class Suggestion:
    """Что предложил помощник: описание для судьи, вопросы человеку и что в черновике собьёт
    судью; список бед не пуст — черновик расплывчатый."""

    description: str
    unclear: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def loose(self) -> bool:
        return bool(self.problems)


def _line(words: str) -> str:
    """Строка человеку; длинная — обрезана с многоточием, а не потеряна."""
    line = " ".join(words.split())
    return line if len(line) <= MAX_LINE else line[: MAX_LINE - 1].rstrip() + "…"


def _lines(raw: list[Any], most: int) -> tuple[str, ...]:
    """Строки списка ответа: непустые строки, не больше `most`."""
    return tuple(_line(item) for item in raw if isinstance(item, str) and item.strip())[:most]


def _object(content: str) -> dict[str, Any] | str:
    """JSON-объект ответа — или почему его нет, словами."""
    try:
        body = json.loads(content)
    except ValueError:
        logger.info("%s: ответ модели не JSON", TOPIC, extra={"content": content[:120]})
        return "ответ модели не разобран"
    return body if isinstance(body, dict) else "ответ модели не объект"


def _unfit(body: dict[str, Any]) -> str | None:
    """Почему объект ответа не по форме — словами; `None` — по форме."""
    description = body.get("description")
    if not isinstance(description, str) or not description.strip():
        return "модель не дала описания"
    if len(description.strip()) > MAX_DESCRIPTION:
        return f"описание модели длиннее {MAX_DESCRIPTION} знаков"
    if not isinstance(body.get("problems"), list):
        return "модель не сказала, что в черновике собьёт судью"
    if not isinstance(body.get("unclear"), list):
        return "вопросы модели — не список"
    return None


def parse(content: str) -> Suggestion | str:
    """Ответ модели → предложение. Негодный ответ — почему, словами: такой не кэшируется."""
    body = _object(content)
    if isinstance(body, str):
        return body
    if (why := _unfit(body)) is not None:
        return why
    return Suggestion(
        str(body["description"]).strip(),
        _lines(body["unclear"], MAX_QUESTIONS),
        _lines(body["problems"], MAX_PROBLEMS),
    )


@dataclass(frozen=True, slots=True)
class Answer:
    """Итог одного вызова: предложение или почему его нет — и сколько токенов оплачено."""

    suggestion: Suggestion | None
    reason: str | None
    tokens: int
    model: str


class HelperClient(ModelClient):
    """Вызов модели помощника — модель судьи сегмента. Не бросает: сбой — `Answer` без предложения."""

    def _default_model(self) -> str:
        return llm_cfg.SALES_SEGMENT_MODEL

    async def suggest(self, draft: str, *, system: str | None = None) -> Answer:
        body = await post_chat(
            self._http,
            api_key=self._api_key,
            payload=build_payload(self._model, draft=draft, system=system),
            topic=TOPIC,
        )
        if isinstance(body, Refusal):
            logger.warning("%s: %s", TOPIC, body)
            return Answer(None, f"модель не ответила: {body}", 0, self._model)
        tokens = tokens_of(body)
        content = content_of(body, topic=TOPIC)
        if not content:
            # Оплачен: рассуждение съело потолок и до ответа не дошло.
            return Answer(None, "пустой ответ модели", tokens, self._model)
        found = parse(content)
        if isinstance(found, str):
            return Answer(None, found, tokens, self._model)
        return Answer(found, None, tokens, self._model)


@dataclass(frozen=True, slots=True)
class Help:
    """Ответ помощника экрану; `cached` — из кэша, без вызова модели."""

    description: str
    unclear: tuple[str, ...]
    problems: tuple[str, ...]
    cached: bool

    @property
    def loose(self) -> bool:
        return bool(self.problems)


def _same(stored: str, draft: str) -> bool:
    """Черновик строки кэша — тот же с точностью до пробелов: отпечаток короткий, и совпадение
    отпечатков у разных текстов не должно отдать чужой ответ."""
    return " ".join(stored.split()) == " ".join(draft.split())


async def _cached(
    session: AsyncSession, *, draft: str, digest: str, model: str
) -> SalesSegmentHelpModel | None:
    row = await session.scalar(
        select(SalesSegmentHelpModel).where(
            SalesSegmentHelpModel.prompt_version == PROMPT_VERSION,
            SalesSegmentHelpModel.model == model,
            SalesSegmentHelpModel.draft_digest == digest,
        )
    )
    return row if row is not None and _same(row.draft, draft) else None


async def help_draft(
    session: AsyncSession,
    draft: str,
    *,
    own: usage.OwnCap | None,
    client: HelperClient | None = None,
    now: datetime | None = None,
) -> Help:
    """Предложение помощника по черновику: из кэша — или вызовом модели под потолками.

    Бросает `EmptyDraftError` (черновика нет), `usage.LlmCapExceededError` (потолок модели
    выбран — до вызова) и `HelperFailedError` (модель не ответила или ответ негоден). Свой клиент
    закрывается здесь, данный снаружи — нет.
    """
    text = draft.strip()
    if not text:
        raise EmptyDraftError(EMPTY)
    model = client.model if client is not None else llm_cfg.SALES_SEGMENT_MODEL
    digest = segment_digest(text)
    row = await _cached(session, draft=text, digest=digest, model=model)
    if row is not None:
        return Help(row.description, tuple(row.unclear), tuple(row.problems), cached=True)
    await usage.ensure_llm_within_cap(session, now=now, own=own)
    caller = client or HelperClient()
    try:
        answer = await caller.suggest(text)
    finally:
        if client is None:
            await caller.aclose()
    if answer.tokens:
        usage.record(session, operation=OPERATION, units=answer.tokens)
    found = answer.suggestion
    if found is None:
        await session.commit()
        raise HelperFailedError(FAILED.format(reason=answer.reason))
    await session.execute(
        insert(SalesSegmentHelpModel)
        .values(
            prompt_version=PROMPT_VERSION,
            model=answer.model,
            draft_digest=digest,
            draft=text,
            description=found.description,
            unclear=list(found.unclear),
            problems=list(found.problems),
            tokens=answer.tokens,
        )
        .on_conflict_do_nothing(constraint="uq_sales_segment_helps_key")
    )
    await session.commit()
    return Help(found.description, found.unclear, found.problems, cached=False)
