"""Экран «Агент: качество» — числа калибровки агента продаж одним ответом.

Что и как считается — `calibration.py`; здесь сборка экрана: когорты (версии агента) с их
числами, показанная когорта — её решения, разрезы по ситуации (с воротами автоответа) и по
причине отклонения, последние правки и отклонения с разницей «черновик → отправлено».

**Показанная когорта** — названная номером; без номера — текущая версия агента, а если она
ещё не писала черновиков — последняя, что писала: после правки базы знаний экран не
открывается пустым. Незнакомый номер — `UnknownCohortError` словами.

**Ситуации — все, что знает агент**, и с нулём: ворота автоответа видны у каждой. Сбой
разбора и письма, отданные человеку до разбора, — строками, только если они есть.

**Разница — по словам** (`difflib`): что убрано из черновика и что дописано в ушедшее.
Слово несёт свои знаки и пробелы за ним: иначе пробелы совпадали бы сами с собой посреди
переписанной фразы, и правка рассыпалась бы на пары слов. Текст собирается из частей
обратно как был.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.agent.stages import OTHER_REASON
from backend.features.sales.agent import calibration, parts
from backend.features.sales.agent.calibration import (
    Cohort,
    Gate,
    Limits,
    Row,
    Tally,
    Took,
    gate,
    tally,
)
from backend.features.sales.agent.situation import Label

#: Ситуация письма словами экрана; `None` — письмо отдано человеку до разбора.
TITLES: Mapping[str | None, str] = {
    Label.WANTS_TO_TALK.value: "хочет говорить",
    Label.ASKS_PRICE.value: "спрашивает цену",
    Label.ASKS_INFO.value: "просит подробности",
    Label.OBJECTION.value: "возражает",
    Label.NOT_NOW.value: "не сейчас",
    Label.REFUSAL.value: "отказ",
    Label.WRONG_PERSON.value: "не тот человек",
    Label.ACK.value: "спасибо, получил",
    Label.AUTORESPONDER.value: "автоответ",
    Label.PARSE_FAILED.value: "агент не понял письмо",
    None: "отдано до разбора",
}
#: Сколько последних правок и отклонений показать.
CHANGES = 20
#: Слов на сторону в разнице: длиннее — оба текста целиком, без разметки.
MAX_WORDS = 3000
#: Общее слово посреди правки не длиннее этого — часть правки, а не граница.
SLIGHT = 3
#: Слово со знаками и пробелами за ним; пробелы в начале текста — своим куском. Текст режется
#: на них без остатка, а пробел отдельным словом не совпадает сам с собой посреди правки.
_WORDS = re.compile(r"\S+\s*|\s+")


class UnknownCohortError(LookupError):
    """Такой версии агента в журнале нет."""


class Piece(StrEnum):
    """Часть разницы: общее, убранное из черновика, дописанное в ушедшее."""

    SAME = "same"
    REMOVED = "removed"
    ADDED = "added"


@dataclass(frozen=True, slots=True)
class Part:
    kind: Piece
    text: str


@dataclass(frozen=True, slots=True)
class CohortLine:
    """Когорта и её числа; когда писались её черновики."""

    cohort: Cohort
    found: Tally
    first: datetime
    last: datetime


@dataclass(frozen=True, slots=True)
class SituationLine:
    situation: str | None
    title: str
    found: Tally
    gate: Gate


@dataclass(frozen=True, slots=True)
class Change:
    """Правка или отклонение: черновик, что ушло, и разница по словам."""

    row: Row
    title: str
    parts: tuple[Part, ...]


@dataclass(frozen=True, slots=True)
class Board:
    limits: Limits
    current: Cohort
    shown: Cohort
    cohorts: tuple[CohortLine, ...]
    found: Tally
    situations: tuple[SituationLine, ...]
    #: Вид причины отклонения готового черновика и сколько раз.
    reasons: tuple[tuple[str, int], ...]
    changes: tuple[Change, ...]


async def board(session: AsyncSession, *, cohort: str | None, now: datetime) -> Board:
    """Экран калибровки: когорты и показанная когорта целиком."""
    rows = await calibration.journal(session, now=now)
    version = await calibration.current(session)
    lines = cohorts(rows)
    shown = chosen(cohort, version, lines)
    mine = [row for row in rows if row.cohort == shown]
    edge = calibration.limits()
    found = tally(mine)
    return Board(
        limits=edge,
        current=version,
        shown=shown,
        cohorts=lines,
        found=found,
        situations=situations(mine, edge),
        reasons=reasons(found),
        changes=changes(mine),
    )


def cohorts(rows: Iterable[Row]) -> tuple[CohortLine, ...]:
    """Когорты журнала, последняя писавшая — первой; числа каждой — по её черновикам."""
    groups: dict[Cohort, list[Row]] = {}
    for row in rows:
        groups.setdefault(row.cohort, []).append(row)
    lines = [
        CohortLine(
            cohort,
            tally(group),
            min(row.written_at for row in group),
            max(row.written_at for row in group),
        )
        for cohort, group in groups.items()
    ]
    newest = {cohort: max(row.draft_id for row in group) for cohort, group in groups.items()}
    lines.sort(key=lambda line: (line.last, newest[line.cohort]), reverse=True)
    return tuple(lines)


def chosen(wanted: str | None, version: Cohort, lines: Sequence[CohortLine]) -> Cohort:
    """Какую когорту показать: названную — или текущую, а без её черновиков — последнюю."""
    if wanted is None:
        known = {line.cohort for line in lines}
        return version if version in known or not lines else lines[0].cohort
    for cohort in (version, *(line.cohort for line in lines)):
        if cohort.id == wanted:
            return cohort
    raise UnknownCohortError(f"версии агента №{wanted} в журнале нет — обновите экран")


def situations(rows: Sequence[Row], edge: Limits) -> tuple[SituationLine, ...]:
    """Ситуации агента по порядку — все, и с нулём; сбой разбора и «до разбора» — если были."""
    present = {row.situation for row in rows}
    rare: tuple[str | None, ...] = (Label.PARSE_FAILED.value, None)
    names: list[str | None] = [label.value for label in Label if label is not Label.PARSE_FAILED]
    names += [name for name in rare if name in present]
    found = []
    for name in names:
        counted = tally(row for row in rows if row.situation == name)
        found.append(SituationLine(name, TITLES[name], counted, gate(counted, edge)))
    return tuple(found)


def reasons(found: Tally) -> tuple[tuple[str, int], ...]:
    """Причины отклонения: весь список этапа и «другое» — и с нулём, прочие — если были."""
    listed = [*parts.REJECT_REASONS, OTHER_REASON]
    rest = sorted(kind for kind in found.reasons if kind not in listed)
    return tuple((kind, found.reasons[kind]) for kind in [*listed, *rest])


def changes(rows: Iterable[Row]) -> tuple[Change, ...]:
    """Последние правки и отклонения — новые первыми, с разницей по словам у правки."""
    touched = [row for row in rows if row.took in (Took.EDITED, Took.REJECTED)]
    touched.sort(key=lambda row: (row.decided_at is not None, row.decided_at, row.draft_id))
    found = []
    for row in reversed(touched[-CHANGES:]):
        sent = row.final_body if row.took is Took.EDITED else None
        pieces = () if sent is None else word_diff(row.body, sent)
        found.append(Change(row, TITLES[row.situation], pieces))
    return tuple(found)


def word_diff(old: str, new: str) -> tuple[Part, ...]:
    """Разница по словам: общее, убранное и дописанное — по порядку текста.

    Убранное и общее собирают черновик, дописанное и общее — ушедшее. Слова сравниваются без
    пробелов за ними: подпись в конце черновика и та же подпись с переводом строки в ушедшем — одно
    слово, разница — только в пробелах. Короткое общее слово посреди правки («на», «и») —
    часть правки: иначе переписанная фраза рвалась бы на куски вокруг случайного совпадения.
    Длиннее `MAX_WORDS` слов — пусто: экран покажет оба текста целиком.
    """
    before, after = _WORDS.findall(old), _WORDS.findall(new)
    if max(len(before), len(after)) > MAX_WORDS:
        return ()
    found: list[Part] = []
    codes = difflib.SequenceMatcher(None, _keys(before), _keys(after), autojunk=False).get_opcodes()
    last = len(codes) - 1
    for number, (tag, start, end, begin, finish) in enumerate(codes):
        if tag == "equal" and not _slight(before[start:end], inside=0 < number < last):
            _same(found, before[start:end], after[begin:finish])
        else:
            _put(found, Piece.REMOVED, before[start:end])
            _put(found, Piece.ADDED, after[begin:finish])
    return tuple(found)


def _keys(words: Sequence[str]) -> list[str]:
    """Слова для сравнения — без пробелов за ними; пробелы в начале текста — как есть."""
    return [word.strip() or word for word in words]


def _same(found: list[Part], before: Sequence[str], after: Sequence[str]) -> None:
    """Общие слова — общей частью; пробелы за словом, если разные, — убранным и дописанным."""
    for was, now in zip(before, after, strict=True):
        word = was.rstrip()
        _put(found, Piece.SAME, [word])
        if was != now:
            _put(found, Piece.REMOVED, [was[len(word) :]])
            _put(found, Piece.ADDED, [now[len(word) :]])
        else:
            _put(found, Piece.SAME, [was[len(word) :]])


def _slight(words: Sequence[str], *, inside: bool) -> bool:
    """Общее посреди правки слишком короткое, чтобы держать её раздельно: одно слово до трёх
    знаков. По краям текста общее — всегда общее: граница правки там настоящая."""
    return inside and len(words) == 1 and len(words[0].strip()) <= SLIGHT


def _put(found: list[Part], kind: Piece, words: Sequence[str]) -> None:
    """Слова — частью разницы; та же часть подряд склеивается, пустое не ложится."""
    text = "".join(words)
    if not text:
        return
    if found and found[-1].kind is kind:
        found[-1] = Part(kind, found[-1].text + text)
    else:
        found.append(Part(kind, text))
