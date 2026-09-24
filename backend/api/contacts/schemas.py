"""Поиск контактов и ручная очередь форм: что уходит на экран."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.api.jobs.routes import JobCard
from backend.features.contacts.forms import FormRow


class ContactsState(BaseModel):
    """Состояние поиска контактов.

    Отдельной строки в базе у задачи нет: она одна на сервис и не
    оставляет после себя ничего, кроме контактов у доноров. Поэтому
    состояние собирается из трёх мест — сколько ждёт в базе, что
    говорит очередь и что вернула последняя задача.
    """

    #: Доноров, которым контакт ещё нужен.
    pending: int
    running: bool
    job_id: str | None = None
    #: Отчёт последней законченной задачи: по ступеням, как в консоли.
    last: dict[str, object] | None = None
    #: Исход последней задачи целиком — в том числе упавшей или ждущей
    #: повтора. Раньше видно было только удачную, и упавший поиск выглядел
    #: как тишина.
    job: JobCard | None = None
    #: Сколько воркеров слушает очередь. `null` — очередь не ответила.
    workers: int | None = None


class ContactsQueued(BaseModel):
    """Поиск поставлен в очередь."""

    job_id: str
    pending: int


class SearchBody(BaseModel):
    """Чего хотим от поиска."""

    limit: int = Field(default=100, ge=1, le=1000)
    #: Ступень браузера: секунды на страницу, включается отдельно.
    use_browser: bool = False


class FormCard(BaseModel):
    """Донор из ручной очереди: у него есть форма и нет адреса."""

    donor_id: int
    domain_id: int
    host: str
    dr: int | None
    org_traffic: int | None
    attempted_at: datetime | None

    @classmethod
    def of(cls, row: FormRow) -> FormCard:
        return cls(
            donor_id=row.donor_id,
            domain_id=row.domain_id,
            host=row.host,
            dr=row.dr,
            org_traffic=row.org_traffic,
            attempted_at=row.attempted_at,
        )


class FormsView(BaseModel):
    """Ручная очередь целиком и остаток месячного потолка."""

    rows: list[FormCard]
    total: int
    #: Сколько форм ещё готовы заполнить руками в этом месяце.
    monthly_left: int
    monthly_cap: int


class FilledBody(BaseModel):
    """Форму заполнили, и донор дал адрес."""

    email: str = Field(min_length=5, max_length=255)


class GiveUpBody(BaseModel):
    """Форму заполнить не вышло. Причина — для журнала."""

    reason: str | None = Field(default=None, max_length=200)
