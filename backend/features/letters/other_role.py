"""Сайту писали в другой роли — одно условие и одни слова на перезапрос цены и карточку донора.

Одному адресату не пишут и как донору, и как рекламодателю, и как лиду продаж
(`recipients._not_written`, `docs/OUTREACH_THREADS.md`). Карточка донора объясняла такой блок
словами доноров — «донору уже писали», «донор ответил» — о письме, которое ему не уходило, и о
разговоре, которого в его диалогах нет (ревью стыков 12.10.2026). Теперь у неё те же условие и
слова, что у перезапроса цены (`recheck_target`): он ставит перед словами имя сайта, карточка —
нет.

**Письмо продаж — номером и ролью только с правом «Продажи»** (решение Anthony 10.10.2026, П2):
без него — «писали в другой роли», без этапа и номера.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, assert_never

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import attempts


class Letter(Protocol):
    """Письмо в словах отказа: номер и этап (`Written` и письмо фактов перезапроса)."""

    @property
    def id(self) -> int: ...

    @property
    def stage(self) -> Stage: ...


@dataclass(frozen=True, slots=True)
class Written:
    """Письмо сайту в другой роли: номер и этап."""

    id: int
    stage: Stage


def elsewhere() -> tuple[ColumnElement[bool], ...]:
    """Письмо сайту в другой роли — не донору — и живое: дошло, в пути, остановлено человеком или
    на него ответили (`attempts.dead`). Условия — к письмам, соединённым со своей рассылкой."""
    return CampaignModel.stage != Stage.DONORS, ~attempts.dead(MessageModel)


async def letter_to(session: AsyncSession, domain_id: int) -> Written | None:
    """Последнее живое письмо сайту `domain_id` в другой роли. `None` — в другой роли не писали."""
    row = (
        await session.execute(
            select(MessageModel.id, CampaignModel.stage)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .where(MessageModel.domain_id == domain_id, *elsewhere())
            .order_by(MessageModel.id.desc())
            .limit(1)
        )
    ).first()
    return None if row is None else Written(id=row[0], stage=row[1])


def words(letter: Letter, *, sales_visible: bool) -> str:
    """Почему письмо донору не уходит — без имени сайта. Продажи подробно — только с их правом."""
    if letter.stage is Stage.SALES and not sales_visible:
        return "уже писали в другой роли: одному адресату письмо в другой роли не уходит."
    return (
        f"уже писали как {_role(letter.stage)} (письмо №{letter.id}): одному адресату "
        "письмо в другой роли не уходит."
    )


def _role(stage: Stage) -> str:
    match stage:
        case Stage.DONORS:
            return "донору"
        case Stage.ADVERTISERS:
            return "рекламодателю"
        case Stage.SALES:
            return "лиду продаж"
        case _:
            assert_never(stage)
