"""Бриф агента продаж — то, что шов получает до письма (`AgentStage.brief`).

Порядок — от дешёвого к дорогому, и модель здесь одна — ситуации письма:

1. **нет имени отправителя продаж** в настройках — человеку: подписать черновик
   нечем. Пустой подписи шов не примет, а общая (`sign_as=None`) подписала бы
   письмо продаж именем отправителя доноров;
2. **разметка ролей модели** в письме собеседника (общая очистка шва заменила
   её меткой) или **сигнатуры инъекции** (`safety.py`: T1–T6) — человеку: похоже
   на попытку управлять агентом. Эту же проверку проходит канарейка инъекций;
3. **язык письма** по алфавиту не определён — человеку: судья не сверит язык
   черновика с письмом, а писать вслепую нельзя;
4. **ситуация** (модель) → «нужен ли ответ» — кодом: «спасибо» и автоответ —
   `no_reply`, писатель не зовётся; сбой разбора — `human`, черновик без текста;
5. **ход** по таблице; ход зовёт, а позвать некуда — `human` со словами, что
   заполнить;
6. **факты под ход** строками, `meta` для калибровки и подпись персоной продаж —
   у всех брифов, где имя задано: «всё же написать» тоже подпишет ею.

Пропуск — без фактов: «всё же написать» поверх пропуска пишет без хода, и судья
продаж отдаёт такой черновик человеку (`judge.py`).

Потолок расхода перед вызовом ситуации проверяет сама ситуация, и её расход
пишется операцией `sales_situation`: шов считает только писателя и судью.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.features.agent.cleaning import ROLE_PLACEHOLDER
from backend.features.agent.stages import Brief, Conversation, Skip, SkipKind
from backend.features.agent.writer import Turn
from backend.features.sales import kb
from backend.features.sales import sender as sales_sender
from backend.features.sales.agent import facts, judge, moves, reading, safety, situation
from backend.features.sales.agent.situation import Label, Situation

#: Почему ответ не нужен — словами для человека.
_SILENT_WHY = {
    Label.ACK: "собеседник благодарит или подтверждает, вопроса нет",
    Label.AUTORESPONDER: "это автоответ",
}
_WHERE = "заполните «Продажи» → «Отправитель»"
_NO_NAME = f"нет имени отправителя продаж в настройках — подписать черновик нечем; {_WHERE}"


def versions() -> dict[str, str]:
    """Версии того, по чему бриф собран, — калибровка сравнивает черновики по ним."""
    return {
        "situation": situation.PROMPT_VERSION,
        "judge": judge.PROMPT_VERSION,
        "moves": moves.table().version,
    }


def held(letter: str) -> str | None:
    """Почему письмо сразу человеку — до модели. `None` — агент берётся.

    Письмо — после общей очистки шва (`agent/cleaning.clean`): так его видит бриф."""
    if ROLE_PLACEHOLDER in letter:
        return "в письме разметка ролей модели — похоже на попытку управлять агентом"
    threat = safety.threat(letter)
    if threat is not None:
        return threat
    if reading.language_of(letter) is None:
        return "язык письма не определён: не кириллица и не латиница — судья его не сверит"
    return None


def _skipped(found: Situation) -> Skip | None:
    """Пропуск по ситуации: ответ не нужен — или агент не понял письмо."""
    if not found.reply_needed:
        return Skip(SkipKind.NO_REPLY, f"ответ не нужен: {_SILENT_WHY[found.label]}")
    if found.label is Label.PARSE_FAILED:
        return Skip(SkipKind.HUMAN, "агент не понял письмо: " + "; ".join(found.notes))
    return None


def _deferred(turns: Sequence[Turn]) -> list[str]:
    """Наши прежние отсрочки — второй раз писатель откладывать не должен."""
    return [phrase for turn in turns if turn.ours for phrase in reading.deferrals(turn.text)]


def _tags(entries: Sequence[kb.Fact]) -> tuple[str, ...]:
    return tuple(sorted({tag for entry in entries for tag in entry.tags}))


def _answered(
    entries: Sequence[kb.Fact],
    found: Situation,
    *,
    language: str,
    sender: sales_sender.Sender,
    conversation: Conversation,
    meta: dict[str, Any],
    sign_as: str,
) -> Brief:
    """Ответ нужен: ход по таблице и факты под него — или человеку, если позвать некуда."""
    move = moves.table().move(found.label)
    selected = facts.select(
        entries,
        move=move,
        situation=found,
        language=language,
        sender=sender,
        deferred=_deferred(conversation.turns),
    )
    meta |= {
        "move": move.name,
        "lead": move.lead,
        "cta": selected.cta.value if selected.cta else None,
        "kb_ids": list(selected.kb_ids),
    }
    if selected.no_cta_link:
        wanted = " или ".join(option.value for option in move.cta)
        why = f"ход «{move.name}» зовёт ({wanted}), а ссылок нет — {_WHERE}"
        return Brief(skip=Skip(SkipKind.HUMAN, why), meta=meta, sign_as=sign_as)
    return Brief(facts=selected.lines, meta=meta, sign_as=sign_as)


async def brief(session: AsyncSession, conversation: Conversation) -> Brief:
    """Что шов отдаст писателю и судье — или почему писать не надо."""
    sender = await sales_sender.read(session)
    sign_as = (sender.values.get("sender_name") or "").strip()
    letter = situation.last_letter(conversation.turns)
    language = reading.language_of(letter)
    meta: dict[str, Any] = {
        "language": language,
        "turn": situation.turn_of(conversation.turns),
        "versions": versions(),
        "judge_mode": sales_cfg.JUDGE_MODE.value,
    }
    if not sign_as:
        return Brief(skip=Skip(SkipKind.HUMAN, _NO_NAME), meta=meta)
    why = held(letter)
    if why is not None or language is None:
        return Brief(skip=Skip(SkipKind.HUMAN, why or ""), meta=meta, sign_as=sign_as)
    entries = await kb.facts(session)
    meta["kb_version"] = kb.version_of(entries)
    found = await situation.classify(session, conversation, tags=_tags(entries))
    meta |= found.meta()
    skip = _skipped(found)
    if skip is not None:
        return Brief(skip=skip, meta=meta, sign_as=sign_as)
    return _answered(
        entries,
        found,
        language=language,
        sender=sender,
        conversation=conversation,
        meta=meta,
        sign_as=sign_as,
    )
