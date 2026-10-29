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
  (`campaigns.run_id` → `runs.country`);
- рекламодателю по найденной ссылке — страна аудитории донора, на котором нашли ссылку
  (`FoundLink.donor_geo` строки очереди — та же, что во фразе письма про аудиторию): он купил
  место там, где его покупатели;
- бизнесу ниши — рынок прогона, из выдачи которого он пришёл (`advertisers.found_run_id` →
  `runs.country`): прогон искал выдачу этой страны.

Своей страны рекламодателя сервис не знает: трафика его сайта мы не покупаем. Страны нет или её
нет в таблице — запасной пояс `OUTREACH_AUTOSEND_ZONE` (Europe/Berlin): доноры и рекламодатели —
в основном в Европе. Письмо не ждёт вечно из-за неизвестной страны — уходит в рабочие часы
Центральной Европы.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
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
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import CampaignModel
from backend.features.core.models.run import RunModel
from backend.features.crawl.niche import NICHE
from backend.features.letters.repository import LetterRepository, QueuedLetter


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
    """Что известно о странах получателей писем прохода: трафик доноров, прогоны рассылок и бизнесов
    ниши, рынки прогонов."""

    #: Домен донора → страна его трафика.
    traffic: dict[int, str | None] = field(default_factory=dict)
    #: Рассылка → прогон, из которого её собрали.
    campaigns: dict[int, int | None] = field(default_factory=dict)
    #: Домен бизнеса ниши → прогон, в выдаче которого его нашли.
    found: dict[int, int | None] = field(default_factory=dict)
    #: Прогон → рынок.
    markets: dict[int, str] = field(default_factory=dict)

    def countries(self, letter: QueuedLetter) -> list[str | None]:
        """Страны получателя письма по порядку шапки — первая известная таблице и решит."""
        domain, campaign = letter.message.domain_id or 0, letter.message.campaign_id or 0
        if letter.stage is Stage.DONORS:
            return [self.traffic.get(domain), self.markets.get(self.campaigns.get(campaign) or 0)]
        if letter.audience == NICHE:
            return [self.markets.get(self.found.get(domain) or 0)]
        return [None if letter.link is None else letter.link.donor_geo]


async def _places(session: AsyncSession, letters: Sequence[QueuedLetter]) -> _Places:
    """Страны получателей одним заходом на все письма прохода: у рекламодателя по ссылке страна —
    уже в строке очереди, запроса не нужно."""
    donors = [letter for letter in letters if letter.stage is Stage.DONORS]
    niche = [
        letter
        for letter in letters
        if letter.stage is Stage.ADVERTISERS and letter.audience == NICHE
    ]
    domains = {letter.message.domain_id for letter in donors}
    campaign_ids = {letter.message.campaign_id for letter in donors}
    found_domains = {letter.message.domain_id for letter in niche}
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
    campaigns = dict(
        (
            await session.execute(
                select(CampaignModel.id, CampaignModel.run_id).where(
                    CampaignModel.id.in_(campaign_ids)
                )
            )
        )
        .tuples()
        .all()
    )
    found = dict(
        (
            await session.execute(
                select(AdvertiserModel.domain_id, AdvertiserModel.found_run_id).where(
                    AdvertiserModel.domain_id.in_(found_domains)
                )
            )
        )
        .tuples()
        .all()
    )
    runs = {run for run in [*campaigns.values(), *found.values()] if run is not None}
    markets = dict(
        (await session.execute(select(RunModel.id, RunModel.country).where(RunModel.id.in_(runs))))
        .tuples()
        .all()
    )
    return _Places(traffic=traffic, campaigns=campaigns, found=found, markets=markets)


def _zone(countries: Sequence[str | None]) -> ZoneInfo:
    """Пояс первой страны, известной таблице, — или запасной."""
    return window.zone_of(zone_name(country) for country in countries) or fallback()


async def zones(session: AsyncSession, letters: Sequence[QueuedLetter]) -> dict[int, ZoneInfo]:
    """Пояс получателя каждого письма (номер письма → пояс) — первой известной страной."""
    if not letters:
        return {}
    places = await _places(session, letters)
    return {letter.message.id: _zone(places.countries(letter)) for letter in letters}


@dataclass(frozen=True, slots=True)
class Open:
    """Письма прохода в окне своих получателей — по порядку очереди — и когда откроется
    ближайшее окно у остальных."""

    ids: list[int]
    #: Первое письмо в окне — по нему проход спрашивает, есть ли свободный ящик.
    first: QueuedLetter | None
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


async def in_window(session: AsyncSession, letters: Sequence[QueuedLetter], now: datetime) -> Open:
    """Какие письма из `letters` (по порядку очереди) можно слать в минуту `now`."""
    frame_ = frame()
    found = await zones(session, letters)
    ids: list[int] = []
    first: QueuedLetter | None = None
    nearest: tuple[datetime, ZoneInfo] | None = None
    for letter in letters:
        zone = found.get(letter.message.id, fallback())
        opens = window.opening(frame_, zone, now)
        if opens <= now:
            ids.append(letter.message.id)
            first = first or letter
        elif nearest is None or opens < nearest[0]:
            nearest = (opens, zone)
    return Open(ids, first, nearest)


async def queue_zone(session: AsyncSession, stage: Stage) -> ZoneInfo:
    """Самый частый пояс получателей очереди этапа — прогнозу рассылки (`forecast.py`)."""
    queued = await LetterRepository(session).queued(stage=stage)
    counted = Counter((await zones(session, queued)).values())
    return counted.most_common(1)[0][0] if counted else fallback()
