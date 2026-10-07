"""Ситуация письма собеседника — строгой формой от модели, вывод из неё — кодом.

**Нужен ли ответ, считает код** (`Situation.reply_needed`): `ack` и
`autoresponder` — не нужен, остальное — нужен. Поле «нужен ли ответ» модель
может написать и сама — оно не читается: молчание в ответ на вопрос лида стоит
лида, и решать его словом модели нельзя.

**Сбой разбора — `parse_failed`, а не догадка.** Не JSON, не объект, незнакомая
метка — ответ нужен, решает человек (план агента, «что не берём из CRM»: метку
при сбое разбора).

**Самооценка модели — не доказательство** (`replies/extract.py`): свои проверки
только понижают. Вопрос собеседника обязан найтись в письме дословно — иначе это
пересказ, а не вопрос; уверенности не назвали — ноль, а не «уверена»; «спасибо,
получил» с вопросом внутри — противоречие, и уверенность падает.

**Карточка диалога** — что мы обещали прислать и ещё не прислали: виды записей
базы знаний (`promised`). Ход в диалоге — номер письма собеседника — считает
код по переписке (`turn_of`), а не модель: числа считает код.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.features.agent import guarding
from backend.features.agent.stages import Conversation
from backend.features.agent.writer import DraftUnavailableError, Turn
from backend.features.core import usage
from backend.features.sales.agent import calling, parts
from backend.features.sales.models import KbKind
from backend.shared.llm import Refusal

logger = logging.getLogger(__name__)

TOPIC = "ситуация письма продаж"
OPERATION = parts.SITUATION_OPERATION
PROMPT = Path(__file__).with_name("prompts") / "situation.md"
#: Меняется при каждой правке промпта: калибровка сравнивает версии.
PROMPT_VERSION = "sales-situation-v1"

#: Знаков вопроса собеседника: длиннее — уже не вопрос, а пересказ письма.
MAX_QUESTION = 500
#: Потолок уверенности, когда своя проверка нашла сомнение.
DOUBT = 0.5

_OPEN, _CLOSE = "<<<CONVERSATION", "CONVERSATION>>>"


class Label(StrEnum):
    """Что собеседник хочет. `parse_failed` ставит код, а не модель."""

    WANTS_TO_TALK = "wants_to_talk"
    ASKS_PRICE = "asks_price"
    ASKS_INFO = "asks_info"
    OBJECTION = "objection"
    NOT_NOW = "not_now"
    REFUSAL = "refusal"
    WRONG_PERSON = "wrong_person"
    ACK = "ack"
    AUTORESPONDER = "autoresponder"
    PARSE_FAILED = "parse_failed"


#: Ответ не нужен: «спасибо, получил» и автоответ.
SILENT = frozenset({Label.ACK, Label.AUTORESPONDER})


@dataclass(frozen=True, slots=True)
class Situation:
    """Разбор письма: метка, вопрос дословно, уверенность, карточка диалога."""

    label: Label
    question: str | None = None
    confidence: float = 0.0
    #: Что мы обещали прислать и не прислали — виды записей базы.
    promised: tuple[KbKind, ...] = ()
    #: Теги базы, о которых письмо, — только из тех, что есть в базе.
    tags: tuple[str, ...] = ()
    #: Почему разбор не удался или что понизило уверенность — словами.
    notes: tuple[str, ...] = ()
    tokens: int = 0

    @property
    def reply_needed(self) -> bool:
        """Нужен ли ответ — по метке, кодом. Сбой разбора — нужен: решает человек."""
        return self.label not in SILENT

    def lowered(self, to: float, why: str) -> Situation:
        """Понизить уверенность и сказать почему. Только понизить."""
        if to >= self.confidence:
            return replace(self, notes=(*self.notes, why))
        return replace(self, confidence=to, notes=(*self.notes, why))

    def meta(self) -> dict[str, Any]:
        """Что ляжет в `meta` черновика — для калибровки."""
        return {
            "situation": self.label.value,
            "confidence": self.confidence,
            "question": self.question,
            "promised": [kind.value for kind in self.promised],
            "tags": list(self.tags),
            "notes": list(self.notes),
        }


def failed(why: str, tokens: int = 0) -> Situation:
    return Situation(label=Label.PARSE_FAILED, notes=(why,), tokens=tokens)


def turn_of(turns: Sequence[Turn]) -> int:
    """Ход в диалоге — какое это письмо собеседника по счёту в переписке агента."""
    return sum(1 for turn in turns if not turn.ours)


def last_letter(turns: Sequence[Turn]) -> str:
    """Письмо собеседника, на которое отвечаем."""
    return next((turn.text for turn in reversed(turns) if not turn.ours), "")


def _quiet(text: str) -> str:
    return text.replace("<<<", "‹‹‹").replace(">>>", "›››")


def user_message(turns: Sequence[Turn], *, tags: Sequence[str]) -> str:
    """Что видит модель: теги базы, виды обещаний и переписка в метках данных."""
    known = {"knowledge_base_tags": list(tags), "promise_kinds": [kind.value for kind in KbKind]}
    letters = [{"from": "us" if turn.ours else "them", "text": _quiet(turn.text)} for turn in turns]
    return (
        f"Known values:\n{json.dumps(known, ensure_ascii=False)}\n"
        f"{_OPEN}\n{json.dumps(letters, ensure_ascii=False, indent=1)}\n{_CLOSE}"
    )


def _squeezed(text: str) -> str:
    return " ".join(text.lower().split())


def _confidence(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return None
    try:
        value = float(raw)
    except ValueError:
        # Не число на месте уверенности: это «не поставила себе оценку», а не ноль
        # от модели — разбор отметит причину в `notes`.
        logger.info("%s: уверенность не число — %r", TOPIC, str(raw)[:40])
        return None
    return min(1.0, max(0.0, value))


def _promised(raw: object) -> tuple[KbKind, ...]:
    if not isinstance(raw, list):
        return ()
    values = {str(item).strip().lower() for item in raw}
    return tuple(kind for kind in KbKind if kind.value in values)


def _tags(raw: object, known: Collection[str]) -> tuple[str, ...]:
    """Теги письма — только из базы: выдуманный тег ничего не выберет."""
    if not isinstance(raw, list):
        return ()
    wanted = {" ".join(str(item).split()).lower() for item in raw}
    return tuple(tag for tag in sorted(known) if tag in wanted)


def _with_question(found: Situation, raw: object, letter: str, labels: dict[str, str]) -> Situation:
    """Вопрос собеседника — только дословный: его увидит человек и писатель."""
    if not isinstance(raw, str) or not raw.strip():
        return found
    question = raw.strip()[:MAX_QUESTION]
    for label, address in labels.items():
        question = question.replace(label, address)
    if _squeezed(question) not in _squeezed(letter):
        return found.lowered(DOUBT, "вопрос не найден в письме дословно — это пересказ, не вопрос")
    found = replace(found, question=question)
    if found.label in SILENT:
        return found.lowered(DOUBT, f"метка «{found.label.value}», а в письме вопрос")
    return found


def parse(
    content: str,
    *,
    letter: str,
    tags: Collection[str] = (),
    tokens: int = 0,
    labels: dict[str, str] | None = None,
) -> Situation:
    """Ответ модели → ситуация. Не та форма — `parse_failed` с причиной."""
    try:
        raw = json.loads(content)
    except ValueError:
        logger.warning("%s: ответ модели не JSON: %r", TOPIC, content[:200])
        return failed("ответ модели не JSON", tokens)
    if not isinstance(raw, dict):
        logger.warning("%s: модель вернула %s вместо формы", TOPIC, type(raw).__name__)
        return failed(f"ответ модели — {type(raw).__name__}, а не форма", tokens)
    code = str(raw.get("situation") or "").strip().lower()
    if code not in {label.value for label in Label} or code == Label.PARSE_FAILED:
        logger.warning("%s: незнакомая метка %r", TOPIC, code[:40])
        return failed(f"метка ситуации «{code[:40]}» незнакома", tokens)
    confidence = _confidence(raw.get("confidence"))
    found = Situation(
        label=Label(code),
        confidence=confidence or 0.0,
        promised=_promised(raw.get("promised")),
        tags=_tags(raw.get("tags"), tags),
        tokens=tokens,
    )
    if confidence is None:
        found = found.lowered(0.0, "модель не поставила себе оценку уверенности")
    return _with_question(found, raw.get("question"), letter, labels or {})


async def classify(
    session: AsyncSession, conversation: Conversation, *, tags: Sequence[str]
) -> Situation:
    """Ситуация последнего письма собеседника. Расход — операцией ситуации.

    Отказ модели — `DraftUnavailableError`, как у писателя: задача черновика
    повторит сбой сети и остановится с причиной на ключе; потолки расхода —
    общий и свой потолок черновиков агента (`guarding.drafts_cap`, ситуация — его
    операция брифа) — проверяются до вызова (`usage.ensure_llm_within_cap`).
    """
    await usage.ensure_llm_within_cap(session, own=guarding.drafts_cap())
    answer = await calling.ask(
        prompt=PROMPT,
        user=user_message(conversation.turns, tags=tags),
        model=llm_cfg.SALES_SITUATION_MODEL,
        topic=TOPIC,
    )
    if isinstance(answer, Refusal):
        raise DraftUnavailableError(
            f"ситуация письма не разобрана: {answer}", permanent=answer.permanent
        )
    if answer.tokens:
        usage.record(session, operation=OPERATION, units=answer.tokens)
    return parse(
        answer.content,
        letter=last_letter(conversation.turns),
        tags=tags,
        tokens=answer.tokens,
        labels=answer.labels,
    )
