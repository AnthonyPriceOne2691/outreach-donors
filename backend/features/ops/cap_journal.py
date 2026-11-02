"""Смена лимита в журнале действий — одна запись у экрана «Расход» и панели лимитов.

Сеттеры (`runs/ahrefs_cap.py`, `core/llm_caps.py`, `core/hunter_cap.py`) заводят строку истории и не
знают о журнале: смена и запись журнала ложатся одной транзакцией вызывающего, а вызывающих теперь
двое — маршрут карточки лимита и смена с панели. Две копии записи разошлись бы в «было → стало»,
и по журналу смена с панели выглядела бы другой, чем та же смена с карточки.

Без фиксации, как и сеттеры.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.features.access.repository import AccessRepository
from backend.features.core import hunter_cap, llm_caps
from backend.features.core.domain import AuditAction, Stage
from backend.features.runs.ahrefs_cap import CapSet, cap_words

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.core.models.access import UserModel


async def ahrefs_cap(session: AsyncSession, done: CapSet, author: UserModel) -> None:
    """Смена капа Ahrefs: «было → стало» и размер ключа на минуту смены."""
    details: dict[str, object] = {
        "кап Ahrefs": {"было": cap_words(done.was), "стало": cap_words(done.change.units_cap)}
    }
    if done.size is not None:
        details["лимит ключа"] = done.size
    await AccessRepository(session).record(
        AuditAction.AHREFS_CAP_CHANGED,
        author_id=author.id,
        target=f"ahrefs_cap:{done.change.id}",
        details=details,
    )


async def llm_block(session: AsyncSession, done: llm_caps.LlmCapSet, author: UserModel) -> None:
    """Смена блока потолков модели: «было → стало» всем блоком, и общий, и лимиты."""
    await AccessRepository(session).record(
        AuditAction.LLM_CAP_CHANGED,
        author_id=author.id,
        target=f"llm_caps:{done.change.id}",
        details={
            "потолки модели": {
                "было": llm_caps.block_words(done.was),
                "стало": llm_caps.block_words(done.change.block),
            }
        },
    )


async def hunter_verify_cap(
    session: AsyncSession, done: hunter_cap.HunterCapSet, author: UserModel
) -> None:
    """Смена потолка проверок адреса: «было → стало». Этап — продажи: это их расход."""
    await AccessRepository(session).record(
        AuditAction.HUNTER_CAP_CHANGED,
        author_id=author.id,
        target=f"hunter_cap:{done.change.id}",
        details={
            "потолок проверок адреса": {
                "было": hunter_cap.cap_words(done.was),
                "стало": hunter_cap.cap_words(done.change.verify_cap),
            }
        },
        stage=Stage.SALES,
    )
