"""Кандидаты в рекламодатели: что уходит на экран."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel


class CandidateCard(BaseModel):
    """Один кандидат: балл, вердикт и всё, по чему человек решает.

    Причины приходят строками, а не кодами: человек читает их глазами,
    и превращать «ссылки с 20 страниц донора +3» в ключ ради
    аккуратности значит переводить их обратно на экране.
    """

    id: int
    donor_host: str
    target_root: str
    points: int
    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)
    links: int
    pages: int
    #: Страница и анкор, под которые будет написано письмо.
    best_page_url: str | None = None
    best_anchor: str | None = None
    confirmed: bool | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None

    @classmethod
    def of(cls, row: CandidateModel) -> CandidateCard:
        return cls(
            id=row.id,
            donor_host=row.donor_host,
            target_root=row.target_root,
            points=row.points,
            verdict=row.verdict,
            reasons=[str(reason) for reason in row.reasons or []],
            links=row.links,
            pages=row.pages,
            best_page_url=row.best_page_url,
            best_anchor=row.best_anchor,
            confirmed=row.confirmed,
            decided_by=row.decided_by,
            decided_at=row.decided_at,
        )


class CandidatesView(BaseModel):
    """Очередь ручной проверки и что вокруг неё.

    Счётчики по всем вердиктам, а не только по спорным: по отсеянным
    видно, что список «кому не пишем» работает, а не молчит.
    """

    rows: list[CandidateCard]
    waiting: int
    counts: dict[str, int] = Field(default_factory=dict)


class DecisionBody(BaseModel):
    """Решение человека по кандидату."""

    #: Это рекламодатель — пишем ему.
    confirmed: bool
    #: Передумать можно, но явно: без этого повторное решение — отказ,
    #: иначе два человека в одной очереди затрут друг друга.
    force: bool = False
