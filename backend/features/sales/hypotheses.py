"""Заведение гипотезы продаж.

Гипотеза — кому и зачем пишем. Заводится данными — командой консоли или формой
«Новая гипотеза» на экране продаж (`POST /sales/hypotheses`), одним правилом здесь, —
а не миграцией: описание гипотезы — коммерческий текст, а строка в миграции уехала
бы в публичную историю навсегда.

Имя — ключ, по которому гипотезу выбирают при загрузке базы и в отчётах:
пробелы по краям и двойные внутри не делают имя новым. Повтор отказывает
словами до обращения к уникальности базы — она остаётся последним рубежом
на случай двух одновременных команд.

**Заведение — в журнале действий** (решение владельца 13.10): кто завёл, имя и описание
сегмента — той же транзакцией, с экрана и из консоли (там автора нет). Описание — вопрос
судье сбора базы: по нему решается, каким сайтам продажи напишут. Своего действия журнала
у гипотезы нет — новое значение перечисления в базе требует миграции, — поэтому запись
идёт общей «правкой» (`USER_UPDATED`): что случилось — словами «действия», цель —
`sales_hypothesis:N`, этап — продажи (без права «Продажи» записи не видно).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Stage
from backend.features.sales.models import NAME_LENGTH, SalesHypothesisModel

logger = logging.getLogger(__name__)

#: «Действие» записи журнала: под общей «Правкой» экран показывает его строкой названия.
MADE = "гипотеза продаж заведена"


class HypothesisError(ValueError):
    """Гипотеза не заведена. Сообщение называет причину."""


class BadNameError(HypothesisError):
    """Имени нет или оно длиннее колонки — гипотезу не по чему выбрать."""


class NameTakenError(HypothesisError):
    """Гипотеза с таким именем уже есть."""


def _clean(name: str) -> str:
    return " ".join(name.split())


async def find(session: AsyncSession, name: str) -> SalesHypothesisModel | None:
    """Гипотеза по имени — так её выбирают при загрузке базы."""
    rows = await session.scalars(
        select(SalesHypothesisModel).where(SalesHypothesisModel.name == _clean(name))
    )
    return rows.first()


async def add(
    session: AsyncSession, name: str, description: str | None, *, author_id: int | None = None
) -> SalesHypothesisModel:
    """Завести гипотезу. Запись и строка журнала — в транзакции вызывающего, коммит — за ним.
    `author_id` — учётка экрана; у команды консоли автора нет."""
    clean = _clean(name)
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
    # Время заведения ставит база: без чтения назад поле не загружено, и экран в async
    # читал бы его ленивым запросом и падал (как у записи базы знаний, `kb._flush`).
    await session.refresh(hypothesis)
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author_id,
        target=f"sales_hypothesis:{hypothesis.id}",
        details={
            "действие": MADE,
            "имя": hypothesis.name,
            "описание сегмента": hypothesis.description,
        },
        stage=Stage.SALES,
    )
    logger.info("продажи: заведена гипотеза", extra={"hypothesis_id": hypothesis.id})
    return hypothesis
