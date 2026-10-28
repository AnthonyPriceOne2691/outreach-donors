"""Что перезапросу цены нужно знать о донорах — одним заходом в базу на весь список.

Список протухших цен на панели обхода спрашивал базу по каждому донору — около
пятнадцати запросов на строку при потолке в пятьсот строк (проверка 12.10.2026).
Теперь факты читаются полутора десятками запросов на весь список, а решение по
каждому донору принимается без базы (`recheck_rules`, `recheck_target`): одно и то же
у списка, у карточки донора и у нажатия — нажатие читает факты одного донора.

Что читается и почему так:

- **«кому не пишем» ТЗ** — условием выходов (`runs.exclusions.not_writing_of`), одним
  запросом на всех: крупный сайт и домен из списков, даже принятые до правила, — им
  перезапрос не уходит ни одним путём (решение владельца 28.10.2026);
- **переписки донора на Этапе 1** — с письмами и ответами: куда уйдёт письмо и не молчит
  ли донор;
- **письма донору в очереди и в пути** на любом этапе — одно письмо адресату за раз;
- **адреса донора** в порядке выбора (`preference.preferred_first`) и на какие уже писали —
  для нового первого письма;
- **ящики**: строки ящиков и доменов рассылки, счёт дневного лимита (первые письма
  и перезапросы за сутки), часового лейна (добивки и перезапросы за час) и сниженные
  лимиты — те же правила, что у отправки и прохода добивок.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.preference import DEAD, preferred_first
from backend.features.core.domain import MessageStatus, ReplyKind, Stage, ThreadStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    SendingDomainModel,
    ThreadModel,
)
from backend.features.crawl.suppliers import supplier_hosts
from backend.features.letters import attempts, other_role, stoplist
from backend.features.letters.chain import CHAINABLE, FIRST_STEP, RECHECK_CAMPAIGN, RECHECK_STEP
from backend.features.outreach import health, limits
from backend.features.outreach.repository import OutreachRepository, in_hourly_lane
from backend.features.outreach.threads import (
    ThreadState,
    asked_again_at,
    donor_word,
    summarize,
)
from backend.features.runs.exclusions import not_writing_of


@dataclass(frozen=True, slots=True)
class Talk:
    """Переписка донора на Этапе 1: письма, ответы и мёртвые адреса её писем."""

    thread: ThreadModel
    messages: Sequence[MessageModel]
    replies: Sequence[ReplyModel]
    #: Адреса домена, которые больше не принимают писем (`preference.DEAD`).
    dead: frozenset[int]

    @property
    def last_word(self) -> ReplyModel | None:
        """Последнее слово донора: письмо человека или подтверждённая цена — и из
        автоответа с суммой (`threads.donor_word`). К нему ложится ветка перезапроса."""
        said = [reply for reply in self.replies if donor_word(reply)]
        return max(said, key=lambda reply: (reply.created_at, reply.id), default=None)

    @property
    def unsubscribed(self) -> bool:
        return self.thread.status is ThreadStatus.UNSUBSCRIBED or any(
            reply.kind is ReplyKind.UNSUBSCRIBE for reply in self.replies
        )

    @property
    def waits(self) -> bool:
        """Ответ ждёт разбора — тем же правилом, что список «Диалогов»."""
        state = summarize(self.messages, self.replies, Stage.DONORS).state
        return state is ThreadState.NEEDS_REVIEW

    @property
    def asked_at(self) -> datetime | None:
        """Когда в переписке ушёл последний перезапрос, дошедший до донора."""
        return asked_again_at(self.messages)

    def reached(self) -> MessageModel | None:
        """Письмо, которое дошло: ушло, не отбилось, адрес жив."""
        return next(
            (m for m in self.messages if m.status in CHAINABLE and m.contact_id not in self.dead),
            None,
        )

    def held(self) -> MessageModel | None:
        """Письмо на живой адрес, остановленное до отправки: «не писать» или стоп-лист."""
        return next(
            (
                m
                for m in self.messages
                if m.status is MessageStatus.STOPPED
                and m.sent_at is None
                and m.contact_id not in self.dead
            ),
            None,
        )

    def first_gone(self) -> MessageModel | None:
        """Первое письмо переписки, ушедшее к почте: его ящик ведёт переписку."""
        went = (MessageStatus.SENDING, *CHAINABLE, MessageStatus.BOUNCED)
        return next((m for m in self.messages if m.step == FIRST_STEP and m.status in went), None)


@dataclass(frozen=True, slots=True)
class Waiting:
    """Письмо донору в очереди или в пути — какое и какого этапа."""

    id: int
    step: int
    status: MessageStatus
    stage: Stage


@dataclass(frozen=True, slots=True)
class Boxes:
    """Ящики этапа доноров и их счёт — правилами отправки и прохода добивок."""

    senders: dict[int, SenderModel]
    domains: dict[str, SendingDomainModel]
    #: Первые письма и перезапросы за сутки — счёт дневного лимита (`DAILY_STEPS`).
    sent_today: dict[int, int]
    #: Добивки и перезапросы за последний час — часовой лейн (`in_hourly_lane`).
    this_hour: dict[int, int]
    #: Сниженный на сутки лимит ящика (мягкие отказы, `health.cuts`).
    cuts: dict[int, int]


@dataclass(frozen=True, slots=True)
class Facts:
    """Всё, по чему решается перезапрос, — прочитанное разом."""

    moment: datetime
    #: Донор по номеру — с доменом.
    donors: dict[int, tuple[DonorModel, str]]
    #: Доноры из «кому не пишем» ТЗ: домен → почему, словами (`exclusions.not_writing_of`).
    not_writing: dict[str, str]
    suppliers: frozenset[str]
    #: Домены, закрытые стоп-листом целиком, и закрытые адреса.
    stopped_domains: frozenset[int]
    stopped_emails: frozenset[str]
    #: По домену: письма в очереди и в пути, последний ушедший перезапрос, переписки
    #: Этапа 1, адреса в порядке выбора, живое письмо в другой роли, первые письма.
    waiting: dict[int, list[Waiting]]
    asked: dict[int, datetime]
    talks: dict[int, list[Talk]]
    contacts: dict[int, list[ContactModel]]
    elsewhere: dict[int, Waiting]
    firsts: dict[int, int]
    #: Адреса, на которые уже уходили письма (`attempts.fresh`).
    written: frozenset[int]
    boxes: Boxes
    #: Перезапросы в переписку, которые ждут в очереди, — по домену.
    queued: dict[int, MessageModel] = field(default_factory=dict)


async def load(session: AsyncSession, donor_ids: Collection[int], moment: datetime) -> Facts:
    """Факты о донорах `donor_ids` — числом запросов, не зависящим от их числа."""
    donors = await _donors(session, donor_ids)
    domain_ids = [donor.domain_id for donor, _ in donors.values()]
    contacts = await _contacts(session, domain_ids)
    emails = [contact.email for listed in contacts.values() for contact in listed]
    dead = frozenset(
        contact.id
        for listed in contacts.values()
        for contact in listed
        if contact.verification_status == DEAD
    )
    talks, queued = await _talks(session, domain_ids, dead)
    stopped_domains, stopped_emails = await _stopped(session, domain_ids, emails, moment)
    return Facts(
        moment=moment,
        donors=donors,
        not_writing=await not_writing_of(session, domain_ids),
        # Поставщики — правилом обхода (`crawl/suppliers.supplier_hosts`): обе записи
        # и только действующие, — список протухших и перезапрос говорят одно.
        suppliers=frozenset(await supplier_hosts(session, now=moment)),
        stopped_domains=stopped_domains,
        stopped_emails=stopped_emails,
        waiting=await _waiting(session, domain_ids),
        asked=await _asked(session, domain_ids),
        talks=talks,
        contacts=contacts,
        elsewhere=await _elsewhere(session, domain_ids),
        firsts=await _firsts(session, domain_ids),
        written=await _written(session, domain_ids),
        boxes=await _boxes(session, moment),
        queued=queued,
    )


async def _donors(
    session: AsyncSession, donor_ids: Collection[int]
) -> dict[int, tuple[DonorModel, str]]:
    rows = await session.execute(
        select(DonorModel, DomainModel.host)
        .join(DomainModel, DomainModel.id == DonorModel.domain_id)
        .where(DonorModel.id.in_(list(donor_ids)))
    )
    return {donor.id: (donor, host) for donor, host in rows.tuples()}


async def _talks(
    session: AsyncSession, domain_ids: Sequence[int], dead: frozenset[int]
) -> tuple[dict[int, list[Talk]], dict[int, MessageModel]]:
    """Переписки доноров в рассылках доноров — старые первыми, с письмами и ответами —
    и перезапросы в переписку, которые ждут в очереди. `dead` — мёртвые адреса доменов."""
    threads = (
        await session.scalars(
            select(ThreadModel)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .where(ThreadModel.domain_id.in_(domain_ids), CampaignModel.stage == Stage.DONORS)
            .order_by(ThreadModel.id)
        )
    ).all()
    ids = [thread.id for thread in threads]
    letters: dict[int, list[MessageModel]] = defaultdict(list)
    for message in await session.scalars(
        select(MessageModel)
        .where(MessageModel.thread_id.in_(ids))
        .order_by(MessageModel.step, MessageModel.id)
    ):
        letters[message.thread_id or 0].append(message)
    answers: dict[int, list[ReplyModel]] = defaultdict(list)
    for reply in await session.scalars(
        select(ReplyModel)
        .where(ReplyModel.thread_id.in_(ids))
        .order_by(ReplyModel.created_at, ReplyModel.id)
    ):
        answers[reply.thread_id or 0].append(reply)
    talks: dict[int, list[Talk]] = defaultdict(list)
    queued: dict[int, MessageModel] = {}
    for thread in threads:
        talks[thread.domain_id].append(Talk(thread, letters[thread.id], answers[thread.id], dead))
        for message in letters[thread.id]:
            if message.step == RECHECK_STEP and message.status is MessageStatus.QUEUED:
                queued[thread.domain_id] = message
    return talks, queued


async def _contacts(
    session: AsyncSession, domain_ids: Sequence[int]
) -> dict[int, list[ContactModel]]:
    """Адреса доноров — в порядке выбора: лучший первым, мёртвые последними."""
    found: dict[int, list[ContactModel]] = defaultdict(list)
    for contact in await session.scalars(
        select(ContactModel)
        .where(ContactModel.domain_id.in_(domain_ids))
        .order_by(ContactModel.domain_id, *preferred_first())
    ):
        found[contact.domain_id].append(contact)
    return found


async def _stopped(
    session: AsyncSession, domain_ids: Sequence[int], emails: Sequence[str], moment: datetime
) -> tuple[frozenset[int], frozenset[str]]:
    """Домены и адреса доноров, которые держит стоп-лист доноров, — правилом самой отправки
    (`stoplist.holding`): перезапрос проверяет то же, на чём она откажет, ещё до письма."""
    held = await stoplist.holding(
        session, stage=Stage.DONORS, domain_ids=domain_ids, emails=emails, moment=moment
    )
    ours, addresses = set(domain_ids), set(emails)
    return (
        frozenset(
            row.domain_id for row in held if row.domain_id is not None and row.domain_id in ours
        ),
        frozenset(row.email for row in held if row.email is not None and row.email in addresses),
    )


async def _letters(
    session: AsyncSession, *where: ColumnElement[bool], newest_first: bool = False
) -> list[tuple[int, Waiting]]:
    """Письма доменов по условиям `where` — с этапом рассылки, по номеру письма."""
    rows = await session.execute(
        select(
            MessageModel.domain_id,
            MessageModel.id,
            MessageModel.step,
            MessageModel.status,
            CampaignModel.stage,
        )
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(*where)
        .order_by(MessageModel.id.desc() if newest_first else MessageModel.id)
    )
    return [
        (domain_id, Waiting(letter_id, step, status, stage))
        for domain_id, letter_id, step, status, stage in rows.tuples()
    ]


async def _waiting(session: AsyncSession, domain_ids: Sequence[int]) -> dict[int, list[Waiting]]:
    """Письма доноров в очереди и в пути — на любом этапе: адресату одно письмо за раз."""
    found: dict[int, list[Waiting]] = defaultdict(list)
    for domain_id, letter in await _letters(
        session, MessageModel.domain_id.in_(domain_ids), MessageModel.status.in_(attempts.PENDING)
    ):
        found[domain_id].append(letter)
    return found


async def _asked(session: AsyncSession, domain_ids: Sequence[int]) -> dict[int, datetime]:
    """Когда донору последний раз ушёл перезапрос — любым путём."""
    ours = select(CampaignModel.id).where(CampaignModel.template_id == RECHECK_CAMPAIGN)
    rows = await session.execute(
        select(MessageModel.domain_id, func.max(MessageModel.sent_at))
        .where(MessageModel.domain_id.in_(domain_ids))
        .where(
            or_(
                MessageModel.step == RECHECK_STEP,
                (MessageModel.step == FIRST_STEP) & MessageModel.campaign_id.in_(ours),
            )
        )
        .group_by(MessageModel.domain_id)
    )
    return {domain_id: moment for domain_id, moment in rows.tuples() if moment is not None}


async def _elsewhere(session: AsyncSession, domain_ids: Sequence[int]) -> dict[int, Waiting]:
    """Живое письмо сайту в другой роли — не донору: рекламодателю или лиду продаж. Условие —
    одно с карточкой донора и с отправкой (`other_role.elsewhere`)."""
    found: dict[int, Waiting] = {}
    for domain_id, letter in await _letters(
        session,
        MessageModel.domain_id.in_(domain_ids),
        other_role.elsewhere(),
        newest_first=True,
    ):
        found.setdefault(domain_id, letter)
    return found


async def _firsts(session: AsyncSession, domain_ids: Sequence[int]) -> dict[int, int]:
    """Сколько первых писем было у домена — на обоих этапах (`attempts.under_cap`)."""
    rows = await session.execute(
        select(MessageModel.domain_id, func.count(MessageModel.id))
        .where(MessageModel.domain_id.in_(domain_ids), MessageModel.step == FIRST_STEP)
        .group_by(MessageModel.domain_id)
    )
    return {domain_id: int(count) for domain_id, count in rows.tuples()}


async def _written(session: AsyncSession, domain_ids: Sequence[int]) -> frozenset[int]:
    """Адреса, на которые уже уходили письма, — первым письмом их второй раз не берут."""
    rows = await session.scalars(
        select(MessageModel.contact_id)
        .where(MessageModel.domain_id.in_(domain_ids), MessageModel.contact_id.is_not(None))
        .distinct()
    )
    return frozenset(contact_id for contact_id in rows.all() if contact_id is not None)


async def _boxes(session: AsyncSession, moment: datetime) -> Boxes:
    """Ящики доноров и их счёт: дневной лимит, часовой лейн, сниженные лимиты."""
    senders = {
        sender.id: sender
        for sender in await session.scalars(
            select(SenderModel).where(SenderModel.stage == Stage.DONORS).order_by(SenderModel.id)
        )
    }
    hour = await session.execute(
        select(MessageModel.sender_id, func.count(MessageModel.id))
        .where(MessageModel.sender_id.in_(list(senders)), in_hourly_lane())
        .where(MessageModel.sent_at >= moment - timedelta(hours=1))
        .group_by(MessageModel.sender_id)
    )
    return Boxes(
        senders=senders,
        domains=await limits.sending_domains(session),
        sent_today=await OutreachRepository(session).sent_today(now=moment, first_only=True),
        this_hour={
            sender_id: int(count) for sender_id, count in hour.tuples() if sender_id is not None
        },
        cuts=await health.cuts(session, Stage.DONORS, moment),
    )
