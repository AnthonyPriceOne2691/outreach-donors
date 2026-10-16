"""Что держит гипотезу продаж: реестр ссылок на неё и счёт по видам — словами для отказа.

Удаляется только пустая гипотеза — «на случай, если завели по ошибке» (решение владельца
15.10). Лиды, диалоги и письма, прогоны сбора с их сайтами и вердиктами судьи, свой набор
цепочки — данные о людях, деньгах и текстах: удаление гипотезы их не стирает. Ключи на
гипотезу — `RESTRICT`, и база сама не даст удалить держащую, но её отказ без слов. Здесь —
какие виды держат и сколько их: для отказа удаления (409) и для окна гипотезы на экране.

**Реестр один** (`HOLDERS`): вид, слова и колонка ключа на гипотезу. Тест сверяет его с
метаданными: таблица, которая заведёт ключ на гипотезу без строки здесь, роняет тест, а не
удаление (`tests/test_sales_hypothesis_delete.py`).

**Диалоги — своим видом**, а не через письма: диалог, начатый набором цепочки гипотезы,
держит её ключом `sales_threads.chain_hypothesis_id`, а диалог её лида — через лида. Письма —
число для слов, а не держатель: письма продаж без диалога не бывает.

Мягкие ссылки гипотезу не держат. Рассылку «Продажи: <имя>» сборка находит по имени —
`hypotheses` переименовывает её вместе с гипотезой и удаляет вместе с пустой. Журнал, задачи
очереди, метка сделки Kommo, слова очистки лидов и тексты недельных отчётов — история; в
настройках (`config/sales.py`, `agent_settings`, `sales_settings`) гипотеза не названа ни
именем, ни номером (проверено 15.10).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from backend.features.core.models.outreach import MessageModel
from backend.features.sales.models import (
    SalesChainTemplateModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.segment_models import (
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    SalesSegmentVerdictModel,
)
from backend.features.sales.segment_spend import number

#: Вид диалогов: считается и по набору цепочки, и по лидам гипотезы (`_dialogs`).
DIALOGS = "dialogs"
#: Письма диалогов гипотезы — число для слов, а не держатель.
LETTERS = "letters"


@dataclass(frozen=True, slots=True)
class Holder:
    """Ссылка на гипотезу, которая держит её: код вида, слова и колонка ключа."""

    kind: str
    words: str
    column: InstrumentedAttribute[Any]


#: Ключи на `sales_hypotheses` — все. Порядок — порядок слов отказа.
HOLDERS: tuple[Holder, ...] = (
    Holder("leads", "лиды", SalesLeadModel.hypothesis_id),
    Holder(DIALOGS, "диалоги", SalesThreadModel.chain_hypothesis_id),
    Holder("runs", "прогоны сбора", SalesSegmentRunModel.hypothesis_id),
    Holder("sites", "сайты сбора", SalesSegmentCandidateModel.hypothesis_id),
    Holder("verdicts", "вердикты судьи сегмента", SalesSegmentVerdictModel.hypothesis_id),
    Holder("chain", "шаблоны своей цепочки", SalesChainTemplateModel.hypothesis_id),
)

#: Вид → слова; письма — после диалогов.
WORDS: dict[str, str] = (
    {holder.kind: holder.words for holder in HOLDERS[:2]}
    | {LETTERS: "письма"}
    | {holder.kind: holder.words for holder in HOLDERS[2:]}
)


def _dialogs(hypothesis_id: int) -> Select[tuple[int]]:
    """Диалоги гипотезы: начатые её набором цепочки и диалоги её лидов."""
    own_leads = select(SalesLeadModel.id).where(SalesLeadModel.hypothesis_id == hypothesis_id)
    return select(SalesThreadModel.thread_id).where(
        or_(
            SalesThreadModel.chain_hypothesis_id == hypothesis_id,
            SalesThreadModel.lead_id.in_(own_leads),
        )
    )


async def _count(session: AsyncSession, query: Select[Any]) -> int:
    return int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)


async def count(session: AsyncSession, hypothesis_id: int) -> dict[str, int]:
    """Вид → сколько, только ненулевые, в порядке слов. Пусто — гипотезу ничто не держит."""
    found: dict[str, int] = {}
    for holder in HOLDERS:
        if holder.kind == DIALOGS:
            continue
        found[holder.kind] = await _count(
            session, select(holder.column).where(holder.column == hypothesis_id)
        )
    dialogs = _dialogs(hypothesis_id)
    found[DIALOGS] = await _count(session, dialogs)
    found[LETTERS] = await _count(
        session, select(MessageModel.id).where(MessageModel.thread_id.in_(dialogs))
    )
    return {kind: found[kind] for kind in WORDS if found[kind]}


def words(found: Mapping[str, int]) -> str | None:
    """«лиды — 12, письма — 30»; ничто не держит — `None`."""
    if not found:
        return None
    return ", ".join(f"{WORDS[kind]} — {number(n)}" for kind, n in found.items())
