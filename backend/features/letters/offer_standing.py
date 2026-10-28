"""Письмо рекламодателю перед отправкой: адресат ещё рекламодатель (бизнес ниши — после
«пишем» человека), площадка оффера — не из «кому не пишем» и не поставщик, ссылка письма —
действующая, цена донора — свежая.

Проверки — при отправке, а не только при сборке (`recipients.py`): между сборкой
и нажатием «Отправить» проходят часы, а то и дни, и за это время меняются и список
рекламодателей, и их ссылки, и цена донора. `offer_refusal` говорит только, почему
письмо не уходит, — причиной (`OfferRefused`) и словами для экрана; класс отказа по
причине выбирает отправка (`sending.Sending._check_advertiser`). Письмо, которому ждать
в очереди нечего, — под снятую ссылку, на площадке из «кому не пишем» или поставщика,
бизнесу ниши из «кому не пишем», с примером оттуда или после «не пишем» человека о нём, —
`check_offer` останавливает своей фиксацией: иначе оно стояло бы в очереди готовым,
и пачка спотыкалась бы о него каждый раз.

Порядок — от главного: снят рекламодатель → площадка из «кому не пишем» → площадка
поставщика → снята или сменилась ссылка → цена донора.

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

**Площадка — не поставщик агентства** (сверка ТЗ 12.10.2026, `crawl/suppliers.supplier_hosts`).
Поставщика вносят в стоп-лист задним числом — после перевода и сборки, — и первое письмо
под ссылку на его площадке, ещё ждущее в очереди, снимается своей причиной
(`attempts.SUPPLIER_WITHDRAWN`), как под снятую ссылку: оно не уходило, «писали» не
считается, и сборка пишет рекламодателю под ссылку другого донора, если она есть
(`promote.apply_suppliers`). Добивка ушедшего оффера держится тем же правилом, что после
«не пишем» по ссылке: остался рекламодатель — переписка идёт, снят — цепочка кончается.

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
«принят», письмо держит. Письмо с примером из «кому не пишем» снимается, как под снятую
ссылку (`attempts.EXAMPLE_WITHDRAWN`): оно не уходило, и следующая сборка напишет бизнесу
с примером оттуда, где пишем.

**Сам бизнес ниши — не из «кому не пишем»** (сверка ТЗ 12.10.2026: список действует на всех
путях). Правило — сайта, а не ссылки (`runs.exclusions.site_not_writing`): имя домена и DR
его замера в прогоне, где бизнес нашёлся. Первое письмо и добивка такому бизнесу
останавливаются с причиной (`donor_standing.stop_for_good`) — его снова не напишут;
ответ в переписке не держится: бизнес написал сам, как донор.

**Бизнесу ниши — только после «пишем» человека** (28.10.2026), тем же условием, что у списка
рекламодателей и поиска адреса (`advertiser_rules.in_work`). «Не пишем» снимает его письма
из очереди и сроки добивок сразу (`niche.decide`), а письмо в «отправляется» не трогает — его
исход решает человек. Отсюда щель: письмо или добивка висели в «отправляется» в минуту
решения, человек не нашёл их в журнале платформы и вернул в очередь (`unknown_outcome.py`),
и пачка или проход добивок отправили бы их бизнесу, о котором сказали «не пишем». Поэтому
решение перепроверяет и отправка, как решение по донору (`donor_standing.py`):

- **«не пишем»** — первое письмо и добивка останавливаются с причиной
  (`donor_standing.stop_for_good`), для цепочки это снятый рекламодатель — она кончается;
- **решения нет** — письмо ждёт его, разговор не рвётся, как у донора, вернувшегося
  в «предложен». Без решения письмо не собирается (сборка берёт только «пишем»,
  `niche_recipients.py`; перевод по ссылке решение бизнеса не переписывает,
  `promote._write`), а у собранного решения нет, только если строку бизнеса после сборки
  сняли (стоп-листы перевода, `promote._drop`) и сбор прогона завёл её заново: человек
  решает о нём заново. Остановить письмо до его решения значило бы потерять бизнес молча —
  остановленное письмо держит адрес как «писали», и после «пишем» новое не соберётся.

Порядок у бизнеса ниши — от главного: «не пишем» человека → сам бизнес из «кому не пишем» →
решения нет (бизнесу из списка ждать решения нечего) → пример площадки. Ответ в переписке
ни решение, ни «кому не пишем» не держат: бизнес написал сам.
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
from backend.features.crawl.advertiser_rules import in_work, writes_to
from backend.features.crawl.niche import NICHE
from backend.features.crawl.offer_stop import out_of_queue
from backend.features.crawl.suppliers import supplier_hosts
from backend.features.letters import attempts
from backend.features.letters.chain import ANSWER_STEP, FIRST_STEP
from backend.features.letters.donor_standing import stop_for_good
from backend.features.letters.niche_recipients import NicheRecipients, run_hosts
from backend.features.runs.exclusions import not_writing_reason, site_not_writing_reason


class OfferRefused(StrEnum):
    """Почему письмо рекламодателю не уходит — от главного; класс отказа по ней выбирает
    отправка (`sending._OFFER_REFUSED`)."""

    #: Рекламодателя сняли — для цепочки это как стоп-лист.
    REMOVED = "removed"
    #: Бизнесу ниши человек сказал «не пишем»: письмо остановлено с причиной, цепочка
    #: кончается, как у снятого рекламодателя.
    DECLINED = "declined"
    #: Площадка оффера — в «кому не пишем»: письмо остановлено с причиной, цепочка кончается.
    NOT_WRITING = "not_writing"
    #: Бизнес ниши снова ждёт решения человека: письмо ждёт, разговор не рвётся.
    UNDECIDED = "undecided"
    #: Письмо собрано под ссылку, которой у рекламодателя больше нет: оно останавливается
    #: со своей причиной, и следующее соберётся под действующую.
    WITHDRAWN = "withdrawn"
    #: Площадка ссылки письма — поставщик агентства, внесённый после сборки: письмо
    #: снимается со своей причиной, следующее соберётся под ссылку другого донора.
    SUPPLIER = "supplier"
    #: Пример площадки в письме бизнесу ниши — из «кому не пишем»: письмо снимается со своей
    #: причиной, как под снятую ссылку, и следующая сборка напишет с другим примером.
    EXAMPLE = "example"
    #: Цена донора или примера площадки устарела или снята: письмо ждёт в очереди
    #: перезапроса цены.
    STALE_PRICE = "stale_price"


#: Письма, которые снимаются как не уходившие (`attempts.WITHDRAWN_REASONS`), — их причиной.
_NOT_SENT = {
    OfferRefused.WITHDRAWN: attempts.LINK_WITHDRAWN,
    OfferRefused.EXAMPLE: attempts.EXAMPLE_WITHDRAWN,
    OfferRefused.SUPPLIER: attempts.SUPPLIER_WITHDRAWN,
}
#: Письма, которым ждать нечего и которых снова не напишут, — в «остановлено» с причиной.
_FOR_GOOD = frozenset({OfferRefused.NOT_WRITING, OfferRefused.DECLINED})


@dataclass(frozen=True, slots=True)
class OfferRefusal:
    """Почему письмо рекламодателю не уходит — причиной и словами для экрана."""

    words: str
    reason: OfferRefused


@dataclass(frozen=True, slots=True)
class _Decision:
    """Решение человека о бизнесе ниши: `writes` — «пишем» (`advertiser_rules.in_work`),
    `at` — когда решено; пусто — решения нет."""

    writes: bool
    at: datetime | None


async def check_offer(
    session: AsyncSession, message: MessageModel, *, host: str, now: datetime
) -> OfferRefusal | None:
    """Проверка отправки: `offer_refusal`, а письмо, которому ждать нечего, — остановлено
    своей фиксацией: под снятую ссылку или на площадке поставщика —
    `crawl/offer_stop.out_of_queue`, на площадке или бизнесу из «кому не пишем» и бизнесу
    после «не пишем» человека — с причиной (`donor_standing.stop_for_good`)."""
    refused = await offer_refusal(session, message, host=host, now=now)
    if refused is None:
        return None
    if refused.reason in _NOT_SENT:
        await out_of_queue(session, message, reason=_NOT_SENT[refused.reason])
        await session.commit()
    elif refused.reason in _FOR_GOOD:
        await stop_for_good(session, message, refused.words)
    return refused


async def offer_refusal(
    session: AsyncSession, message: MessageModel, *, host: str, now: datetime
) -> OfferRefusal | None:
    """Почему письмо `message` рекламодателю `host` уйти не может. `None` — может."""
    advertiser = (
        await session.execute(
            select(
                AdvertiserModel.best_page_url,
                AdvertiserModel.best_donor_host,
                in_work().label("in_work"),
                AdvertiserModel.decided_at,
            ).where(AdvertiserModel.domain_id == message.domain_id)
        )
    ).first()
    if advertiser is None:
        return OfferRefusal(_removed(host, message), OfferRefused.REMOVED)
    audience = await session.scalar(
        select(CampaignModel.audience).where(CampaignModel.id == message.campaign_id)
    )
    if audience == NICHE:
        decision = _Decision(writes=bool(advertiser.in_work), at=advertiser.decided_at)
        return await _niche_offer(session, message, decision, host=host, now=now)
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
    проверяется у каждого письма оффера, поставщик, ссылка и цена — у первого."""
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
    gone = await _gone_link(
        session, message, host=host, page=page, donor=donor, only_rejected=only_rejected, now=now
    )
    if gone is not None:
        return gone
    stale = await _stale_price(session, donor, now)
    return None if stale is None else _stale_offer(message, host=host, donor=donor, stale=stale)


