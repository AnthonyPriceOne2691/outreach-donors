"""Чтение для раздела «Продажи»: гипотезы со счётчиками и лиды с фильтрами под колонками.

Экран показывает то, что записали загрузка (`intake.py`) и очистка (`cleaning.py`),
и ничего не решает сам: состояния и причины — те, что лежат в базе, фильтр — по тем
же кодам. Здесь только чтение; пишут загрузка и очистка.

**Фильтр — по колонке, тем же словом, что в её ячейке** (как у отбора, 26.09.2026):
состояние, причина отказа, гипотеза, поиск по адресу, имени, компании и домену.
Сводка — по всем лидам, а не по странице: она отвечает на вопрос «сколько готово
к письмам», и фильтры таблицы её не трогают.

**Счётчики — полным перечислением, с нулями.** Экран строит фильтр причин по
ключам ответа, а не по своему списку: причина, которой ещё никто не получил,
иначе пропала бы из фильтра, а новая причина очистки появилась бы на экране
без правки фронта. Код причины в фильтре — строкой, как в базе: незнакомый код
из устаревшей ссылки ничего не находит, а не отказывает.

**Страница — номером, размер называет сервер** (`PAGE_SIZE`): второй экземпляр
числа на фронте разошёлся бы при первой правке. Порядок — новые первыми, до
номера записи: на стыке страниц строка не показывается дважды.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.domain import DomainModel
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
)

#: Лидов на странице — как у отбора (замечание 26.09.2026: «максимум 20»).
PAGE_SIZE = 20
#: Больше за раз не отдаём: страница — чтение глазами, а не выгрузка.
MAX_PAGE_SIZE = 100
#: Длиннее искать незачем: адрес не длиннее 254 знаков, имя — колонки в 255.
SEARCH_LENGTH = 200


@dataclass(frozen=True, slots=True)
class LeadFilters:
    state: LeadStatus | None = None
    #: Код причины — значение `RejectionReason`, строкой, как в базе.
    reason: str | None = None
    hypothesis_id: int | None = None
    search: str | None = None
    #: Страница — с единицы. Страница за концом — пустая, с настоящим `total`.
    page: int = 1
    size: int = PAGE_SIZE


@dataclass(frozen=True, slots=True)
class LeadRow:
    lead: SalesLeadModel
    #: Домен компании и имя гипотезы — словами, а не номерами: их читают в строке.
    host: str
    hypothesis: str


@dataclass(frozen=True, slots=True)
class LeadsPage:
    rows: list[LeadRow]
    total: int
    #: Состояние → сколько, по всем лидам. Каждое состояние названо, хоть и нулём.
    states: dict[str, int]
    #: Причина отказа → сколько отклонённых. Каждая причина названа.
    reasons: dict[str, int]


@dataclass(frozen=True, slots=True)
class HypothesisRow:
    hypothesis: SalesHypothesisModel
    states: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.states.values())


def _by_search(text: str) -> ColumnElement[bool]:
    needle = f"%{text.strip().lower()}%"
    return or_(
        SalesLeadModel.email.ilike(needle),
        SalesLeadModel.name.ilike(needle),
        SalesLeadModel.company.ilike(needle),
        DomainModel.host.ilike(needle),
    )


def _conditions(filters: LeadFilters) -> list[ColumnElement[bool]]:
    """Условия фильтров под колонками — все сразу, «и»."""
    found: list[ColumnElement[bool]] = []
    if filters.state is not None:
        found.append(SalesLeadModel.status == filters.state)
    if filters.reason is not None:
        found.append(SalesLeadModel.rejection_reason == filters.reason)
    if filters.hypothesis_id is not None:
        found.append(SalesLeadModel.hypothesis_id == filters.hypothesis_id)
    if filters.search and filters.search.strip():
        found.append(_by_search(filters.search))
    return found


def _base() -> Select[Any]:
    """Лид с доменом компании и именем гипотезы: без них строка — номера."""
    return (
        select(SalesLeadModel, DomainModel.host, SalesHypothesisModel.name)
        .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
        .join(SalesHypothesisModel, SalesHypothesisModel.id == SalesLeadModel.hypothesis_id)
    )


def _complete(counted: Mapping[str, int], codes: Iterable[str]) -> dict[str, int]:
    """Каждый код назван, хоть и нулём, в порядке перечисления; код из базы,
    которого перечисление не знает, — после известных, а не потерян."""
    named = {code: counted.get(code, 0) for code in codes}
    return {**named, **{code: n for code, n in counted.items() if code not in named}}


async def _states(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(
        select(SalesLeadModel.status, func.count()).group_by(SalesLeadModel.status)
    )
    counted = {LeadStatus(status).value: int(n) for status, n in rows.all()}
    return _complete(counted, (status.value for status in LeadStatus))


async def _reasons(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(
        select(SalesLeadModel.rejection_reason, func.count())
        .where(
            SalesLeadModel.status == LeadStatus.REJECTED,
            SalesLeadModel.rejection_reason.is_not(None),
        )
        .group_by(SalesLeadModel.rejection_reason)
    )
    counted = {str(reason): int(n) for reason, n in rows.all()}
    return _complete(counted, (reason.value for reason in RejectionReason))


async def leads(session: AsyncSession, filters: LeadFilters) -> LeadsPage:
    """Страница лидов под фильтрами и сводка по всем лидам."""
    narrowed = _base().where(*_conditions(filters))
    rows = await session.execute(
        narrowed.order_by(SalesLeadModel.id.desc())
        .limit(filters.size)
        .offset((filters.page - 1) * filters.size)
    )
    total = await session.scalar(select(func.count()).select_from(narrowed.subquery()))
    return LeadsPage(
        rows=[LeadRow(lead, host, name) for lead, host, name in rows.all()],
        total=int(total or 0),
        states=await _states(session),
        reasons=await _reasons(session),
    )


async def hypotheses(session: AsyncSession) -> list[HypothesisRow]:
    """Все гипотезы, старшие первыми, и сколько у каждой лидов в каждом состоянии."""
    found = await session.scalars(select(SalesHypothesisModel).order_by(SalesHypothesisModel.id))
    counted = await session.execute(
        select(SalesLeadModel.hypothesis_id, SalesLeadModel.status, func.count()).group_by(
            SalesLeadModel.hypothesis_id, SalesLeadModel.status
        )
    )
    by_hypothesis: dict[int, dict[str, int]] = {}
    for hypothesis_id, status, n in counted.all():
        by_hypothesis.setdefault(int(hypothesis_id), {})[LeadStatus(status).value] = int(n)
    return [
        HypothesisRow(
            hypothesis,
            _complete(by_hypothesis.get(hypothesis.id, {}), (s.value for s in LeadStatus)),
        )
        for hypothesis in found.all()
    ]
