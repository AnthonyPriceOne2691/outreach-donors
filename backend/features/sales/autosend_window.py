"""Окно автоотправки: какие первые письма продаж в очереди проход может взять и какие — сейчас.

Письмо проход берёт в окне получателя — тем же правилом, что у отправки: окно из политики продаж,
пояса лида (`policy.py`); по порядку очереди — номер письма и есть порядок сборки.

**Письмо, которое держит адрес лида, — ожидание, а не отказ** (`unconfirmed.first_letter`: адрес не
подтверждён проверкой — `unknown` — или проверен тестовым проверяльщиком при живом). Отправка ему
откажет, а пересборка не поможет: лида решит очистка, проверив адрес снова. Бери проход такие
письма, окно из одних них давало бы «отказ каждому письму окна» и через 15 минут — тревогу о
письмах, которые ждут по делу. Поэтому в окно они не идут, а считаются отдельно (`Waiting.held`):
очередь из одних них проход называет словами, что они ждут проверки адреса (`held`). Правило — то
же, что у счёта очереди автосборки (`autobuild.queued`, `Queued.held`): одно у прохода, экрана и
автосборки.

**Прогнозу рассылки** (мост `core/stage_forecast.py`) — пояса очереди (`queue_zones`: тем же правилом
пояса лида, что у прохода, группой по поясу и стране лида) и письма, которые не уйдут, пока их не
поправят (`queue_stuck`: тем же счётом, что у прохода, — адрес держит проверка, пояса нет).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import window
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters.chain import FIRST_STEP
from backend.features.sales.models import SalesLeadModel, SalesThreadModel
from backend.features.sales.policy import sales_policy, zones_of
from backend.features.sales.unconfirmed import first_letter

#: Очередь — из одних писем, которые держит адрес лида: слова прохода, журнала и экрана.
HELD = (
    "письма очереди ждут проверки адреса ({}): адрес их лидов не подтверждён проверкой (unknown) "
    "или проверен тестовым проверяльщиком; пересборка не поможет — очистка проверит адрес снова"
)


@dataclass(frozen=True, slots=True)
class Waiting:
    """Первые письма продаж в очереди: сколько проход может взять и какие из них в окне сейчас."""

    #: Сколько писем проход может взять — без тех, что держит адрес лида (`held`).
    queued: int
    #: Письма в окне получателя — по порядку очереди.
    ids: tuple[int, ...]
    #: Первое из них — им спрашивается выбор ящика (`autosend.facts`).
    first: MessageModel | None = None
    #: Без пояса получателя — окна не посчитать, письмо ждёт (`window.NO_ZONE`).
    zoneless: int = 0
    #: Письма, которые держит адрес лида (`unconfirmed.first_letter`): ждут очистки, не в окне.
    held: int = 0


async def in_window(session: AsyncSession, now: datetime) -> Waiting:
    """Первые письма продаж в очереди и те из них, у чьих получателей сейчас окно, — тем же
    правилом, что у отправки: окно политики продаж, пояса лида. Письма, которые держит адрес
    лида, — только счётом (`Waiting.held`)."""
    rows = await session.execute(_queued(MessageModel, SalesLeadModel).order_by(MessageModel.id))
    frame = sales_policy().window
    found: list[MessageModel] = []
    queued = zoneless = held = 0
    for letter, lead in rows.tuples().all():
        if first_letter(lead) is not None:
            held += 1
            continue
        queued += 1
        late = window.check(frame, zones_of(lead), now)
        if late is None:
            found.append(letter)
        elif late.delay is None:
            zoneless += 1
    first = found[0] if found else None
    return Waiting(queued, tuple(letter.id for letter in found), first, zoneless, held)


def _queued(*columns: Any) -> Select[Any]:
    """Первые письма продаж в очереди с их лидами — одно условие у прохода и у поясов очереди."""
    return (
        select(*columns)
        .select_from(MessageModel)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(
            CampaignModel.stage == Stage.SALES,
            MessageModel.status == MessageStatus.QUEUED,
            MessageModel.step == FIRST_STEP,
        )
    )


@dataclass(frozen=True, slots=True)
class _Where:
    """Где получатель — страна и пояс лида (`policy.Located`)."""

    country: str | None
    timezone: str | None


async def queue_zones(session: AsyncSession) -> dict[str | None, int]:
    """Пояса получателей первых писем продаж в очереди → писем: пояс — первый известный (лида, его
    страны — `policy.zones_of`, `window.zone_of`), как у прохода; `None` — неизвестен."""
    lead = SalesLeadModel
    rows = await session.execute(
        _queued(lead.timezone, lead.country, func.count()).group_by(lead.timezone, lead.country)
    )
    found: Counter[str | None] = Counter()
    for timezone, country, letters in rows.tuples().all():
        zone = window.zone_of(zones_of(_Where(country, timezone)))
        found[None if zone is None else zone.key] += letters
    return dict(found)


async def queue_stuck(session: AsyncSession) -> dict[str, int]:
    """Первые письма продаж в очереди, которые не уйдут, пока что-то не поправят, — счётом прохода
    (`in_window`): адрес лида держит проверка (`held`) или пояса получателя не знает никто
    (`window.NO_ZONE`). Слова причины → писем; причин без писем нет."""
    waiting = await in_window(session, datetime.now(UTC))
    found = {held(waiting): waiting.held, window.NO_ZONE: waiting.zoneless}
    return {words: letters for words, letters in found.items() if letters}


def outside(waiting: Waiting) -> str:
    """Писем в окне нет — словами: сколько их и у скольких пояс не известен."""
    zoneless = (
        f", у {waiting.zoneless} из них пояс получателя неизвестен — окна не посчитать"
        if waiting.zoneless
        else ""
    )
    return f"вне окна получателя у всех писем очереди ({waiting.queued}){zoneless}"


def held(waiting: Waiting) -> str:
    """Очередь — из одних писем, которые держит адрес лида, — словами."""
    return HELD.format(waiting.held)