async def _gone_link(
    session: AsyncSession,
    message: MessageModel,
    *,
    host: str,
    page: str | None,
    donor: str,
    only_rejected: bool,
    now: datetime,
) -> OfferRefusal | None:
    """Первое письмо — под ссылку, под которую писать нельзя: её площадка — поставщик
    агентства (`crawl/suppliers.supplier_hosts`), или ссылку сняли — «не пишем», повторный
    обход. Поставщик — первым: письмо под его ссылку снимается своей причиной."""
    if donor and donor in await supplier_hosts(session, now=now):
        return OfferRefusal(
            f"Письмо №{message.id} снято: площадка оффера {donor} — поставщик агентства, его "
            f"рекламодателям не пишем. Рекламодателю {host} следующая сборка напишет под "
            "ссылку другого донора, если она есть",
            OfferRefused.SUPPLIER,
        )
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
    return None


def _stale_offer(message: MessageModel, *, host: str, donor: str, stale: str) -> OfferRefusal:
    """Цена донора ссылки не свежая (`stale` — что с ней не так): оффер ждёт перезапроса."""
    return OfferRefusal(
        f"Цена донора {donor or 'ссылки'} устарела или снята — {stale}. Письмо "
        f"№{message.id} рекламодателю {host} стоит убрать или перезапросить цену: "
        "оффер «мы дешевле» держится на ней",
        OfferRefused.STALE_PRICE,
    )


