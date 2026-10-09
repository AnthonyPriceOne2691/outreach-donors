"""Адресаты оффера бизнесу ниши: кому из бизнесов ниши можно написать и о чём.

Бизнес ниши из выдачи прогона (`crawl/niche.py`) получает письмо, только
когда человек сказал «пишем»; дальше — те же правила, что у любого адресата
Этапа 2 (`recipients.Recipients`): свежий адрес, не в стоп-листе, не писали.

**О чём письмо.** Ссылки, которую «мы видели», у бизнеса ниши нет, и оффер
строится на другом (`compose.NicheOffer`): пример нашей площадки — принятый
донор того же прогона со свежей ценой, тема — ключи, по которым бизнес
нашёлся в выдаче, страна — страна прогона. Нет примера площадки со свежей
ценой — писать не о чем: оффер «разместим вас у таких-то» без площадки с
ценой обещает то, чего не продать.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from backend.config import filters as filters_cfg
from backend.features.contacts.preference import preferred_first
from backend.features.core.domain import Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel
from backend.features.crawl.niche import NICHE, niche_funnel
from backend.features.letters.compose import NicheOffer
from backend.features.letters.offer_addresses import advertiser_addresses
from backend.features.letters.recipients import Candidate, Recipients

#: Решение по донору, после которого он — наша площадка.
_ACCEPTED = "accepted"

#: Предел подсчёта для воронки: бизнесов ниши с решением «пишем» — единицы
#: и десятки, их решает человек по одному.
_EVERYONE = 10_000


@dataclass(frozen=True, slots=True)
class _Found:
    """Бизнес ниши и прогон, в выдаче которого он нашёлся."""

    run_id: int
    host: str


class NicheRecipients(Recipients):
    """Отбор бизнесов ниши на настоящей базе — правилами общего отбора."""

    async def niche_candidates(self, *, limit: int, now: datetime | None = None) -> list[Candidate]:
        """Бизнесы ниши, которым можно написать: свежие прогоны первыми."""
        moment = now or datetime.now(UTC)
        inner = (
            advertiser_addresses(AdvertiserModel.found_run_id.label("run_id"))
            .where(
                AdvertiserModel.source == NICHE,
                AdvertiserModel.confirmed_by_human.is_(True),
                AdvertiserModel.found_run_id.is_not(None),
            )
            .distinct(DomainModel.id)
            .order_by(DomainModel.id, *preferred_first())
        )
        inner = self._not_suppressed(inner, Stage.ADVERTISERS)
        inner = self._not_written(inner)
        picked = inner.subquery()
        rows = (
            await self._session.execute(
                select(picked).order_by(picked.c.run_id.desc(), picked.c.domain_id)
            )
        ).all()
        offers = await self._offers(rows, moment)
        return [
            Candidate(
                domain_id=row.domain_id,
                host=row.host,
                contact_id=row.contact_id,
                email=row.email,
                dr=None,
                niche=offers[(row.run_id, row.host)],
                attempt=row.attempt,
            )
            for row in rows
            if (row.run_id, row.host) in offers
        ][:limit]

    async def niche_report(self) -> dict[str, int]:
        """Воронка бизнесов ниши, последняя ступень — кому можно писать сейчас.

        «Ещё не писали» здесь — ровно те, кого возьмёт сборка: с решением
        «пишем», свежим адресом, вне стоп-листа и с примером площадки. Ноль
        при ненулевом «с адресом» — либо написаны все, либо у их прогонов нет
        принятого донора со свежей ценой.
        """
        ready = await self.niche_candidates(limit=_EVERYONE)
        return {**await niche_funnel(self._session), "ещё не писали": len(ready)}

    async def _offers(
        self, rows: Sequence[Any], moment: datetime
    ) -> dict[tuple[int, str], NicheOffer]:
        """Оффер на каждого: пример площадки прогона, тема и страна."""
        run_ids = sorted({row.run_id for row in rows})
        runs = (await self._session.scalars(select(RunModel).where(RunModel.id.in_(run_ids)))).all()
        examples = {run.id: await self._example_host(run, moment) for run in runs}
        return _offers_of(rows, {run.id: run for run in runs}, examples)

    async def offer_of(self, domain_id: int, now: datetime | None = None) -> NicheOffer | None:
        """Оффер бизнесу ниши сейчас — для правки письма, уже стоящего в очереди.

        Пересчитывается, а не хранится, — как ссылка у оффера по найденной
        ссылке: пример площадки мог потерять свежую цену, и мерить правку от
        оффера, которого больше не собрать, незачем. `None` — письмо пора
        убрать, а не править.
        """
        row = (
            await self._session.execute(
                select(DomainModel.host, AdvertiserModel.found_run_id)
                .join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
                .where(DomainModel.id == domain_id, AdvertiserModel.source == NICHE)
            )
        ).first()
        if row is None or row.found_run_id is None:
            return None
        offers = await self._offers([_Found(row.found_run_id, row.host)], now or datetime.now(UTC))
        return offers.get((row.found_run_id, row.host))

    async def _example_host(self, run: RunModel, moment: datetime) -> str | None:
        """Принятый донор прогона со свежей ценой — сильнейший по DR."""
        hosts = [str(host).lower() for host in (run.candidates or {}).get("hosts", [])]
        border = moment - timedelta(days=filters_cfg.PRICE_TTL_DAYS)
        host = await self._session.scalar(
            select(DomainModel.host)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(
                DomainModel.host.in_(hosts),
                DonorModel.review == _ACCEPTED,
                DonorModel.last_price.is_not(None),
                DonorModel.last_price_at >= border,
            )
            .order_by(DonorModel.dr.desc().nullslast(), DomainModel.id)
            .limit(1)
        )
        return None if host is None else str(host)


def _offers_of(
    rows: Sequence[Any], runs: dict[int, RunModel], examples: dict[int, str | None]
) -> dict[tuple[int, str], NicheOffer]:
    """Тема — первый ключ, по которому бизнес нашёлся; нет — первый ключ прогона."""
    offers: dict[tuple[int, str], NicheOffer] = {}
    for row in rows:
        run = runs.get(row.run_id)
        example = examples.get(row.run_id)
        if run is None or example is None:
            continue
        found_by = (run.candidates or {}).get("found_by", {})
        keys = list(found_by.get(row.host) or run.keywords or ())
        if not keys:
            continue
        offers[(row.run_id, row.host)] = NicheOffer(
            example_host=example, niche=str(keys[0]), donor_geo=run.country
        )
    return offers
