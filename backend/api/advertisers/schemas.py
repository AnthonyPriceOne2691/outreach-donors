"""Кандидаты в рекламодатели: что уходит на экран."""

from __future__ import annotations

from datetime import date, datetime

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
    #: Когда вышла эта статья: свежее размещение — живое, давнее — забытое.
    best_published: date | None = None
    confirmed: bool | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None

    @classmethod
    def of(cls, row: CandidateModel, *, published: date | None = None) -> CandidateCard:
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
            best_published=published,
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


class PromotionView(BaseModel):
    """Над кнопкой «Перевести»: сколько переводить и сколько уже заведено."""

    #: Домены среди «куплено» и «пишем» — их и переводит кнопка.
    ready: int
    #: Из них среди рекламодателей ещё нет.
    fresh: int
    advertisers: int
    with_address: int


class PromoteResult(BaseModel):
    """Чем кончился перевод — числами по причинам, как в консоли."""

    report: dict[str, int]
    #: Заведённые впервые — по ним человек узнаёт, что кнопка сработала.
    fresh: list[str] = Field(default_factory=list)
    #: Сколько рекламодателей ждёт адреса после перевода.
    pending: int
    #: Поиск адресов, поставленный самим переводом; `null` — искать некому.
    contacts_job_id: str | None = None
