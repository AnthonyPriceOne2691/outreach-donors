"""Письмо рекламодателю перед отправкой: адресат ещё рекламодатель, цена донора свежая.

Обе проверки — при отправке, а не только при сборке (`recipients.py`): между сборкой
и нажатием «Отправить» проходят часы, а то и дни, и за это время меняются и список
рекламодателей, и цена донора. Здесь — только почему письмо не уходит, словами для
экрана; класс отказа выбирает отправка (`sending.Sending._check_advertiser`).

**Рекламодатель не снят.** Перевод кандидатов снимает тех, кого стоп-лист поставщиков
или общий отсеял задним числом: донор стал партнёром — и его рекламодатели теперь
чужие клиенты. Письмо, собранное до этого, уйти не должно, а добивка — тем более:
для цепочки это как стоп-лист.

**Цена донора свежая** (сверка ТЗ 12.10.2026). Оффер «мы дешевле» держится на цене
донора, под чью ссылку он написан, хотя и не называет её: поэтому сборка берёт только
свежую — не старше 150 дней, а старше — сначала перезапрос цены. Пока письмо ждёт
в очереди, донор мог ответить «не продаём» — такой ответ снимает цену
(`donors/price.py`), — или цена успела устареть, и без этой проверки оффер ушёл бы
всё равно. Правило свежести то же, что у сборки (`Recipients._fresh_price`).

Донор — площадка лучшей ссылки рекламодателя (`advertisers.best_donor_host`): под неё
письмо собрано, её же показывают очередь и правка письма. Своего донора письмо
не помнит, и если пересчёт обхода сменил лучшую ссылку после сборки, проверяется
цена новой площадки.

Цена проверяется только у самого оффера — первого письма по найденной ссылке:

- добивка цену заново не обещает и донора не называет; отказ ей значил бы обрыв
  цепочки или перенос раз в час до перезапроса цены — решения, которого
  в требованиях нет;
- ответ в переписке ведёт человек, и что он обещает, он видит сам;
- у оффера бизнесу ниши ссылки нет: пример площадки он берёт при сборке
  (`niche_recipients.py`), а строка рекламодателя его не хранит.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import filters as filters_cfg
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import MessageModel
from backend.features.crawl.niche import NICHE
from backend.features.letters.chain import FIRST_STEP


@dataclass(frozen=True, slots=True)
class OfferRefusal:
    """Почему письмо рекламодателю не уходит — словами для экрана."""

    words: str
    #: Рекламодателя сняли — для цепочки это как стоп-лист. Иначе отказ — по цене донора.
    removed: bool = False


async def offer_refusal(
    session: AsyncSession, message: MessageModel, *, host: str, now: datetime
) -> OfferRefusal | None:
    """Почему письмо `message` рекламодателю `host` уйти не может. `None` — может."""
    advertiser = (
        await session.execute(
            select(AdvertiserModel.source, AdvertiserModel.best_donor_host).where(
                AdvertiserModel.domain_id == message.domain_id
            )
        )
    ).first()
    if advertiser is None:
        return OfferRefusal(
            f"Рекламодателя {host} сняли после сборки письма №{message.id}: "
            "его отсеял стоп-лист поставщиков или общий. Письмо стоит убрать из очереди",
            removed=True,
        )
    if message.step != FIRST_STEP or advertiser.source == NICHE:
        return None
    donor = (advertiser.best_donor_host or "").strip().lower()
    stale = await _stale_price(session, donor, now)
    if stale is None:
        return None
    return OfferRefusal(
        f"Цена донора {donor or 'ссылки'} устарела или снята — {stale}. Письмо "
        f"№{message.id} рекламодателю {host} стоит убрать или перезапросить цену: "
        "оффер «мы дешевле» держится на ней"
    )


async def _stale_price(session: AsyncSession, donor: str, now: datetime) -> str | None:
    """Что не так с ценой донора `donor`, словами. `None` — цена свежая."""
    if not donor:
        return "площадка ссылки рекламодателя не записана"
    price = (
        await session.execute(
            select(DonorModel.last_price, DonorModel.last_price_at)
            .join(DomainModel, DomainModel.id == DonorModel.domain_id)
            .where(DomainModel.host == donor)
        )
    ).first()
    ttl = filters_cfg.PRICE_TTL_DAYS
    if price is None or price.last_price is None:
        return "цены у донора нет"
    if price.last_price_at is None:
        return "у цены нет даты"
    if price.last_price_at >= now - timedelta(days=ttl):
        return None
    return f"названа {price.last_price_at:%d.%m.%Y}, а свежей цена считается {ttl} дней"
