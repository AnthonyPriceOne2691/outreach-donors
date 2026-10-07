"""Цепочка писем продаж: первое письмо и две добивки на двух языках — данными в базе.

**Текстов в коде нет.** Репозиторий публичный: шаблоны — строки `sales_chain_templates`,
наполнение — файлом вне репозитория (`chain_load.py`) или на экране. Пустая цепочка —
не «шаблон по умолчанию», а отказ словами (`Chain.check_ready`), и экран говорит
«цепочка не задана». Правила текста шаблона — `chain_text.py`.

**Набор и гипотеза.** Шаблон без гипотезы — общий набор, с гипотезой — её свой. Цепочка
гипотезы на языке — своя целиком, если у гипотезы есть хоть один включённый шаг на этом
языке, иначе общая целиком (`resolve`): шаги двух наборов не смешиваются — добивка общего
набора к первому письму гипотезы напоминала бы не о том письме.

**Версия цепочки** — `chain-` и 12 знаков sha256 от отсортированных «шаг, язык, тема,
тело» включённых шагов, как версия базы знаний: правка, вернувшая текст, возвращает и
версию; по ней сборка писем и калибровка узнают, каким текстом написано письмо.

**Запись — здесь, одна для экрана** (`save`): журнал в транзакции правки, с версией
цепочки до и после и прежними значениями правленых полей. Загрузка файлом — `chain_load.py`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction
from backend.features.sales import sender
from backend.features.sales.chain_text import (
    STEP_WORDS,
    STEPS,
    StepTemplate,
    language_code,
    unsigned,
    zones_of,
)
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import SalesChainTemplateModel, SalesHypothesisModel

logger = logging.getLogger(__name__)

VERSION_PREFIX = "chain-"
#: Знаков отпечатка в версии: десятки версий за жизнь цепочки, совпадение исключено.
VERSION_DIGITS = 12
#: Поля шаблона, которые правят; набор, шаг и язык — его ключ.
FIELDS = ("subject", "body", "active")


class ChainNotReadyError(RuntimeError):
    """Цепочка лида неполна: письмо продаж не собирается. Текст — чего нет и где задать."""


def version_of(found: Iterable[StepTemplate | SalesChainTemplateModel]) -> str:
    """Отпечаток включённых шагов: порядок, номера строк и автор правки не влияют."""
    canon = sorted(
        [item.step, item.language, item.subject or "", item.body] for item in found if item.active
    )
    digest = hashlib.sha256(json.dumps(canon, ensure_ascii=False).encode("utf-8")).hexdigest()
    return VERSION_PREFIX + digest[:VERSION_DIGITS]


def set_name(hypothesis_id: int | None) -> str:
    """Набор словами — для журнала и отказов."""
    return "общий набор" if hypothesis_id is None else f"набор гипотезы №{hypothesis_id}"


@dataclass(frozen=True, slots=True)
class Chain:
    """Цепочка лида на языке: включённые шаги одного набора — гипотезы или общего."""

    language: str
    #: Чей набор: номер гипотезы или `None` — общий.
    hypothesis_id: int | None
    steps: Mapping[int, StepTemplate]

    @property
    def version(self) -> str:
        return version_of(self.steps.values())

    @property
    def missing(self) -> list[str]:
        """Каких шагов нет — словами, в порядке цепочки. Пусто — цепочка полна."""
        return [STEP_WORDS[step] for step in STEPS if step not in self.steps]

    def check_ready(self) -> None:
        """Сборка писем спрашивает здесь: неполная цепочка — отказ словами."""
        if self.missing:
            raise ChainNotReadyError(
                f"цепочка писем продаж ({self.language}, {set_name(self.hypothesis_id)}) "
                f"не задана: нет {', '.join(self.missing)} — задайте на экране «Продажи» → "
                "«Цепочка писем» или загрузите файлом: outreach sales-chain-load"
            )


def _of(row: SalesChainTemplateModel) -> StepTemplate:
    zones = zones_of(row.body)
    return StepTemplate(row.step, row.language, row.subject, row.body, zones, row.active)


def _in_set(hypothesis_id: int | None) -> ColumnElement[bool]:
    column = SalesChainTemplateModel.hypothesis_id
    return column.is_(None) if hypothesis_id is None else column == hypothesis_id


async def _active(
    session: AsyncSession, hypothesis_id: int | None, language: str
) -> dict[int, StepTemplate]:
    query = select(SalesChainTemplateModel).where(
        _in_set(hypothesis_id),
        SalesChainTemplateModel.language == language,
        SalesChainTemplateModel.active.is_(True),
    )
    return {row.step: _of(row) for row in await session.scalars(query)}


async def resolve(session: AsyncSession, *, hypothesis_id: int | None, language: str) -> Chain:
    """Цепочка гипотезы на языке: своя целиком, если есть хоть один включённый свой шаг,
    иначе общая целиком. Шаги двух наборов не смешиваются."""
    code = language_code(language)
    if hypothesis_id is not None:
        own = await _active(session, hypothesis_id, code)
        if own:
            return Chain(code, hypothesis_id, own)
    return Chain(code, None, await _active(session, None, code))


async def set_version(session: AsyncSession, hypothesis_id: int | None, language: str) -> str:
    """Версия цепочки набора на языке — её пишет журнал до и после правки."""
    return version_of((await _active(session, hypothesis_id, language)).values())


async def known(session: AsyncSession, hypothesis_id: int | None) -> None:
    """Набор гипотезы — только у заведённой гипотезы."""
    if hypothesis_id is not None and await session.get(SalesHypothesisModel, hypothesis_id) is None:
        raise UnknownHypothesisError(f"гипотезы №{hypothesis_id} нет — обновите список гипотез")


async def rows(session: AsyncSession, hypothesis_id: int | None) -> list[SalesChainTemplateModel]:
    """Все шаблоны набора — включённые и нет: экран показывает оба."""
    query = select(SalesChainTemplateModel).where(_in_set(hypothesis_id))
    order = (SalesChainTemplateModel.step, SalesChainTemplateModel.language)
    return list(await session.scalars(query.order_by(*order)))


async def _row(
    session: AsyncSession, hypothesis_id: int | None, step: int, language: str
) -> SalesChainTemplateModel | None:
    query = select(SalesChainTemplateModel).where(
        _in_set(hypothesis_id),
        SalesChainTemplateModel.step == step,
        SalesChainTemplateModel.language == language,
    )
    return (await session.scalars(query)).first()


def _changed(row: SalesChainTemplateModel | None, new: StepTemplate) -> dict[str, object]:
    """Правленые поля с прежними значениями; новый шаблон — все поля, прежде пустые."""
    if row is None:
        return dict.fromkeys(FIELDS)
    return {name: getattr(row, name) for name in FIELDS if getattr(row, name) != getattr(new, name)}


async def save(
    session: AsyncSession,
    new: StepTemplate,
    *,
    hypothesis_id: int | None,
    author: str,
    author_id: int | None,
) -> SalesChainTemplateModel:
    """Записать шаг набора: завести или поправить. Без изменений — без журнала.

    Запись и журнал — в транзакции вызывающего, коммит — за ним."""
    await known(session, hypothesis_id)
    unsigned(new, await sender.read(session))
    row = await _row(session, hypothesis_id, new.step, new.language)
    was = _changed(row, new)
    if row is not None and not was:
        return row
    before = await set_version(session, hypothesis_id, new.language)
    if row is None:
        row = SalesChainTemplateModel(
            hypothesis_id=hypothesis_id, step=new.step, language=new.language
        )
        session.add(row)
    row.subject, row.body, row.active, row.updated_by = new.subject, new.body, new.active, author
    await session.flush()
    await session.refresh(row)  # время правки ставит база — читаем назад сразу
    after = await set_version(session, hypothesis_id, new.language)
    await AccessRepository(session).record(
        AuditAction.SALES_CHAIN_CHANGED,
        author_id=author_id,
        target=f"sales_chain_template:{row.id}",
        details={
            "шаг": row.step,
            "язык": row.language,
            "набор": set_name(hypothesis_id),
            "поля": list(was),
            "было": was,
            "версия": {"было": before, "стало": after},
        },
    )
    logger.info(
        "продажи: шаблон цепочки изменён",
        extra={"template_id": row.id, "fields": list(was), "version": after},
    )
    return row
