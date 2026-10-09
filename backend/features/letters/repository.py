"""Запросы под очередь писем.

Собраны здесь по той же причине, что и у остальной рассылки: ни сборка
очереди, ни веб-слой не должны знать, из скольких таблиц складывается
«кому мы ещё не писали». Сам отбор — кого можно собрать и где кончились
адресаты — живёт в `recipients.py`; здесь его вход, очередь и записи.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, assert_never

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
from backend.features.core.stages import SALES_ELSEWHERE, SalesNotConnectedError
from backend.features.crawl.niche import LINKS, NICHE
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.compose import FoundLink
from backend.features.letters.draft import assert_same_audience, split_audience
from backend.features.letters.funnel import AdvertiserFunnel, Funnel
from backend.features.letters.niche_recipients import NicheRecipients
from backend.features.letters.recipients import Candidate, Recipients, donor_geo_of


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
    #: Аудитория рассылки Этапа 2: у бизнеса ниши ссылки нет по природе, и
    #: правка его письма меряется от оффера ниши (`niche_recipients`).
    audience: str = LINKS


class UnknownLetterError(ValueError):
    """Письма с таким номером нет."""


class LetterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- отбор (`recipients.py`) ---
    #
    # Этап разбирается здесь, целиком, и только здесь: путь, принимавший этап,
    # отдавал принятых доноров любому этапу, кроме рекламодателей. Продажам —
    # отказ: их адресаты — лиды, очередь собирает модуль продаж;
    # следующему новому этапу — ошибка mypy, а не очередь из доноров.

    async def funnel(
        self, stage: Stage, *, run_ids: Sequence[int] = ()
    ) -> Funnel | AdvertiserFunnel:
        """Воронка отбора: где именно кончились адресаты этапа."""
        recipients = Recipients(self._session)
        match stage:
            case Stage.DONORS:
                return await recipients.donor_funnel(run_ids=run_ids)
            case Stage.ADVERTISERS:
                return await recipients.advertiser_funnel()
            case Stage.SALES:
                raise SalesNotConnectedError(
                    f"Воронка отбора этапа {stage.value}", words=SALES_ELSEWHERE
                )
            case _:
                assert_never(stage)

    async def candidates(
        self,
        stage: Stage,
        *,
        limit: int,
        run_ids: Sequence[int] = (),
        audience: str = LINKS,
    ) -> list[Candidate]:
        """Кому писать, по одному адресу на адресата этапа (и аудитории Этапа 2). У
        рекламодателей прогонов нет: их находит обход (`building.run_scope`); бизнесы
        ниши — свой отбор (`niche_recipients`): решение человека и пример площадки."""
        recipients = Recipients(self._session)
        match stage:
            case Stage.DONORS:
                return await recipients.donor_candidates(limit=limit, run_ids=run_ids)
            case Stage.ADVERTISERS if audience == NICHE:
                return await NicheRecipients(self._session).niche_candidates(limit=limit)
            case Stage.ADVERTISERS:
                return await recipients.advertiser_candidates(limit=limit)
            case Stage.SALES:
                raise SalesNotConnectedError(
                    f"Отбор адресатов этапа {stage.value}", words=SALES_ELSEWHERE
                )
            case _:
                assert_never(stage)

    async def funnel_report(
        self, stage: Stage, *, run_ids: Sequence[int] = (), audience: str = LINKS
    ) -> dict[str, int]:
        """Воронка для отчёта сборки. У бизнесов ниши своя: решение человека и адрес."""
        if stage is Stage.ADVERTISERS and audience == NICHE:
            return await NicheRecipients(self._session).niche_report()
        return (await self.funnel(stage, run_ids=run_ids)).as_report()

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
                CampaignModel.audience,
                AdvertiserModel.best_donor_host,
                AdvertiserModel.best_page_url,
                AdvertiserModel.best_anchor,
                donor_geo_of(AdvertiserModel.best_donor_host),
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
        message, host, email, campaign, days, stage, audience, donor_host, page_url, anchor, geo = (
            row
        )
        link = (
            FoundLink(donor_host=donor_host, page_url=page_url, anchor=anchor, donor_geo=geo)
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
            audience=audience,
        )

    async def queued(
        self, *, stage: Stage | None = None, audience: str | None = None, limit: int = 200
    ) -> list[QueuedLetter]:
        """Что ждёт отправки — всё или одного этапа и аудитории.

        Только очередь: отправленное живёт в диалогах, и смешивать их
        в одном списке значит потерять смысл экрана — здесь то, по чему
        человек принимает решение прямо сейчас. Этапы и аудитории разводятся
        по той же причине: оффер по найденной ссылке, оффер бизнесу ниши
        и вопрос донору о цене читаются разными глазами.

        **Только первые письма** (находка ревью 07.10.2026). Добивка и ответ,
        которые отказ почты вернул «в очередь», стояли здесь же: их брала
        пачка, и уходили они с любого свободного ящика — первым письмом вне
        своей переписки. У них свой путь и свой ящик (`mailbox.py`).
        """
        statement = self._waiting(self._letters(), stage, audience)
        rows = await self._session.execute(statement.order_by(MessageModel.id).limit(limit))
        return [self._queued_letter(row) for row in rows.all()]

    async def queued_count(self, *, stage: Stage, audience: str | None = None) -> int:
        """Сколько писем этапа (и аудитории) ждёт в очереди — всех, без потолка экрана
        и пачки. Аудитория не названа — все письма этапа.

        Отбор тот же, что у `queued`. Итог пачки «осталось в очереди» считался
        длиной `queued` с потолком пачки и при тысяче писем говорил «осталось 200».
        """
        counted = (
            select(func.count())
            .select_from(MessageModel)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        )
        waiting = self._waiting(counted, stage, audience)
        return int(await self._session.scalar(waiting) or 0)

    @staticmethod
    def _waiting(
        statement: Select[Any], stage: Stage | None, audience: str | None = None
    ) -> Select[Any]:
        """Отбор очереди: первые письма «в очереди» — все, одного этапа или одной
        аудитории Этапа 2 (`campaigns.audience`, `split_audience`)."""
        statement = statement.where(
            MessageModel.status == MessageStatus.QUEUED, MessageModel.step == FIRST_STEP
        )
        if stage is not None:
            statement = statement.where(CampaignModel.stage == stage)
        audience = split_audience(stage, audience)
        if audience is not None:
            statement = statement.where(CampaignModel.audience == audience)
        return statement

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
        audience: str = LINKS,
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
            assert_same_audience(campaign=name, stored=found.audience, sent=audience)
            return found

        created = CampaignModel(
            name=name,
            stage=stage,
            run_id=run_id,
            status="draft",
            followup_days=list(followup_days) or None,
            letter_template=letter_template,
            audience=audience,
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
