"""Правка лида на экране: имя, должность, компания, язык, страна и пояс.

**Зачем.** Имя лида задавал только файл: лид из «пишите другому» без имени и лид без языка
ждали в очереди всегда, а новая загрузка их не починит — её отсечёт очистка как дубль.

**Правила полей — те же, что у загрузки базы**: язык — основой кода (`lead_values`), страна —
кодом или названием, пояс — именем из базы поясов (`geo`), текст — по ширине колонки
(`intake.TEXT_LENGTH`). Пустое поле — «не задано»: пустой пояс окно берёт по стране
(`policy.zones_of`). Отказ — словами, что поправить, ничего не записав.

**Журнал — как у соседних правок** (база знаний, отправитель): поля, что было и что стало,
автор; без изменений — без записи. Действие журнала — то же, что у загрузки базы
(`SALES_LEADS_IMPORTED`): у лида одна история данных — откуда взялись имя и язык, ушедшие в
письме, — а своё действие потребовало бы миграции типа общего журнала (как отправитель пишет
действием базы знаний).

**Письмо в очереди собирается заново** (`edited`): лида правили после того, как его первое
письмо легло в очередь, — сборка соберёт письмо заново на том же месте (правило пересборки
очереди, `queue._current`), а отправка до того его не пустит (`mail.check`): в нём прежние
имя или компания. Время правки и время письма ставит база (`now()` транзакции) — правка
позже письма, если её транзакция началась позже.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.chain import FIRST_STEP
from backend.features.sales import geo, lead_values
from backend.features.sales.intake import TEXT_LENGTH
from backend.features.sales.models import SalesLeadModel, SalesThreadModel

logger = logging.getLogger(__name__)

#: Поля, которые правят на экране, — в порядке формы.
FIELDS = ("name", "position", "company", "language", "country", "timezone")
#: Поле словами — в отказе.
TITLES = {"name": "имя", "position": "должность", "company": "компания"}
#: Почему письмо в очереди не уходит, если лид менялся после сборки, — словами отказа отправки.
EDITED = "лид изменился после сборки письма — в письме прежние данные, соберите очередь заново"


class LeadEditError(ValueError):
    """Значение не годится полю лида. Текст — что поправить."""


class UnknownLeadError(LookupError):
    """Лида с таким номером нет."""


@dataclass(frozen=True, slots=True)
class Changed:
    """Итог правки: лид, какие поля изменились и ждёт ли его письмо в очереди пересборки."""

    lead: SalesLeadModel
    fields: list[str]
    rebuild: bool


def _text(name: str) -> Callable[[str], str | None]:
    def rule(value: str) -> str | None:
        if len(value) > TEXT_LENGTH:
            raise LeadEditError(
                f"{TITLES[name]} длиннее {TEXT_LENGTH} знаков ({len(value)}) — сократите"
            )
        return value or None

    return rule


def _language(value: str) -> str | None:
    if not value:
        return None
    if code := lead_values.language_code(value):
        return code
    raise LeadEditError(f"язык «{value}» — не код языка: {lead_values.LANGUAGE_HINT}")


def _country(value: str) -> str | None:
    if not value:
        return None
    if code := geo.country_code(value):
        return code
    raise LeadEditError(
        f"страна «{value}» не распознана: ждём код или название — de, Germany, Германия"
    )


def _zone(value: str) -> str | None:
    if not value:
        return None
    if zone := geo.zone_name(value):
        return zone
    raise LeadEditError(f"пояс «{value}» не распознан: ждём имя из базы поясов — Europe/Berlin")


_RULES: dict[str, Callable[[str], str | None]] = {
    "name": _text("name"),
    "position": _text("position"),
    "company": _text("company"),
    "language": _language,
    "country": _country,
    "timezone": _zone,
}


def cleaned(changes: Mapping[str, str | None]) -> dict[str, str | None]:
    """Поля правки → значения для базы: пробелы сведены, пусто — «не задано». Непонятое —
    `LeadEditError` первой причиной."""
    unknown = sorted(set(changes) - set(FIELDS))
    if unknown:
        raise LeadEditError(f"поля «{unknown[0]}» у лида не правят; правят: {', '.join(FIELDS)}")
    return {name: _RULES[name](" ".join((raw or "").split())) for name, raw in changes.items()}


def edited(lead: SalesLeadModel, letter: MessageModel) -> bool:
    """Лид менялся после того, как его письмо записано в очередь: в письме прежние данные.
    Меняет лида правка; очистка время правки не двигает (`clean_write`): имени и компании
    она не меняет, а лишняя пересборка письма стоила бы вызовов модели."""
    return lead.updated_at > letter.updated_at


async def queued(session: AsyncSession, lead_id: int) -> bool:
    """Первое письмо лида ждёт в очереди — его соберёт заново следующая сборка."""
    found = await session.scalar(
        select(MessageModel.id)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .where(
            SalesThreadModel.lead_id == lead_id,
            MessageModel.step == FIRST_STEP,
            MessageModel.status == MessageStatus.QUEUED,
        )
        .limit(1)
    )
    return found is not None


async def change(
    session: AsyncSession, lead_id: int, changes: Mapping[str, str | None], *, author_id: int | None
) -> Changed:
    """Поправить лида: только присланные поля. Запись и журнал — в транзакции вызывающего,
    коммит — за ним. Без изменений — без журнала."""
    lead = await session.get(SalesLeadModel, lead_id)
    if lead is None:
        raise UnknownLeadError(f"лида №{lead_id} нет — обновите список лидов")
    fresh = cleaned(changes)
    was = {name: getattr(lead, name) for name in fresh if getattr(lead, name) != fresh[name]}
    if was:
        for name in was:
            setattr(lead, name, fresh[name])
        await session.flush()
        # Время правки ставит база: без чтения назад поле истекло бы, а экран читал бы его
        # в async ленивым запросом (как у записи базы знаний, `kb._flush`).
        await session.refresh(lead)
        await AccessRepository(session).record(
            AuditAction.SALES_LEADS_IMPORTED,
            author_id=author_id,
            target=f"sales_lead:{lead.id}",
            details={
                "действие": "правка лида",
                "поля": list(was),
                "было": was,
                "стало": {name: fresh[name] for name in was},
            },
        )
        logger.info("продажи: лид поправлен", extra={"lead_id": lead.id, "fields": list(was)})
    return Changed(lead, list(was), rebuild=bool(was) and await queued(session, lead.id))
