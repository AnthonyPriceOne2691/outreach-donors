"""Калибровка агента продаж: как люди обходятся с его черновиками — числами по журналу.

Шаг «диагноз» докрутки агента (журнал → диагноз → правка → прогон → выкатка): черновики
этапа продаж (`agent_drafts`) и что с ними сделал человек. Экран «Агент: качество»
(`calibration_board.py`) и ворота автоответа по ситуации читают одно и то же — отсюда.

**Что сделал агент** (`Agent`):

- `ready` — написал черновик, и судья его пропустил: черновик ждёт одной кнопки;
- `held` — отдал человеку: бриф не взялся, писатель засомневался или судья не пропустил
  (`escalated`, с текстом или без). «Как есть» у такого закрыто (`agent/drafts.py`);
- `silent` — «ответ не нужен» (`skipped`); и тогда, когда человек велел всё же написать.

**Что сделал человек** — правилом прогона (`replay_drafts.decided`): как есть, с правкой,
отклонил, ответил сам, промолчал. Молчание человека — исход только через
`replay_drafts.SETTLE` после письма собеседника: раньше он ещё может ответить. Автоотправка
(`decided_by = autopilot`) — не решение оператора и в доли не входит.

**Доли — от решений оператора, а не от всех черновиков.** «Без правки», «с правкой» и
«отклонено» — от решений по готовым черновикам; ждущие, отданные человеку и молчание в
основу не входят. Верное молчание — от молчаний агента с исходом; ложное — агент молчал,
а человек ответил.

**Когорта — версия агента**: промпты черновика, ситуации и судьи, таблица ходов и версия
базы знаний — то, по чему написан черновик (`brief.versions`, `meta.kb_version`). Когорты
не смешиваются: числа каждой — только по её черновикам. Настройки агента в когорту не входят:
каждое сохранение формы — новая версия настроек, и когорта дробилась бы без правки.

**Ворота автоответа по ситуации** (`gate`): без правки не ниже 80% и верное молчание не
ниже 90% — каждое число по своей основе, если она есть, и основа не меньше N решений
(`SALES_AUTOREPLY_MIN_N`). Меньше N — «данных мало»: без долей и без вывода. Флаг
автоответа ситуации здесь не включается — его включает человек.

**Чего журнал не знает.** «Агент отдал человеку, а тот отправил бы как есть» не видно: у
отданного человеку «как есть» закрыто, и правкой тем же текстом — тоже. Мерить это можно
отметкой оператора «отдан зря» — её в журнале нет — или прогоном версии на этих случаях
(`scripts/sales_replay.py`).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.features.agent.drafts import AUTOPILOT
from backend.features.agent.stages import SkipKind
from backend.features.core.domain import DraftStatus
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.sales import kb
from backend.features.sales.agent import brief, parts, replay_drafts
from backend.features.sales.agent.replay import LABELS, Decision

#: Пороги ворот автоответа по ТЗ — процентами, не ниже.
AS_IS_MIN = 80
SILENCE_MIN = 90
#: Знаков номера когорты: десятки версий за жизнь агента, совпадение исключено.
ID_DIGITS = 10
#: Вид причины отклонения, которого у черновика нет: причина своими словами.
NO_KIND = "без вида"
#: Знаков после запятой у доли рядом с порогом — пока она не встанет по свою сторону.
_MAX_DIGITS = 6


class Agent(StrEnum):
    """Что агент сделал с письмом собеседника."""

    READY = "ready"
    HELD = "held"
    SILENT = "silent"


class Took(StrEnum):
    """Что с черновиком сделал человек (или автоотправка)."""

    AS_IS = "as_is"
    EDITED = "edited"
    REJECTED = "rejected"
    OWN = "own"
    SILENT = "silent"
    PENDING = "pending"
    AUTO = "auto"


class Bucket(StrEnum):
    """Куда черновик ложится в счёте — ровно в одно место."""

    #: Решения оператора по готовому черновику.
    AS_IS = "as_is"
    EDITED = "edited"
    REJECTED = "rejected"
    #: Агент молчал: человек тоже промолчал — или ответил.
    TRUE_SILENCE = "true_silence"
    FALSE_SILENCE = "false_silence"
    #: Агент отдал человеку: тот отправил (с правкой или своё), отклонил — или ещё не решил.
    HELD_SENT = "held_sent"
    HELD_REJECTED = "held_rejected"
    HELD_WAITING = "held_waiting"
    #: Готовый черновик ждёт решения или молчание агента ещё без исхода.
    WAITING = "waiting"
    #: Ушёл автоотправкой — не решение оператора.
    AUTO = "auto"


_TOOK = {
    Decision.SENT_AS_IS: Took.AS_IS,
    Decision.SENT_EDITED: Took.EDITED,
    Decision.OWN: Took.OWN,
    Decision.REJECTED: Took.REJECTED,
    Decision.SILENT: Took.SILENT,
}
#: Человек ответил письмом.
REPLIED = frozenset({Took.AS_IS, Took.EDITED, Took.OWN})
_READY = {Took.AS_IS: Bucket.AS_IS, Took.EDITED: Bucket.EDITED, Took.REJECTED: Bucket.REJECTED}
#: Молчание агента: исхода нет, человек промолчал, человек ответил.
_SILENCE: dict[bool | None, Bucket] = {
    None: Bucket.WAITING,
    True: Bucket.TRUE_SILENCE,
    False: Bucket.FALSE_SILENCE,
}


@dataclass(frozen=True, slots=True)
class Cohort:
    """Версия агента, по которой написан черновик: промпты, таблица ходов, база знаний."""

    reply: str | None = None
    situation: str | None = None
    judge: str | None = None
    moves: str | None = None
    kb: str | None = None

    @property
    def id(self) -> str:
        """Номер когорты для адреса экрана: отпечаток её версий."""
        raw = json.dumps([self.reply, self.situation, self.judge, self.moves, self.kb])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:ID_DIGITS]


@dataclass(frozen=True, slots=True)
class Row:
    """Черновик продаж: что сделал агент, что — человек, и по какой версии."""

    draft_id: int
    thread_id: int | None
    #: Ситуация письма по разбору агента; `None` — письмо отдано человеку до разбора.
    situation: str | None
    cohort: Cohort
    agent: Agent
    took: Took
    #: Человек ответил на это письмо — кнопкой черновика или из переписки.
    replied: bool
    #: Письму собеседника больше `SETTLE`: молчание человека — уже исход.
    settled: bool
    written_at: datetime
    decided_at: datetime | None = None
    turn: int | None = None
    #: Почему агент отдал человеку или промолчал — словами брифа, писателя или судьи.
    agent_reason: str | None = None
    reject_reason: str | None = None
    reject_kind: str | None = None
    body: str = ""
    final_body: str | None = None

    @property
    def silence(self) -> bool | None:
        """Верно ли агент молчал: `False` — человек ответил, `True` — промолчал;
        `None` — агент не молчал или исхода ещё нет."""
        if self.agent is not Agent.SILENT:
            return None
        if self.replied:
            return False
        if self.settled and self.took in (Took.SILENT, Took.REJECTED):
            return True
        return None

    @property
    def bucket(self) -> Bucket:
        if self.took is Took.AUTO:
            return Bucket.AUTO
        if self.agent is Agent.SILENT:
            return _SILENCE[self.silence]
        if self.agent is Agent.HELD:
            if self.took in REPLIED:
                return Bucket.HELD_SENT
            return Bucket.HELD_REJECTED if self.took is Took.REJECTED else Bucket.HELD_WAITING
        return _READY.get(self.took, Bucket.WAITING)


@dataclass(slots=True)
class Tally:
    """Счёт черновиков по местам и причины отклонения готовых."""

    buckets: Counter[Bucket] = field(default_factory=Counter)
    #: Вид причины отклонения готового черновика: пункт списка, «другое» — или без вида.
    reasons: Counter[str] = field(default_factory=Counter)

    def add(self, row: Row) -> None:
        place = row.bucket
        self.buckets[place] += 1
        if place is Bucket.REJECTED:
            self.reasons[row.reject_kind or NO_KIND] += 1

    def __getitem__(self, place: Bucket) -> int:
        return self.buckets[place]

    @property
    def decisions(self) -> int:
        """Решений оператора по готовым черновикам — основа долей «как есть», «правка», «отклонено»."""
        return self[Bucket.AS_IS] + self[Bucket.EDITED] + self[Bucket.REJECTED]

    @property
    def silences(self) -> int:
        """Молчаний агента с исходом — основа верного и ложного молчания."""
        return self[Bucket.TRUE_SILENCE] + self[Bucket.FALSE_SILENCE]

    @property
    def drafts(self) -> int:
        return sum(self.buckets.values())


def tally(rows: Iterable[Row]) -> Tally:
    found = Tally()
    for row in rows:
        found.add(row)
    return found


# --- ворота автоответа ---------------------------------------------------------------------


class GateState(StrEnum):
    """Итог ворот ситуации: данных мало, проходит, не проходит."""

    FEW = "few"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Limits:
    """Пороги ворот: N решений (`SALES_AUTOREPLY_MIN_N`) и доли процентами, не ниже."""

    min_n: int
    as_is: int = AS_IS_MIN
    silence: int = SILENCE_MIN


def limits() -> Limits:
    return Limits(min_n=sales_cfg.AUTOREPLY_MIN_N)


@dataclass(frozen=True, slots=True)
class Gate:
    """Ворота ситуации: итог и почему — словами."""

    state: GateState
    said: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Measure:
    """Число ворот: доля `part / base` против порога `floor` процентов."""

    name: str
    part: int
    base: int
    floor: int
    #: Что за основа — словами, для «данных мало».
    of: str

    @property
    def passes(self) -> bool:
        """Не ниже порога — точной дробью: 0,8 · 30 в плавающей точке — не 24."""
        return self.part * 100 >= self.floor * self.base

    def few(self, least: int) -> str:
        return f"данных мало: {self.of} {self.base} из {least}"

    def said(self) -> str:
        side = "не ниже" if self.passes else "ниже"
        share = percent(self.part, self.base, self.floor)
        return f"{self.name} {share} ({self.part} из {self.base}) — {side} {self.floor}%"


def _measures(found: Tally, edge: Limits) -> list[_Measure]:
    return [
        _Measure(
            "без правки",
            found[Bucket.AS_IS],
            found.decisions,
            edge.as_is,
            "решений по готовым черновикам",
        ),
        _Measure(
            "верное молчание",
            found[Bucket.TRUE_SILENCE],
            found.silences,
            edge.silence,
            "молчаний агента с исходом",
        ),
    ]


def gate(found: Tally, edge: Limits) -> Gate:
    """Ворота автоответа: каждое число, у которого есть основа, — по своей основе.

    Основа меньше N — «данных мало» без доли; ниже порога на полной основе — «не
    проходит», даже если у другого числа данных мало: вывод о нём уже есть. Ни одной
    основы — данных мало тоже.
    """
    measures = [measure for measure in _measures(found, edge) if measure.base]
    if not measures:
        return Gate(GateState.FEW, (f"данных мало: решений нет, нужно не меньше {edge.min_n}",))
    few = [m.few(edge.min_n) for m in measures if m.base < edge.min_n]
    failed = [m.said() for m in measures if m.base >= edge.min_n and not m.passes]
    if failed:
        return Gate(GateState.FAILED, (*failed, *few))
    if few:
        return Gate(GateState.FEW, tuple(few))
    return Gate(GateState.PASSED, tuple(m.said() for m in measures))


def percent(part: int, base: int, floor: int) -> str:
    """Доля процентами по-русски: десятая — когда она есть, а у порога точность растёт,
    пока число не встанет по ту же сторону порога, что и само: 3999 из 5000 при пороге
    80% — «79,98%», а не «80%»."""
    exact = Fraction(part * 100, base)
    above = exact >= floor
    shown = Decimal(0)
    for digits in range(1, _MAX_DIGITS + 1):
        shown = (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(
            Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP
        )
        if (shown >= floor) == above:
            break
    return f"{shown.normalize():f}".replace(".", ",") + "%"


# --- журнал --------------------------------------------------------------------------------


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _skips(meta: Mapping[str, Any]) -> set[str | None]:
    """Виды пропуска брифа: прямо (`skip`) и под «всё же написать» (`forced_over_skip`)."""
    forced = meta.get("forced_over_skip")
    return {
        _text(meta.get("skip")),
        _text(forced.get("kind")) if isinstance(forced, dict) else None,
    }


def agent_of(draft: AgentDraftModel) -> Agent:
    """Что сделал агент — по черновику, а не по его нынешнему статусу: решение человека
    статус переписывает, а пропуск брифа и причина отдачи остаются."""
    skipped = _skips(draft.meta or {})
    if draft.status is DraftStatus.SKIPPED or SkipKind.NO_REPLY.value in skipped:
        return Agent.SILENT
    if SkipKind.HUMAN.value in skipped or draft.reason is not None or not draft.body.strip():
        return Agent.HELD
    return Agent.READY


def took_of(draft: AgentDraftModel, *, answered: bool, settled: bool) -> Took:
    """Что сделал человек: решение правилом прогона; молчание моложе срока — ещё не исход."""
    if draft.decided_by == AUTOPILOT:
        return Took.AUTO
    human = replay_drafts.decided(draft, answered=answered)
    if human is None:
        return Took.PENDING
    took = _TOOK[human.decision]
    return Took.PENDING if took is Took.SILENT and not settled else took


def _cohort(draft: AgentDraftModel, meta: Mapping[str, Any]) -> Cohort:
    versions = meta.get("versions")
    known: Mapping[str, Any] = versions if isinstance(versions, dict) else {}
    return Cohort(
        reply=_text(draft.prompt_version),
        situation=_text(known.get("situation")),
        judge=_text(known.get("judge")),
        moves=_text(known.get("moves")),
        kb=_text(meta.get("kb_version")),
    )


def row_of(draft: AgentDraftModel, reply: ReplyModel, *, answered: bool, settled: bool) -> Row:
    meta = draft.meta or {}
    label = meta.get("situation")
    turn = meta.get("turn")
    took = took_of(draft, answered=answered, settled=settled)
    return Row(
        draft_id=draft.id,
        thread_id=reply.thread_id,
        situation=label if isinstance(label, str) and label in LABELS else None,
        cohort=_cohort(draft, meta),
        agent=agent_of(draft),
        took=took,
        replied=answered or took in REPLIED,
        settled=settled,
        written_at=draft.created_at,
        decided_at=draft.decided_at,
        turn=turn if isinstance(turn, int) and not isinstance(turn, bool) else None,
        agent_reason=draft.reason,
        reject_reason=draft.reject_reason,
        reject_kind=draft.reject_kind,
        body=draft.body,
        final_body=draft.final_body,
    )


async def journal(
    session: AsyncSession, *, now: datetime, settle: timedelta = replay_drafts.SETTLE
) -> list[Row]:
    """Все черновики продаж — с решением человека и версией агента."""
    rows = (await session.execute(replay_drafts.sales_drafts())).all()
    replied = await replay_drafts.answered(session, [reply.id for _, reply in rows])
    return [
        row_of(draft, reply, answered=reply.id in replied, settled=reply.created_at <= now - settle)
        for draft, reply in rows
    ]


async def current(session: AsyncSession) -> Cohort:
    """Версия агента сейчас: промпты и таблица ходов кода и база знаний в базе."""
    versions = brief.versions()
    return Cohort(
        reply=parts.PROMPT_VERSION,
        situation=versions["situation"],
        judge=versions["judge"],
        moves=versions["moves"],
        kb=await kb.version(session),
    )


async def verdict(session: AsyncSession, situation: str, *, now: datetime) -> Gate:
    """Ворота автоответа ситуации — по текущей версии агента: решения прежних версий о ней
    не говорят. Для флага ситуации: включает его человек, и только если ворота пройдены."""
    version = await current(session)
    rows = await journal(session, now=now)
    mine = (row for row in rows if row.cohort == version and row.situation == situation)
    return gate(tally(mine), limits())
