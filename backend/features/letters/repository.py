"""Запросы под очередь писем.

Собраны здесь по той же причине, что и у остальной рассылки: ни сборка
очереди, ни веб-слой не должны знать, из скольких таблиц складывается
«кому мы ещё не писали».

**Отсев считается по ступеням.** Запрос мог бы вернуть просто список
годных доноров, но тогда пустая очередь выглядела бы одинаково при
«все уже написаны» и «ни у кого нет контакта» — а это разные новости
и разные действия. Поэтому рядом со списком идёт воронка: сколько
подходящих, у скольких есть адрес, скольких вычеркнул стоп-лист,
скольким уже писали.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import DonorStatus, MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ThreadModel,
)


@dataclass(frozen=True, slots=True)
class Candidate:
    """Донор, которому можно написать, и адрес, на который."""

    domain_id: int
    host: str
    contact_id: int
    email: str
    dr: int | None


@dataclass(frozen=True, slots=True)
class QueuedLetter:
    """Строка очереди писем: письмо вместе с тем, кому оно."""

    message: MessageModel
    host: str
    email: str | None
    campaign: str


class UnknownLetterError(ValueError):
    """Письма с таким номером нет."""


@dataclass(frozen=True, slots=True)
class Funnel:
    """Сколько доноров отсеялось на каждой ступени отбора."""

    suitable: int
    with_contact: int
    not_suppressed: int
    not_written: int

    def as_report(self) -> dict[str, int]:
        return {
            "подходящих": self.suitable,
            "с адресом": self.with_contact,
            "вне стоп-листа": self.not_suppressed,
            "ещё не писали": self.not_written,
        }


#: Любой запрос отбора: ступени стоп-листа и «уже писали» дописывают
#: условия и не трогают набор колонок, поэтому тип сохраняется.
_Query = TypeVar("_Query", bound=Select[Any])


class LetterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- отбор ---

    def _suitable(self) -> Select[tuple[int]]:
        return (
            select(DomainModel.id)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(DonorModel.status == DonorStatus.SUITABLE)
        )

    def _has_contact(self, statement: _Query) -> _Query:
        return statement.where(
            select(ContactModel.id).where(ContactModel.domain_id == DomainModel.id).exists()
        )

    def _not_suppressed(self, statement: _Query, stage: Stage) -> _Query:
        """Стоп-лист работает на двух уровнях: адрес блокирует себя, донор —
        все свои адреса. Пустой этап в записи значит «на обоих этапах»."""
        stage_matches = or_(SuppressionModel.stage.is_(None), SuppressionModel.stage == stage)
        by_domain = (
            select(SuppressionModel.id)
            .where(SuppressionModel.domain_id == DomainModel.id)
            .where(stage_matches)
            .exists()
        )
        by_email = (
            select(SuppressionModel.id)
            .where(SuppressionModel.email == ContactModel.email)
            .where(ContactModel.domain_id == DomainModel.id)
            .where(stage_matches)
            .exists()
        )
        return statement.where(~by_domain).where(~by_email)

    def _not_written(self, statement: _Query, stage: Stage) -> _Query:
        """Одно письмо на донора за раз (docs/OUTREACH_THREADS.md).

        Проверяется по этапу, а не по кампании: вторая кампания того же
        этапа — это второе письмо тому же человеку, и для него это
        рассылка по всем найденным ящикам, то есть спам.
        """
        written = (
            select(MessageModel.id)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .where(MessageModel.domain_id == DomainModel.id)
            .where(CampaignModel.stage == stage)
            .exists()
        )
        return statement.where(~written)

    async def funnel(self, stage: Stage) -> Funnel:
        """Воронка отбора: где именно кончились доноры."""
        base = self._suitable()
        with_contact = self._has_contact(base)
        not_suppressed = self._not_suppressed(with_contact, stage)
        not_written = self._not_written(not_suppressed, stage)

        return Funnel(
            suitable=await self._count(base),
            with_contact=await self._count(with_contact),
            not_suppressed=await self._count(not_suppressed),
            not_written=await self._count(not_written),
        )

    async def _count(self, statement: Select[Any]) -> int:
        rows = await self._session.execute(select(func.count()).select_from(statement.subquery()))
        return int(rows.scalar_one())

    async def candidates(self, stage: Stage, *, limit: int) -> list[Candidate]:
        """Кому писать, по одному адресу на донора.

        Лучший адрес — тот, с которого уже отвечали: дальше пишем тому,
        кто отвечает, а не в ящик, где письмо пролежало неделю. Дальше
        по оценке проверки адреса, дальше по возрасту записи.
        """
        inner = (
            select(
                DomainModel.id.label("domain_id"),
                DomainModel.host.label("host"),
                ContactModel.id.label("contact_id"),
                ContactModel.email.label("email"),
                DonorModel.dr.label("dr"),
            )
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .join(ContactModel, ContactModel.domain_id == DomainModel.id)
            .where(DonorModel.status == DonorStatus.SUITABLE)
            .distinct(DomainModel.id)
            .order_by(
                DomainModel.id,
                ContactModel.last_replied_at.desc().nullslast(),
                ContactModel.verification_score.desc().nullslast(),
                ContactModel.id,
            )
        )
        inner = self._not_suppressed(inner, stage)
        inner = self._not_written(inner, stage)

        picked = inner.subquery()
        rows = await self._session.execute(
            select(picked).order_by(picked.c.dr.desc().nullslast(), picked.c.domain_id).limit(limit)
        )
        return [
            Candidate(
                domain_id=row.domain_id,
                host=row.host,
                contact_id=row.contact_id,
                email=row.email,
                dr=row.dr,
            )
            for row in rows
        ]

    # --- очередь ---

    def _letters(self) -> Select[Any]:
        return (
            select(MessageModel, DomainModel.host, ContactModel.email, CampaignModel.name)
            .join(DomainModel, DomainModel.id == MessageModel.domain_id)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .outerjoin(ContactModel, ContactModel.id == MessageModel.contact_id)
        )

    async def queued(self, *, limit: int = 200) -> list[QueuedLetter]:
        """Что ждёт отправки.

        Только очередь: отправленное живёт в диалогах, и смешивать их
        в одном списке значит потерять смысл экрана — здесь то, по чему
        человек принимает решение прямо сейчас.
        """
        rows = await self._session.execute(
            self._letters()
            .where(MessageModel.status == MessageStatus.QUEUED)
            .order_by(MessageModel.id)
            .limit(limit)
        )
        return [
            QueuedLetter(message=message, host=host, email=email, campaign=campaign)
            for message, host, email, campaign in rows.all()
        ]

    async def letter(self, message_id: int) -> QueuedLetter:
        rows = await self._session.execute(self._letters().where(MessageModel.id == message_id))
        found = rows.first()
        if found is None:
            raise UnknownLetterError(f"Письма №{message_id} нет")
        message, host, email, campaign = found
        return QueuedLetter(message=message, host=host, email=email, campaign=campaign)

    # --- запись ---

    async def campaign(
        self, *, name: str, stage: Stage, run_id: int | None = None
    ) -> CampaignModel:
        """Кампания по имени. Одноимённая переиспользуется: повторный запуск
        сборки дополняет очередь, а не заводит вторую такую же."""
        rows = await self._session.execute(
            select(CampaignModel)
            .where(CampaignModel.name == name)
            .where(CampaignModel.stage == stage)
        )
        found = rows.scalars().first()
        if found is not None:
            return found

        created = CampaignModel(name=name, stage=stage, run_id=run_id, status="draft")
        self._session.add(created)
        await self._session.flush()
        return created

    async def thread(self, *, domain_id: int, campaign_id: int, contact_id: int) -> ThreadModel:
        rows = await self._session.execute(
            select(ThreadModel)
            .where(ThreadModel.domain_id == domain_id)
            .where(ThreadModel.campaign_id == campaign_id)
            .where(ThreadModel.contact_id == contact_id)
        )
        found = rows.scalars().first()
        if found is not None:
            return found

        created = ThreadModel(domain_id=domain_id, campaign_id=campaign_id, contact_id=contact_id)
        self._session.add(created)
        await self._session.flush()
        return created
