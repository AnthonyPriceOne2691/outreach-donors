"""Кого не берём в отбор вовсе — и чем это отличается от свежести.

Свежесть отвечает на вопрос «за этот домен уже заплачено», исключение —
на вопрос «этот домен нам не нужен». Числа складывать нельзя, и в отчёте
они стоят разными строками: первое радует, второе прячет домен от
оператора, и он обязан знать, почему тот пропал.

**Гейт стоит до сметы.** До этого среза стоп-лист проверялся только при
сборке очереди писем: домен, который отписался, пожаловался или лежит
в поставщиках, проходил весь платный путь — просев, метрики, страны —
и отсеивался в самом конце. На базе 22.09.2026 таких 47%: список
маленький только потому, что боевой рассылки ещё не было.

**Три причины, два источника.** Стоп-лист и поставщики лежат в разных
таблицах — первая про адресатов, вторая про доноров, — и сводить их
в одну здесь не время. Молчание своей записи не имеет вовсе: оно
считается по письмам, и теневая строка на каждого промолчавшего
завела бы второй источник правды там, где хватает первого.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import distinct, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core.domain import (
    MessageStatus,
    Stage,
    SuppressionReason,
    ThreadStatus,
)
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel

#: Письмо ушло от нас и не было отвергнуто. Отказ доставки сюда
#: не входит: до адресата мы не добрались, и считать это «мы ему уже
#: писали» значило бы похоронить донора из-за мёртвого ящика —
#: лестница контактов найдёт ему другой.
LEFT_OUR_HANDS = (MessageStatus.SENT, MessageStatus.DELIVERED)


class ExclusionReason(StrEnum):
    """Почему домен не пошёл в прогон. Оператор читает именно это."""

    STOPLIST = "stoplist"
    SUPPLIER = "supplier"
    SILENT = "silent"
    DECLINES = "declines"

    @property
    def caption(self) -> str:
        """Как причина называется в отчёте. Не `title`: у `str` он свой."""
        return _CAPTIONS[self]


_CAPTIONS = {
    ExclusionReason.STOPLIST: "в стоп-листе",
    ExclusionReason.SUPPLIER: "поставщик агентства",
    ExclusionReason.SILENT: "писали, не ответил",
    ExclusionReason.DECLINES: "ответил: размещений не продаёт",
}


def counts(excluded: dict[str, ExclusionReason]) -> dict[str, int]:
    """Сколько по каждой причине — в том виде, в каком это читают."""
    tally: dict[str, int] = {}
    for reason in excluded.values():
        tally[reason.caption] = tally.get(reason.caption, 0) + 1
    return tally


class Exclusions:
    """Три запроса к базе, ни одного к провайдеру.

    Поэтому гейт и стоит первым: он ничего не стоит, а отсекает то,
    за что иначе платят полный путь.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        silence_days: int = cfg.SILENCE_DAYS,
        decline_days: int = cfg.DECLINE_DAYS,
    ) -> None:
        self._session = session
        self._silence_days = silence_days
        self._decline_days = decline_days

    async def excluded_hosts(
        self,
        hosts: Sequence[str],
        *,
        stage: Stage = Stage.DONORS,
        now: datetime | None = None,
    ) -> dict[str, ExclusionReason]:
        """Домены, которые в прогон не идут, и почему.

        Причина у домена одна, хотя совпасть могут все. Порядок
        от слабой к сильной: молчание перебивается ответом «не продаём»,
        ответ — поставщиком, поставщик — стоп-листом. Иначе донор, который отписался
        и молчит, объяснялся бы оператору молчанием.
        """
        if not hosts:
            return {}

        moment = now or datetime.now(UTC)
        found: dict[str, ExclusionReason] = {}
        for host in await self._silent(hosts, stage, moment):
            found[host] = ExclusionReason.SILENT
        if stage is Stage.DONORS:
            # Только донорам: «не продаём размещения» — ответ про донорство.
            # Рекламодателем тот же сайт быть может — ему письмо о другом.
            for host in await self._declined(hosts, moment):
                found[host] = ExclusionReason.DECLINES
        for host in await self._suppliers(hosts, moment):
            found[host] = ExclusionReason.SUPPLIER
        found.update(await self._stoplisted(hosts, stage, moment))
        return found

    async def _declined(self, hosts: Sequence[str], moment: datetime) -> list[str]:
        """Сами ответили «не продаём размещения» — и не так давно."""
        border = moment - timedelta(days=self._decline_days)
        rows = await self._session.execute(
            select(DomainModel.host)
            .where(DomainModel.host.in_(hosts))
            .where(DomainModel.seller_answer == "declines")
            .where(DomainModel.seller_answer_at > border)
        )
        return [host for (host,) in rows.all()]

    async def _stoplisted(
        self, hosts: Sequence[str], stage: Stage, moment: datetime
    ) -> dict[str, ExclusionReason]:
        """Стоп-лист — только записи по домену.

        Запись на один адрес домена не закрывает: у сайта их несколько,
        и отказ секретаря не означает отказа редакции. При сборке письма
        такая запись всё равно сработает — там адресат уже известен.
        """
        rows = await self._session.execute(
            select(DomainModel.host, SuppressionModel.reason)
            .join(SuppressionModel, SuppressionModel.domain_id == DomainModel.id)
            .where(DomainModel.host.in_(hosts))
            .where(or_(SuppressionModel.stage.is_(None), SuppressionModel.stage == stage))
            .where(SuppressionModel.in_force(moment))
        )
        return {
            host: (
                ExclusionReason.SUPPLIER
                if reason is SuppressionReason.SUPPLIER
                else ExclusionReason.STOPLIST
            )
            for host, reason in rows.all()
        }

    async def _suppliers(self, hosts: Sequence[str], moment: datetime) -> set[str]:
        rows = await self._session.execute(
            select(SupplierDonorModel.host)
            .where(SupplierDonorModel.host.in_(hosts))
            .where(SupplierDonorModel.in_force(moment))
        )
        return set(rows.scalars().all())

    async def _silent(self, hosts: Sequence[str], stage: Stage, moment: datetime) -> set[str]:
        """Домены, которым письмо уходило, а ответа не было.

        Ответ смотрится по диалогу, а не по письму: отвечают с другого
        адреса чаще, чем кажется. Ответивший из отбора не выпадает —
        его держат сроки годности, и по ним же требование велит
        перезапросить цену, когда ей исполнится 150 дней.

        Считается по этапу, а не по кампании — тем же правилом, что
        и «кому мы ещё не писали» при сборке очереди. Этапы спрашивают
        разное: у донора цену, у рекламодателя размещение, — и молчание
        на первый вопрос не значит молчания на второй.

        Отдельной проверки «цепочка кончилась» здесь нет намеренно.
        Цепочка укладывается в две недели, а срок тишины — год: домен,
        которому пишут прямо сейчас, попадает под то же условие и точно
        так же не должен уходить в новый прогон.
        """
        border = moment - timedelta(days=self._silence_days)
        replied = (
            select(ThreadModel.domain_id)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .where(ThreadModel.status == ThreadStatus.REPLIED)
            .where(CampaignModel.stage == stage)
        )
        rows = await self._session.execute(
            select(distinct(DomainModel.host))
            .join(MessageModel, MessageModel.domain_id == DomainModel.id)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .where(DomainModel.host.in_(hosts))
            .where(CampaignModel.stage == stage)
            .where(MessageModel.status.in_(LEFT_OUR_HANDS))
            .where(MessageModel.sent_at.is_not(None))
            .where(MessageModel.sent_at > border)
            .where(DomainModel.id.not_in(replied))
        )
        return set(rows.scalars().all())
