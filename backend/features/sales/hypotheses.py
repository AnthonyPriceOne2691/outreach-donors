"""Гипотеза продаж: заведение, правка и удаление — одним правилом здесь, с экрана и из консоли.

Гипотеза — кому и зачем пишем. Заводится данными — командой консоли или формой
«Новая гипотеза» на экране продаж (`POST /sales/hypotheses`), правится окном гипотезы и
описанием сегмента на «Сборе базы» (`PATCH /sales/hypotheses/{N}`), удаляется пустая
(`DELETE /sales/hypotheses/{N}`) — а не миграцией: описание гипотезы — коммерческий текст,
а строка в миграции уехала бы в публичную историю навсегда.

Имя — ключ, по которому гипотезу выбирают при загрузке базы и в отчётах:
пробелы по краям и двойные внутри не делают имя новым. Повтор отказывает
словами до обращения к уникальности базы — она остаётся последним рубежом
на случай двух одновременных команд.

**Рассылка гипотезы — по имени** («Продажи: <имя>», `campaign_title`): сборка очереди находит
её по имени и этапу (`queue.campaign_name`), а не по номеру гипотезы. Поэтому правка имени
переименовывает и рассылку — той же транзакцией, а рассылка с новым именем, если она уже есть
(остаток прежней гипотезы, чужая), — отказ словами: письма двух гипотез легли бы в одну. Тем же
правилом проверяется имя новой гипотезы. Удаление пустой гипотезы уносит её пустую рассылку.

**Удаляется только пустая** (решение владельца 15.10: «на случай, если завели по ошибке»): что
держит гипотезу и сколько — `hypothesis_holds`, отказ — словами с числами каждого вида и
подсказкой переименовать. **Проверка и удаление — в одной транзакции под блокировкой строки
гипотезы** (`FOR UPDATE`): лид, которого заводят в ту же секунду, либо уже виден счёту, либо
ждёт конца удаления и получает отказ ключа — висячих лидов нет. Правка описания держит строку
слабее (`FOR NO KEY UPDATE`): две правки идут по очереди, «было» в журнале верное, а загрузку
лидов правка описания не держит. **Переименование — тоже `FOR UPDATE`**: имя — под уникальным
индексом, и Postgres считает его правку правкой ключа — она ждёт незафиксированную загрузку
лидов в гипотезу, а та — её (нашёл тест на настоящих фиксациях, 15.10). Блокировка берётся
сразу нужной силы: ждать — в начале, под точкой сохранения, а не посреди записи. Ждать — не
дольше `LOCK_WAIT`: дольше — отказ словами, а не зависший запрос (замер на Postgres 16,
15.10.2026: «кто кого ждёт» — в `tests/test_sales_hypothesis_delete.py`).

**Всё — в журнале действий** (решение владельца 13.10): заведение, переименование, правка
описания сегмента и удаление — той же транзакцией, с экрана и из консоли (там автора нет).
Описание — вопрос судье сбора базы: по нему решается, каким сайтам продажи напишут. Своего
действия журнала у гипотезы нет — новое значение перечисления в базе требует миграции, —
поэтому запись идёт общей «правкой» (`USER_UPDATED`): что случилось — словами «действия»,
цель — `sales_hypothesis:N`, этап — продажи (без права «Продажи» записи не видно).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.donors.probe import emptied_campaign
from backend.features.sales import hypothesis_holds
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import NAME_LENGTH, SalesHypothesisModel
from backend.features.sales.segment_spend import number

logger = logging.getLogger(__name__)

#: «Действие» записи журнала: под общей «Правкой» экран показывает его строкой названия.
MADE = "гипотеза продаж заведена"
RENAMED = "гипотеза продаж переименована"
REMOVED = "гипотеза продаж удалена"
#: Начало «действия» правки описания: «… задано», «… поправлено», «… снято».
SEGMENT = "описание сегмента"

#: Сколько ждать блокировку строки гипотезы: тот же предел, что у миграций продаж. Строкой
#: целиком — значение не из ввода; тест предела подменяет её короткой.
LOCK_WAIT = "SET LOCAL lock_timeout = '5s'"
#: SQLSTATE «блокировку не дождались» (`lock_not_available`).
LOCK_NOT_AVAILABLE = "55P03"

BUSY = (
    "Гипотезу сейчас меняет другая операция — загрузка базы, прогон сбора или правка; "
    "повторите через минуту"
)
#: Удаляется только пустая — что делать с непустой, говорит последняя часть слов.
ONLY_EMPTY = "Удаляется только пустая гипотеза, заведённая по ошибке; эту можно переименовать"


class HypothesisError(ValueError):
    """Гипотеза не заведена, не поправлена или не удалена. Сообщение называет причину."""


class BadNameError(HypothesisError):
    """Имени нет или оно длиннее колонки — гипотезу не по чему выбрать."""


class NothingToEditError(HypothesisError):
    """В правке нет ни имени, ни описания."""


class HypothesisRefusedError(HypothesisError):
    """Отказ по состоянию, а не по запросу: имя занято, гипотезу держат, строка занята."""


class NameTakenError(HypothesisRefusedError):
    """Гипотеза с таким именем уже есть."""


class CampaignTakenError(NameTakenError):
    """Имя занято рассылкой продаж: письма двух гипотез легли бы в одну."""


class BusyError(HypothesisRefusedError):
    """Строку гипотезы держит другая операция дольше `LOCK_WAIT`."""


class HeldError(HypothesisRefusedError):
    """Гипотеза не пустая: её держат лиды, прогоны, письма или свой набор цепочки."""


@dataclass(frozen=True, slots=True)
class Removed:
    """Что ушло: имя гипотезы и её пустые рассылки (имя рассылки — один раз)."""

    name: str
    campaign: str | None


def _clean(name: str) -> str:
    return " ".join(name.split())


def campaign_title(name: str) -> str:
    """Рассылка гипотезы: одна на гипотезу, сборка находит её по этому имени."""
    return f"Продажи: {name}"


def _name_of(name: str) -> str:
    """Имя по правилу заведения — или отказ словами."""
    clean = _clean(name)
    if not clean:
        raise BadNameError(
            f"нет имени (передано {name!r}) — по нему гипотезу выбирают при загрузке базы"
        )
    if len(clean) > NAME_LENGTH:
        raise BadNameError(f"имя длиннее {NAME_LENGTH} знаков ({len(clean)}) — нужно короткое")
    return clean


async def _free(session: AsyncSession, clean: str, *, own: int | None = None) -> None:
    """Имя не занято ни другой гипотезой, ни рассылкой продаж с этим именем."""
    query = select(SalesHypothesisModel.id).where(SalesHypothesisModel.name == clean)
    if own is not None:
        query = query.where(SalesHypothesisModel.id != own)
    taken = await session.scalar(query)
    if taken is not None:
        raise NameTakenError(f"имя «{clean}» уже у гипотезы №{taken}")
    title = campaign_title(clean)
    found = await session.scalar(
        select(CampaignModel.id)
        .where(CampaignModel.stage == Stage.SALES, CampaignModel.name == title)
        .order_by(CampaignModel.id)
        .limit(1)
    )
    if found is not None:
        letters = await session.scalar(
            select(func.count()).select_from(MessageModel).where(MessageModel.campaign_id == found)
        )
        raise CampaignTakenError(
            f"рассылка «{title}» уже есть (№{found}, писем — {number(int(letters or 0))}) — "
            "письма двух гипотез легли бы в одну рассылку: выберите другое имя"
        )


async def find(session: AsyncSession, name: str) -> SalesHypothesisModel | None:
    """Гипотеза по имени — так её выбирают при загрузке базы."""
    rows = await session.scalars(
        select(SalesHypothesisModel).where(SalesHypothesisModel.name == _clean(name))
    )
    return rows.first()


async def _journal(
    session: AsyncSession, author_id: int | None, hypothesis_id: int, details: dict[str, Any]
) -> None:
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author_id,
        target=f"sales_hypothesis:{hypothesis_id}",
        details=details,
        stage=Stage.SALES,
    )


async def add(
    session: AsyncSession, name: str, description: str | None, *, author_id: int | None = None
) -> SalesHypothesisModel:
    """Завести гипотезу. Запись и строка журнала — в транзакции вызывающего, коммит — за ним.
    `author_id` — учётка экрана; у команды консоли автора нет."""
    clean = _name_of(name)
    await _free(session, clean)
    hypothesis = SalesHypothesisModel(name=clean, description=(description or "").strip() or None)
    session.add(hypothesis)
    await session.flush()
    # Время заведения ставит база: без чтения назад поле не загружено, и экран в async
    # читал бы его ленивым запросом и падал (как у записи базы знаний, `kb._flush`).
    await session.refresh(hypothesis)
    await _journal(
        session,
        author_id,
        hypothesis.id,
        {"действие": MADE, "имя": hypothesis.name, "описание сегмента": hypothesis.description},
    )
    logger.info("продажи: заведена гипотеза", extra={"hypothesis_id": hypothesis.id})
    return hypothesis


def _state(exc: DBAPIError) -> str:
    """SQLSTATE отказа базы — как его читает общий обработчик (`api/errors._database_code`)."""
    orig = exc.orig
    return str(getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None) or "")


async def _locked(session: AsyncSession, hypothesis_id: int, *, key: bool) -> SalesHypothesisModel:
    """Строка гипотезы под блокировкой: `key` — `FOR UPDATE` (удаление и переименование: держит
    и вставку ссылок на неё), иначе `FOR NO KEY UPDATE` (правка описания: ссылкам не мешает).
    Чтение — в точке сохранения: отказ по пределу ожидания не ломает транзакцию вызывающего.
    Значения — из базы, а не из памяти сессии: по ним пишется «было»."""
    await session.execute(text(LOCK_WAIT))
    query = (
        select(SalesHypothesisModel)
        .where(SalesHypothesisModel.id == hypothesis_id)
        .with_for_update(key_share=not key)
        .execution_options(populate_existing=True)
    )
    try:
        async with session.begin_nested():
            found = await session.scalar(query)
    except DBAPIError as exc:
        if _state(exc) != LOCK_NOT_AVAILABLE:
            raise
        logger.warning(
            "продажи: строку гипотезы держит другая операция", extra={"id": hypothesis_id}
        )
        raise BusyError(BUSY) from exc
    if found is None:
        raise UnknownHypothesisError(f"гипотезы №{hypothesis_id} нет — обновите список гипотез")
    return found


def _segment_change(was: str | None, now: str | None) -> str:
    """Что стало с описанием — словами «действия». Пустое «было» или «стало» экран журнала
    строкой подробностей не выводит: «задано» и «снято» говорят это сами."""
    if was is None:
        return f"{SEGMENT} задано"
    if now is None:
        return f"{SEGMENT} снято"
    return f"{SEGMENT} поправлено"


async def _rename(
    session: AsyncSession, found: SalesHypothesisModel, name: str, author_id: int | None
) -> None:
    """Новое имя — и рассылке гипотезы. То же имя после чистки пробелов — не правка."""
    clean, was = _name_of(name), found.name
    if clean == was:
        return
    await _free(session, clean, own=found.id)
    found.name = clean
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        # Две правки в одно имя разом: проверка выше прошла у обеих, уникальность базы — нет.
        raise NameTakenError(f"имя «{clean}» только что заняла другая гипотеза") from exc
    moved = await session.execute(
        update(CampaignModel)
        .where(CampaignModel.stage == Stage.SALES, CampaignModel.name == campaign_title(was))
        .values(name=campaign_title(clean))
        .returning(CampaignModel.id)
        .execution_options(synchronize_session="fetch")
    )
    details: dict[str, Any] = {"действие": RENAMED, "было": was, "стало": clean}
    if moved.first() is not None:
        details["рассылка"] = f"«{campaign_title(was)}» → «{campaign_title(clean)}»"
    await _journal(session, author_id, found.id, details)


async def _describe(
    session: AsyncSession, found: SalesHypothesisModel, description: str, author_id: int | None
) -> None:
    """Описание сегмента: пустое — описания нет; то же самое — не правка, записи нет. В журнале —
    кто, гипотеза, было и стало: «было» и «стало» — своими строками, как у правки лида
    (`lead_edit`), — экран журнала режет каждую строку подробностей на трёх."""
    was, now = found.description, description.strip() or None
    if now == was:
        return
    found.description = now
    await _journal(
        session,
        author_id,
        found.id,
        {"действие": _segment_change(was, now), "гипотеза": found.name, "было": was, "стало": now},
    )


async def edit(
    session: AsyncSession,
    hypothesis_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    author_id: int | None = None,
) -> SalesHypothesisModel:
    """Поправить имя, описание сегмента или оба. `None` — поле не трогать. Записи и журнал —
    в транзакции вызывающего, коммит — за ним."""
    if name is None and description is None:
        raise NothingToEditError("нечего править: дайте имя или описание сегмента")
    # Имя — под уникальным индексом: его правка в Postgres — правка ключа (`FOR UPDATE`).
    found = await _locked(session, hypothesis_id, key=name is not None)
    if name is not None:
        await _rename(session, found, name, author_id)
    if description is not None:
        await _describe(session, found, description, author_id)
    logger.info("продажи: гипотеза поправлена", extra={"hypothesis_id": hypothesis_id})
    return found


def held_line(found: Mapping[str, int]) -> str | None:
    """Почему гипотезу нельзя удалить — строкой под закрытой кнопкой; пустая — `None`."""
    said = hypothesis_holds.words(found)
    return None if said is None else f"Удалить нельзя — её держат: {said}. {ONLY_EMPTY}"


async def _refused(session: AsyncSession, hypothesis_id: int, name: str) -> HeldError | None:
    found = await hypothesis_holds.count(session, hypothesis_id)
    said = hypothesis_holds.words(found)
    if said is None:
        return None
    return HeldError(f"Гипотеза «{name}» не удалена — её держат: {said}. {ONLY_EMPTY}")


async def remove(
    session: AsyncSession, hypothesis_id: int, *, author_id: int | None = None
) -> Removed:
    """Удалить пустую гипотезу и её пустую рассылку; непустая — `HeldError` словами. Записи и
    журнал — в транзакции вызывающего, коммит — за ним."""
    found = await _locked(session, hypothesis_id, key=True)
    name, description = found.name, found.description
    refused = await _refused(session, hypothesis_id, name)
    if refused is not None:
        raise refused
    try:
        async with session.begin_nested():
            await session.delete(found)
            await session.flush()
    except IntegrityError as exc:
        # Под блокировкой сюда не попасть, если реестр держащих полон: держит то, чего он не
        # знает. Слова — по счёту заново; не нашёл и он — громко в лог и словами.
        refused = await _refused(session, hypothesis_id, name)
        if refused is not None:
            raise refused from exc
        logger.exception(
            "продажи: гипотезу держит ссылка вне реестра держащих",
            extra={
                "hypothesis_id": hypothesis_id,
                # Первая строка — без DETAIL с номерами; текста может не быть вовсе.
                "error": next(iter(str(exc.orig).splitlines()), ""),
            },
        )
        raise HeldError(
            f"Гипотеза «{name}» не удалена — её держит запись, которой проверка не знает; "
            "сообщите разработчику: реестр держащих (hypothesis_holds) её не называет"
        ) from exc
    gone = await session.execute(
        delete(CampaignModel)
        .where(
            CampaignModel.stage == Stage.SALES,
            CampaignModel.name == campaign_title(name),
            emptied_campaign(),
        )
        .returning(CampaignModel.id)
    )
    campaign = campaign_title(name) if gone.first() is not None else None
    details: dict[str, Any] = {"действие": REMOVED, "имя": name, "описание сегмента": description}
    if campaign is not None:
        details["рассылка"] = f"«{campaign}» — пустая, удалена"
    await _journal(session, author_id, hypothesis_id, details)
    logger.info("продажи: удалена гипотеза", extra={"hypothesis_id": hypothesis_id})
    return Removed(name, campaign)
