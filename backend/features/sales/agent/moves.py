"""Ход агента продаж: что делать в ситуации письма — таблицей данными.

Таблица — `moves.toml` в пакете (package-data): ситуация → ход, виды записей
базы под него и куда позвать лида. Правка поведения — правка файла, а не кода,
и версия таблицы ложится в `meta` черновика: калибровка сравнивает версии.

**Нужен ли ответ, решает код, а не таблица** (`situation.reply_needed`). В
таблице только ситуации, где ответ нужен: молчание и сбой разбора бриф решает
до неё, и строка «ack → ход» здесь была бы вторым ответом на тот же вопрос.

**Таблица проверяется при чтении целиком.** Каждая ситуация, где нужен ответ,
стоит ровно раз; виды записей — из набора базы, призыв — из известных. Ошибка
в файле — отказ словами при первом чтении и в шаге CI «промпты читаются», а не
ход по умолчанию в живой переписке.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from backend.features.sales.agent.situation import SILENT, Label
from backend.features.sales.models import KbKind

TABLE = Path(__file__).with_name("moves.toml")
#: Имя хода — метка строки брифа `[move <имя>]`: другие знаки судья назад не прочтёт.
_NAME = re.compile(r"[a-z_]+")
#: Ситуации, где ответ нужен и агент пишет: остальное бриф решает до таблицы.
ANSWERED = frozenset(Label) - SILENT - {Label.PARSE_FAILED}


class Cta(StrEnum):
    """Куда агент зовёт лида — ссылкой из настроек отправителя."""

    CALL = "call"
    TELEGRAM = "telegram"


class MovesTableError(ValueError):
    """Таблица ходов не годится. Текст — что поправить в файле."""


@dataclass(frozen=True, slots=True)
class Move:
    """Что агент делает в ситуации."""

    name: str
    #: Что сделать — словами: строкой брифа уходит писателю.
    does: str
    #: Виды записей базы под ход — сверх тех, что нужны каждому письму.
    kinds: tuple[KbKind, ...] = ()
    #: Куда звать, по порядку: берётся первая ссылка, заданная в настройках.
    #: Пусто — ход без призыва.
    cta: tuple[Cta, ...] = ()
    #: Разговор становится лидом — пометка в `meta` (передача лида — Ф5).
    lead: bool = False


@dataclass(frozen=True, slots=True)
class Table:
    version: str
    #: Виды записей под каждое письмо.
    always: tuple[KbKind, ...]
    #: Виды, которые сужаются до тегов письма.
    by_tags: frozenset[KbKind]
    moves: Mapping[Label, Move]

    def move(self, label: Label) -> Move:
        """Ход ситуации. Ситуация без хода — ответ на неё не пишется."""
        found = self.moves.get(label)
        if found is None:
            raise LookupError(f"у ситуации «{label.value}» хода нет: ответ на неё не пишется")
        return found


def _strings(raw: object, where: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise MovesTableError(f"{where}: ждём список строк")
    return [str(item) for item in raw]


def _kinds(raw: object, where: str) -> tuple[KbKind, ...]:
    values = _strings(raw, where)
    known = [kind.value for kind in KbKind]
    if bad := [value for value in values if value not in known]:
        raise MovesTableError(f"{where}: вида «{bad[0]}» в базе нет; есть: {', '.join(known)}")
    return tuple(KbKind(value) for value in values)


def _ctas(raw: object, where: str) -> tuple[Cta, ...]:
    values = _strings(raw, where)
    known = [cta.value for cta in Cta]
    if bad := [value for value in values if value not in known]:
        raise MovesTableError(f"{where}: призыва «{bad[0]}» нет; есть: {', '.join(known)}")
    return tuple(Cta(value) for value in values)


def _move(code: str, raw: object) -> Move:
    where = f"ситуация «{code}»"
    if not isinstance(raw, dict):
        raise MovesTableError(f"{where}: ждём раздел [situations.{code}]")
    name, does = raw.get("move"), raw.get("does")
    if (
        not isinstance(name, str)
        or not isinstance(does, str)
        or not (name.strip() and does.strip())
    ):
        raise MovesTableError(f"{where}: нет хода (move) или что он делает (does)")
    if not _NAME.fullmatch(name.strip()):
        raise MovesTableError(
            f"{where}: имя хода «{name.strip()}» — латиница строчными и «_»: оно метка строки "
            "брифа, и судья читает его назад (`facts.read`)"
        )
    return Move(
        name=name.strip(),
        does=" ".join(does.split()),
        kinds=_kinds(raw.get("kinds", []), where),
        cta=_ctas(raw.get("cta", []), where),
        lead=raw.get("lead") is True,
    )


#: Коды меток: все и те, где ответ нужен.
_LABELS = frozenset(label.value for label in Label)
_ANSWERED = frozenset(label.value for label in ANSWERED)


def _codes_checked(codes: set[str]) -> None:
    """Каждая ситуация, где нужен ответ, — ровно раз, и ничего сверх них."""
    if unknown := sorted(codes - _LABELS):
        known = ", ".join(sorted(_LABELS))
        raise MovesTableError(f"ситуации «{unknown[0]}» нет среди меток: {known}")
    if silent := sorted(codes - _ANSWERED):
        raise MovesTableError(
            f"у ситуации «{silent[0]}» ответа нет — нужен ли ответ, решает код, а не таблица"
        )
    if missing := sorted(_ANSWERED - codes):
        raise MovesTableError(f"нет хода для ситуаций: {', '.join(missing)}")


def _situations(raw: object) -> dict[Label, Move]:
    if not isinstance(raw, dict):
        raise MovesTableError("нет разделов [situations.<метка>]")
    _codes_checked(set(raw))
    return {Label(code): _move(code, value) for code, value in raw.items()}


def build(raw: Mapping[str, Any]) -> Table:
    """Прочитанный файл → проверенная таблица. Отказ — первой причиной, словами."""
    version = raw.get("version")
    if not isinstance(version, str) or not version.strip():
        raise MovesTableError("нет версии таблицы (version) — по ней калибровка сравнивает ходы")
    return Table(
        version=version.strip(),
        always=_kinds(raw.get("always", []), "always"),
        by_tags=frozenset(_kinds(raw.get("by_tags", []), "by_tags")),
        moves=MappingProxyType(_situations(raw.get("situations"))),
    )


@lru_cache(maxsize=1)
def table() -> Table:
    """Таблица ходов из пакета — читается один раз за запуск."""
    return build(tomllib.loads(TABLE.read_text(encoding="utf-8")))
