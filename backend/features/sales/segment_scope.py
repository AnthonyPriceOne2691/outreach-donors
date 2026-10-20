"""Разбор описания сегмента судьёй v4: страны и виды сайтов — один раз на описание, а не по сайту.

Страны, где по описанию работают компании сегмента, и виды сайтов, которые он просит, — факты
описания, а не сайта. Спрошенные у модели вместе с каждым сайтом, они шумели: в синтетике 5 ответов
из 121 называли не те страны, а однажды не назвали ни одной — и сайт ушёл бы в «сегмент без места».
Поэтому описание разбирается отдельным коротким вызовом (`prompts/segment_scope.md`), один раз на
описание и версию разбора, и итог лежит в отчёте прогона (`report["scope"]`, без миграции): пересуд
того же описания модель за ним не зовёт, а оператор видит словами, как судья понял описание.

**Сбой разбора — не «мест нет».** Модель не ответила, ответ обрезан или не той формы — разбора нет
(`Scope` не получен): судья не решает по месту ничего, сайт — «на проверку» словами «описание сегмента
не разобрано», и следующий проход или пересуд спросит снова (`segment_rules`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import literal, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from backend.features.core import usage
from backend.features.sales.geo import COUNTRIES
from backend.features.sales.hypothesis_countries import known_codes
from backend.features.sales.segment_models import SalesSegmentRunModel

logger = logging.getLogger(__name__)

#: Версия промпта разбора: правка файла — новая версия, прежний разбор в отчёте не годится.
SCOPE_VERSION = "sales-segment-scope-v1"
PROMPT_PATH = Path(__file__).with_name("prompts") / "segment_scope.md"
#: Ключ разбора в отчёте прогона.
KEY = "scope"
#: Виды сайтов, которые сегмент может просить.
KINDS = ("own", "intermediary", "media", "other")
#: Виды сайтов словами отчёта.
KIND_WORDS = {
    "own": "сайты самих компаний",
    "intermediary": "каталоги, биржи, площадки",
    "media": "издания и блоги",
    "other": "госорганы, школы, ассоциации",
}
#: Стран в словах — не больше; остальные — числом.
NAMED_MAX = 12
SCHEMA: dict[str, Any] = {
    "name": "segment_scope",
    "schema": {
        "type": "object",
        "properties": {
            "countries": {"type": "array", "items": {"type": "string"}},
            "kinds": {"type": "array", "items": {"type": "string", "enum": list(KINDS)}},
            "excludes_networks": {"type": "boolean"},
        },
        "required": ["countries", "kinds", "excludes_networks"],
        "additionalProperties": False,
    },
}

Run = SalesSegmentRunModel


@lru_cache(maxsize=1)
def load_prompt() -> str:
    """Промпт разбора файлом рядом с модулем: читается один раз на процесс."""
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _names() -> dict[str, str]:
    return {code.upper(): russian for code, _, russian, _ in COUNTRIES}


def country_words(codes: frozenset[str] | set[str]) -> str:
    """Страны словами по-русски (`geo.COUNTRIES`, CLDR), по алфавиту; много — первые и число."""
    names = sorted(_names().get(code, code) for code in codes)
    if len(names) <= NAMED_MAX:
        return ", ".join(names)
    return f"{', '.join(names[:NAMED_MAX])} и ещё {len(names) - NAMED_MAX}"


@dataclass(frozen=True, slots=True)
class Scope:
    """Как судья понял описание: страны, где работают компании сегмента, и виды сайтов."""

    countries: frozenset[str]
    kinds: frozenset[str]
    #: Описание говорит, что крупные международные компании и сети (их региональные сайты) не
    #: подходят: правило «крупная сеть» (`segment_rules`) действует только тогда.
    excludes_networks: bool = False

    @property
    def words(self) -> str:
        """Разбор словами — для отчёта прогона."""
        where = (
            f"страна сегмента по описанию — {country_words(self.countries)}"
            if self.countries
            else "описание стран не называет — место сайта не сверяется"
        )
        kinds = ", ".join(KIND_WORDS[kind] for kind in KINDS if kind in self.kinds)
        networks = "; крупные международные сети — не подходят" if self.excludes_networks else ""
        return f"{where}; виды сайтов — {kinds}{networks}"

    def as_json(self) -> dict[str, Any]:
        return {
            "countries": sorted(self.countries),
            "kinds": sorted(self.kinds),
            "excludes_networks": self.excludes_networks,
        }


def _body(content: str) -> dict[str, Any] | str:
    """Ответ разбора → объект с полями своих типов; иначе — почему нет, словами."""
    try:
        body = json.loads(content)
    except ValueError:
        logger.warning("разбор описания: ответ модели — не JSON", extra={"chars": len(content)})
        return "ответ разбора описания не разобран"
    if not isinstance(body, dict):
        return "ответ разбора описания не объект"
    lists = isinstance(body.get("countries"), list) and isinstance(body.get("kinds"), list)
    if not lists or not isinstance(body.get("excludes_networks", False), bool):
        return "ответ разбора описания не той формы"
    return body


def read_scope(content: str) -> Scope | str:
    """Ответ модели → разбор; не той формы или без вида сайтов — почему, словами. Код страны не из
    ISO 3166-1 — не годится весь разбор: страны сегмента наполовину — хуже, чем спросить снова."""
    body = _body(content)
    if isinstance(body, str):
        return body
    codes = {str(code).strip().upper() for code in body["countries"]}
    if unknown := sorted(code for code in codes if code not in known_codes()):
        return f"разбор описания назвал не код страны: {', '.join(unknown[:3])}"
    named = {str(kind).strip().lower() for kind in body["kinds"]} & set(KINDS)
    if not named:
        return "разбор описания не назвал вида сайтов"
    return Scope(frozenset(codes), frozenset(named), body.get("excludes_networks", False))


def remembered(report: dict[str, Any] | None, digest: str) -> Scope | None:
    """Разбор того же описания и той же версии из отчёта прогона; нет или был сбой — `None`."""
    found = (report or {}).get(KEY)
    if not isinstance(found, dict) or found.get("digest") != digest:
        return None
    if found.get("version") != SCOPE_VERSION or found.get("failed"):
        return None
    keys = ("countries", "kinds", "excludes_networks")
    read = read_scope(json.dumps({key: found[key] for key in keys if key in found}))
    return read if isinstance(read, Scope) else None


def entry(digest: str, read: Scope | str, tokens: int) -> dict[str, Any]:
    """Запись разбора в отчёт прогона: итог или сбой словами."""
    base = {"digest": digest, "version": SCOPE_VERSION, "tokens": tokens}
    if isinstance(read, str):
        return {**base, "failed": read, "words": f"описание сегмента не разобрано: {read}"}
    return {**base, **read.as_json(), "words": read.words}


async def write(session: AsyncSession, run: Run, found: dict[str, Any], *, operation: str) -> None:
    """Разбор — в отчёт прогона одной SQL-правкой своего ключа (прочие ключи пишут другие), токены —
    в журнал расхода; объект прогона в сессии видит то же без лишней записи. Коммит — здесь: разбор
    оплачен и не должен потеряться с откатом прохода."""
    await session.execute(
        update(Run)
        .where(Run.id == run.id)
        .values(report=Run.report.op("||")(literal({KEY: found}, type_=JSONB)))
        .execution_options(synchronize_session=False)
    )
    if found.get("tokens"):
        usage.record(session, operation=operation, units=int(found["tokens"]))
    await session.commit()
    set_committed_value(run, "report", {**(run.report or {}), KEY: found})
    if found.get("failed"):
        logger.warning(
            "судья сегмента: описание не разобрано", extra={"run": run.id, "why": found["failed"]}
        )
