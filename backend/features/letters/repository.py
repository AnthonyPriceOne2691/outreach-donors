"""Запросы под очередь писем.

Собраны здесь по той же причине, что и у остальной рассылки: ни сборка
очереди, ни веб-слой не должны знать, из скольких таблиц складывается
«кому мы ещё не писали». Сам отбор — кого можно собрать и где кончились
адресаты — живёт в `recipients.py`; здесь его вход, очередь и записи.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.repository import manual_address
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ThreadModel,
)
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.letters.compose import FoundLink
from backend.features.letters.recipients import AdvertiserFunnel, Candidate, Funnel, Recipients


@dataclass(frozen=True, slots=True)
class QueuedLetter:
    """Строка очереди писем: письмо вместе с тем, кому оно."""

    message: MessageModel
    host: str
    email: str | None
    campaign: str
    #: Сроки добивок рассылки. Нужны экрану: согласуя первое письмо,
    #: человек согласует цепочку, и когда уйдут остальные — часть решения.
    followup_days: list[int] | None = None
    #: Этап рассылки: от него шаблон, против которого меряется правка,
    #: и добивки, которые показываются рядом.
    stage: Stage = Stage.DONORS
    #: Нынешняя лучшая ссылка рекламодателя. Пусто у донора — и у
    #: рекламодателя, которого сняли после сборки письма.
    link: FoundLink | None = None


class UnknownLetterError(ValueError):
    """Письма с таким номером нет."""


class LetterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- отбор (`recipients.py`) ---

    async def funnel(
        self, stage: Stage, *, run_ids: Sequence[int] = ()
    ) -> Funnel | AdvertiserFunnel:
        """Воронка отбора: где именно кончились адресаты этапа."""
        return await Recipients(self._session).funnel(stage, run_ids=run_ids)

    async def candidates(
        self, stage: Stage, *, limit: int, run_ids: Sequence[int] = ()
    ) -> list[Candidate]:
        """Кому писать, по одному адресу на адресата этапа."""
        return await Recipients(self._session).candidates(stage, limit=limit, run_ids=run_ids)

    # --- очередь ---

    def _letters(self) -> Select[Any]:
        return (
            select(
                MessageModel,
                DomainModel.host,
                ContactModel.email,
                CampaignModel.name,
                CampaignModel.followup_days,
                CampaignModel.stage,
                AdvertiserModel.best_donor_host,
                AdvertiserModel.best_page_url,
                AdvertiserModel.best_anchor,
            )
            .join(DomainModel, DomainModel.id == MessageModel.domain_id)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .outerjoin(ContactModel, ContactModel.id == MessageModel.contact_id)
            # Ссылка нужна только письмам Этапа 2: у донора, который заодно
            # чей-то рекламодатель, её быть не должно.
            .outerjoin(
                AdvertiserModel,
                (AdvertiserModel.domain_id == MessageModel.domain_id)
                & (CampaignModel.stage == Stage.ADVERTISERS),
            )
        )

    @staticmethod
    def _queued_letter(row: Any) -> QueuedLetter:
        message, host, email, campaign, days, stage, donor_host, page_url, anchor = row
        link = (
            FoundLink(donor_host=donor_host, page_url=page_url, anchor=anchor)
            if donor_host and page_url and anchor
            else None
        )
        return QueuedLetter(
            message=message,
            host=host,
            email=email,
            campaign=campaign,
            followup_days=days,
            stage=stage,
            link=link,
        )

    async def queued(self, *, stage: Stage | None = None, limit: int = 200) -> list[QueuedLetter]:
        """Что ждёт отправки — всё или одного этапа.

        Только очередь: отправленное живёт в диалогах, и смешивать их
        в одном списке значит потерять смысл экрана — здесь то, по чему
        человек принимает решение прямо сейчас. Этапы разводятся по той же
        причине: оффер рекламодателю и вопрос донору о цене читаются
        разными глазами.
        """
        statement = self._letters().where(MessageModel.status == MessageStatus.QUEUED)
        if stage is not None:
            statement = statement.where(CampaignModel.stage == stage)
        rows = await self._session.execute(statement.order_by(MessageModel.id).limit(limit))
        return [self._queued_letter(row) for row in rows.all()]

    async def letter(self, message_id: int) -> QueuedLetter:
        rows = await self._session.execute(self._letters().where(MessageModel.id == message_id))
        found = rows.first()
        if found is None:
            raise UnknownLetterError(f"Письма №{message_id} нет")
        return self._queued_letter(found)

    # --- запись ---

    async def campaign(
        self,
        *,
        name: str,
        stage: Stage,
        run_id: int | None = None,
        followup_days: Sequence[int] = (),
        letter_template: str | None = None,
    ) -> CampaignModel:
        """Кампания по имени. Одноимённая переиспользуется: повторный запуск
        сборки дополняет очередь, а не заводит вторую такую же.

        **Сроки добивок у найденной не переписываются.** Её цепочки уже
        идут по ним, и новая правка сдвинула бы письма, отправленные
        вчера: срок посчитан от отправки, а не от правки настройки.
        Текст письма — тоже, и его расхождение ловит маршрут до постановки
        сборки (`draft.assert_same`).
        """
        found = await self.find_campaign(name=name, stage=stage)
        if found is not None:
            return found

        created = CampaignModel(
            name=name,
            stage=stage,
            run_id=run_id,
            status="draft",
            followup_days=list(followup_days) or None,
            letter_template=letter_template,
        )
        self._session.add(created)
        await self._session.flush()
        return created

    async def find_campaign(self, *, name: str, stage: Stage) -> CampaignModel | None:
        rows = await self._session.execute(
            select(CampaignModel)
            .where(CampaignModel.name == name)
            .where(CampaignModel.stage == stage)
        )
        return rows.scalars().first()

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

    # --- прогоны рассылки ---

    async def runs(self, run_ids: Sequence[int]) -> list[RunModel]:
        rows = await self._session.execute(select(RunModel).where(RunModel.id.in_(run_ids)))
        return list(rows.scalars().all())

    async def contacts_pending(self, run_ids: Sequence[int]) -> int:
        """Принятые в этих прогонах, кому контакт ещё не искали.

        Рассылка по прогону, у которого поиск контактов не закончен, ушла
        бы части доноров, а остальные молча выпали бы до следующей сборки.

        Донор с вписанным руками адресом поиска не ждёт: общий поиск его
        не берёт (`contacts.repository.manual_address`), и считать его
        ждущим значило бы запереть сборку по прогону навсегда.
        """
        rows = await self._session.execute(
            select(func.count(func.distinct(RunCandidateModel.domain_id)))
            .join(DonorModel, DonorModel.domain_id == RunCandidateModel.domain_id)
            .where(RunCandidateModel.run_id.in_(run_ids))
            .where(RunCandidateModel.status == "accepted")
            .where(DonorModel.contact_attempted_at.is_(None))
            .where(~manual_address())
        )
        return int(rows.scalar_one())