async def _niche_offer(
    session: AsyncSession, message: MessageModel, decision: _Decision, *, host: str, now: datetime
) -> OfferRefusal | None:
    """Письмо бизнесу ниши: сам бизнес (`_niche_business`) — у первого письма и добивок,
    ответ в переписке его не держит — бизнес написал сам; дальше — пример площадки."""
    if message.step < ANSWER_STEP:
        refused = await _niche_business(session, message, decision, host=host)
        if refused is not None:
            return refused
    return await _niche_example(session, message, host=host, now=now)


async def _niche_business(
    session: AsyncSession, message: MessageModel, decision: _Decision, *, host: str
) -> OfferRefusal | None:
    """Сам бизнес ниши — от главного (шапка модуля): человек сказал о нём «не пишем» —
    письмо останавливается; бизнес из «кому не пишем» — тоже; решения о нём нет — письмо
    ждёт его."""
    if not decision.writes and decision.at is not None:
        return OfferRefusal(
            f"Письмо №{message.id} бизнесу ниши {host} остановлено: человек сказал о нём "
            f"«не пишем» {decision.at:%d.%m.%Y}. Добивки не уйдут",
            OfferRefused.DECLINED,
        )
    off = await site_not_writing_reason(session, message.domain_id)
    if off is not None:
        return OfferRefusal(
            f"Письмо №{message.id} бизнесу ниши {host} остановлено: {off}. Добивки не уйдут, "
            "и новое письмо ему не соберётся",
            OfferRefused.NOT_WRITING,
        )
    if decision.writes:
        return None
    return OfferRefusal(
        f"Бизнес ниши {host} снова ждёт решения человека: пока о нём не скажут «пишем», "
        f"письмо №{message.id} ждёт. Решить — на экране «Рекламодатели», карточка «Бизнесы "
        "ниши из выдачи»",
        OfferRefused.UNDECIDED,
    )


async def _niche_example(
    session: AsyncSession, message: MessageModel, *, host: str, now: datetime
) -> OfferRefusal | None:
    """Пример площадки в первом письме бизнесу ниши — ещё принятый донор со свежей ценой
    и не из «кому не пишем» (`NicheRecipients.fresh_examples`).

    Пример из «кому не пишем» — своя причина, и проверяется первым: запрет не зависит
    от того, что ещё названо в тексте (человек мог дописать второй пример). Такое письмо
    не уходило — оно снимается, как под снятую ссылку, и следующая сборка напишет бизнесу
    с примером оттуда, где пишем: ни перезапрос цены, ни решение человека этот пример
    в предложение не вернут. До 12.10.2026 отказ говорил про цену, и письмо стояло
    в очереди, советуя перезапросить цену."""
    if message.step != FIRST_STEP:
        return None
    run = await session.scalar(
        select(RunModel)
        .join(AdvertiserModel, AdvertiserModel.found_run_id == RunModel.id)
        .where(AdvertiserModel.domain_id == message.domain_id)
    )
    if run is None:
        return _stale_example(message, host)
    body = message.body or ""
    for example in run_hosts(run):
        off = await _not_writing(session, example) if _named(example, body) else None
        if off is not None:
            return OfferRefusal(
                f"Письмо №{message.id} снято: пример площадки {example} — из «кому не пишем» "
                f"({off}). Бизнесу ниши {host} её не предлагаем — следующая сборка напишет "
                "с примером оттуда, где пишем",
                OfferRefused.EXAMPLE,
            )
    examples = await NicheRecipients(session).fresh_examples(run, now)
    if any(_named(example, body) for example in examples):
        return None
    return _stale_example(message, host)


def _stale_example(message: MessageModel, host: str) -> OfferRefusal:
    """Пример в письме уже не пример: цена устарела, снята или решение «принят» ушло."""
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
