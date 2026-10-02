"""Заведение гипотезы продаж.

Гипотеза — кому и зачем пишем. Стартовые заводятся данными через команду,
а не миграцией: описание гипотезы — коммерческий текст, а строка в миграции
уехала бы в публичную историю навсегда.

Имя — ключ, по которому гипотезу выбирают при загрузке базы и в отчётах:
пробелы по краям и двойные внутри не делают имя новым. Повтор отказывает
словами до обращения к уникальности базы — она остаётся последним рубежом
на случай двух одновременных команд.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales.models import NAME_LENGTH, SalesHypothesisModel

logger = logging.getLogger(__name__)


class HypothesisError(ValueError):
    """Гипотеза не заведена. Сообщение называет причину."""


class BadNameError(HypothesisError):
    """Имени нет или оно длиннее колонки — гипотезу не по чему выбрать."""


class NameTakenError(HypothesisError):
    """Гипотеза с таким именем уже есть."""


async def add(session: AsyncSession, name: str, description: str | None) -> SalesHypothesisModel:
    """Завести гипотезу. Запись — в транзакции вызывающего, коммит — за ним."""
    clean = " ".join(name.split())
    if not clean:
        raise BadNameError(
            f"нет имени (передано {name!r}) — по нему гипотезу выбирают при загрузке базы"
        )
    if len(clean) > NAME_LENGTH:
        raise BadNameError(f"имя длиннее {NAME_LENGTH} знаков ({len(clean)}) — нужно короткое")
    taken = await session.scalar(
        select(SalesHypothesisModel.id).where(SalesHypothesisModel.name == clean)
    )
    if taken is not None:
        raise NameTakenError(f"имя «{clean}» уже у гипотезы №{taken}")
    hypothesis = SalesHypothesisModel(name=clean, description=(description or "").strip() or None)
    session.add(hypothesis)
    await session.flush()
    logger.info("продажи: заведена гипотеза", extra={"hypothesis_id": hypothesis.id})
    return hypothesis
