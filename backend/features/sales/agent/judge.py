"""Судья черновика продаж (`AgentStage.guard`): правила кодом — первыми, затем модель.

Петля правки и закрытый отказ — у шва (`agent/guarding.py`): исключение и
таймаут судьи шов сам превращает в `escalate`. Здесь — что и в каком порядке
проверить:

1. **черновик без хода** — бриф хода не давал, это «всё же написать» поверх
   пропуска: такой черновик решает человек (`escalate`);
2. **язык письма собеседника не определён** — человеку: язык черновика не сверить;
3. **правила кодом** (`judge_rules.py`) — нарушения уходят писателю правкой
   (`block`), модель не зовётся;
4. **судья-модель** (`prompts/judge.md`) — строгий JSON по схеме у провайдера
   (`SCHEMA`): утверждения черновика со ссылками на номера записей базы, обещания
   вне базы, тон. Судья видит записи базы, письмо собеседника и отправителя —
   персону, ссылки, призыв и ход: призыв и ссылки из настроек — не утверждения.
   Самооценка модели только добавляет нарушения: утверждение без опоры или со
   ссылкой на запись, которой в брифе нет, — нарушение; тон, не подтверждённый
   `true`, — тоже. Одно исключение — кодом: утверждение внутри «голого» призыва
   (`judge_cta.py`: предложение со ссылкой призыва из настроек, без цены, сроков,
   гарантий и похвалы себе) опору имеет — настройки отправителя, и черновик оно не
   задерживает. Отказ модели — `escalate` со словами; неразобранный ответ —
   один повтор, и только второй неразобранный — `escalate`: судья, который не смог
   проверить, не пропускает, и причина видна человеку.

**Режим — `SALES_JUDGE_MODE`.** `enforce` (по умолчанию) отдаёт шву вердикт как
есть. `shadow` — для замера: `block` записан словами в попытки черновика и в
журнал, а шву уходит `allow`, и черновик не задерживается. `escalate` в обоих
режимах остаётся `escalate`: судья, который не смог проверить, не пропускает и
в наблюдении. Вердикт без режима — `verdict`: его меряет eval судьи.

**Своего потолка расхода у судьи нет**: в `GuardInput` нет сессии. Потолок
проверяет шов перед каждым письмом писателя, а расход судьи шов пишет из
`Verdict.tokens` операцией `sales_judge`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config.sales import SalesJudgeMode
from backend.features.agent.stages import GuardInput, Verdict, VerdictKind
from backend.features.sales.agent import calling, facts, judge_cta, judge_rules, reading
from backend.features.sales.agent.facts import Context
from backend.shared.llm import Refusal

logger = logging.getLogger(__name__)

TOPIC = "судья черновика продаж"
PROMPT = Path(__file__).with_name("prompts") / "judge.md"
#: Меняется при каждой правке промпта или правила, меняющего вердикт модели: калибровка
#: сравнивает версии. v5 — утверждение внутри голого призыва не в счёт (`judge_cta.py`).
PROMPT_VERSION = "sales-judge-v5"
#: Сколько раз спросить судью-модель, если её ответ не разобран: битый ответ — не вердикт,
#: но и не повод звать модель без конца.
ATTEMPTS = 2

#: Строгая форма ответа судьи-модели — схемой у провайдера: объект другой формы
#: модель не вернёт. Разбор (`parse`) всё равно проверяет форму сам.
SCHEMA: dict[str, Any] = {
    "name": "sales_judge_opinion",
    "schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "quote": {"type": "string"},
                        "kb": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["quote", "kb"],
                    "additionalProperties": False,
                },
            },
            "promises": {"type": "array", "items": {"type": "string"}},
            "tone": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}, "problem": {"type": "string"}},
                "required": ["ok", "problem"],
                "additionalProperties": False,
            },
        },
        "required": ["claims", "promises", "tone"],
        "additionalProperties": False,
    },
}
#: Начало причины в режиме наблюдения: вердикт записан, черновик не задержан.
SHADOW = "shadow"

#: Знаков цитаты в нарушении: дальше — пересказ черновика, а не указание на место.
MAX_QUOTE = 200


def _quiet(text: str) -> str:
    return text.replace("<<<", "‹‹‹").replace(">>>", "›››")


def sender_of(context: Context) -> dict[str, Any]:
    """Кто пишет, куда зовёт и что велит ход — то, что судья не должен считать утверждением."""
    cta = None if context.cta is None else {"channel": context.cta[0].value, "link": context.cta[1]}
    return {
        "persona": context.persona,
        "links": dict(context.links),
        "cta": cta,
        "move": {"name": context.move, "does": context.move_does or None},
    }


def user_message(check: GuardInput, context: Context) -> str:
    """Записи базы брифа, отправитель, черновик и письмо собеседника — в метках данных."""
    records = [{"id": number, "text": text} for number, text in context.kb.items()]
    return (
        f"records:\n{json.dumps(records, ensure_ascii=False)}\n"
        f"sender:\n{json.dumps(sender_of(context), ensure_ascii=False)}\n"
        f"<<<DRAFT\n{_quiet(check.draft)}\nDRAFT>>>\n"
        f"<<<LETTER\n{_quiet(check.incoming)}\nLETTER>>>"
    )


def _ids(raw: object) -> list[int]:
    if not isinstance(raw, list):
        return []
    found = []
    for item in raw:
        if isinstance(item, int) and not isinstance(item, bool):
            found.append(item)
        elif isinstance(item, str) and item.strip().isdigit():
            found.append(int(item))
    return found


def _claim(item: Mapping[str, Any], known: Mapping[int, str], cta: str | None) -> str | None:
    quote = " ".join(str(item.get("quote") or "").split())[:MAX_QUOTE]
    if cta is not None and judge_cta.inside(quote, cta):
        logger.info("%s: утверждение внутри призыва не в счёт", TOPIC, extra={"quote": quote})
        return None
    ids = _ids(item.get("kb"))
    if not ids:
        return f"утверждение без опоры на базу: «{quote}» — уберите его или возьмите из фактов"
    stray = [number for number in ids if number not in known]
    if stray:
        return f"утверждение ссылается на запись базы №{stray[0]}, которой нет в брифе: «{quote}»"
    return None


def _tone(tone: Mapping[str, Any]) -> list[str]:
    if tone.get("ok") is True:
        return []
    problem = " ".join(str(tone.get("problem") or "").split())
    return [f"тон: {problem or 'судья не подтвердил, что тон в порядке'}"]


def _list_of(raw: object, kind: type) -> list[Any] | None:
    if isinstance(raw, list) and all(isinstance(item, kind) for item in raw):
        return raw
    return None


def _misshaped(raw: object) -> str | None:
    """Что не так с формой ответа — словами для журнала. `None` — форма верна."""
    if not isinstance(raw, dict):
        return f"ждали объект, пришло {type(raw).__name__}"
    if _list_of(raw.get("claims"), dict) is None:
        return "claims — не список объектов"
    if _list_of(raw.get("promises"), str) is None:
        return "promises — не список строк"
    if not isinstance(raw.get("tone"), dict):
        return "tone — не объект"
    return None


def _shaped(raw: object) -> tuple[list[Any], list[Any], Mapping[str, Any]] | None:
    """Форма ответа: три ключа, каждый своего вида. Иначе — не разобрано."""
    problem = _misshaped(raw)
    if problem is not None or not isinstance(raw, dict):
        logger.warning("%s: ответ модели не той формы — %s", TOPIC, problem)
        return None
    return raw["claims"], raw["promises"], raw["tone"]


def parse(content: str, *, known: Mapping[int, str], cta: str | None = None) -> list[str] | None:
    """Ответ судьи-модели → нарушения словами. `None` — ответ не разобран.

    `cta` — голый призыв черновика (`judge_cta.bare`): утверждение внутри него не в счёт."""
    try:
        raw = json.loads(content)
    except ValueError:
        logger.warning("%s: ответ модели не JSON: %r", TOPIC, content[:200])
        return None
    shaped = _shaped(raw)
    if shaped is None:
        return None
    claims, promises, tone = shaped
    return [
        *(found for item in claims if (found := _claim(item, known, cta)) is not None),
        *(f"обещание вне базы: «{' '.join(text.split())}» — уберите его" for text in promises),
        *_tone(tone),
    ]


def _unchecked(check: GuardInput, context: Context) -> str | None:
    """Почему черновик человеку без проверки. `None` — судья проверяет."""
    if context.move is None:
        return "бриф не дал хода — черновик написан поверх пропуска брифа: решает человек"
    if reading.language_of(check.incoming) is None:
        return "язык письма собеседника не определён — язык черновика не сверить"
    return None


async def _model(check: GuardInput, context: Context, prompt: Path) -> Verdict:
    """Судья-модель: строгий JSON → нарушения. Битый ответ — один повтор; не смогла
    проверить — человеку."""
    tokens = 0
    cta = judge_cta.bare(check.draft, context)
    for attempt in range(1, ATTEMPTS + 1):
        answer = await calling.ask(
            prompt=prompt,
            user=user_message(check, context),
            model=llm_cfg.SALES_JUDGE_MODEL,
            topic=TOPIC,
            schema=SCHEMA,
        )
        if isinstance(answer, Refusal):
            why = f"судья-модель не проверила черновик: {answer}"
            return Verdict(VerdictKind.ESCALATE, (why,), tokens=tokens)
        tokens += answer.tokens
        found = parse(answer.content, known=context.kb, cta=cta)
        if found is not None:
            kind = VerdictKind.BLOCK if found else VerdictKind.ALLOW
            return Verdict(kind, tuple(found), tokens=tokens)
        logger.warning("%s: ответ не разобран, попытка %s из %s", TOPIC, attempt, ATTEMPTS)
    why = f"судья-модель {ATTEMPTS} раза ответила не по форме — черновик не проверен"
    return Verdict(VerdictKind.ESCALATE, (why,), tokens=tokens)


async def verdict(check: GuardInput, *, prompt: Path = PROMPT) -> Verdict:
    """Вердикт черновику продаж без режима: правила кодом, затем модель.

    `prompt` — промпт судьи-модели; другой подставляет только eval (порча промпта)."""
    context = facts.read(check.facts)
    why = _unchecked(check, context)
    if why is not None:
        return Verdict(VerdictKind.ESCALATE, (why,))
    broken = judge_rules.violations(check.draft, incoming=check.incoming, context=context)
    if broken:
        return Verdict(VerdictKind.BLOCK, tuple(broken))
    return await _model(check, context, prompt)


def shadowed(found: Verdict) -> Verdict:
    """Вердикт режима наблюдения: `block` записан словами, а черновик пропущен.

    `escalate` не трогается: не смог проверить — человеку и в наблюдении.
    """
    if found.kind is not VerdictKind.BLOCK:
        return found
    said = "; ".join(found.reasons)
    logger.info("%s: режим shadow — вернул бы черновик на правку: %s", TOPIC, said)
    reasons = tuple(f"{SHADOW} {found.kind.value}: {reason}" for reason in found.reasons)
    return Verdict(VerdictKind.ALLOW, reasons, tokens=found.tokens)


async def guard(check: GuardInput) -> Verdict:
    """Вердикт черновику продаж в режиме `SALES_JUDGE_MODE` — его получает шов."""
    found = await verdict(check)
    if sales_cfg.JUDGE_MODE is SalesJudgeMode.SHADOW:
        return shadowed(found)
    return found
