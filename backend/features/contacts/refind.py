"""Заново найти адрес для названных доменов — тем же поиском, что и общий.

Ложный адрес, однажды записанный, общий поиск не перепроверяет: домен
с найденным адресом он не берёт, а сборка писем выбирает старший адрес
домена (`preference.preferred_first`). Так вышло с адресами из голого «at»
(#137): «hosted here at site.com» стало here@site.com. Правка извлечения
таких адресов больше не даёт, но записанные остались. Решение Anthony
(02.10.2026) — штатная команда: домены списком; без флага — только
показать «было → станет», с `--yes` — записать.

Порядок тот же, что у общего поиска (`search.search_contacts`): своя здесь
только очередь — названные домены. Лестница, платная ступень и запись
исхода общие. Своё одно: перед записью снимаются прежние адреса,
найденные лестницей, — иначе письмо ушло бы на старший из них, то есть
на ложный. Снимаются только при окончательном исходе: квота или поломка
платной ступени — «не спросили», и прежний адрес остаётся.

Не трогается:
- домен с адресом, вписанным человеком, — заново не ищется вовсе, как и
  в общем поиске (`repository.manual_address`);
- адрес, по которому уже есть переписка (`manual.removal_refusals`), —
  остаётся, и это сказано.

Показ платную ступень не зовёт: платить за просмотр незачем, а при записи
она спросится, как в общем поиске.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.manual import removal_refusals
from backend.features.contacts.repository import ContactRepository
from backend.features.core.domain import ContactSource, ContactStatus, MessageStatus
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import MessageModel
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.donors.host import normalize_host

logger = logging.getLogger(__name__)

#: Исходы, после которых прежние адреса лестницы снимаются: сайт ответил,
#: и новый исход — его ответ. Квота, частота, поломка — «не спросили».
CONCLUSIVE = frozenset({ContactStatus.FOUND, ContactStatus.NOT_FOUND, ContactStatus.FORM_ONLY})


#: Письмо ещё уйдёт — его можно остановить.
_WAITING = frozenset({MessageStatus.QUEUED, MessageStatus.SENDING})
#: Письмо ушло.
_GONE = frozenset({MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED})


def _why_kept(refusal: str, letters: dict[int, tuple[int, int]], contact: ContactModel) -> str:
    """Почему адрес остаётся — и что с письмами на него (ревью #142).

    Новых писем такой адрес не получит (сборка не берёт адрес с письмом),
    но собранное и ждущее в очереди уйдёт, когда снимут предохранитель:
    его надо остановить руками.
    """
    queued, gone = letters.get(contact.id, (0, 0))
    said = f"{refusal} В очереди писем: {queued}, ушло: {gone}."
    return f"{said} Письма в очереди — остановить на экране «Письма»." if queued else said


@dataclass(slots=True)
class Refound:
    """Что стало с одним доменом."""

    host: str
    #: Адреса домена до прохода, найденные лестницей.
    before: list[str] = field(default_factory=list)
    result: LadderResult | None = None
    #: Сняты (или будут сняты — в показе).
    removed: list[str] = field(default_factory=list)
    #: Остались: адрес и почему.
    kept: list[tuple[str, str]] = field(default_factory=list)
    #: Почему домен не искали. Пусто — искали.
    skipped: str = ""


class NamedDomains:
    """Очередь общего поиска из названных доменов (`search.ContactQueue`).

    `write=False` — показ: исходы собираются, в базу не пишется ничего.
    """

    def __init__(self, session: AsyncSession, hosts: Sequence[str], *, write: bool) -> None:
        self._session = session
        self._write = write
        self.domains: dict[str, Refound] = {}
        for raw in hosts:
            host = normalize_host(raw)
            if host:
                self.domains.setdefault(host, Refound(host=host))
            else:
                self.domains[raw] = Refound(host=raw, skipped="не похоже на домен")
        self._ids: dict[str, int] = {}
        self._donors: set[str] = set()
        self._advertisers: set[str] = set()

    async def pending_hosts(self, *, limit: int = 100) -> list[str]:
        """Названные домены, которые есть в базе донором или рекламодателем
        и без адреса, вписанного руками."""
        named = [host for host, found in self.domains.items() if not found.skipped]
        rows = await self._session.execute(
            select(DomainModel.host, DomainModel.id).where(DomainModel.host.in_(named))
        )
        self._ids = dict(rows.tuples().all())
        self._donors = await self._hosts_of(DonorModel)
        self._advertisers = await self._hosts_of(AdvertiserModel)
        for host in named:
            await self._judge(host)
        return [host for host in named if not self.domains[host].skipped][:limit]

    async def _hosts_of(self, model: type[DonorModel] | type[AdvertiserModel]) -> set[str]:
        rows = await self._session.execute(
            select(DomainModel.host)
            .join(model, model.domain_id == DomainModel.id)
            .where(DomainModel.id.in_(list(self._ids.values())))
        )
        return set(rows.scalars().all())

    async def _judge(self, host: str) -> None:
        """Искать ли домен, и что у него записано сейчас."""
        found = self.domains[host]
        if host not in self._ids:
            found.skipped = "домена нет в базе"
            return
        if host not in self._donors | self._advertisers:
            found.skipped = "домен в базе не донор и не рекламодатель"
            return
        contacts = await self._contacts(host)
        if any(contact.source is ContactSource.MANUAL for contact in contacts):
            found.skipped = "адрес вписан человеком — заново не ищем"
            return
        found.before = [contact.email for contact in contacts]

    async def _letters(self, contact_ids: list[int]) -> dict[int, tuple[int, int]]:
        """Писем на адрес: (в очереди, ушло)."""
        if not contact_ids:
            return {}
        rows = await self._session.execute(
            select(MessageModel.contact_id, MessageModel.status, func.count())
            .where(MessageModel.contact_id.in_(contact_ids))
            .group_by(MessageModel.contact_id, MessageModel.status)
        )
        counts: dict[int, tuple[int, int]] = {}
        for contact_id, status, count in rows.tuples():
            if contact_id is None:
                continue
            queued, gone = counts.get(contact_id, (0, 0))
            if status in _WAITING:
                queued += count
            elif status in _GONE:
                gone += count
            counts[contact_id] = (queued, gone)
        return counts

    async def _contacts(self, host: str) -> list[ContactModel]:
        rows = await self._session.execute(
            select(ContactModel)
            .where(ContactModel.domain_id == self._ids[host])
            .order_by(ContactModel.id)
        )
        return list(rows.scalars().all())

    async def save(self, results: Sequence[LadderResult]) -> int:
        """Собрать исходы; при записи — снять прежние адреса и записать, как общий поиск."""
        for result in results:
            self.domains[result.host].result = result
            await self._clear(result)
        if self._write:
            donors = [r for r in results if r.host in self._donors]
            advertisers = [r for r in results if r.host in self._advertisers]
            if donors:
                await ContactRepository(self._session).save(donors)
            if advertisers:
                await AdvertiserContactRepository(self._session).save(advertisers)
        return sum(result.contact is not None for result in results)

    async def _clear(self, result: LadderResult) -> None:
        """Прежние адреса лестницы, кроме нового: снять или назвать, почему остаются."""
        if result.status not in CONCLUSIVE:
            return
        found = self.domains[result.host]
        keep = result.contact.email if result.contact else None
        old = [c for c in await self._contacts(result.host) if c.email != keep]
        refusals = await removal_refusals(self._session, old)
        letters = await self._letters([contact.id for contact in old if contact.id in refusals])
        for contact in old:
            if contact.id in refusals:
                found.kept.append(
                    (contact.email, _why_kept(refusals[contact.id], letters, contact))
                )
                continue
            found.removed.append(contact.email)
            if self._write:
                await self._session.delete(contact)
        if self._write:
            await self._session.flush()
            logger.info(
                "контакты заново: у %s снято %s, оставлено %s",
                result.host,
                len(found.removed),
                len(found.kept),
            )
