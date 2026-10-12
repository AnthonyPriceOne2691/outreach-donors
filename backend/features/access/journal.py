"""Журнал действий для экрана: кто, что, когда и над чем — постранично, с фильтрами.

Записи пишутся с первого дня — той же транзакцией, что и само действие
(`AccessRepository.record`), — а прочитать их было негде: чтение журнала никто не звал
(сверка ТЗ 11.10.2026; ТЗ, вариант 3: «роли, журнал действий»). Читает тот, кто заводит
учётки (`users`): в журнале входы и отказы входа с почтами, права и пароли — это разбор
доступа, а не работа с базой.

**Порядок — полный, до номера записи.** Время записи ставит база временем транзакции:
у записей одного действия оно одно, и без номера записи строка со стыка страниц
показывалась бы на обеих или ни на одной (`docs/UI_RULES.md`, «Таблицы»).

**Без права «Продажи» — без действий этапа продаж** (решение Anthony 10.10.2026, П2):
данные продаж на общих экранах — только с правом. Общие писатели журнала кладут этап
в подробности в момент действия (`AccessRepository.record`, ключ «этап») — так запись
узнаётся и после того, как строку, о которой она, удалили (снятая запись стоп-листа,
чистка проб; проверка ветки 12.10.2026). Записи до этого и записи модуля продаж
узнаются по самой записи (`sales_entry`): действия, которые бывают только у продаж;
цели модуля продаж («sales_…»); проба продаж в чистке; письмо, ответ (правилом ответа),
черновик и настройки агента, ящик и запись стоп-листа этапа продаж — по своей строке.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    and_,
    case,
    cast,
    exists,
    func,
    or_,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute

from backend.features.access.repository import STAGE_KEY
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)

#: Записей на странице по умолчанию и больше которых за раз не отдаём: журнал читают
#: глазами, а не выгружают.
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

#: Действия, которые бывают только у продаж.
SALES_ACTIONS = frozenset(
    {
        AuditAction.SALES_LEADS_IMPORTED,
        AuditAction.SALES_KB_CHANGED,
        AuditAction.SALES_CHAIN_CHANGED,
    }
)

#: Этап продаж в подробностях записи — кодом (`AccessRepository.record`, настройки агента)
#: или словом (сборка писем).
SALES_STAGE_WORDS = (Stage.SALES.value, "продажи")

#: Проба продаж в подробностях чистки (`sales/trials.TrialTrace.as_details`): в записях до
#: того, как чистка стала класть этап, — единственный признак. Связь держит тест.
SALES_TRIAL_KEY = "проба продаж"


class JournalRangeError(ValueError):
    """«С» не раньше «по»: такой отрезок пуст по построению, и пустой ответ читался бы
    как «действий не было»."""


@dataclass(frozen=True, slots=True)
class JournalQuery:
    """Что показать. Пустой фильтр — без ограничения."""

    page: int = 1
    size: int = PAGE_SIZE
    action: AuditAction | None = None
    author_id: int | None = None
    #: С какого момента (включительно) и до какого (не включая).
    since: datetime | None = None
    until: datetime | None = None
    #: Показывать действия этапа продаж — у читающего право «Продажи».
    with_sales: bool = True


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """Запись журнала вместе с почтой автора."""

    id: int
    created_at: datetime
    action: AuditAction
    author_id: int | None
    #: Почта автора. Пусто — автора нет: неудачный вход с чужой почтой, загрузка из
    #: консоли (кто — в подробностях, ключом «кто»).
    author_email: str | None
    target: str | None
    details: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class JournalPage:
    entries: list[JournalEntry]
    #: Сколько записей под фильтром всего — по нему экран считает страницы.
    total: int


def _numbered(prefix: str) -> ColumnElement[int]:
    """Номер строки из цели «prefix:N»; у другой цели — пусто.

    Через CASE, а не условием рядом: порядок условий база выбирает сама и привела бы
    к числу и «sales_kb», и «advertisers:promote».
    """
    target = AuditLogModel.target
    return case(
        (
            target.regexp_match(f"^{prefix}:[0-9]{{1,18}}$"),
            cast(func.split_part(target, ":", 2), BigInteger),
        ),
        else_=None,
    )


def _sales_message(message_id: ColumnElement[Any] | QueryableAttribute[Any]) -> ColumnElement[bool]:
    """Письмо рассылки этапа продаж."""
    return exists().where(
        MessageModel.id == message_id,
        CampaignModel.id == MessageModel.campaign_id,
        CampaignModel.stage == Stage.SALES,
    )


def _sales_reply(reply_id: ColumnElement[Any]) -> ColumnElement[bool]:
    """Ответ этапа продаж — правилом ответа (`replies.repository.stage_of`): по диалогу,
    а без диалога — по письму. Диалог переживает удаление письма."""
    by_thread = exists().where(
        ReplyModel.id == reply_id,
        ThreadModel.id == ReplyModel.thread_id,
        CampaignModel.id == ThreadModel.campaign_id,
        CampaignModel.stage == Stage.SALES,
    )
    by_letter = exists().where(
        ReplyModel.id == reply_id,
        ReplyModel.thread_id.is_(None),
        _sales_message(ReplyModel.message_id),
    )
    return or_(by_thread, by_letter)


def sales_entry() -> ColumnElement[bool]:
    """Запись о действии этапа продаж — по этапу в ней самой, а у прежних записей — по
    строкам, на которые она ссылается. Действие, которого здесь не узнать, — общее: его
    видят все."""
    details = AuditLogModel.details
    draft = _numbered("agent_draft")
    return or_(
        details[STAGE_KEY].astext.in_(SALES_STAGE_WORDS),
        AuditLogModel.action.in_(sorted(SALES_ACTIONS)),
        AuditLogModel.target.like("sales\\_%", escape="\\"),
        details.has_key(SALES_TRIAL_KEY),
        # Запуск задач модуля продаж: «очистка лидов продаж», «сборка очереди продаж».
        and_(
            AuditLogModel.action == AuditAction.RUN_STARTED,
            details["действие"].astext.like("%продаж%"),
        ),
        _sales_message(_numbered("message")),
        _sales_reply(_numbered("reply")),
        exists().where(
            SenderModel.id == _numbered("sender"),
            SenderModel.stage == Stage.SALES,
        ),
        exists().where(
            AgentDraftModel.id == draft,
            AgentSettingsModel.id == AgentDraftModel.settings_id,
            AgentSettingsModel.stage == Stage.SALES,
        ),
        exists().where(
            AgentSettingsModel.id == _numbered("agent_settings"),
            AgentSettingsModel.stage == Stage.SALES,
        ),
        exists().where(
            SuppressionModel.id == _numbered("suppression"),
            SuppressionModel.stage == Stage.SALES,
        ),
    )


def _aware(moment: datetime | None) -> datetime | None:
    """Момент без пояса — UTC: время журнала хранится с поясом, и сравнивать их иначе
    нельзя. Экран шлёт момент с поясом; без пояса приходит только рукописный запрос."""
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _filters(query: JournalQuery) -> list[ColumnElement[bool]]:
    since, until = _aware(query.since), _aware(query.until)
    if since is not None and until is not None and since >= until:
        raise JournalRangeError(
            "Начало отрезка — не раньше его конца: такой отрезок пуст. Поменяйте даты местами"
        )
    found: list[ColumnElement[bool]] = []
    if query.action is not None:
        found.append(AuditLogModel.action == query.action)
    if query.author_id is not None:
        found.append(AuditLogModel.user_id == query.author_id)
    if since is not None:
        found.append(AuditLogModel.created_at >= since)
    if until is not None:
        found.append(AuditLogModel.created_at < until)
    if not query.with_sales:
        # «Не истина», а не «ложь»: у записи без подробностей или без цели части условия —
        # пустота, и NOT от пустоты — пустота, то есть запись пропала бы молча.
        found.append(sales_entry().is_not(true()))
    return found


async def read_journal(session: AsyncSession, query: JournalQuery) -> JournalPage:
    """Страница журнала, новые сверху. Страница за концом — пустая, с настоящим `total`:
    по нему экран уходит на последнюю, как в истории прогонов."""
    where = _filters(query)
    total = await session.scalar(select(func.count()).select_from(AuditLogModel).where(*where))
    rows = await session.execute(
        select(AuditLogModel, UserModel.email)
        .outerjoin(UserModel, UserModel.id == AuditLogModel.user_id)
        .where(*where)
        .order_by(AuditLogModel.created_at.desc(), AuditLogModel.id.desc())
        .limit(query.size)
        .offset((query.page - 1) * query.size)
    )
    return JournalPage(entries=_entries(rows.tuples().all()), total=int(total or 0))


def _entries(rows: Sequence[tuple[AuditLogModel, str | None]]) -> list[JournalEntry]:
    return [
        JournalEntry(
            id=entry.id,
            created_at=entry.created_at,
            action=entry.action,
            author_id=entry.user_id,
            author_email=email,
            target=entry.target,
            details=entry.details,
        )
        for entry, email in rows
    ]
