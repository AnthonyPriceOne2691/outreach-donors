"""База знаний и отправитель продаж: что приходит с экрана и что уходит на него.

Тела запросов — поля формы как есть: приводит и проверяет ядро (`kb.entry`,
`sender.cleaned`), его отказ — словами. Здесь только типы и запрет лишних полей:
опечатка в имени поля не должна молча ничего не менять.

Границы полей называет сервер (`limits`): экран проверяет ими поле до нажатия,
и второго экземпляра числа на фронте нет.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.features.sales import kb, sender
from backend.features.sales.models import TAG_LENGTH, TITLE_LENGTH, KbKind, SalesKbEntryModel

#: Границы записи для экрана — те же числа, которыми отказывает ядро.
KB_LIMITS = {"title": TITLE_LENGTH, "text": kb.TEXT_LENGTH, "tag": TAG_LENGTH, "tags": kb.MAX_TAGS}


class KbEntryBody(BaseModel):
    """Новая запись. Пробелы, регистр языка и тегов приводит ядро."""

    model_config = ConfigDict(extra="forbid")

    kind: KbKind
    language: str
    title: str
    text: str
    tags: list[str] = []
    active: bool = True


class KbEntryPatch(BaseModel):
    """Правка записи: только присланные поля. `active` — включить или выключить."""

    model_config = ConfigDict(extra="forbid")

    kind: KbKind | None = None
    language: str | None = None
    title: str | None = None
    text: str | None = None
    tags: list[str] | None = None
    active: bool | None = None


class KbEntryCard(BaseModel):
    """Запись базы знаний: что в ней, включена ли, кто и когда правил."""

    id: int
    kind: KbKind
    language: str
    title: str
    text: str
    tags: list[str]
    active: bool
    updated_by: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, row: SalesKbEntryModel) -> KbEntryCard:
        return cls(
            id=row.id,
            kind=row.kind,
            language=row.language,
            title=row.title,
            text=row.text,
            tags=list(row.tags),
            active=row.active,
            updated_by=row.updated_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class KbView(BaseModel):
    """Все записи — включённые и нет — и версия базы, которую сейчас видит агент."""

    rows: list[KbEntryCard]
    total: int
    #: Сколько записей включено — их видит агент.
    active: int
    version: str
    #: Виды в порядке набора: по ним экран строит выбор вида и группы.
    kinds: list[KbKind]
    limits: dict[str, int]

    @classmethod
    def of(cls, rows: list[SalesKbEntryModel], *, version: str) -> KbView:
        return cls(
            rows=[KbEntryCard.of(row) for row in rows],
            total=len(rows),
            active=sum(row.active for row in rows),
            version=version,
            kinds=list(KbKind),
            limits=KB_LIMITS,
        )


class FactCard(BaseModel):
    id: int
    title: str
    text: str
    tags: list[str]


class FactGroup(BaseModel):
    """Факты одного вида на одном языке."""

    kind: KbKind
    language: str
    facts: list[FactCard]


class AgentView(BaseModel):
    """Что увидит агент: только включённые записи, группами по виду и языку."""

    version: str
    total: int
    groups: list[FactGroup]

    @classmethod
    def of(cls, found: list[kb.Fact]) -> AgentView:
        groups = [
            FactGroup(
                kind=group.kind,
                language=group.language,
                facts=[
                    FactCard(id=f.id, title=f.title, text=f.text, tags=list(f.tags))
                    for f in group.facts
                ],
            )
            for group in kb.grouped(found)
        ]
        return cls(version=kb.version_of(found), total=len(found), groups=groups)


class SenderBody(BaseModel):
    """Настройки отправителя целиком: чего нет в теле — «не задано»."""

    model_config = ConfigDict(extra="forbid")

    sender_name: str | None = None
    sender_position: str | None = None
    signature: str | None = None
    website: str | None = None
    telegram: str | None = None
    physical_address: str | None = None
    call_link: str | None = None


class SenderView(SenderBody):
    """Настройки, кто и когда их правил и чего не хватает для отправки продаж."""

    model_config = ConfigDict(extra="ignore")

    updated_by: str | None
    updated_at: datetime | None
    #: Чего не хватает — теми же словами, какими откажет отправка (Ф4).
    missing: list[str]
    #: Предел длины каждого поля — экран проверяет им поле до нажатия.
    limits: dict[str, int]

    @classmethod
    def of(cls, found: sender.Sender) -> SenderView:
        return cls(
            **found.values,
            updated_by=found.updated_by,
            updated_at=found.updated_at,
            missing=found.missing,
            limits={name: limit for name, (_, limit) in sender.FIELDS.items()},
        )
