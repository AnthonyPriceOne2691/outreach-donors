"""Цепочка писем продаж: что приходит с экрана и что уходит на него.

Тело запроса — поля формы как есть: приводит и проверяет ядро (`chain_text.step_template`),
его отказ — словами. Здесь только типы и запрет лишних полей: опечатка в имени поля не
должна молча ничего не менять.

Шаги, языки, подстановки и границы полей называет сервер: экран строит по ним сетку
и проверяет поле до нажатия, и второго экземпляра этих списков на фронте нет.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.features.letters.template import Zone, ZoneKind
from backend.features.sales import chain, chain_text
from backend.features.sales.models import SUBJECT_LENGTH, SalesChainTemplateModel

#: Границы шаблона для экрана — те же числа, которыми отказывает ядро.
CHAIN_LIMITS = {"subject": SUBJECT_LENGTH, "body": chain_text.BODY_LENGTH}


class ChainPreviewBody(BaseModel):
    """Шаблон шага для предпросмотра: ничего не пишет, проверяет теми же правилами."""

    model_config = ConfigDict(extra="forbid")

    step: int
    language: str
    subject: str | None = None
    body: str


class ChainStepBody(ChainPreviewBody):
    """Шаг набора целиком: завести или поправить. Без гипотезы — общий набор."""

    hypothesis_id: int | None = Field(default=None, ge=1)
    active: bool = True


class ZoneCard(BaseModel):
    """Зона письма: имя, что с ней сделает сборка, текст."""

    name: str
    kind: ZoneKind
    text: str

    @classmethod
    def of(cls, zone: Zone) -> ZoneCard:
        return cls(name=zone.name, kind=zone.kind, text=zone.text)


class ChainStepCard(BaseModel):
    """Шаблон шага: чей набор, что в нём, включён ли, кто и когда правил."""

    id: int
    hypothesis_id: int | None
    step: int
    language: str
    subject: str | None
    body: str
    zones: list[ZoneCard]
    active: bool
    updated_by: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, row: SalesChainTemplateModel) -> ChainStepCard:
        return cls(
            id=row.id,
            hypothesis_id=row.hypothesis_id,
            step=row.step,
            language=row.language,
            subject=row.subject,
            body=row.body,
            zones=[ZoneCard.of(zone) for zone in chain_text.zones_of(row.body)],
            active=row.active,
            updated_by=row.updated_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ChainState(BaseModel):
    """Цепочка, которую получит лид набора на языке: чья она, полна ли, версия."""

    language: str
    #: `own` — своя цепочка гипотезы, `common` — общая: смотрим общий набор,
    #: или у гипотезы на этом языке нет ни одного включённого своего шага.
    source: Literal["own", "common"]
    #: Каких шагов нет — словами отказа сборки. Пусто — цепочка полна.
    missing: list[str]
    version: str

    @classmethod
    def of(cls, found: chain.Chain) -> ChainState:
        return cls(
            language=found.language,
            source="common" if found.hypothesis_id is None else "own",
            missing=found.missing,
            version=found.version,
        )


class ChainView(BaseModel):
    """Шаблоны набора — включённые и нет — и цепочки по языкам."""

    #: Чей набор показан: номер гипотезы или `null` — общий.
    hypothesis_id: int | None
    rows: list[ChainStepCard]
    chains: list[ChainState]
    steps: list[int]
    languages: list[str]
    #: Имена подстановок, которые знает шаблон: `{{name}}` и прочие.
    placeholders: list[str]
    limits: dict[str, int]

    @classmethod
    def of(
        cls,
        hypothesis_id: int | None,
        rows: list[SalesChainTemplateModel],
        chains: list[chain.Chain],
    ) -> ChainView:
        return cls(
            hypothesis_id=hypothesis_id,
            rows=[ChainStepCard.of(row) for row in rows],
            chains=[ChainState.of(found) for found in chains],
            steps=list(chain_text.STEPS),
            languages=list(chain_text.LANGUAGES),
            placeholders=list(chain_text.PLACEHOLDERS),
            limits=CHAIN_LIMITS,
        )


class PreviewView(BaseModel):
    """Письмо, как его увидит адресат: тема, зоны с выдуманными значениями, подпись и
    физический адрес из настроек отправителя — в этом порядке их допишет сборка."""

    #: Тема первого письма; у добивки — `null`: тему даёт первое письмо.
    subject: str | None
    zones: list[ZoneCard]
    #: Какие выдуманные значения подставлены.
    values: dict[str, str]
    sender_name: str | None
    signature: str | None
    address: str | None
    #: Чего не хватает для отправки продаж — теми же словами, какими откажет отправка.
    missing: list[str]

    @classmethod
    def of(cls, found: chain_text.Preview) -> PreviewView:
        settings = found.sender.values
        return cls(
            subject=found.subject,
            zones=[ZoneCard.of(zone) for zone in found.zones],
            values=found.values,
            sender_name=settings.get("sender_name"),
            signature=settings.get("signature"),
            address=settings.get("physical_address"),
            missing=found.sender.missing,
        )
