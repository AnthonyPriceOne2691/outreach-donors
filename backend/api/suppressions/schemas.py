"""Стоп-лист: что уходит на экран и что приходит с него."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from backend.features.core.domain import Stage, SuppressionReason
from backend.features.letters.stoplist import AddedRow, StopRow


class StopEntry(BaseModel):
    """Строка списка. Домен или адрес — ровно одно из двух."""

    id: int
    host: str | None
    email: str | None
    reason: SuppressionReason
    stage: Stage | None
    created_by: str | None
    created_at: datetime
    #: Докуда запись держит. Пусто — навсегда.
    expires_at: datetime | None
    #: Срок вышел: запись видна, но письма не держит. Считает сервер —
    #: сравнение дат на фронте шло бы по часам браузера.
    expired: bool
    #: Решение адресата, а не наше: снимается только с причиной.
    #: Считает сервер — второй экземпляр правила на фронте разошёлся бы
    #: с настоящим на первой новой причине.
    donor_decision: bool

    @classmethod
    def of(cls, row: StopRow, *, now: datetime | None = None) -> StopEntry:
        return cls(
            id=row.id,
            host=row.host,
            email=row.email,
            reason=row.reason,
            stage=row.stage,
            created_by=row.created_by,
            created_at=row.created_at,
            expires_at=row.expires_at,
            expired=row.expired(now or datetime.now(UTC)),
            donor_decision=row.donor_decision,
        )


class AddedEntry(StopEntry):
    """Заведённая запись — и легла ли она на домен из базы."""

    #: Домена в базе не было, запись завела его новым: донора с ним нет.
    #: Экран говорит это словами, а не «письма сняты с очереди» — иначе
    #: опечатка в домене выглядит как закрытый донор (проверка QA 10.10.2026).
    new_domain: bool

    @classmethod
    def added(cls, row: AddedRow) -> AddedEntry:
        return cls(**StopEntry.of(row).model_dump(), new_domain=row.new_domain)


class StopListView(BaseModel):
    """Весь список и его состав по причинам — числа для шапки экрана."""

    rows: list[StopEntry]
    total: int
    donor_decisions: int
    #: Сколько строк уже не держат. Число в шапке, потому что пустой
    #: стоп-лист и стоп-лист из одних истёкших записей — разные новости.
    expired: int = 0


class AddBody(BaseModel):
    """Завести запись руками."""

    #: Домен, ссылка на сайт или адрес. Пределов длины у поля нет намеренно:
    #: прежние 3–253 отказывали по-английски («String should have at most
    #: 253 characters») и не пускали ссылку с длинным путём, из которой нужен
    #: один домен. Пределы домена, адреса и вписанного знает стоп-лист
    #: (`letters/stoplist.py`) — и отказывает словами (проверка QA 10.10.2026).
    target: str
    reason: SuppressionReason
    #: Пусто — запрет действует на обоих этапах.
    stage: Stage | None = None
    #: Пусто — навсегда. Дата в прошлом отвергается сервисом:
    #: запись, которая ничего не держит, выглядит как защита.
    expires_at: datetime | None = None


class RemoveBody(BaseModel):
    """Снять запись. Причина обязательна для решения адресата."""

    reason: str | None = Field(default=None, max_length=500)
