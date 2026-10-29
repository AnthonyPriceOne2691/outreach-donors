"""Окно получателя у очереди, которая уходит сама (`autosend.py`): какие письма прохода — в рабочие
часы своих получателей.

Холодное письмо в три часа ночи по времени получателя читают утром среди ночной почты — чаще
всего как спам. У продаж окно — политика этапа и судит каждое письмо (`core/stages.mail_policy`).
Письмо доноров и рекламодателей по одному и пачку отправляет человек, когда решил, — окна у них
нет. Окно — только у прохода, который шлёт без человека: письмо вне рабочих часов получателя ждёт
в очереди, а проход берёт тех, у кого окно открыто сейчас. Окно — `OUTREACH_AUTOSEND_DAYS` и
`OUTREACH_AUTOSEND_HOURS` (по умолчанию пн–пт 09:00–18:00) по часам получателя (`core/window`).

**Пояс — по стране получателя**, первой известной в таком порядке (`core/country_zones`):
- донору — страна, откуда у донора больше всего трафика (`donors.geo`, верх топ-5): там его
  читатели и, как правило, он сам; нет — рынок прогона, из которого собрана рассылка
  (`runs.country`);
- рекламодателю по найденной ссылке — страна донора, на котором нашли ссылку
  (`advertisers.best_donor_host` → `donors.geo`): он купил место там, где его покупатели; нет —
  рынок прогона, в котором его нашли (`advertisers.found_run_id`);
- бизнесу ниши — рынок прогона, из выдачи которого он пришёл (`found_run_id` → `runs.country`):
  прогон искал выдачу этой страны; нет — страна донора-примера.

Своей страны рекламодателя сервис не знает: трафика его сайта мы не покупаем. Страны нет или её
нет в таблице — запасной пояс `OUTREACH_AUTOSEND_ZONE` (Europe/Berlin): доноры и рекламодатели —
в основном в Европе. Письмо не ждёт вечно из-за неизвестной страны — уходит в рабочие часы
Центральной Европы.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core import window
from backend.features.core.country_zones import zone_name
from backend.features.core.domain import Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.core.models.run import RunModel
from backend.features.crawl.niche import NICHE
from backend.features.letters.repository import LetterRepository


def frame() -> window.SendWindow:
    """Окно получателя у прохода — из настроек."""
    return window.SendWindow(
        days=cfg.AUTOSEND_DAYS, start=cfg.AUTOSEND_OPENS, end=cfg.AUTOSEND_CLOSES
    )


def fallback() -> ZoneInfo:
    """Пояс, когда страна получателя неизвестна."""
    return ZoneInfo(cfg.AUTOSEND_ZONE)


@dataclass(frozen=True, slots=True)
class _Places:
    """Что известно о странах получателей писем: трафик доноров, донор-пример и прогон
    рекламодателей, рынки прогонов."""

    #: Домен донора → страна его трафика.
    traffic: dict[int, str | None] = field(default_factory=dict)
    #: Домен рекламодателя → (донор, на котором нашли ссылку; прогон, где нашли).
    found: dict[int, tuple[str | None, int | None]] = field(default_factory=dict)
    #: Донор по имени → страна его трафика.
    hosts: dict[str, str | None] = field(default_factory=dict)
    #: Прогон → рынок.
    markets: dict[int, str] = field(default_factory=dict)

    def countries(
        self, stage: Stage, audience: str, domain_id: int | None, run_id: int | None
    ) -> list[str | None]:
        """Страны получателя письма по порядку шапки — первая известная таблице и решит."""
        if stage is Stage.DONORS:
            return [self.traffic.get(domain_id or 0), self.markets.get(run_id or 0)]
        host, found_run = self.found.get(domain_id or 0, (None, None))
        donor = self.hosts.get(host or "")
        market = self.markets.get(found_run or 0)
        return [market, donor] if audience == NICHE else [donor, market]


async def _places(session: AsyncSession, domains: set[int], runs: set[int]) -> _Places:
    """Страны получателей одним заходом на все письма прохода."""
    traffic = dict(
        (
            await session.execute(
                select(DonorModel.domain_id, DonorModel.geo).where(
                    DonorModel.domain_id.in_(domains)
                )
            )
        )
        .tuples()
        .all()
    )
    found = {
        domain: (host, run)
        for domain, host, run in (
            await session.execute(
                select(
                    AdvertiserModel.domain_id,
                    AdvertiserModel.best_donor_host,
                    AdvertiserModel.found_run_id,
                ).where(AdvertiserModel.domain_id.in_(domains))
            )
        )
        .tuples()
        .all()
    }
    hosts = {host for host, _ in found.values() if host}
    by_host = dict(
        (
            await session.execute(
                select(DomainModel.host, DonorModel.geo)
                .join(DonorModel, DonorModel.domain_id == DomainModel.id)
                .where(DomainModel.host.in_(hosts))
            )
        )
        .tuples()
        .all()
    )
    runs |= {run for _, run in found.values() if run is not None}
    markets = dict(
        (await session.execute(select(RunModel.id, RunModel.country).where(RunModel.id.in_(runs))))
        .tuples()
        .all()
    )
    return _Places(traffic=traffic, found=found, hosts=by_host, markets=markets)


def _zone(countries: Iterable[str | None]) -> ZoneInfo:
    """Пояс первой страны, известной таблице, — или запасной."""
    return window.zone_of(zone_name(country) for country in countries) or fallback()


async def zones(session: AsyncSession, letter_ids: Collection[int]) -> dict[int, ZoneInfo]:
    """Пояс получателя каждого письма — первой известной страной (порядок — в шапке)."""
    if not letter_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(
                    MessageModel.id,
                    MessageModel.domain_id,
                    CampaignModel.stage,
                    CampaignModel.audience,
                    CampaignModel.run_id,
                )
                .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
                .where(MessageModel.id.in_(letter_ids))
            )
        )
        .tuples()
        .all()
    )
    domains = {domain for _, domain, *_ in rows if domain is not None}
    runs = {run for *_, run in rows if run is not None}
    places = await _places(session, domains, runs)
    return {
        letter: _zone(places.countries(stage, audience, domain, run))
        for letter, domain, stage, audience, run in rows
    }


@dataclass(frozen=True, slots=True)
class Open:
    """Письма прохода в окне своих получателей — по порядку очереди — и когда откроется
    ближайшее окно у остальных."""

    ids: list[int]
    #: Первое письмо в окне — по нему проход спрашивает, есть ли свободный ящик.
    first: MessageModel | None
    #: Открытие ближайшего окна среди писем вне окна и пояс его получателя.
    nearest: tuple[datetime, ZoneInfo] | None = None

    @property
    def words(self) -> str:
        """Почему проход никого не взял: у всех получателей не рабочее время — и когда начнётся."""
        if self.nearest is None:
            return "ждёт рабочего времени получателей"
        opens, zone = self.nearest
        local = opens.astimezone(zone)
        return (
            f"ждёт рабочего времени получателей — ближайшее откроется в {local:%H:%M} по их "
            f"времени ({window.WEEKDAYS[local.weekday()]} {local:%d.%m}, {zone.key})"
        )


async def in_window(session: AsyncSession, letters: Sequence[MessageModel], now: datetime) -> Open:
    """Какие письма из `letters` (по порядку очереди) можно слать в минуту `now`."""
    frame_ = frame()
    found = await zones(session, [letter.id for letter in letters])
    ids: list[int] = []
    first: MessageModel | None = None
    nearest: tuple[datetime, ZoneInfo] | None = None
    for letter in letters:
        zone = found.get(letter.id, fallback())
        opens = window.opening(frame_, zone, now)
        if opens <= now:
            ids.append(letter.id)
            first = first or letter
        elif nearest is None or opens < nearest[0]:
            nearest = (opens, zone)
    return Open(ids, first, nearest)


async def queue_zone(session: AsyncSession, stage: Stage) -> ZoneInfo:
    """Самый частый пояс получателей очереди этапа — прогнозу рассылки (`forecast.py`)."""
    queued = await LetterRepository(session).queued(stage=stage)
    counted = Counter((await zones(session, [row.message.id for row in queued])).values())
    return counted.most_common(1)[0][0] if counted else fallback()
