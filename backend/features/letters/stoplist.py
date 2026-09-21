"""Стоп-лист: кто в нём, как туда попадают руками и как оттуда выходят.

Правило ширины и то, чем отписка кнопкой отличается от отписки письмом,
описано в `okf/unsubscribe.md`; здесь исполнение и работа человека
со списком.

**Снятие записи об отписке требует причины.** Это единственное действие
сервиса, которое разрешает написать тому, кто просил не писать: донор
пишет «пишите всё-таки», и вернуть его надо уметь, — но след обязан
остаться, иначе через полгода на вопрос «почему мы ему писали» ответить
будет нечем. Причина уходит в журнал вместе с именем снявшего.

**Запись человека снимается без объяснений.** Список поставщиков и
ручные исключения — наше собственное решение, а не чужая просьба:
требовать объяснение за отмену своего же решения значит приучить
писать «не нужно» в поле, которое потом читают как согласие донора.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import (
    MessageStatus,
    Stage,
    SuppressionReason,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel

#: Причины, за которыми стоит решение адресата, а не наше. Снять такую
#: запись можно, но только назвав причину: письмо после неё уходит тому,
#: кто просил не писать.
DONOR_DECISION = (SuppressionReason.UNSUBSCRIBED, SuppressionReason.COMPLAINED)

#: Причины, которые человек заводит сам с экрана. Отписка и жалоба сюда
#: не входят: их заводят страница отписки и приём ответов.
HAND_REASONS = (SuppressionReason.MANUAL, SuppressionReason.SUPPLIER)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


class StopListError(ValueError):
    """Со списком так нельзя. Сообщение говорит, почему."""


@dataclass(frozen=True, slots=True)
class StopRow:
    """Строка списка в том виде, в каком её читает человек."""

    id: int
    host: str | None
    email: str | None
    reason: SuppressionReason
    stage: Stage | None
    created_by: str | None
    created_at: datetime

    @property
    def target(self) -> str:
        return self.host or self.email or "—"

    @property
    def donor_decision(self) -> bool:
        """Решение адресата, а не наше: снимается только с причиной."""
        return self.reason in DONOR_DECISION


async def rows(session: AsyncSession) -> list[StopRow]:
    """Весь список, свежие сверху."""
    found = await session.execute(
        select(SuppressionModel, DomainModel.host)
        .outerjoin(DomainModel, DomainModel.id == SuppressionModel.domain_id)
        .order_by(SuppressionModel.created_at.desc(), SuppressionModel.id.desc())
    )
    return [
        StopRow(
            id=row.id,
            host=host,
            email=row.email,
            reason=row.reason,
            stage=row.stage,
            created_by=row.created_by,
            created_at=row.created_at,
        )
        for row, host in found.all()
    ]


async def add(
    session: AsyncSession,
    target: str,
    *,
    reason: SuppressionReason,
    stage: Stage | None = None,
    author: str,
) -> StopRow:
    """Завести запись руками: домен целиком или один адрес.

    Домен, которого мы ещё не видели, заводится строкой в `domains`:
    список поставщиков приходит раньше первого прогона, и ждать, пока
    донор найдётся сам, значит написать ему до того.
    """
    if reason not in HAND_REASONS:
        raise StopListError(
            f"Причину «{reason.value}» ставит сам сервис, руками её не заводят. "
            "Руками — «вручную» и «поставщик»"
        )
    cleaned = target.strip().lower().removeprefix("http://").removeprefix("https://").strip("/")
    if _EMAIL_RE.match(cleaned):
        return await _add_email(session, cleaned, reason=reason, stage=stage, author=author)
    if _HOST_RE.match(cleaned):
        return await _add_host(session, cleaned, reason=reason, stage=stage, author=author)
    raise StopListError(f"«{target}» не похоже ни на домен, ни на адрес почты")


async def remove(session: AsyncSession, row_id: int, *, reason: str | None) -> StopRow:
    """Снять запись. Решение адресата снимается только с причиной."""
    row = await session.get(SuppressionModel, row_id)
    if row is None:
        raise StopListError(f"Записи №{row_id} в стоп-листе нет")

    host = None
    if row.domain_id is not None:
        domain = await session.get(DomainModel, row.domain_id)
        host = domain.host if domain is not None else None

    taken = StopRow(
        id=row.id,
        host=host,
        email=row.email,
        reason=row.reason,
        stage=row.stage,
        created_by=row.created_by,
        created_at=row.created_at,
    )
    if taken.donor_decision and not (reason or "").strip():
        raise StopListError(
            f"«{taken.target}» просил больше не писать (причина «{row.reason.value}»). "
            "Снять такую запись можно, но надо написать, почему: причина уйдёт в журнал"
        )
    await session.delete(row)
    return taken


async def stop_pending(
    session: AsyncSession, *, domain_id: int | None = None, email: str | None = None
) -> int:
    """Снять с очереди и со сроков всё, что этому адресату предстояло.

    Одной строки стоп-листа мало. Проверка перед отправкой откажет, но
    письмо до тех пор висит в очереди как готовое, а добивка живёт
    не в очереди, а сроком у уже отправленного письма: пока срок цел,
    адресат остаётся в планах. Видно это стало бы только отказом
    в момент отправки — то есть человеку, а не в базе.
    """
    statement = select(MessageModel).where(
        or_(
            MessageModel.status == MessageStatus.QUEUED,
            MessageModel.next_action_at.is_not(None),
        )
    )
    if domain_id is not None:
        statement = statement.where(MessageModel.domain_id == domain_id)
    else:
        statement = statement.where(
            MessageModel.contact_id.in_(select(ContactModel.id).where(ContactModel.email == email))
        )

    found = await session.execute(statement)
    stopped = 0
    for message in found.scalars().all():
        if message.status is MessageStatus.QUEUED:
            message.status = MessageStatus.STOPPED
        message.next_action_at = None
        stopped += 1
    return stopped


async def _add_host(
    session: AsyncSession,
    host: str,
    *,
    reason: SuppressionReason,
    stage: Stage | None,
    author: str,
) -> StopRow:
    found = await session.execute(select(DomainModel).where(DomainModel.host == host))
    domain = found.scalars().first()
    if domain is None:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()

    await _refuse_duplicate(session, SuppressionModel.domain_id == domain.id, stage, host)
    row = SuppressionModel(domain_id=domain.id, reason=reason, stage=stage, created_by=author)
    session.add(row)
    await session.flush()
    await stop_pending(session, domain_id=domain.id)
    return StopRow(
        id=row.id,
        host=host,
        email=None,
        reason=reason,
        stage=stage,
        created_by=author,
        created_at=row.created_at,
    )


async def _add_email(
    session: AsyncSession,
    email: str,
    *,
    reason: SuppressionReason,
    stage: Stage | None,
    author: str,
) -> StopRow:
    await _refuse_duplicate(session, SuppressionModel.email == email, stage, email)
    row = SuppressionModel(email=email, reason=reason, stage=stage, created_by=author)
    session.add(row)
    await session.flush()
    await stop_pending(session, email=email)
    return StopRow(
        id=row.id,
        host=None,
        email=email,
        reason=reason,
        stage=stage,
        created_by=author,
        created_at=row.created_at,
    )


async def _refuse_duplicate(
    session: AsyncSession, same_target: object, stage: Stage | None, shown: str
) -> None:
    """Второй записи на того же адресата не заводим.

    Молчаливое согласие завело бы список, где один донор лежит трижды,
    и снятие одной записи выглядело бы как возврат, которым оно не было.
    """
    found = await session.execute(
        select(SuppressionModel.id).where(
            same_target,  # type: ignore[arg-type]
            or_(SuppressionModel.stage.is_(None), SuppressionModel.stage == stage),
        )
    )
    if found.first() is not None:
        raise StopListError(f"«{shown}» уже в стоп-листе")
