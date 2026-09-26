"""Кому писать: отбор адресатов обоих этапов и воронка этого отбора.

Отдельно от очереди писем, потому что это другой вопрос: очередь — что
уже собрано и ждёт человека, отбор — кого ещё можно собрать. Вход один,
`Recipients.candidates(stage)`: этап выбирает, из кого — принятые доноры
Этапа 1 или рекламодатели Этапа 2, найденные обходом.

**Отсев считается по ступеням.** Запрос мог бы вернуть просто список
годных адресатов, но тогда пустая очередь выглядела бы одинаково при
«все уже написаны» и «ни у кого нет контакта» — а это разные новости
и разные действия. Поэтому рядом со списком идёт воронка: сколько
подходящих, у скольких есть адрес, скольких вычеркнул стоп-лист,
скольким уже писали. У рекламодателей ступеней больше: без найденной
ссылки письмо не под что писать, а без свежей цены донора оффер
«мы дешевле» — обещание, которого не сдержать.

**Ступени стоп-листа и «уже писали» общие для обоих этапов.** Стоп-лист
один на оба этапа, «уже писали» — тоже: один адресат не получает письмо
и как донор, и как рекламодатель.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.config import filters as filters_cfg
from backend.features.contacts.preference import preferred_first
from backend.features.contacts.quality import rejection_reason
from backend.features.core.domain import DonorStatus, Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel
from backend.features.core.models.run import RunCandidateModel
from backend.features.letters.compose import FoundLink


@dataclass(frozen=True, slots=True)
class Candidate:
    """Кому можно написать, и адрес, на который: донор или рекламодатель."""

    domain_id: int
    host: str
    contact_id: int
    email: str
    dr: int | None
    #: Найденная ссылка — только у рекламодателя: под неё пишется письмо.
    link: FoundLink | None = None


@dataclass(frozen=True, slots=True)
class LetterAddress:
    """Куда ушло бы первое письмо донору — адрес или причина, почему никуда."""

    contact_id: int | None = None
    #: Почему письмо не соберётся ни на один адрес. Пусто вместе с адресом —
    #: у донора нет адресов вовсе, и сказать тут нечего.
    blocked: str | None = None


@dataclass(frozen=True, slots=True)
class Funnel:
    """Сколько доноров отсеялось на каждой ступени отбора."""

    suitable: int
    accepted: int
    with_contact: int
    not_suppressed: int
    not_written: int

    def as_report(self) -> dict[str, int]:
        return {
            "подходящих": self.suitable,
            "принятых": self.accepted,
            "с адресом": self.with_contact,
            "вне стоп-листа": self.not_suppressed,
            "ещё не писали": self.not_written,
        }


@dataclass(frozen=True, slots=True)
class AdvertiserFunnel:
    """Воронка Этапа 2: где кончились рекламодатели.

    Ступени свои, потому что и вопросы свои. «Нет ссылки» значит, что
    письмо не под что писать; «цена донора протухла» — что оффер «мы
    дешевле» держится на числе, которому больше 150 дней, и сначала
    нужен перезапрос цены, а не письмо.
    """

    advertisers: int
    with_link: int
    fresh_price: int
    with_contact: int
    not_suppressed: int
    not_written: int

    def as_report(self) -> dict[str, int]:
        return {
            "рекламодателей": self.advertisers,
            "со ссылкой": self.with_link,
            "цена донора свежая": self.fresh_price,
            "с адресом": self.with_contact,
            "вне стоп-листа": self.not_suppressed,
            "ещё не писали": self.not_written,
        }


def _filled(column: Any) -> ColumnElement[bool]:
    """Колонка не пуста и не из одних пробелов: пустой анкор — не анкор."""
    return func.coalesce(func.trim(column), "") != ""


#: Любой запрос отбора: ступени стоп-листа и «уже писали» дописывают
#: условия и не трогают набор колонок, поэтому тип сохраняется.
_Query = TypeVar("_Query", bound=Select[Any])


class Recipients:
    """Отбор адресатов на настоящей базе."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- доноры (Этап 1) и общие ступени ---

    def _suitable(self) -> Select[tuple[int]]:
        return (
            select(DomainModel.id)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(DonorModel.status == DonorStatus.SUITABLE)
        )

    def _accepted(self, statement: _Query, run_ids: Sequence[int] = ()) -> _Query:
        """Принятые человеком — и, если названы прогоны, принятые в них.

        Пороги отвечают «годен ли по цифрам», человек — «берём ли»: без
        этой ступени первые письма прогона 23.09.2026 ушли бы x.com
        и microsoft.com. Прогоны сужают рассылку до своей страны и ниши:
        из всей базы в неё попадали бы сайты ставок из ЮАР рядом с SaaS.
        """
        statement = statement.where(DonorModel.review == "accepted")
        if not run_ids:
            return statement
        in_runs = (
            select(RunCandidateModel.id)
            .where(RunCandidateModel.domain_id == DomainModel.id)
            .where(RunCandidateModel.run_id.in_(run_ids))
            .where(RunCandidateModel.status == "accepted")
            .exists()
        )
        return statement.where(in_runs)

    def _has_contact(self, statement: _Query) -> _Query:
        return statement.where(
            select(ContactModel.id).where(ContactModel.domain_id == DomainModel.id).exists()
        )

    def _not_suppressed(self, statement: _Query, stage: Stage) -> _Query:
        """Стоп-лист работает на двух уровнях: адрес блокирует себя, донор —
        все свои адреса. Пустой этап в записи значит «на обоих этапах»."""
        stage_matches = or_(SuppressionModel.stage.is_(None), SuppressionModel.stage == stage)
        in_force = SuppressionModel.in_force(datetime.now(UTC))
        by_domain = (
            select(SuppressionModel.id)
            .where(SuppressionModel.domain_id == DomainModel.id)
            .where(stage_matches)
            .where(in_force)
            .exists()
        )
        by_email = (
            select(SuppressionModel.id)
            .where(SuppressionModel.email == ContactModel.email)
            .where(ContactModel.domain_id == DomainModel.id)
            .where(stage_matches)
            .where(in_force)
            .exists()
        )
        return statement.where(~by_domain).where(~by_email)

    def _not_written(self, statement: _Query) -> _Query:
        """Одно письмо на адресата за раз (docs/OUTREACH_THREADS.md) — на оба этапа.

        Не по кампании: вторая кампания — это второе письмо тому же
        человеку, и для него это рассылка по всем найденным ящикам, то есть
        спам. И не по этапу: один адресат не получает письмо и как донор,
        и как рекламодатель — тот же довод, что у общего стоп-листа. Сайт,
        которому мы предложили покупать размещения дешевле, не должен через
        неделю получить вопрос о цене своего, и наоборот.
        """
        written = select(MessageModel.id).where(MessageModel.domain_id == DomainModel.id).exists()
        return statement.where(~written)

    async def funnel(
        self, stage: Stage, *, run_ids: Sequence[int] = ()
    ) -> Funnel | AdvertiserFunnel:
        """Воронка отбора: где именно кончились адресаты этапа."""
        if stage is Stage.ADVERTISERS:
            return await self._advertiser_funnel()
        base = self._suitable()
        accepted = self._accepted(base, run_ids)
        with_contact = self._has_contact(accepted)
        not_suppressed = self._not_suppressed(with_contact, stage)
        not_written = self._not_written(not_suppressed)

        return Funnel(
            suitable=await self._count(base),
            accepted=await self._count(accepted),
            with_contact=await self._count(with_contact),
            not_suppressed=await self._count(not_suppressed),
            not_written=await self._count(not_written),
        )

    def _donor_picks(self, run_ids: Sequence[int] = ()) -> Select[Any]:
        """Один адрес на донора, которому можно писать, — лучший по порядку
        `preferred_first`, среди адресов вне стоп-листа.

        Им пользуются и сборка очереди (`candidates`), и карточка донора
        (`letter_address`): «на какой адрес уйдёт письмо» — один запрос,
        а не два похожих.
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
            .order_by(DomainModel.id, *preferred_first())
        )
        inner = self._accepted(inner, run_ids)
        inner = self._not_suppressed(inner, Stage.DONORS)
        return self._not_written(inner)

    async def letter_address(self, domain_id: int) -> LetterAddress:
        """На какой адрес ушло бы первое письмо донору, если собрать очередь
        сейчас, — или почему не ушло бы никакое.

        Адрес — тот же запрос, что у сборки (`_donor_picks`), суженный
        до донора; и та же перепроверка адреса, что у сборки перед письмом
        (`building._bad_address`): не прошедший её адрес сборка пропускает
        вместе с донором, а не переходит к следующему. Причина отказа нужна
        экрану, только когда адреса нет: называется первое невыполненное
        условие сборки.
        """
        picked = (
            await self._session.execute(self._donor_picks().where(DomainModel.id == domain_id))
        ).first()
        if picked is not None:
            bad = rejection_reason(picked.email)
            if bad is None:
                return LetterAddress(contact_id=picked.contact_id)
            return LetterAddress(
                blocked=f"письмо не соберётся: адрес для него не проходит проверку ({bad})"
            )
        return LetterAddress(blocked=await self._why_not(domain_id))

    async def _why_not(self, domain_id: int) -> str | None:
        """Первое условие сборки, которое донор не прошёл. `None` — адресов нет."""
        one = select(DomainModel.id).where(DomainModel.id == domain_id)
        donor = one.join(DonorModel, DonorModel.domain_id == DomainModel.id)
        if not await self._exists(self._has_contact(one)):
            return None
        if not await self._exists(donor.where(DonorModel.status == DonorStatus.SUITABLE)):
            return "письма не собираются: донор не прошёл пороги"
        if not await self._exists(self._accepted(donor)):
            return "письма уходят только донорам, принятым человеком"
        if not await self._exists(self._not_written(one)):
            return "донору уже писали — следующие письма идут в тот же диалог"
        return "все адреса донора в стоп-листе"

    async def _exists(self, statement: Select[Any]) -> bool:
        return (await self._session.execute(statement.limit(1))).first() is not None

    async def _count(self, statement: Select[Any]) -> int:
        rows = await self._session.execute(select(func.count()).select_from(statement.subquery()))
        return int(rows.scalar_one())

    async def candidates(
        self, stage: Stage, *, limit: int, run_ids: Sequence[int] = ()
    ) -> list[Candidate]:
        """Кому писать, по одному адресу на адресата.

        Лучший адрес — тот, с которого уже отвечали: дальше пишем тому,
        кто отвечает, а не в ящик, где письмо пролежало неделю. Дальше
        по оценке проверки адреса, дальше по возрасту записи.

        Этап выбирает, из кого: доноры Этапа 1 или рекламодатели Этапа 2.
        Вход один нарочно — отдельная функция для второго этапа оставила
        бы первую с параметром `stage`, который молча отдаёт доноров
        на любой этап.
        """
        if stage is Stage.ADVERTISERS:
            return await self._advertiser_candidates(limit=limit)
        picked = self._donor_picks(run_ids).subquery()
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

    # --- рекламодатели (Этап 2) ---

    def _advertisers(self) -> Select[tuple[int]]:
        return select(DomainModel.id).join(
            AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id
        )

    @staticmethod
    def _with_link(statement: _Query) -> _Query:
        """Есть под что писать: площадка, страница и анкор найденной ссылки.

        Письмо рекламодателю пишется «под конкретную найденную ссылку»,
        и без любой из трёх частей его текст — про ничто.
        """
        return statement.where(
            _filled(AdvertiserModel.best_donor_host),
            _filled(AdvertiserModel.best_page_url),
            _filled(AdvertiserModel.best_anchor),
        )

    @staticmethod
    def _fresh_price(statement: _Query, moment: datetime) -> _Query:
        """Цена донора, на чьей площадке нашли ссылку, не старше срока.

        Оффер «мы дешевле» держится на этой цене, хотя и не называет её:
        протухшая превращает его в обещание, которого не сдержать.
        Требование: свежесть цены 150 дней, старше — сначала перезапрос.
        """
        border = moment - timedelta(days=filters_cfg.PRICE_TTL_DAYS)
        donor_domain = aliased(DomainModel)
        fresh = (
            select(DonorModel.id)
            .join(donor_domain, donor_domain.id == DonorModel.domain_id)
            .where(donor_domain.host == func.lower(AdvertiserModel.best_donor_host))
            .where(DonorModel.last_price.is_not(None))
            .where(DonorModel.last_price_at >= border)
            .exists()
        )
        return statement.where(fresh)

    async def _advertiser_funnel(self, *, now: datetime | None = None) -> AdvertiserFunnel:
        moment = now or datetime.now(UTC)
        base = self._advertisers()
        with_link = self._with_link(base)
        fresh_price = self._fresh_price(with_link, moment)
        with_contact = self._has_contact(fresh_price)
        not_suppressed = self._not_suppressed(with_contact, Stage.ADVERTISERS)
        not_written = self._not_written(not_suppressed)

        return AdvertiserFunnel(
            advertisers=await self._count(base),
            with_link=await self._count(with_link),
            fresh_price=await self._count(fresh_price),
            with_contact=await self._count(with_contact),
            not_suppressed=await self._count(not_suppressed),
            not_written=await self._count(not_written),
        )

    async def _advertiser_candidates(
        self, *, limit: int, now: datetime | None = None
    ) -> list[Candidate]:
        """Рекламодатели, которым можно написать, по одному адресу на домен.

        Сначала самые доказательные: балл, потом число наших доноров,
        на которых он размещается. Если письма за раз кончатся на середине,
        они кончатся на сомнительных, а не на случайных.
        """
        inner = (
            select(
                DomainModel.id.label("domain_id"),
                DomainModel.host.label("host"),
                ContactModel.id.label("contact_id"),
                ContactModel.email.label("email"),
                AdvertiserModel.points.label("points"),
                AdvertiserModel.donors.label("donors"),
                AdvertiserModel.best_donor_host.label("donor_host"),
                AdvertiserModel.best_page_url.label("page_url"),
                AdvertiserModel.best_anchor.label("anchor"),
            )
            .join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
            .join(ContactModel, ContactModel.domain_id == DomainModel.id)
            .distinct(DomainModel.id)
            .order_by(DomainModel.id, *preferred_first())
        )
        inner = self._with_link(inner)
        inner = self._fresh_price(inner, now or datetime.now(UTC))
        inner = self._not_suppressed(inner, Stage.ADVERTISERS)
        inner = self._not_written(inner)

        picked = inner.subquery()
        rows = await self._session.execute(
            select(picked)
            .order_by(picked.c.points.desc(), picked.c.donors.desc(), picked.c.domain_id)
            .limit(limit)
        )
        return [
            Candidate(
                domain_id=row.domain_id,
                host=row.host,
                contact_id=row.contact_id,
                email=row.email,
                dr=None,
                link=FoundLink(
                    donor_host=row.donor_host.strip().lower(),
                    page_url=row.page_url.strip(),
                    anchor=row.anchor.strip(),
                ),
            )
            for row in rows
        ]
