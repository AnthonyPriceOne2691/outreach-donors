"""Письмо на адрес из ответа рекламодателя: решение по ответу и письмо в ту же переписку.

Ответ «напишите вот на этот адрес» приём ставит на разбор (`replies/lead_redirect.note`), здесь
по нему решают (`decide`), а отправляет письмо проход (`redirect_send.py`).

    переписку уже ведут? → модель: просят ли писать туда и куда → адрес из ответа годится? →
    контакт рекламодателя → письмо: тема и текст первого письма, сверху — одна неизменяемая строка

**Письмо — то же первое письмо, как ушло**, с одной строкой сверху (`FIXED_LINE`), без модели: её
утверждает владелец, и текст, который никто не перечитает перед отправкой, не меняется от вызова к
вызову. Ящик — тот, что начал переписку (`answers.thread_sender`): письмо — часть разговора, и
с другого ящика для получателя оно было бы незнакомцем. Шаг — `chain.REDIRECT_STEP`, ключ — свой
(`redirect_key`): ключ первого письма «домен + этап + шаг» занят, а этот держит «одно письмо на
адрес из ответа в переписке» и на повторе прохода. Письмо — к ответу, который просил
(`answers_reply_id`): в переписке оно «наш ответ», агент на тот ответ черновик не пишет.

**Адрес проверяется тем же, что проходит адрес первого письма** (`address_refusal`): синтаксис и
фильтр качества (`contacts.quality`), робот, стоп-листы адреса и обоих доменов, мёртвый адрес, DNS
домена (нет домена или нулевой MX), проверка перед первым письмом (`address_gate`), если она у
писем рекламодателям включена. Адрес от самого адресата ложится контактом `manual`, как адрес из
автоответа мёртвого ящика (`replies/redirect.py`), и проверка ему верит без платного вызова
(`address_check.kind_of`, правило 11.11.2026); уже известный адрес — каким был.

**Чужой домен допустим**: партнёрская программа часто живёт на своём. Письмо уходит, а пометка
(`foreign_note`) говорит человеку, что адрес не на домене рекламодателя.

Сбой модели или проверки адреса — строка ждёт следующего прохода не раньше `RETRY_AFTER`; потолок
расхода на модель — тоже «позже»: ответ не судится без модели.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.features.contacts.address_check import Kind
from backend.features.contacts.manual import MANUAL_SCORE
from backend.features.contacts.preference import DEAD
from backend.features.contacts.quality import rejection_reason
from backend.features.contacts.verifier import EmailVerifier
from backend.features.core import usage
from backend.features.core.domain import GONE_STATUSES, ContactSource, MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import MessageModel, ReplyModel, ThreadModel
from backend.features.core.models.redirect import LeadRedirectModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.donors.host import normalize_host
from backend.features.letters import recipient_dns, stoplist
from backend.features.letters.address_gate import checker_of
from backend.features.letters.answers import AnswerRefusedError, thread_sender
from backend.features.letters.chain import FIRST_STEP, REDIRECT_STEP
from backend.features.outreach import stage_of
from backend.features.replies import robots
from backend.features.replies.inbound import masked_for_log
from backend.features.replies.lead_redirect import State, candidates, held, seen_for, written
from backend.features.replies.redirect import same_site
from backend.features.replies.redirect_ask import Asked

logger = logging.getLogger(__name__)

#: Неизменяемая строка над первым письмом — по-английски, без модели. Утверждает владелец.
FIXED_LINE = (
    "Your colleague at {colleague} suggested I write to you directly, so here is my note again:"
)

#: Отметка контакта, названного в ответе рекламодателя (`contacts.verification_status`): откуда он
#: взялся, видно и через год.
FROM_REPLY = "from_reply"

#: Пауза после сбоя модели, потолка расхода или проверки адреса: строку проход берёт не раньше.
RETRY_AFTER = timedelta(minutes=15)

#: Строка закрыта без переадресации — словами для журнала и команды.
NO_ADDRESSES = "в ответе нет адресов, кроме наших, отправителя и нашего письма"
NOT_ADVERTISER = "ответ не из переписки рекламодателя"
NO_FIRST = "у переписки нет ушедшего первого письма — пересылать нечего"
NOT_AMONG = "адреса нет среди адресов ответа"
GONE = "проверка адреса перед письмом: такого ящика нет"


class Asker(Protocol):
    """Кто отвечает, просят ли писать на другой адрес (`redirect_ask.RedirectClient`)."""

    async def ask(self, text: str, *, subject: str, names: Sequence[str]) -> Asked: ...


@dataclass(frozen=True, slots=True)
class Talk:
    """Ответ и его переписка — всё, что нужно решению."""

    reply: ReplyModel
    thread: ThreadModel
    host: str
    #: Этап рассылки переписки; пусто — рассылки уже нет.
    stage: Stage | None
    #: Первое письмо переписки, ушедшее к почте. Пусто — пересылать нечего.
    first: MessageModel | None


async def talk_of(session: AsyncSession, reply_id: int) -> Talk | None:
    """Ответ с перепиской, доменом, этапом и первым ушедшим письмом; `None` — ответа или
    переписки нет."""
    reply = await session.get(ReplyModel, reply_id)
    thread = (
        await session.get(ThreadModel, reply.thread_id)
        if reply is not None and reply.thread_id is not None
        else None
    )
    if reply is None or thread is None:
        return None
    host = await session.scalar(select(DomainModel.host).where(DomainModel.id == thread.domain_id))
    stage = await stage_of.of_thread(session, thread.id)
    first = await session.scalar(
        select(MessageModel)
        .where(
            MessageModel.thread_id == thread.id,
            MessageModel.step == FIRST_STEP,
            MessageModel.status.in_(GONE_STATUSES),
        )
        .order_by(MessageModel.sent_at, MessageModel.id)
        .limit(1)
    )
    return Talk(reply=reply, thread=thread, host=host or "", stage=stage, first=first)


def redirect_key(host: str, thread_id: int) -> str:
    """Ключ письма на адрес из ответа: одно на переписку — повтор прохода второго не заводит."""
    return f"{Stage.ADVERTISERS.value}:{host}:redirect:{thread_id}"


def colleague_of(talk: Talk, wrote_to: str | None) -> str:
    """Кто предложил писать новому адресату: адрес, с которого ответили, а нет его — наш адресат."""
    return (talk.reply.from_email or wrote_to or "").strip().lower()


def letter_text(body: str, *, colleague: str) -> str:
    """Текст письма: неизменяемая строка, пустая строка и первое письмо, как оно ушло."""
    return f"{FIXED_LINE.format(colleague=colleague)}\n\n{body.strip()}"


def on_site(address: str, host: str) -> bool:
    """Адрес на домене рекламодателя или его поддомене. Зарегистрированное имя (`same_site`)
    знает не каждый домен — тогда сравнивается само имя: `mail.brand.com` — тот же сайт."""
    domain, site = address.partition("@")[2].lower(), host.strip().lower()
    return same_site(address, host) or domain == site or domain.endswith(f".{site}")


def foreign_note(address: str, host: str) -> str | None:
    """Пометка человеку: адрес не на домене рекламодателя — переслали, но так и сказано."""
    if on_site(address, host):
        return None
    domain = address.partition("@")[2]
    return (
        f"адрес на чужом домене ({domain}), а не на {host}: так бывает у партнёрских программ — "
        "письмо ушло и туда"
    )


async def _domain_ids(session: AsyncSession, address: str) -> list[int]:
    """Строка `domains` домена адреса, если он известен базе: стоп-лист держит и его."""
    host = normalize_host(address.partition("@")[2])
    found = await session.scalar(select(DomainModel.id).where(DomainModel.host == host))
    return [found] if found is not None else []


async def address_refusal(session: AsyncSession, address: str, talk: Talk) -> str | None:
    """Почему на адрес из ответа письмо не уйдёт — словами; `None` — уйдёт. Без записи и без
    платных вызовов: проверку перед письмом (`address_gate`) зовёт `decide` отдельно."""
    bad = rejection_reason(address)
    if bad is not None:
        return f"адрес не проходит проверку адреса ({bad})"
    if robots.robot(address):
        return "это адрес робота: письма туда не читают"
    domains = [talk.thread.domain_id, *await _domain_ids(session, address)]
    held_by = await stoplist.holding(
        session, stage=Stage.ADVERTISERS, domain_ids=domains, emails=[address]
    )
    if held_by:
        return f"адрес или домен в стоп-листе (причина «{held_by[0].reason.value}»)"
    dead = await session.scalar(
        select(ContactModel.id)
        .where(func.lower(ContactModel.email) == address, ContactModel.verification_status == DEAD)
        .limit(1)
    )
    if dead is not None:
        return "адрес уже не принимает письма: на него был отказ доставки"
    if await recipient_dns.no_mail(address):
        return f"у домена {recipient_dns.domain_of(address)} нет почты (ответ DNS)"
    return None


async def contact_for(session: AsyncSession, address: str, domain_id: int) -> ContactModel:
    """Контакт рекламодателя с адресом из ответа: известный — каким был, новый — от адресата."""
    found = await session.scalar(
        select(ContactModel).where(
            ContactModel.domain_id == domain_id, func.lower(ContactModel.email) == address
        )
    )
    if found is not None:
        return found
    contact = ContactModel(
        domain_id=domain_id,
        email=address,
        source=ContactSource.MANUAL,
        verification_status=FROM_REPLY,
        verification_score=MANUAL_SCORE,
    )
    session.add(contact)
    await session.flush()
    return contact


async def letter_for(
    session: AsyncSession, talk: Talk, first: MessageModel, contact: ContactModel
) -> MessageModel:
    """Письмо в очередь переписки: тема и текст первого письма `first`, строка сверху, новый
    адресат `contact`."""
    wrote_to = (
        await session.get(ContactModel, first.contact_id) if first.contact_id is not None else None
    )
    colleague = colleague_of(talk, wrote_to.email if wrote_to is not None else None)
    message = MessageModel(
        campaign_id=talk.thread.campaign_id,
        thread_id=talk.thread.id,
        domain_id=talk.thread.domain_id,
        contact_id=contact.id,
        step=REDIRECT_STEP,
        status=MessageStatus.QUEUED,
        subject=first.subject,
        body=letter_text(first.body or "", colleague=colleague),
        idempotency_key=redirect_key(talk.host, talk.thread.id),
        answers_reply_id=talk.reply.id,
    )
    session.add(message)
    await session.flush()
    return message


def _close(row: LeadRedirectModel, state: State, reason: str | None, now: datetime) -> None:
    row.state, row.reason, row.decided_at = state, reason, now


def _spent(session: AsyncSession, row: LeadRedirectModel, asked: Asked) -> None:
    """Токены вызова — строкой журнала расхода и в строке решения; ответ модели — снимком."""
    if asked.tokens:
        usage.record(
            session, operation="lead_redirect", units=asked.tokens, stage=Stage.ADVERTISERS
        )
        row.tokens += asked.tokens
    if asked.snapshot:
        row.model_answer = asked.snapshot


async def _ask(
    session: AsyncSession, asker: Asker, row: LeadRedirectModel, talk: Talk, now: datetime
) -> bool:
    """Спросить модель. Правда — адрес выбран и записан в строку; ложь — строка закрыта или
    ждёт следующего прохода (`row.reason` говорит чего)."""
    seen = await seen_for(session, talk.reply, talk.thread.id)
    found = candidates(talk.reply.raw_body, seen)
    if not found:
        _close(row, State.NONE, NO_ADDRESSES, now)
        return False
    try:
        await usage.ensure_llm_within_cap(session, now=now)
    except LlmCapExceededError as exc:
        row.reason = f"ждёт разбора: {exc}"
        logger.info("переадресация: ответ №%s ждёт — %s", row.reply_id, exc)
        return False
    names = (outreach_cfg.SENDER_NAME, talk.reply.from_name or "")
    asked = await asker.ask(
        written(talk.reply.raw_body), subject=talk.reply.subject or "", names=names
    )
    _spent(session, row, asked)
    if asked.retry:
        row.reason = f"ждёт разбора: {asked.trouble}"
        return False
    row.address = asked.address
    # Ответ модели и расход — в базе до проверок адреса: сбой дальше не теряет строку журнала,
    # и следующий проход модель второй раз не спрашивает (адрес уже выбран).
    await session.commit()
    if asked.trouble is not None:
        _close(row, State.REFUSED, asked.trouble, now)
    elif not asked.redirect or asked.address is None:
        _close(row, State.NONE, None, now)
    elif asked.address not in found:
        _close(row, State.REFUSED, seen.why_not(asked.address) or NOT_AMONG, now)
    else:
        return True
    return False


Gate = tuple[Literal["pass", "gone", "held"], str]


async def _gate(
    session: AsyncSession, contact: ContactModel, verifier: EmailVerifier | None
) -> Gate:
    """Проверка перед первым письмом рекламодателю (`address_gate`), если она включена. Её
    вердикт фиксируется самой проверкой — как у сборки очереди."""
    async with checker_of(session, Stage.ADVERTISERS, verifier) as checker:
        if checker is None:
            return "pass", ""
        done = (await checker.check([contact.id]))[contact.id]
    if done.passes:
        return "pass", ""
    if done.kind is Kind.GONE:
        return "gone", GONE
    return "held", f"проверка адреса перед письмом не состоялась: {done.said}"


async def decide(
    session: AsyncSession,
    asker: Asker,
    row: LeadRedirectModel,
    *,
    now: datetime,
    verifier: EmailVerifier | None = None,
) -> State:
    """Решить по строке «ждёт» — и завести письмо, если переслать можно. С фиксацией."""
    row.tried_at = now
    state = await _decided(session, asker, row, now=now, verifier=verifier)
    await session.commit()
    logger.info(
        "переадресация: ответ №%s — %s%s",
        row.reply_id,
        state.value,
        f" ({masked_for_log(row.address)})" if row.address else "",
    )
    return state


async def _decided(
    session: AsyncSession,
    asker: Asker,
    row: LeadRedirectModel,
    *,
    now: datetime,
    verifier: EmailVerifier | None,
) -> State:
    talk = await talk_of(session, row.reply_id)
    if talk is None or talk.stage is not Stage.ADVERTISERS:
        _close(row, State.NONE, NOT_ADVERTISER, now)
        return State.NONE
    why = await held(session, talk.thread.id)
    if why is not None:
        _close(row, State.REFUSED, why, now)
        return State.REFUSED
    if row.address is None and not await _ask(session, asker, row, talk, now):
        return State(row.state)
    address, first = row.address or "", talk.first
    refusal = (
        NO_FIRST
        if first is None
        else await address_refusal(session, address, talk) or await _box_refusal(session, talk)
    )
    if refusal is not None or first is None:
        _close(row, State.REFUSED, refusal or NO_FIRST, now)
        return State.REFUSED
    contact = await contact_for(session, address, talk.thread.domain_id)
    verdict, words = await _gate(session, contact, verifier)
    if verdict != "pass":
        if verdict == "gone":
            _close(row, State.REFUSED, words, now)
        else:
            row.reason = words
        return State(row.state)
    message = await letter_for(session, talk, first, contact)
    row.message_id, row.note = message.id, foreign_note(address, talk.host)
    _close(row, State.LETTER, None, now)
    return State.LETTER


async def _box_refusal(session: AsyncSession, talk: Talk) -> str | None:
    """Письмо уходит только с ящика, начавшего переписку: его нет — пересылать нечем."""
    try:
        await thread_sender(session, talk.thread.id)
    except AnswerRefusedError as exc:
        logger.info("переадресация: в переписке №%s писать нечем — %s", talk.thread.id, exc)
        return str(exc)
    return None
