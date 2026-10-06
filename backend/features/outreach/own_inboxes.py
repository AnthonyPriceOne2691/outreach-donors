"""Следы проверки на настоящих доменах: всё, что адресовано своим ящикам.

Зачем. Боевая проверка Этапов 1–2 (06.10.2026) шла кнопками на настоящих
донорах: в карточку вписывали свой ящик, письмо уходило себе, ответ с ценой
ложился в карточку донора — цена, которой донор не называл, а с ней донор
попадал в обход Этапа 2 (`crawl/targets.py`). Чистка `--probes` таких следов
не берёт: она убирает липовые домены (зона `.invalid`) целиком, а настоящий
домен удалять нельзя. Адрес с перепиской не удаляет и карточка донора
(`contacts/manual.py`) — и правильно: в настоящей переписке это стёрло бы,
с кем она шла.

**Тест — всё, что адресовано своим ящикам.** Новой пометки в базе нет: свои
ящики уже названы предохранителем отправки — общим списком и списками этапов
(`config/outreach.mail_account`). Настоящему донору наш ящик не принадлежит:
адрес донора, совпавший с ящиком из списка, вписан для проверки.

**Только ящики, не домены.** Строка списка `@ours.example` пускает отправку
на весь домен, но чистка по домену взяла бы и чужие адреса, впиши кто-нибудь
в список почтовый сервис. Такие строки называются и не берутся.

**Что уходит.** Свой ящик в карточке; письма на него и переписка с ним —
с ответами и их вложениями; ответ «продаёт / не продаёт», записанный из этих
ответов; цена донора, если других ответов с ценой на домене нет — тогда
принести её могли только эти; рассылки, в которых ничего не останется. Исход
поиска адреса — честный, как при удалении адреса с карточки
(`manual.status_without_addresses`).

**Что остаётся.** Донор и решение по нему, обходы, кандидаты, рекламодатели —
настоящие данные; журнал действий и расход; цена, указанная человеком
(`donors/manual_price.py`), — её ответ не приносил. Цена, рядом с которой на домене
есть и другой ответ с ценой, остаётся и называется в показе: какой из ответов
её принёс, база не помнит, а затереть настоящую цену хуже, чем оставить
тестовую на виду.

**Липовые домены — не здесь.** Их адрес — тоже свой ящик, но домен уходит
целиком с `--probes`. Убрав здесь только адрес, мы оставили бы липового донора
без адреса, и поиск контактов пошёл бы искать адрес выдуманному сайту.

Условия повторены в запросах удаления, как у остальной чистки
(`runs/prune.py`): что бы ни лежало в плане, уходит только адресованное своему
ящику и только на настоящем домене.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Exists,
    Row,
    Select,
    and_,
    delete,
    exists,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased

from backend.config import outreach as outreach_cfg
from backend.features.contacts.manual import status_without_addresses
from backend.features.core.domain import ContactStatus, PriceSource, Stage
from backend.features.core.models._mixins import ContactAttemptMixin
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.donors.probe import emptied_campaign, probe_domain

#: Исход поиска после чистки — словами. Из «найден» выходит один из двух.
_STATUS_WORDS: dict[ContactStatus | None, str] = {
    None: "не искали",
    ContactStatus.NOT_FOUND: "адреса нет",
}

#: Ответ продавца словами (`domains.seller_answer`).
_ANSWER_WORDS = {"sells": "продаёт", "declines": "не продаёт", "free": "возьмёт бесплатно"}


@dataclass(frozen=True, slots=True)
class OwnInboxes:
    """Свои ящики из предохранителя — и строки, по которым чистка не берёт."""

    #: Ящики целиком, нижним регистром: по ним и чистка.
    addresses: tuple[str, ...]
    #: Строки-домены (`@ours.example`): называются, но не берутся.
    domains: tuple[str, ...] = ()
    #: Откуда список — имена настроек, для слов отказа.
    settings: tuple[str, ...] = ()

    def refusal(self) -> str:
        """Почему чистить не по чему. Зовётся, только когда ящиков нет."""
        where = ", ".join(self.settings)
        if self.domains:
            return (
                f"Тестовых ящиков нет: в предохранителе ({where}) только домены — "
                f"{', '.join(self.domains)}. По домену чистка не берёт: под него попали бы "
                "и чужие адреса. Впишите свои ящики поимённо"
            )
        return (
            f"Тестовых ящиков нет: предохранитель отправки не задан ({where} пуст) — "
            "следы проверки не по чему отличить от настоящей переписки"
        )


def own_inboxes() -> OwnInboxes:
    """Свои ящики — из предохранителя общей учётки и учёток этапов.

    Читается при каждом вызове, как и сама учётка (`mail_account`): правка
    `.env` с перезапуском и подмена в тестах видны сразу.
    """
    accounts = [outreach_cfg.mail_account(), *(outreach_cfg.mail_account(s.value) for s in Stage)]
    entries = sorted(
        {item.strip().lower() for account in accounts for item in account.allowed_recipients}
    )
    return OwnInboxes(
        addresses=tuple(item for item in entries if _mailbox(item)),
        domains=tuple(item for item in entries if not _mailbox(item)),
        settings=tuple(sorted({account.allowlist_setting for account in accounts})),
    )


def _mailbox(entry: str) -> bool:
    """Строка списка — ящик целиком, а не домен (`@ours.example`, `ours.example`)."""
    local, at, domain = entry.partition("@")
    return bool(at and local and domain)


@dataclass(slots=True)
class DomainTrace:
    """Следы на одном домене — строка показа."""

    domain_id: int
    host: str
    emails: list[str] = field(default_factory=list)
    letters: int = 0
    threads: int = 0
    replies: int = 0
    #: Цена донора уходит — «150.00 USD». Пусто — цену чистка не трогает.
    price: str | None = None
    #: Цена остаётся: на домене есть и другие ответы с ценой.
    price_kept: str | None = None
    #: Ответ продавца из этих ответов — словами; снимается.
    seller_answer: str | None = None
    #: Адресов не останется: роль → исход поиска после чистки, словами.
    contact_status: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class InboxTrace:
    """Что уходит при `prune --test-traces`. Сам план ничего не меняет."""

    inboxes: OwnInboxes
    domains: list[DomainTrace] = field(default_factory=list)
    contacts: list[int] = field(default_factory=list)
    threads: list[int] = field(default_factory=list)
    letters: list[int] = field(default_factory=list)
    replies: list[int] = field(default_factory=list)
    #: Рассылки, в которых не останется ни писем, ни переписки.
    campaigns: list[int] = field(default_factory=list)

    def as_details(self) -> dict[str, Any]:
        """Запись в журнал: какие ящики, на каких доменах и что ушло."""
        return {
            "следы проверки": {
                "ящики": list(self.inboxes.addresses),
                "домены": [item.host for item in self.domains],
                "адресов": len(self.contacts),
                "писем": len(self.letters),
                "переписок": len(self.threads),
                "ответов": len(self.replies),
                "цена снята": {item.host: item.price for item in self.domains if item.price},
                "цена оставлена": {
                    item.host: item.price_kept for item in self.domains if item.price_kept
                },
                "рассылки": self.campaigns,
            }
        }


def _own(inboxes: OwnInboxes) -> ColumnElement[bool]:
    """Адрес — свой ящик, и домен настоящий: липовые уходят с `--probes` целиком."""
    return and_(
        func.lower(func.trim(ContactModel.email)).in_(inboxes.addresses),
        ContactModel.domain_id.not_in(select(DomainModel.id).where(probe_domain())),
    )


def _to_own(trace: InboxTrace) -> ColumnElement[bool]:
    """Письмо своему ящику — или в переписке с ним (добивка, наш ответ)."""
    return or_(
        MessageModel.contact_id.in_(trace.contacts), MessageModel.thread_id.in_(trace.threads)
    )


def _priced() -> ColumnElement[bool]:
    """В ответе есть цена — белая или серая: только такой ставит цену донору."""
    return or_(ReplyModel.price_white.is_not(None), ReplyModel.price_grey.is_not(None))


def _other_price(domain_id: int | InstrumentedAttribute[int], reply_ids: Collection[int]) -> Exists:
    """На домене есть ответ с ценой, кроме этих. Домен ответа — по диалогу,
    потом по письму, как у приёма (`replies.repository.domain_of`)."""
    thread, letter = aliased(ThreadModel), aliased(MessageModel)
    return (
        select(ReplyModel.id)
        .outerjoin(thread, thread.id == ReplyModel.thread_id)
        .outerjoin(letter, letter.id == ReplyModel.message_id)
        .where(
            func.coalesce(thread.domain_id, letter.domain_id) == domain_id,
            ReplyModel.id.not_in(list(reply_ids)),
            _priced(),
        )
        .exists()
    )


async def inbox_trace(session: AsyncSession, inboxes: OwnInboxes) -> InboxTrace:
    """Следы проверки и всё, что за ними тянется. Ничего не меняет."""
    trace = InboxTrace(inboxes=inboxes)
    contacts = (
        await session.execute(
            select(ContactModel.id, ContactModel.domain_id, ContactModel.email)
            .where(_own(inboxes))
            .order_by(ContactModel.id)
        )
    ).all()
    if not contacts:
        return trace
    trace.contacts = [row.id for row in contacts]
    threads = (
        await session.execute(
            select(ThreadModel.id, ThreadModel.domain_id, ThreadModel.campaign_id)
            .where(ThreadModel.contact_id.in_(trace.contacts))
            .order_by(ThreadModel.id)
        )
    ).all()
    trace.threads = [row.id for row in threads]
    letters = (
        await session.execute(
            select(MessageModel.id, MessageModel.domain_id, MessageModel.campaign_id)
            .where(_to_own(trace))
            .order_by(MessageModel.id)
        )
    ).all()
    trace.letters = [row.id for row in letters]
    replies = (await session.execute(_replies_of(trace))).all()
    trace.replies = [row.id for row in replies]
    trace.domains = await _per_domain(session, contacts, threads, letters, replies)
    await _effects(session, trace, {row.domain_id for row in replies if row.priced})
    touched = {row.campaign_id for row in [*threads, *letters]}
    trace.campaigns = await _emptied(session, trace, touched)
    return trace


def _replies_of(trace: InboxTrace) -> Select[tuple[int, int, bool]]:
    """Ответы в переписке со своим ящиком или на письмо ему — с доменом и ценой."""
    thread, letter = aliased(ThreadModel), aliased(MessageModel)
    return (
        select(
            ReplyModel.id,
            func.coalesce(thread.domain_id, letter.domain_id).label("domain_id"),
            _priced().label("priced"),
        )
        .outerjoin(thread, thread.id == ReplyModel.thread_id)
        .outerjoin(letter, letter.id == ReplyModel.message_id)
        .where(
            or_(ReplyModel.thread_id.in_(trace.threads), ReplyModel.message_id.in_(trace.letters))
        )
        .order_by(ReplyModel.id)
    )


async def _per_domain(
    session: AsyncSession,
    contacts: Sequence[Row[Any]],
    threads: Sequence[Row[Any]],
    letters: Sequence[Row[Any]],
    replies: Sequence[Row[Any]],
) -> list[DomainTrace]:
    """Строки показа по доменам: адреса, письма, переписка и ответы на каждом.

    Домен письма и переписки — домен их адреса (`letters/recipients.py`), но
    считается по своему полю: строка показа не должна зависеть от этого правила.
    """
    ids = {row.domain_id for rows in (contacts, threads, letters, replies) for row in rows}
    found = await session.execute(
        select(DomainModel.id, DomainModel.host).where(DomainModel.id.in_(ids))
    )
    items = {domain_id: DomainTrace(domain_id=domain_id, host=host) for domain_id, host in found}
    for row in contacts:
        items[row.domain_id].emails.append(row.email)
    for row in threads:
        items[row.domain_id].threads += 1
    for row in letters:
        items[row.domain_id].letters += 1
    for row in replies:
        items[row.domain_id].replies += 1
    return sorted(items.values(), key=lambda item: item.host)


async def _effects(session: AsyncSession, trace: InboxTrace, priced: set[int]) -> None:
    """Что тянется за ответами и адресами: цена, ответ продавца, исход поиска."""
    by_id = {item.domain_id: item for item in trace.domains}
    for domain_id in sorted(priced):
        await _price(session, by_id[domain_id], trace.replies)
    answers = await session.execute(
        select(DomainModel.id, DomainModel.seller_answer).where(
            DomainModel.id.in_(list(by_id)), DomainModel.seller_answer_reply_id.in_(trace.replies)
        )
    )
    for domain_id, answer in answers.tuples():
        by_id[domain_id].seller_answer = _ANSWER_WORDS.get(answer or "", answer)
    for item in trace.domains:
        await _statuses(session, item, trace.contacts)


async def _price(session: AsyncSession, item: DomainTrace, reply_ids: list[int]) -> None:
    """Цена донора уходит, если принести её мог только тестовый ответ. Цену,
    указанную человеком (`donors/manual_price.py`), не приносил ни один ответ —
    чистка её не трогает и не называет."""
    donor = (
        await session.execute(
            select(
                DonorModel.last_price, DonorModel.last_price_currency, DonorModel.last_price_source
            ).where(DonorModel.domain_id == item.domain_id)
        )
    ).first()
    if donor is None or donor.last_price is None or donor.last_price_source == PriceSource.MANUAL:
        return
    shown = " ".join(part for part in (str(donor.last_price), donor.last_price_currency) if part)
    if await session.scalar(select(_other_price(item.domain_id, reply_ids))):
        item.price_kept = shown
    else:
        item.price = shown


async def _statuses(session: AsyncSession, item: DomainTrace, contact_ids: list[int]) -> None:
    """Адресов у домена не останется — каким станет исход поиска у его ролей."""
    left = await session.scalar(
        select(func.count(ContactModel.id)).where(
            ContactModel.domain_id == item.domain_id, ContactModel.id.not_in(contact_ids)
        )
    )
    if left:
        return
    for title, role in await _roles(session, item.domain_id):
        after = status_without_addresses(role)
        if after is not role.contact_status:
            item.contact_status[title] = _STATUS_WORDS.get(after, str(after))


async def _roles(session: AsyncSession, domain_id: int) -> list[tuple[str, ContactAttemptMixin]]:
    """Роли домена с исходом поиска адреса: адресов не осталось — пересчёт у обеих."""
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain_id))
    advertiser = await session.scalar(
        select(AdvertiserModel).where(AdvertiserModel.domain_id == domain_id)
    )
    roles: list[tuple[str, ContactAttemptMixin]] = []
    if donor is not None:
        roles.append(("донор", donor))
    if advertiser is not None:
        roles.append(("рекламодатель", advertiser))
    return roles


async def _emptied(session: AsyncSession, trace: InboxTrace, touched: set[int]) -> list[int]:
    """Рассылки, где кроме следов проверки нет ни писем, ни переписки."""
    rows = await session.scalars(
        select(CampaignModel.id)
        .where(
            CampaignModel.id.in_(sorted(touched)),
            ~exists().where(
                MessageModel.campaign_id == CampaignModel.id,
                MessageModel.id.not_in(trace.letters),
            ),
            ~exists().where(
                ThreadModel.campaign_id == CampaignModel.id,
                ThreadModel.id.not_in(trace.threads),
            ),
        )
        .order_by(CampaignModel.id)
    )
    return list(rows.all())


async def remove_inbox_trace(session: AsyncSession, trace: InboxTrace) -> None:
    """Убрать следы проверки по плану. Без коммита: решает вызывающий.

    Порядок — по ссылкам. Ответ продавца на домене ссылается на ответ без
    внешнего ключа и гаснет первым. Ответы — до писем: удаление письма обнулило
    бы у ответа ссылку, и ответ остался бы висеть непривязанным. Письма и
    переписка — до адресов: адрес в них гасится (`SET NULL`), и, удалённый
    раньше, он стёр бы признак, по которому они тестовые. Цена и исход поиска —
    после, по тому, что осталось.
    """
    if not trace.contacts:
        return
    await session.execute(
        update(DomainModel)
        .where(
            DomainModel.id.in_([item.domain_id for item in trace.domains]),
            DomainModel.seller_answer_reply_id.in_(trace.replies),
        )
        .values(seller_answer=None, seller_answer_at=None, seller_answer_reply_id=None)
    )
    await session.execute(
        delete(ReplyModel).where(
            ReplyModel.id.in_(trace.replies),
            or_(ReplyModel.thread_id.in_(trace.threads), ReplyModel.message_id.in_(trace.letters)),
        )
    )
    await session.execute(
        delete(MessageModel).where(MessageModel.id.in_(trace.letters), _to_own(trace))
    )
    await session.execute(
        delete(ThreadModel).where(
            ThreadModel.id.in_(trace.threads), ThreadModel.contact_id.in_(trace.contacts)
        )
    )
    await session.execute(
        delete(ContactModel).where(ContactModel.id.in_(trace.contacts), _own(trace.inboxes))
    )
    await _settle(session, trace)


async def _settle(session: AsyncSession, trace: InboxTrace) -> None:
    """Цена, исход поиска и пустые рассылки — по тому, что осталось после удаления."""
    priced = [item.domain_id for item in trace.domains if item.price]
    if priced:
        # С ценой уходят и список цен того же ответа, и её источник: без цены
        # они говорили бы о цене, которой нет.
        await session.execute(
            update(DonorModel)
            .where(
                DonorModel.domain_id.in_(priced),
                DonorModel.last_price_source.is_distinct_from(PriceSource.MANUAL.value),
                ~_other_price(DonorModel.domain_id, trace.replies),
            )
            .values(
                last_price=None,
                last_price_currency=None,
                last_price_at=None,
                last_offers=None,
                last_price_source=None,
            )
        )
    for item in trace.domains:
        left = await session.scalar(
            select(func.count(ContactModel.id)).where(ContactModel.domain_id == item.domain_id)
        )
        if left:
            continue
        for _, role in await _roles(session, item.domain_id):
            role.contact_status = status_without_addresses(role)
    if trace.campaigns:
        await session.execute(
            delete(CampaignModel).where(CampaignModel.id.in_(trace.campaigns), emptied_campaign())
        )
    await session.flush()
