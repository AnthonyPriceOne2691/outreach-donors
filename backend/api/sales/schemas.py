"""Раздел «Продажи»: что уходит на экран — мастеру загрузки, вкладкам гипотез и лидов, —
и что приходит из окна «Новая гипотеза»."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.features.sales.browse import HypothesisRow, LeadRow, LeadsPage
from backend.features.sales.columns import LeadField
from backend.features.sales.intake import Lead, Preview, Problem
from backend.features.sales.models import LeadSource, LeadStatus


class IntakeView(BaseModel):
    """Предпросмотр или итог загрузки. `loaded` пуст — в базу ничего не записано."""

    source: str
    header: bool
    #: Имена колонок или «колонка N», если заголовка нет.
    columns: list[str]
    #: Первые строки данных — по ним человек сопоставляет колонки руками.
    sample: list[list[str]]
    mapping: dict[LeadField, int]
    #: Колонка почты не найдена: лиды не считались, нужно сопоставить руками.
    needs_mapping: bool
    rows: int
    accepted: int
    rejected: int
    #: Первые лиды — что получится; сколько всего — `accepted`.
    leads: list[Lead]
    #: Отчёт целиком: каждая отклонённая строка и каждое замечание.
    problems: list[Problem]
    loaded: int | None = None

    @classmethod
    def of(cls, found: Preview, *, shown: int, loaded: int | None = None) -> IntakeView:
        return cls(
            source=found.source,
            header=found.header,
            columns=found.columns,
            sample=found.sample,
            mapping=found.mapping,
            needs_mapping=found.needs_mapping,
            rows=found.rows,
            accepted=len(found.leads),
            rejected=found.rejected,
            leads=found.leads[:shown],
            problems=found.problems,
            loaded=loaded,
        )


class HypothesisBody(BaseModel):
    """Новая гипотеза из окна «Новая гипотеза»: имя и, если есть, описание словами.

    Пробелы и пустоту имени судит ядро (`hypotheses.add`) — его отказ словами, а не схема;
    лишнее поле — отказ схемы: опечатка в имени поля не должна молча ничего не менять."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None


class HypothesisCard(BaseModel):
    """Гипотеза и сколько у неё лидов в каждом состоянии: по ней выбирают,
    куда грузить базу и откуда брать готовых к письмам."""

    id: int
    name: str
    description: str | None
    created_at: datetime
    #: Состояние → сколько. Все состояния названы, хоть и нулём.
    leads: dict[str, int]
    total: int

    @classmethod
    def of(cls, row: HypothesisRow) -> HypothesisCard:
        found = row.hypothesis
        return cls(
            id=found.id,
            name=found.name,
            description=found.description,
            created_at=found.created_at,
            leads=row.states,
            total=row.total,
        )


class HypothesesView(BaseModel):
    rows: list[HypothesisCard]
    total: int


class LeadCard(BaseModel):
    """Строка лида: кто он, где работает, куда попал после очистки и почему —
    кодом (`rejection_reason`, по нему фильтр) и словами (`cleaning_note`)."""

    id: int
    email: str
    name: str | None
    position: str | None
    company: str | None
    #: Домен компании — слово, а не `domain_id`: его читают в строке.
    host: str
    country: str | None
    timezone: str | None
    language: str | None
    hypothesis_id: int
    hypothesis: str
    source: LeadSource
    status: LeadStatus
    rejection_reason: str | None
    cleaning_note: str | None
    verification_status: str | None
    created_at: datetime

    @classmethod
    def of(cls, row: LeadRow) -> LeadCard:
        lead = row.lead
        return cls(
            id=lead.id,
            email=lead.email,
            name=lead.name,
            position=lead.position,
            company=lead.company,
            host=row.host,
            country=lead.country,
            timezone=lead.timezone,
            language=lead.language,
            hypothesis_id=lead.hypothesis_id,
            hypothesis=row.hypothesis,
            source=lead.source,
            status=lead.status,
            rejection_reason=lead.rejection_reason,
            cleaning_note=lead.cleaning_note,
            verification_status=lead.verification_status,
            created_at=lead.created_at,
        )


class LeadsView(BaseModel):
    rows: list[LeadCard]
    total: int
    #: Какая это страница и сколько на ней лидов: размер страницы знает сервер.
    page: int
    limit: int
    #: Сводка по всем лидам, не по фильтру: состояние → сколько, причина → сколько.
    #: Ключи — полный перечень кодов: по ним экран строит плитки и фильтры.
    states: dict[str, int]
    reasons: dict[str, int]

    @classmethod
    def of(cls, page: LeadsPage, *, page_number: int, limit: int) -> LeadsView:
        return cls(
            rows=[LeadCard.of(row) for row in page.rows],
            total=page.total,
            page=page_number,
            limit=limit,
            states=page.states,
            reasons=page.reasons,
        )
