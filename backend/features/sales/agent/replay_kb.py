"""База знаний версии агента продаж — файлом вместо таблицы, на время прогона.

Новая версия агента — это и правка базы знаний, а база живого агента — таблица: правка на
экране сразу пишет живые черновики. Поэтому прогон версии (`replay.py`) берёт базу из
`kb.json` каталога версии — списка записей, как у загрузки `sales-kb-load`: тот же разбор и
те же правила записи (`kb_load.read`), ошибка хоть в одной записи — отказ словами, файл в
копии репозитория не читается вовсе. Прошла версия ворота — тот же файл грузится в таблицу
той же загрузкой.

Агент видит только включённые записи, по порядку таблицы (вид, язык, заголовок). Номера
фактов — по порядку с единицы: у записей файла номеров таблицы нет, и в строках брифа номера
другие, чем у той же базы из таблицы.

Подмена — выборки базы `kb.facts` (её берут бриф и версия прогона) и только на время прогона:
`replay.using` возвращает прежнюю в любом случае.
"""

from __future__ import annotations

from collections.abc import Coroutine, Iterable
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales import kb, kb_load
from backend.features.sales.models import KbKind


class KbVersionError(ValueError):
    """База знаний версии не годится — словами, с файлом и записями."""


class Facts(Protocol):
    """Подпись выборки базы (`kb.facts`) — её подменяет база версии из файла."""

    def __call__(
        self,
        session: AsyncSession,
        *,
        kinds: Iterable[KbKind] | None = ...,
        language: str | None = ...,
        tags: Iterable[str] | None = ...,
    ) -> Coroutine[Any, Any, list[kb.Fact]]: ...


def facts_of(path: Path) -> tuple[kb.Fact, ...]:
    """Что увидит агент из файла базы: только включённые записи, по порядку таблицы."""
    try:
        entries, problems = kb_load.read(path)
    except kb_load.KbFileError as exc:
        raise KbVersionError(f"база знаний версии не читается: {exc}") from exc
    if problems:
        said = "; ".join(f"№{found.number}: {found.reason}" for found in problems[:3])
        raise KbVersionError(
            f"база знаний версии ({path}) не годится — записей с ошибкой {len(problems)}: {said}"
        )
    kinds = list(KbKind)
    seen = sorted(
        (entry for entry in entries if entry.active),
        key=lambda entry: (kinds.index(entry.kind), entry.language, entry.title),
    )
    return tuple(
        kb.Fact(number, entry.kind, entry.language, entry.title, entry.text, entry.tags)
        for number, entry in enumerate(seen, start=1)
    )


def substitute(found: tuple[kb.Fact, ...]) -> Facts:
    """Замена `kb.facts`: записи файла целиком. Выборки по виду, языку и тегам у неё нет —
    бриф берёт базу целиком; позовут с выборкой — отказ, а не тихая разница с живым агентом."""

    async def facts(
        session: AsyncSession,  # noqa: ARG001 — подпись `kb.facts`: прогон подменяет её
        *,
        kinds: Iterable[KbKind] | None = None,
        language: str | None = None,
        tags: Iterable[str] | None = None,
    ) -> list[kb.Fact]:
        if kinds is not None or language is not None or tags:
            raise KbVersionError("база знаний версии из файла отдаётся целиком: выборки нет")
        return list(found)

    return facts
