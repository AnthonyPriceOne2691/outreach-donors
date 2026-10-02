"""Загрузка базы продаж: что уходит на экран мастера загрузки."""

from __future__ import annotations

from pydantic import BaseModel

from backend.features.sales.columns import LeadField
from backend.features.sales.intake import Lead, Preview, Problem


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
