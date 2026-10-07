"""Прогон версии агента продаж на входящих с известным исходом — шаг «прогон» докрутки.

Докрутка идёт по кругу: черновики и решения людей ложатся в журнал, по правкам ставится
диагноз, новая версия (промпты, таблица ходов, правила судьи) гонится на тех же входящих,
где решение человека уже известно, и сравнивается с прежней; хуже прежней — не выкатывается.

**Случай** (`Case`) — переписка до входящего письма и решение человека по нему. Источников
два, форма одна: набор вне репозитория по манифесту (`replay_sets.py`) и живые продажи —
черновики этапа продаж с решением человека (`replay_drafts.py`). Сравнение с решениями
людей и ворота «не хуже прежней» — `replay_gate.py`, команда — `scripts/sales_replay.py`.

**Тот же путь, что у агента** (строка продаж `agent/stages.SALES_STAGE`; в реестре этапов
она только по тумблеру, прогону реестр не нужен): очистка переписки шва
(`drafting.conversation`), бриф продаж (ситуация → ход → факты), писатель и судья с петлёй
правки (`guarding.compose`). Черновик не записывается и никуда не уходит; пишется только
расход модели — он настоящий.

**Судья прогона — без режима и с промптом версии** (`partial(judge.verdict, prompt=…)`):
в наблюдении (`SALES_JUDGE_MODE=shadow`) судья этапа пропускает черновик и прячет
нарушения в слова — прогон их бы не увидел, и ворота по нарушениям стали бы всегда
открытыми. Прогон меряет вердикт, а не режим. Промпт — явно: умолчание `judge.verdict`
вычислено при импорте, и подмена постоянной модуля до него не дошла бы.

**Версия в файлах** (`Prompts`) — промпты ситуации, черновика и судьи и таблица ходов.
Прежнюю версию можно прогнать рядом с новой из каталога файлов (`using`): на время прогона
агент берёт их вместо файлов пакета. Правила судьи кодом — те, что в дереве.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.config import outreach as outreach_cfg
from backend.features.agent import drafting, guarding
from backend.features.agent.settings import AgentSettings, AgentSettingsRepository, settings_of
from backend.features.agent.stages import (
    SALES_STAGE,
    AgentStage,
    Brief,
    Conversation,
    SkipKind,
    VerdictKind,
)
from backend.features.agent.writer import DraftUnavailableError, Request, Turn
from backend.features.core.domain import Stage
from backend.features.core.usage import LlmCapExceededError
from backend.features.sales import kb
from backend.features.sales.agent import judge, moves, parts, situation

logger = logging.getLogger(__name__)

#: Файлы версии в каталоге — вместо файлов пакета.
FILES = {
    "situation": "situation.md",
    "reply": "reply.md",
    "judge": "judge.md",
    "moves": "moves.toml",
}
#: Метки ситуации, какими их знает агент.
LABELS = frozenset(label.value for label in situation.Label)

#: Время писем случая: порядок важен, часы — нет.
_START = datetime(2000, 1, 1, tzinfo=UTC)


class Decision(StrEnum):
    """Что человек сделал с входящим письмом."""

    SENT_AS_IS = "sent_as_is"
    SENT_EDITED = "sent_edited"
    #: Ответил сам: черновика с текстом не было — агент промолчал или отдал без текста.
    OWN = "own"
    REJECTED = "rejected"
    SILENT = "silent"


#: Решения, где человек ответил. У отклонённого — как было: мог ответить сам, мог нет.
REPLIED = frozenset({Decision.SENT_AS_IS, Decision.SENT_EDITED, Decision.OWN})


class Outcome(StrEnum):
    """Что сделала бы версия — по брифу, писателю, правилам и судье."""

    #: Судья пропустил первый черновик — «прошло бы как есть».
    AS_IS = "as_is"
    #: Пропустил после правок — «правка».
    EDITED = "edited"
    #: Не пропустил или агент не взялся — человеку: «отклонено».
    REJECTED = "rejected"
    #: Ответ не нужен.
    SILENT = "silent"


class SetNotFoundError(LookupError):
    """Набора нет по пути — сообщение называет путь."""


class SetError(ValueError):
    """Манифест, набор, версия или файл прогона не годятся — словами, с местом."""


@dataclass(frozen=True, slots=True)
class Human:
    """Решение человека по входящему — с ним сравнивается версия."""

    decision: Decision
    replied: bool
    #: Метка ситуации — эталон сравнения меток; `None` — эталона нет.
    situation: str | None = None
    #: Кто поставил метку: `human` — набор, `model` — версия, писавшая живой черновик.
    labelled_by: str = "human"
    #: Почему отклонил — словами человека.
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Case:
    """Входящее с известным исходом: переписка до него и решение человека."""

    id: str
    #: По порядку, последним — письмо собеседника; текст как есть: очистка — при прогоне.
    turns: tuple[Turn, ...]
    human: Human

    def digest(self) -> str:
        """Отпечаток случая: прогоны сравниваются только на одинаковых случаях."""
        human = self.human
        canon = [
            [[turn.ours, turn.text] for turn in self.turns],
            [human.decision.value, human.replied, human.situation],
        ]
        raw = json.dumps(canon, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Result:
    """Что версия сделала с одним случаем — рядом с решением человека."""

    case: str
    digest: str
    human: Human
    #: Метка ситуации версии; `None` — бриф отдал письмо человеку до модели.
    label: str | None
    outcome: Outcome
    #: Нарушения первого черновика по правилам и судье-модели — до правок.
    violations: tuple[str, ...] = ()
    #: Почему человеку или почему молчит — словами брифа, писателя или судьи.
    why: str | None = None
    #: Черновик после петли правки.
    draft: str = ""

    @property
    def false_silence(self) -> bool:
        """Версия молчит, а человек ответил."""
        return self.outcome is Outcome.SILENT and self.human.replied


@dataclass(frozen=True, slots=True)
class Run:
    """Прогон версии: исходы по случаям, версия и полнота."""

    source: str
    version: Mapping[str, Any]
    results: tuple[Result, ...]
    #: Случаи, которые не прогнались: номер и почему.
    errors: tuple[tuple[str, str], ...] = ()
    #: Почему прогон остановлен до конца; `None` — дошёл.
    stopped: str | None = None

    @property
    def complete(self) -> bool:
        return not self.errors and self.stopped is None


# --- версия в файлах -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Prompts:
    """Версия агента в файлах: промпты ситуации, черновика и судьи, таблица ходов."""

    situation: Path
    reply: Path
    judge: Path
    moves: Path

    def files(self) -> dict[str, Path]:
        return {
            "situation": self.situation,
            "reply": self.reply,
            "judge": self.judge,
            "moves": self.moves,
        }

    def fingerprint(self) -> dict[str, str]:
        """Отпечатки файлов: по ним видно, та же версия или другая."""
        return {
            name: hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            for name, path in self.files().items()
        }


#: Версия пакета — файлы рядом с кодом агента.
PACKAGED = Prompts(situation.PROMPT, parts.PROMPT, judge.PROMPT, moves.TABLE)


def from_folder(folder: Path) -> Prompts:
    """Версия из каталога: его файлы вместо файлов пакета, чего в нём нет — из пакета."""
    if not folder.is_dir():
        raise SetError(f"каталога версии нет: {folder}")
    packaged = PACKAGED.files()
    chosen = {
        name: folder / file if (folder / file).is_file() else packaged[name]
        for name, file in FILES.items()
    }
    if chosen == packaged:
        raise SetError(f"в каталоге версии {folder} нет ни одного из {', '.join(FILES.values())}")
    return Prompts(**chosen)


@contextmanager
def using(prompts: Prompts) -> Iterator[None]:
    """Агент берёт файлы версии вместо файлов пакета — до выхода из блока.

    Промпт ситуации и таблица ходов — постоянные модулей агента: на время прогона они
    подменяются и возвращаются в любом случае. Промпт черновика уходит писателю в запросе
    (`Request.prompt`), промпт судьи — судье прогона явно (`judged`). Таблица проверяется
    сразу: негодная — отказ словами до первого вызова модели.
    """
    saved = situation.PROMPT, moves.TABLE
    situation.PROMPT, moves.TABLE = prompts.situation, prompts.moves
    moves.table.cache_clear()
    try:
        try:
            moves.table()
        except moves.MovesTableError as exc:
            raise SetError(f"таблица ходов версии ({prompts.moves}) не годится: {exc}") from exc
        yield
    finally:
        situation.PROMPT, moves.TABLE = saved
        moves.table.cache_clear()


def judged(prompts: Prompts) -> AgentStage:
    """Строка продаж для прогона: судья — без режима и с промптом версии.

    Режим наблюдения ослепил бы прогон (нарушения — только словами при `allow`), а
    умолчание промпта у `judge.verdict` вычислено при импорте: промпт версии — явно.
    """
    return replace(SALES_STAGE, guard=partial(judge.verdict, prompt=prompts.judge))


# --- прогон --------------------------------------------------------------------------------


async def run(
    session: AsyncSession,
    writer: guarding.Writer,
    cases: Sequence[Case],
    *,
    source: str,
    prompts: Prompts = PACKAGED,
) -> Run:
    """Прогнать версию на случаях — по одному, тем же путём, что агент.

    Отказ модели на случае — ошибка случая; насовсем (ключ, права) или потолок расхода —
    прогон остановлен: дальше каждый случай упал бы так же. Неполный прогон ворота не
    проходит (`replay_gate.compare`).
    """
    stage = judged(prompts)
    current = await AgentSettingsRepository(session).current(Stage.SALES)
    settings = parts.DEFAULTS if current is None else settings_of(current)
    results: list[Result] = []
    errors: list[tuple[str, str]] = []
    stopped: str | None = None
    with using(prompts):
        version = await _version(session, prompts, None if current is None else current.version)
        for case in cases:
            try:
                results.append(
                    await replay_case(
                        session, writer, case, stage=stage, settings=settings, prompt=prompts.reply
                    )
                )
            except DraftUnavailableError as exc:
                logger.warning("прогон агента продаж: случай %s не прогнан — %s", case.id, exc)
                errors.append((case.id, str(exc)))
                if exc.permanent:
                    stopped = f"модель отказала насовсем: {exc}"
                    break
            except LlmCapExceededError as exc:
                logger.warning("прогон агента продаж остановлен потолком расхода — %s", exc)
                stopped = f"потолок расхода на модель: {exc}"
                break
    return Run(source, version, tuple(results), tuple(errors), stopped)


async def replay_case(
    session: AsyncSession,
    writer: guarding.Writer,
    case: Case,
    *,
    stage: AgentStage,
    settings: AgentSettings,
    prompt: Path,
) -> Result:
    """Один случай тем путём, каким агент пишет черновик, — без записи черновика."""
    turns, cleaned = drafting.conversation(*_timed(case.turns))
    talk = Conversation(
        stage=Stage.SALES, thread_id=0, reply_id=0, turns=turns, settings=settings, cleaned=cleaned
    )
    brief = await stage.brief(session, talk)
    label = brief.meta.get("situation")
    label = label if isinstance(label, str) else None
    if brief.skip is not None:
        silent = brief.skip.kind is SkipKind.NO_REPLY
        outcome = Outcome.SILENT if silent else Outcome.REJECTED
        return Result(case.id, case.digest(), case.human, label, outcome, why=brief.skip.reason)
    composed = await guarding.compose(
        session,
        stage,
        writer,
        _request(talk, brief, stage, prompt),
        incoming=situation.last_letter(turns),
    )
    first = composed.attempts[0] if composed.attempts else {}
    blocked = first.get("verdict") == VerdictKind.BLOCK.value
    return Result(
        case.id,
        case.digest(),
        case.human,
        label,
        _outcome(composed),
        violations=tuple(first.get("reasons", ())) if blocked else (),
        why=composed.reason,
        draft=composed.body,
    )


def _timed(
    turns: Sequence[Turn],
) -> tuple[list[tuple[datetime, str]], list[tuple[datetime, str]]]:
    """Письма случая — со временем по порядку: так их читает очистка шва."""
    at = [_START + timedelta(minutes=number) for number in range(len(turns))]
    letters = [(at[n], turn.text) for n, turn in enumerate(turns) if turn.ours]
    replies = [(at[n], turn.text) for n, turn in enumerate(turns) if not turn.ours]
    return letters, replies


def _request(talk: Conversation, brief: Brief, stage: AgentStage, prompt: Path) -> Request:
    """Запрос писателю — как у шва (`drafting.draft_answer`): подпись и факты брифа,
    промпт версии, модель этапа. Разобранной цены у входящего продаж нет."""
    return Request(
        stage=talk.stage,
        settings=talk.settings,
        turns=talk.turns,
        sign_as=outreach_cfg.SENDER_NAME.strip() if brief.sign_as is None else brief.sign_as,
        parsed={},
        facts=brief.facts,
        prompt=prompt,
        model=stage.model,
    )


def _outcome(composed: guarding.Composed) -> Outcome:
    if composed.held:
        return Outcome.REJECTED
    return Outcome.EDITED if len(composed.attempts) > 1 else Outcome.AS_IS


async def _version(
    session: AsyncSession, prompts: Prompts, settings_version: int | None
) -> dict[str, Any]:
    """Что за версия гонится: отпечатки файлов, их объявленные версии, база, настройки, модели."""
    return {
        "prompts": prompts.fingerprint(),
        "declared": {
            "situation": situation.PROMPT_VERSION,
            "reply": parts.PROMPT_VERSION,
            "judge": judge.PROMPT_VERSION,
            "moves": moves.table().version,
        },
        "kb": await kb.version(session),
        "settings": settings_version,
        "models": {
            "situation": llm_cfg.SALES_SITUATION_MODEL,
            "draft": SALES_STAGE.model,
            "judge": llm_cfg.SALES_JUDGE_MODEL,
        },
    }
