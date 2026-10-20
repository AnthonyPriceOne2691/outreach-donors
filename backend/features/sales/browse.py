"""Чтение для раздела «Продажи»: гипотезы со счётчиками и лиды с фильтрами под колонками.

Экран показывает то, что записали загрузка (`intake.py`), очистка (`cleaning.py`) и правка
лида (`lead_edit.py`), и ничего не решает сам: состояния и причины — те, что лежат в базе,
фильтр — по тем же кодам. Здесь только чтение.

**Фильтр — по колонке, тем же словом, что в её ячейке** (как у отбора, 26.09.2026):
состояние, причина отказа, гипотеза, поиск по адресу, имени, компании и домену, период
добавления. Сводка — по всем лидам, а не по странице: она отвечает на вопрос «сколько готово
к письмам», и фильтры таблицы её не трогают.

**Период добавления — тем же полуинтервалом, что у воронки** (`funnel.Period`): лид входит,
если строка заведена (`created_at`) не раньше начала и раньше конца. Дни человека в моменты
переводит экран — полночью первого дня и полночью после последнего по часам браузера, как свои
даты воронки: пояса человека сервер не знает.

**Счётчики — полным перечислением, с нулями.** Экран строит фильтр причин по
ключам ответа, а не по своему списку: причина, которой ещё никто не получил,
иначе пропала бы из фильтра, а новая причина очистки появилась бы на экране
без правки фронта. Код причины в фильтре — строкой, как в базе: незнакомый код
из устаревшей ссылки ничего не находит, а не отказывает.

**Страница — номером, размер называет сервер** (`PAGE_SIZE`): второй экземпляр
числа на фронте разошёлся бы при первой правке. Порядок — новые первыми, до
номера записи: на стыке страниц строка не показывается дважды.

**Лид «готов», который ждёт, назван словами** (`elsewhere.waiting_of`): сайт в работе у
другого направления, и письмо ему не собирается, пока сайт не освободится. Без слов он
неотличим от лида, которому письмо просто ещё не собрали.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.domain import DomainModel
from backend.features.sales import elsewhere, verifier
from backend.features.sales.funnel import Period
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
)
from backend.shared.database.text_search import contains

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
    #: Когда лид появился в сервисе (`created_at`): полуинтервал `[since, until)`. Без границ —
    #: всё время.
    added: Period = field(default_factory=Period)
    #: Страница — с единицы. Страница за концом — пустая, с настоящим `total`.
    page: int = 1
    size: int = PAGE_SIZE


@dataclass(frozen=True, slots=True)
class LeadRow:
    lead: SalesLeadModel
    #: Домен компании и имя гипотезы — словами, а не номерами: их читают в строке.
    host: str
    hypothesis: str
    #: Почему лид «готов» ждёт письма: сайт в работе у другого направления (`elsewhere.py`).
    elsewhere: str | None = None


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
    #: Лидов «готов», которых очистка возьмёт снова: адрес проверял выдуманный проверяльщик, а
    #: задан живой (`verifier.recheck`). При выдуманном — ноль.
    recheck: int = 0
    #: Лидов, которых возьмёт очистка, — тем же условием, что у прохода (`verifier.due`). Не
    #: «новые» плюс `recheck`: новый лид с первым `unknown` ждёт дня перепроверки.
    waiting: int = 0

    @property
    def total(self) -> int:
        return sum(self.states.values())


def _by_search(text: str) -> ColumnElement[bool]:
    """Набранное — текст, а не шаблон (`text_search`): «_» и «%» находили всех лидов."""
    return or_(
        contains(SalesLeadModel.email, text),
        contains(SalesLeadModel.name, text),
        contains(SalesLeadModel.company, text),
        contains(DomainModel.host, text),
    )


def _added(period: Period) -> list[ColumnElement[bool]]:
    """Лид заведён в период добавления; без границ — условия нет вовсе."""
    if period.since is None and period.until is None:
        return []
    return [period.holds(SalesLeadModel.created_at)]


def _conditions(filters: LeadFilters) -> list[ColumnElement[bool]]:
    """Условия фильтров под колонками — все сразу, «и»."""
    found: list[ColumnElement[bool]] = _added(filters.added)
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
    found = rows.tuples().all()
    waits = await elsewhere.waiting_of(session, [lead for lead, _, _ in found])
    return LeadsPage(
        rows=[LeadRow(lead, host, name, waits.get(lead.id)) for lead, host, name in found],
        total=int(total or 0),
        states=await _states(session),
        reasons=await _reasons(session),
    )


async def lead(session: AsyncSession, lead_id: int) -> LeadRow | None:
    """Лид строкой экрана — тем же чтением, что страница: после правки строка та же."""
    found = (await session.execute(_base().where(SalesLeadModel.id == lead_id))).first()
    if found is None:
        return None
    lead, host, hypothesis = found._tuple()
    waits = await elsewhere.waiting_of(session, [lead])
    return LeadRow(lead, host, hypothesis, waits.get(lead.id))


async def hypotheses(
    session: AsyncSession, loaded: Period | None = None, *, now: datetime | None = None
) -> list[HypothesisRow]:
    """Все гипотезы, старшие первыми, и сколько у каждой лидов в каждом состоянии.

    `loaded` — только лиды, заведённые в период: так «контакты» недельного отчёта
    (`weekly.py`) считает та же функция, что числа экрана гипотез, а не своя копия."""
    found = await session.scalars(select(SalesHypothesisModel).order_by(SalesHypothesisModel.id))
    query = select(SalesLeadModel.hypothesis_id, SalesLeadModel.status, func.count()).group_by(
        SalesLeadModel.hypothesis_id, SalesLeadModel.status
    )
    if loaded is not None:
        query = query.where(loaded.holds(SalesLeadModel.created_at))
    counted = await session.execute(query)
    by_hypothesis: dict[int, dict[str, int]] = {}
    for hypothesis_id, status, n in counted.all():
        by_hypothesis.setdefault(int(hypothesis_id), {})[LeadStatus(status).value] = int(n)
    again = await _recheck(session)
    waiting = await _waiting(session, now or datetime.now(UTC))
    return [
        HypothesisRow(
            hypothesis,
            _complete(by_hypothesis.get(hypothesis.id, {}), (s.value for s in LeadStatus)),
            again.get(hypothesis.id, 0),
            waiting.get(hypothesis.id, 0),
        )
        for hypothesis in found.all()
    ]


async def _waiting(session: AsyncSession, now: datetime) -> dict[int, int]:
    """Гипотеза → сколько её лидов возьмёт очистка (`verifier.due`) — число у кнопки «Очистить»."""
    rows = await session.execute(
        select(SalesLeadModel.hypothesis_id, func.count())
        .where(verifier.due(verifier.live(), now))
        .group_by(SalesLeadModel.hypothesis_id)
    )
    return {int(hypothesis_id): int(n) for hypothesis_id, n in rows.all()}


async def _recheck(session: AsyncSession) -> dict[int, int]:
    """Гипотеза → сколько её лидов «готов» очистка проверит заново (`verifier.recheck`)."""
    if not verifier.live():
        return {}
    rows = await session.execute(
        select(SalesLeadModel.hypothesis_id, func.count())
        .where(verifier.recheck(real=True))
        .group_by(SalesLeadModel.hypothesis_id)
    )
    return {int(hypothesis_id): int(n) for hypothesis_id, n in rows.all()}
