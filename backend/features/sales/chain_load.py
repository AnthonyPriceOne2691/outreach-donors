"""Загрузка шаблонов цепочки писем из файла — первичное наполнение набора.

**Файл — JSON: список шаблонов** `{"step", "language", "subject", "body", "active"}`;
тема — только у первого письма, `active` необязателен. Не CSV: тело — зоны и абзацы,
в CSV их держали бы в кавычках со сдвоенными кавычками внутри. Файл лежит **вне
репозитория** (`outside.py`): тексты писем — коммерческие, а репозиторий публичный.

**Один файл — один набор**: общий или гипотезы (`--hypothesis` у команды). **Всё или
ничего**: ошибка хотя бы в одном шаблоне — отказ с номером шаблона и словами, в базе
ничего не меняется; правила — те же, что у экрана (`chain_text.step_template`, `unsigned`).

**Повтор не задваивает.** Шаблон узнаётся по шагу и языку в наборе. Такой же — «без
изменений»; с другой темой, телом или включением — «отличается» и без `--update` не
трогается: его могли поправить на экране после первой загрузки, а старый файл молча
откатил бы правку. Шаблоны набора, которых нет в файле, не трогаются тоже. Журнал —
одной записью на загрузку, с версиями цепочек по языкам до и после.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction
from backend.features.letters.guards import ForbiddenContentError
from backend.features.sales import chain, chain_text, outside
from backend.features.sales.chain_text import ChainError, StepTemplate
from backend.features.sales.models import SalesChainTemplateModel
from backend.features.sales.sender import Sender

logger = logging.getLogger(__name__)

#: Больше файл цепочки не бывает: шесть писем по нескольку абзацев.
MAX_BYTES = 1024 * 1024
#: Поля шаблона в файле: обязательные и все.
NEEDED = ("step", "language", "body")
KNOWN = ("step", "language", "subject", "body", "active")


class ChainFileError(ValueError):
    """Файл не читается целиком. Текст — что делать."""


@dataclass(frozen=True, slots=True)
class Problem:
    """Шаблон с ошибкой: номер в файле с единицы, шаг и язык, если есть, и причина."""

    number: int
    where: str | None
    reason: str


#: Что станет с набором: по шагу и языку шаблон — новый, такой же или отличается.
type Plan = outside.Sorting[StepTemplate, SalesChainTemplateModel]


@dataclass(frozen=True, slots=True)
class Loaded:
    added: int
    updated: int
    #: Версия цепочки набора по языкам — до загрузки и после.
    versions: dict[str, dict[str, str]]


def _where(item: Any) -> str | None:
    """Шаг и язык записи словами — чтобы отказ называл, о каком письме речь."""
    if not isinstance(item, dict):
        return None
    step, language = item.get("step"), item.get("language")
    return f"шаг {step}, {language}" if step is not None and language is not None else None


def _keys_problem(item: dict[str, Any]) -> str | None:
    if extra := sorted(set(item) - set(KNOWN)):
        return f"лишние поля {', '.join(extra)}: ждём {', '.join(KNOWN)}"
    lacking = [name for name in NEEDED if name not in item]
    return f"нет полей {', '.join(lacking)}" if lacking else None


def _types_problem(item: dict[str, Any]) -> str | None:
    # `True` — тоже `int` в Python: шаг-флажок отсеивается здесь, а не правилами шага.
    if type(item["step"]) is not int:
        return "шаг — число: 1 — первое письмо, 2 и 3 — добивки"
    if not isinstance(item["language"], str) or not isinstance(item["body"], str):
        return "язык и текст — строки"
    if item.get("subject") is not None and not isinstance(item["subject"], str):
        return "тема — строка; у добивок её нет вовсе"
    return None if isinstance(item.get("active", True), bool) else "включён — true или false"


def _problem(item: Any) -> str | None:
    """Чего не хватает записи по форме — до правил ядра."""
    if not isinstance(item, dict):
        return 'не шаблон: ждём объект {"step": 1, "language": "en", "subject": …, "body": …}'
    return _keys_problem(item) or _types_problem(item)


def _template(item: Any, found: Sender) -> StepTemplate | str:
    """Запись файла → проверенный шаблон или причина отказа словами."""
    if (reason := _problem(item)) is not None:
        return reason
    try:
        new = chain_text.step_template(**item)
        chain_text.unsigned(new, found)
    except (ChainError, ForbiddenContentError) as exc:
        logger.info("продажи: шаблон файла цепочки не годится", extra={"where": _where(item)})
        return str(exc)
    return new


def read(path: Path, found: Sender) -> tuple[list[StepTemplate], list[Problem]]:
    """Файл → шаблоны и ошибки по номерам. `found` — настройки отправителя: подпись и
    адрес из них в шаблоне не повторяются. Отказ целиком — `ChainFileError`."""
    try:
        data = outside.read_json(
            path, leaks="тексты писем", too_big="это не цепочка писем", max_bytes=MAX_BYTES
        )
    except outside.OutsideFileError as exc:
        raise ChainFileError(str(exc)) from exc
    if not isinstance(data, list) or not data:
        raise ChainFileError(f"в файле {path.name} ждём непустой список шаблонов: [{{…}}, {{…}}]")
    seen: dict[tuple[int, str], int] = {}
    templates: list[StepTemplate] = []
    problems: list[Problem] = []
    for number, item in enumerate(data, start=1):
        made = _template(item, found)
        if isinstance(made, str):
            problems.append(Problem(number, _where(item), made))
        elif (twin := seen.setdefault(made.key, number)) != number:
            reason = f"тот же шаг и язык, что у №{twin}: в наборе он один"
            problems.append(Problem(number, _where(item), reason))
        else:
            templates.append(made)
    return templates, problems


def _same(new: StepTemplate, row: SalesChainTemplateModel) -> bool:
    return (new.subject, new.body, new.active) == (row.subject, row.body, row.active)


async def plan(
    session: AsyncSession, templates: Sequence[StepTemplate], hypothesis_id: int | None
) -> Plan:
    """Сверка файла с набором по шагу и языку. Ничего не пишет."""
    rows = {(row.step, row.language): row for row in await chain.rows(session, hypothesis_id)}
    return outside.sort_against(templates, rows, key=lambda new: new.key, same=_same)


async def _versions(session: AsyncSession, hypothesis_id: int | None) -> dict[str, str]:
    return {
        code: await chain.set_version(session, hypothesis_id, code) for code in chain_text.LANGUAGES
    }


async def apply(
    session: AsyncSession,
    planned: Plan,
    *,
    hypothesis_id: int | None,
    update: bool,
    author: str,
    source: str,
) -> Loaded:
    """Записать новое и — с `update` — отличающееся. Журнал одной записью, коммит — за
    вызывающим. Нечего записать — ни журнала, ни строки лога о загрузке."""
    before = await _versions(session, hypothesis_id)
    updated = planned.differs if update else []
    if not planned.added and not updated:
        return Loaded(0, 0, {code: {"было": v, "стало": v} for code, v in before.items()})
    for new in planned.added:
        session.add(
            SalesChainTemplateModel(
                hypothesis_id=hypothesis_id,
                step=new.step,
                language=new.language,
                subject=new.subject,
                body=new.body,
                active=new.active,
                updated_by=author,
            )
        )
    for new, row in updated:
        row.subject, row.body, row.active, row.updated_by = (
            new.subject,
            new.body,
            new.active,
            author,
        )
    await session.flush()
    after = await _versions(session, hypothesis_id)
    versions = {code: {"было": before[code], "стало": after[code]} for code in chain_text.LANGUAGES}
    await AccessRepository(session).record(
        AuditAction.SALES_CHAIN_CHANGED,
        target="sales_chain",
        details={
            "источник": source,
            "набор": chain.set_name(hypothesis_id),
            "добавлено": len(planned.added),
            "обновлено": len(updated),
            "без изменений": len(planned.same),
            "отличается, не тронуто": len(planned.differs) - len(updated),
            "версия": versions,
        },
    )
    logger.info("продажи: цепочка писем загружена", extra={"source": source, "versions": after})
    return Loaded(len(planned.added), len(updated), versions)
