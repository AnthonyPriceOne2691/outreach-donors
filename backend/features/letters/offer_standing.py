"""Письмо рекламодателю перед отправкой: адресат ещё рекламодатель, площадка оффера —
не из «кому не пишем», ссылка письма — действующая, цена донора — свежая.

Проверки — при отправке, а не только при сборке (`recipients.py`): между сборкой
и нажатием «Отправить» проходят часы, а то и дни, и за это время меняются и список
рекламодателей, и их ссылки, и цена донора. `offer_refusal` говорит только, почему
письмо не уходит, — причиной (`OfferRefused`) и словами для экрана; класс отказа по
причине выбирает отправка (`sending.Sending._check_advertiser`). Письмо, которому ждать
в очереди нечего, — под снятую ссылку или на площадке из «кому не пишем», — `check_offer`
останавливает своей фиксацией: иначе оно стояло бы в очереди готовым, и пачка
спотыкалась бы о него каждый раз.

Порядок — от главного: снят рекламодатель → площадка из «кому не пишем» → снята или
сменилась ссылка → цена донора.

**Рекламодатель не снят.** Его снимают «не пишем» человека (`crawl/promote.withdraw`)
и перевод — тех, кого стоп-лист поставщиков или общий отсеял задним числом: донор
стал партнёром, и его рекламодатели теперь чужие клиенты. Письмо, собранное до этого,
уйти не должно, а добивка — тем более: для цепочки это как стоп-лист.

**Оффер — под действующую ссылку рекламодателя** (ревью 12.10.2026: рекламодателю
пишем под действующую ссылку). Первое письмо рассылки «по ссылке» написано про
страницу, и она обязана быть его нынешней лучшей ссылкой (`advertisers.best_page_url`)
и не снятой человеком. Иначе письмо ушло бы под ссылку, которой нет: человек сказал
«не пишем», повторный обход сменил страницу, письмо вернули в очередь после
неизвестного исхода, две сборки или два решения разошлись во времени. Такое письмо
останавливается со своей причиной (`attempts.LINK_WITHDRAWN`) и следующую сборку
не держит — она пишет под действующую ссылку.

Решение «не пишем», сказанное до того, как стало доходить до рекламодателя, и не
доведённое ещё сборкой или переводом (`promote.apply_rejections`), держит здесь же:
у домена остались одни снятые кандидаты — первое письмо останавливается, а добивке
отказ, как у снятого рекламодателя.

**Площадка — не из «кому не пишем»** (12.10.2026, `runs.exclusions.not_writing`). Сборка
таких площадок рекламодателю не предлагает (`Recipients._fresh_price`), а письмо оффера,
собранное или ушедшее до правила, при отправке останавливается с причиной, как письмо
донору из этого списка (`donor_standing.stop_for_good`). Проверяется каждое письмо
оффера по найденной ссылке — и первое, и добивки: предложение такой площадки
невыполнимо, а добивка его повторяет. Ждать в очереди такому письму нечего: ни
перезапрос цены, ни решение человека площадку в предложение не вернут.

**Цена донора свежая** (сверка ТЗ 12.10.2026). Оффер «мы дешевле» держится на цене
донора, под чью ссылку он написан, хотя и не называет её: поэтому сборка берёт только
свежую — не старше 150 дней, а старше — сначала перезапрос цены. Пока письмо ждёт
в очереди, донор мог ответить «не продаём» — такой ответ снимает цену
(`donors/price.py`), — или цена успела устареть, и без этой проверки оффер ушёл бы
всё равно. Правило свежести то же, что у сборки (`Recipients._fresh_price`).

Донор — площадка лучшей ссылки рекламодателя (`advertisers.best_donor_host`): под неё
письмо собрано, её же показывают очередь и правка письма. Своего донора письмо
не помнит, и если пересчёт обхода сменил лучшую ссылку после сборки, письмо
останавливает проверка ссылки раньше, чем дойдёт до цены.

Цена проверяется только у самого оффера — первого письма: добивка донора не называет
и цену заново не обещает, и отказ ей значил бы обрыв цепочки или перенос раз в час
до перезапроса цены — решения, которого в требованиях нет; ответ в переписке ведёт
человек, и что он обещает, он видит сам.

**Бизнесу ниши** ссылки нет — какая проверка, решает аудитория рассылки, как и у сборки.
Его оффер «дешевле» держится на цене примера площадки, который письмо называет
(`advertiser_niche.txt`): принятого донора прогона, где бизнес нашёлся, со свежей ценой
и не из «кому не пишем» — правилом сборки (`NicheRecipients.fresh_examples`). Своего
примера письмо не помнит, и годится любой такой донор, названный в тексте: появился
донор сильнее — письмо со своим примером уходит, а пример, потерявший цену или решение
«принят», письмо держит. Пример из «кому не пишем» письмо останавливает с причиной, как
площадку оффера по ссылке: ждать ему нечего. Решение человека о самом бизнесе держит
`niche.decide`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.config import filters as filters_cfg
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.core.models.run import RunModel
from backend.features.crawl.advertiser_rules import writes_to
from backend.features.crawl.niche import NICHE
from backend.features.crawl.offer_stop import out_of_queue
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.donor_standing import stop_for_good
from backend.features.letters.niche_recipients import NicheRecipients, run_hosts
from backend.features.runs.exclusions import not_writing_reason


class OfferRefused(StrEnum):
    """Почему письмо рекламодателю не уходит — от главного; класс отказа по ней выбирает
    отправка (`sending._OFFER_REFUSED`)."""

    #: Рекламодателя сняли — для цепочки это как стоп-лист.
    REMOVED = "removed"
    #: Площадка оффера — в «кому не пишем»: письмо остановлено с причиной, цепочка кончается.
    NOT_WRITING = "not_writing"
    #: Письмо собрано под ссылку, которой у рекламодателя больше нет: оно останавливается
    #: со своей причиной, и следующее соберётся под действующую.
    WITHDRAWN = "withdrawn"
    #: Цена донора или примера площадки устарела или снята: письмо ждёт в очереди
    #: перезапроса цены.
    STALE_PRICE = "stale_price"


@dataclass(frozen=True, slots=True)
class OfferRefusal:
    """Почему письмо рекламодателю не уходит — причиной и словами для экрана."""

    words: str
    reason: OfferRefused


async def check_offer(
    session: AsyncSession, message: MessageModel, *, host: str, now: datetime
) -> OfferRefusal | None:
    """Проверка отправки: `offer_refusal`, а письмо, которому ждать нечего, — остановлено
    своей фиксацией: под снятую ссылку — `crawl/offer_stop.out_of_queue`, на площадке
    из «кому не пишем» — с причиной (`donor_standing.stop_for_good`)."""
    refused = await offer_refusal(session, message, host=host, now=now)
    if refused is None:
        return None
    if refused.reason is OfferRefused.WITHDRAWN:
        await out_of_queue(session, message)
        await session.commit()
    elif refused.reason is OfferRefused.NOT_WRITING:
        await stop_for_good(session, message, refused.words)
    return refused


async def offer_refusal(
    session: AsyncSession, message: MessageModel, *, host: str, now: datetime
) -> OfferRefusal | None:
    """Почему письмо `message` рекламодателю `host` уйти не может. `None` — может."""
    advertiser = (
        await session.execute(
            select(AdvertiserModel.best_page_url, AdvertiserModel.best_donor_host).where(
                AdvertiserModel.domain_id == message.domain_id
            )
        )
    ).first()
    if advertiser is None:
        return OfferRefusal(_removed(host, message), OfferRefused.REMOVED)
    audience = await session.scalar(
        select(CampaignModel.audience).where(CampaignModel.id == message.campaign_id)
    )
    if audience == NICHE:
        return await _niche_example(session, message, host=host, now=now)
    return await _link_offer(
        session,
        message,
        host=host,
        page=advertiser.best_page_url,
        donor=(advertiser.best_donor_host or "").strip().lower(),
        now=now,
    )


async def _link_offer(
    session: AsyncSession,
    message: MessageModel,
    *,
    host: str,
    page: str | None,
    donor: str,
    now: datetime,
) -> OfferRefusal | None:
    """Письмо оффера по найденной ссылке — в порядке от главного (шапка модуля). Площадка
    проверяется у каждого письма оффера, ссылка и цена — у первого."""
    first = message.step == FIRST_STEP
    only_rejected = await _only_rejected(session, host)
    if only_rejected and not first:
        return OfferRefusal(_removed(host, message), OfferRefused.REMOVED)
    off = await _not_writing(session, donor)
    if off is not None:
        return OfferRefusal(
            f"Письмо №{message.id} остановлено: площадка оффера {donor} — из «кому не пишем» "
            f"({off}). Рекламодателю {host} её не предлагаем",
            OfferRefused.NOT_WRITING,
        )
    if not first:
        return None
    if (
        not page
        or page not in (message.body or "")
        or only_rejected
        or await _rejected_link(session, host, page)
    ):
        return OfferRefusal(
            f"Письмо №{message.id} собрано под ссылку, которой у рекламодателя {host} больше "
            "нет: её сняли «не пишем» или сменил повторный обход. Письмо остановлено",
            OfferRefused.WITHDRAWN,
        )
    stale = await _stale_price(session, donor, now)
    if stale is None:
        return None
    return OfferRefusal(
        f"Цена донора {donor or 'ссылки'} устарела или снята — {stale}. Письмо "
        f"№{message.id} рекламодателю {host} стоит убрать или перезапросить цену: "
        "оффер «мы дешевле» держится на ней",
        OfferRefused.STALE_PRICE,
    )


async def _niche_example(
    session: AsyncSession, message: MessageModel, *, host: str, now: datetime
) -> OfferRefusal | None:
    """Пример площадки в первом письме бизнесу ниши — ещё принятый донор со свежей ценой
    и не из «кому не пишем» (`NicheRecipients.fresh_examples`).

    Пример из «кому не пишем» — своя причина, и письмо останавливается, как оффер по
    ссылке на такой площадке: ни перезапрос цены, ни решение человека пример в
    предложение не вернут. До 12.10.2026 отказ говорил про цену, и письмо стояло
    в очереди, советуя перезапросить цену."""
    if message.step != FIRST_STEP:
        return None
    run = await session.scalar(
        select(RunModel)
        .join(AdvertiserModel, AdvertiserModel.found_run_id == RunModel.id)
        .where(AdvertiserModel.domain_id == message.domain_id)
    )
    body = message.body or ""
    examples = [] if run is None else await NicheRecipients(session).fresh_examples(run, now)
    if any(_named(example, body) for example in examples):
        return None
    for example in [] if run is None else run_hosts(run):
        off = await _not_writing(session, example) if _named(example, body) else None
        if off is not None:
            return OfferRefusal(
                f"Письмо №{message.id} остановлено: пример площадки {example} — из «кому не "
                f"пишем» ({off}). Бизнесу ниши {host} её не предлагаем",
                OfferRefused.NOT_WRITING,
            )
    return OfferRefusal(
        f"Цена примера площадки в письме №{message.id} бизнесу ниши {host} устарела или "
        "снята: принятого донора его прогона со свежей ценой письмо не называет. Письмо "
        "стоит убрать — следующая сборка возьмёт пример со свежей ценой — или перезапросить "
        "цену: оффер «дешевле» держится на ней",
        OfferRefused.STALE_PRICE,
    )


def _named(host: str, text: str) -> bool:
    """Площадка `host` названа в тексте целым именем, а не куском другого."""
    pattern = rf"(?<![\w.-]){re.escape(host)}(?!\.?[\w-])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _removed(host: str, message: MessageModel) -> str:
    return (
        f"Рекламодателя {host} сняли после сборки письма №{message.id}: «не пишем» "
        "человека, стоп-лист поставщиков или общий. Письмо стоит убрать из очереди"
    )


async def _not_writing(session: AsyncSession, donor: str) -> str | None:
    """Почему площадке `donor` не пишем — словами (`runs.exclusions`); `None` — пишем."""
    domain_id = await session.scalar(select(DomainModel.id).where(DomainModel.host == donor))
    return None if domain_id is None else await not_writing_reason(session, domain_id)


async def _only_rejected(session: AsyncSession, host: str) -> bool:
    """У домена есть снятые человеком кандидаты, а подходящих нет."""
    rejected = await session.scalar(
        select(CandidateModel.id)
        .where(CandidateModel.target_root == host, CandidateModel.confirmed.is_(False))
        .limit(1)
    )
    if rejected is None:
        return False
    other = aliased(CandidateModel)
    chosen = await session.scalar(
        select(other.id).where(other.target_root == host, writes_to(other)).limit(1)
    )
    return chosen is None


async def _rejected_link(session: AsyncSession, host: str, page: str) -> bool:
    """Лучшая ссылка рекламодателя — ссылка кандидата, о котором сказали «не пишем»."""
    found = await session.scalar(
        select(CandidateModel.id)
        .where(
            CandidateModel.target_root == host,
            CandidateModel.best_page_url == page,
            CandidateModel.confirmed.is_(False),
        )
        .limit(1)
    )
    return found is not None


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
