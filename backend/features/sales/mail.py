"""Письмо продаж в общей почте — ответы модуля продаж на вопросы моста `core/stages.py`.

Почта (`letters/`) модуль продаж не импортирует (контракт `mail-does-not-know-sales`):
о письме продаж она спрашивает мост (протокол `stages.SalesMail`), мост — этот модуль:
он подключён к мосту при загрузке пакета продаж (`sales/__init__.py`). Своей отправки, своих добивок
и своего балансировщика у продаж нет: письмо уходит общей отправкой (`letters/sending.py`),
пачкой — общим `send_queue`, добивки — общим проходом (`letters/followups.py`), ящик —
общим `sender_rules.pick` по этапу продаж. Здесь — только то, что знают продажи:

- `recipient` — кому и от чьего имени, до выбора учётки этапа: подключены ли продажи
  (`connection.py`), адрес лида по явной связи диалога (`sales_threads`), имя в From
  из «Отправителя» на языке диалога (`Sender.in_language`: набор языка, пустое поле — общее;
  тем же набором подписаны письмо, добивки и ответы), готова ли цепочка набора, которым
  написано первое письмо (ответу в переписке — нет: он не шаг цепочки); пояса получателя
  для окна (`policy.zones_of`: лида, его страны). Ответ в переписке уходит тому, кто
  написал, если это коллега лида со своего адреса на домене компании (`colleague_of`,
  решение владельца 13.10), — и ручной, и черновик агента: оба уходят этой отправкой;
  иначе — лиду, как прежде;
- `check` — ветка продаж проверки перед отправкой (вместо отказа 1.1b в
  `sending._check_review`): лид не снят, не передан телемаркетологу
  (`handoff.handed_off` — переданному цепочка не идёт), не в стоп-листе (`unwritable` —
  то же правило решает, писать ли агенту черновик ответа лида), а ответ коллеге — и его
  адрес не в стоп-листе (`stopped_by` с его адресом); письмо цепочки — лиду не из
  «кому не пишем» (`not_writing.py`, решение владельца 13.10); первое письмо —
  в коридоре отличия, собрано после последней правки лида (`lead_edit.edited`), а адрес не
  проверен выдуманным проверяльщиком при живом (`verifier.unproven`) и не «не подтверждён»
  (`unconfirmed.first_letter`); подпись и адрес на месте, подстановок без значения нет
  (`letter.problem`, у ответа в переписке — `letter.answer_problem`); добивку, которую так
  не собрать, проверка останавливает;
- `connected` — подключены ли продажи: проход добивок берёт их сроки только тогда, иначе
  срок цел и проход называет его вслух;
- `missing` — чего не хватает продажам, словами (`connection.missing`): отказ пачке называет
  причину, как отказ одному письму (`core/stages.check_connected`);
- `answer` — ответ лиду в переписке до заведения письма: продажи подключены, у переписки
  есть лид, ему можно писать; текст — с подписью и адресом из «Отправителя» в конце, и
  метрик Ahrefs в нём нет;
- `followup` — текст добивки: собранный при сборке первого письма (`followup_texts.py`,
  у переписки лида), если цепочка набора с тех пор не менялась, иначе — шаг цепочки того же
  набора и языка, что у первого письма, с подстановками лида; его не собрать — текст,
  сохранённый при сборке прежней версией цепочки; подпись и адрес — из «Отправителя». Модели
  здесь нет. Тему даёт первое письмо — у шаблона добивки её нет;
- `policy` — политика почты продаж (`policy.sales_policy`): окно получателя, мягкие
  сигналы ящика, сторож и темп автоотправки (`autosend.pace`, пока она включена);
- `threads_to` — переписки, чьи письма уходят на этот адрес: лида с ним и те, где ответ ушёл
  коллеге лида с ним. По ним стоп-лист снимает письма продаж адресу
  (`letters/stoplist.stop_pending`) — строки `contacts` у них нет, — а сверка событий доставки
  узнаёт своё письмо (`letters/events`);
- `addresses` — адрес лида каждой переписки (`lead_addresses.py`): спискам «Диалогов» и писем —
  строки `contacts` у переписки продаж нет, и без ответа модуля там стояло «адрес не определён».

**Отказ — только словами почты** (договор моста): «писать больше нельзя» (лид снят,
передан, в стоп-листе) — `LeadStoppedError`, наследник стоп-листа: цепочка кончается; лид в
«кому не пишем» — `NotWritingError` и добивку не собрать — `FollowupStoppedError`, тоже
наследники стоп-листа; «письмо не то» — `NotReadyError`: письмо стоит в очереди с причиной,
пачка идёт дальше (и первое письмо, чья цепочка набора неполна: письма других наборов
уходят); «пока нельзя» (продажи не подключены, цепочка добивки неполна) —
`SalesNotConnectedError`: пачка встаёт, срок добивки возвращается. Другое исключение мост
считает поломкой модуля. Ответы читают базу и ничего не сохраняют: мост спрашивает модуль в
своей точке сохранения.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.known_addresses import FREE_MAILBOX_DOMAINS
from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.core.stages import (
    MailPolicy,
    Recipient,
    SalesFollowup,
    SalesNotConnectedError,
)
from backend.features.letters import guards
from backend.features.letters.chain import ANSWER_STEP, FIRST_STEP
from backend.features.letters.sending import (
    NotReadyError,
    NotWritingError,
    SendError,
    SuppressedError,
)
from backend.features.letters.uniqueness import corridor_verdict
from backend.features.replies import redirect, robots
from backend.features.sales import (
    autosend,
    chain,
    connection,
    followup_texts,
    handoff,
    lead_addresses,
    letter,
    sender,
)
from backend.features.sales.intake import host_key
from backend.features.sales.lead_edit import EDITED, edited
from backend.features.sales.models import (
    LeadStatus,
    SalesLeadModel,
    SalesStoplistModel,
    SalesThreadModel,
)
from backend.features.sales.not_writing import excluded
from backend.features.sales.policy import sales_policy, zones_of
from backend.features.sales.suppressions import holds_sales
from backend.features.sales.unconfirmed import first_letter

logger = logging.getLogger(__name__)


class LeadStoppedError(SuppressedError):
    """Этому лиду писать больше нельзя: снят, передан телемаркетологу, в стоп-листе.
    Для цепочки — как стоп-лист: проход добивок её останавливает."""


class LeadMissingError(NotReadyError):
    """У письма продаж нет лида: его собрала не сборка очереди продаж, или связь с лидом
    пропала. Письмо не уйдёт, пока его не уберут из очереди. Наследник «не готово», а не
    просто отказ: итог пачки называет его «письмо не готово к отправке» (`letters/batch.why`),
    а не «почта отказала» — почта тут ни при чём (ревью стыков, хвост R1 5.3)."""


@dataclass(frozen=True, slots=True)
class Linked:
    """Диалог продаж вместе с его лидом и сайтом компании."""

    link: SalesThreadModel
    lead: SalesLeadModel
    host: str


async def linked(session: AsyncSession, thread_id: int | None) -> Linked | None:
    """Лид диалога по явной связи. `None` — диалог начат не сборкой продаж."""
    if thread_id is None:
        return None
    return await _dialog(session, SalesThreadModel.thread_id == thread_id)


async def lead_dialog(session: AsyncSession, lead_id: int) -> Linked | None:
    """Диалог лида по той же связи — экрану очереди (`followup_texts.ahead`); `None` — лиду
    ещё не писали."""
    return await _dialog(session, SalesThreadModel.lead_id == lead_id)


async def _dialog(session: AsyncSession, where: ColumnElement[bool]) -> Linked | None:
    found = (
        await session.execute(
            select(SalesThreadModel, SalesLeadModel, DomainModel.host)
            .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
            .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
            .where(where)
        )
    ).first()
    return None if found is None else Linked(*found._tuple())


def of_company(address: str, dialog: Linked) -> bool:
    """Адрес компании лида: его домен — сайт компании или домен адреса лида, они сами или их
    поддомены; по зарегистрированному имени (`redirect.same_site`: `mail.acme.com` — тот же
    сайт), а где его нет (выдуманные зоны) — по самому имени. Одно правило у ответа коллеге
    (`colleague_of`) и у автоответа мёртвого ящика (`dead_mailbox.py`).

    Бесплатная почта — не домен компании, даже если на ней сам лид (решение владельца 13.10):
    у лида `jane@gmail.com` адрес `bob@gmail.com` — частный ящик, а не коллега, и ни ответ ему,
    ни лид из автоответа без человека не заводятся. Доменом компании тогда остаётся сайт."""
    domain = address.rpartition("@")[2]
    owns = {dialog.host.lower(), dialog.lead.email.rpartition("@")[2].lower()} - {""}
    for own in owns - FREE_MAILBOX_DOMAINS:
        if domain == own or domain.endswith(f".{own}") or redirect.same_site(address, own):
            return True
    return False


def colleague_of(sender: str | None, dialog: Linked) -> str | None:
    """Коллега лида, ответивший со своего адреса на домене компании, — ответ в переписке уходит
    ему (решение владельца 13.10): агент пишет «Здравствуйте, <коллега>», и письмо доходит до
    того, кто спросил, а не до ящика лида. `None` — ответ лиду, как прежде: написал сам лид (в
    любом регистре) или адреса нет; адрес не на домене компании (`of_company`: бесплатная почта
    им не бывает, даже если на ней сам лид); робот (noreply, почтовая служба) — человека за
    адресом нет."""
    address = (sender or "").strip().lower()
    local, _, domain = address.rpartition("@")
    if not (local and domain) or address == dialog.lead.email.strip().lower():
        return None
    if robots.robot(address):
        return None
    return address if of_company(address, dialog) else None


async def _answered_colleague(
    session: AsyncSession, message: MessageModel, dialog: Linked
) -> str | None:
    """Коллега лида, которому уходит это письмо (`colleague_of`): только ответ в переписке (у
    него одного есть ответ, на который он, — `answers_reply_id`) и только на письмо коллеги.
    `None` — письмо лиду: цепочка пишет лиду всегда."""
    if message.answers_reply_id is None:
        return None
    sender = await session.scalar(
        select(ReplyModel.from_email).where(ReplyModel.id == message.answers_reply_id)
    )
    return colleague_of(sender, dialog)


async def _ready_chain(session: AsyncSession, link: SalesThreadModel, what: str) -> chain.Chain:
    """Цепочка набора первого письма для добивки — полная; неполная — «пока нельзя» с её
    словами: срок добивки возвращается."""
    found = await chain.of_set(session, link.chain_hypothesis_id, link.language)
    try:
        found.check_ready()
    except chain.ChainNotReadyError as exc:
        raise SalesNotConnectedError(what, str(exc)) from exc
    return found


async def recipient(session: AsyncSession, message: MessageModel, what: str) -> Recipient:
    """Адрес лида и имя отправителя — если продажи подключены и цепочка письма полна; ответ в
    переписке на письмо коллеги лида с домена компании — его адрес (`colleague_of`).

    Неполная цепочка — отказ этому письму, а не этапу (`NotReadyError`): цепочка своя у набора
    письма (гипотеза и язык), и письма других наборов уходят — пачка считает это письмо и идёт
    дальше (`letters/batch.py`), как на стоп-листе одного лида. Ответу лиду в переписке
    (`ANSWER_STEP`) цепочка не нужна: он не её шаг."""
    found = await connection.check(session, what)
    dialog = await linked(session, message.thread_id)
    if dialog is None:
        raise LeadMissingError(
            f"{what}: у письма продаж нет лида — его собрала не сборка очереди продаж. "
            "Письмо стоит убрать из очереди"
        )
    if message.step < ANSWER_STEP:
        link = dialog.link
        try:
            (await chain.of_set(session, link.chain_hypothesis_id, link.language)).check_ready()
        except chain.ChainNotReadyError as exc:
            raise NotReadyError(f"{what} не уходит: {exc}") from exc
    to = await _answered_colleague(session, message, dialog) or dialog.lead.email
    name = found.in_language(dialog.link.language).values["sender_name"]  # From — языка письма
    return Recipient(Stage.SALES, to, name, zones=zones_of(dialog.lead))


async def stopped_by(
    session: AsyncSession,
    lead: SalesLeadModel,
    host: str,
    *,
    now: datetime | None = None,
    address: str | None = None,
) -> str | None:
    """Почему этому лиду — или адресу `address` его компании (коллеге, которому уходит ответ) —
    писать нельзя: стоп-лист продаж или общий; `None` — можно.

    Те же правила, что у очистки (`cleaning.py`): стоп-лист продаж держит адрес, домен
    компании и домен адреса; общий — строки без этапа, этапа продаж и те, где человек
    сам просил не писать (`suppressions.holds_sales` — одно условие на очистку и отправку)."""
    email = address or lead.email
    mailbox = email.rpartition("@")[2]
    hosts = sorted({host, mailbox, host_key(mailbox)} - {""})
    manual = await session.scalar(
        select(func.count())
        .select_from(SalesStoplistModel)
        .where(
            or_(
                SalesStoplistModel.email == email.lower(),
                SalesStoplistModel.host.in_(hosts),
            )
        )
    )
    if manual:
        return "в ручном стоп-листе продаж"
    reason = await session.scalar(
        select(SuppressionModel.reason)
        .where(
            or_(SuppressionModel.domain_id == lead.domain_id, SuppressionModel.email == email),
            holds_sales(now or datetime.now(UTC)),
        )
        .limit(1)
    )
    return None if reason is None else f"стоп-лист, причина «{reason.value}»"


async def unwritable(session: AsyncSession, dialog: Linked) -> str | None:
    """Почему лиду диалога писать нельзя — словами; `None` — можно: не снят, не передан
    телемаркетологу, не в стоп-листе. Одно правило у отправки письма и ответа (`_writable`)
    и у черновика агента (`replies.py`): лиду, которому не пишут, черновик не пишется —
    модель не платит за письмо, которое не уйдёт."""
    lead = dialog.lead
    if lead.status is not LeadStatus.READY:
        return f"лида {lead.email} сняли (сейчас «{lead.status.value}») — писать ему нельзя"
    if await handoff.handed_off(session, lead.id):
        return f"лид {lead.email} у телемаркетолога — письма продаж ему не идут"
    if (why := await stopped_by(session, lead, dialog.host)) is not None:
        return f"лиду {lead.email} писать нельзя: {why}"
    return None


async def _writable(session: AsyncSession, dialog: Linked, what: str) -> None:
    """Лиду ещё можно писать (`unwritable`) — или отказ, как стоп-лист: цепочка кончается."""
    why = await unwritable(session, dialog)
    if why is not None:
        raise LeadStoppedError(f"{what}: {why}")


async def _colleague_writable(
    session: AsyncSession, message: MessageModel, dialog: Linked, what: str
) -> None:
    """Ответ уходит коллеге лида (`colleague_of`) — и его адресу можно писать: стоп-лист продаж
    и общий по правилу продаж (`stopped_by` с его адресом); иначе отказ, как стоп-лист."""
    colleague = await _answered_colleague(session, message, dialog)
    if colleague is None:
        return
    why = await stopped_by(session, dialog.lead, dialog.host, address=colleague)
    if why is not None:
        raise LeadStoppedError(f"{what}: коллеге лида {colleague} писать нельзя: {why}")


def _first_problem(dialog: Linked, message: MessageModel) -> str | None:
    """Почему первое письмо не уходит — словами; `None` — уходит: адрес проверен не выдуманным
    проверяльщиком при живом и подтверждён (`unconfirmed.first_letter`), письмо в коридоре
    отличия и собрано после последней правки лида (`lead_edit.edited`). Адрес — первым:
    пересборка его не исправит, и «соберите очередь заново» увело бы человека не туда."""
    if (held := first_letter(dialog.lead)) is not None:
        return held
    verdict = corridor_verdict(message.uniqueness_pct or 0.0)
    if verdict is not None:
        return f"{verdict} — письмо продаж уходит только в коридоре, соберите очередь заново"
    return EDITED if edited(dialog.lead, message) else None


class FollowupStoppedError(SuppressedError):
    """Добивку не собрать ни нынешней цепочкой, ни сохранённым прежним текстом (решение владельца
    13.10): для цепочки — как стоп-лист, проход добивок её останавливает с этой причиной."""


async def check(session: AsyncSession, message: MessageModel) -> None:
    """Проверка письма продаж перед отправкой — ветка продаж `sending._check_review`. Ответ
    лиду в переписке (`ANSWER_STEP`) — не шаг цепочки: подпись в его тексте не отказ, и
    «кому не пишем» его не держит — лид написал сам. Ответ коллеге лида уходит на его
    адрес — стоп-лист судит и этот адрес (`_colleague_writable`)."""
    what = f"Письмо №{message.id}"
    dialog = await linked(session, message.thread_id)
    if dialog is None:
        raise LeadMissingError(f"{what}: у письма продаж нет лида — письмо стоит убрать из очереди")
    await _writable(session, dialog, what)
    await _colleague_writable(session, message, dialog, what)
    if (refused := _chain_refused(dialog, message, what)) is not None:
        raise refused
    rule = letter.answer_problem if message.step >= ANSWER_STEP else letter.problem
    signer = (await sender.read(session)).in_language(dialog.link.language)  # набор языка письма
    problem = rule(message.body or "", signer)
    if problem is not None:
        raise _text_refused(message, what, problem)


def _chain_refused(dialog: Linked, message: MessageModel, what: str) -> SendError | None:
    """Почему письмо цепочки не уходит — отказом; `None` — уходит. Лид в «кому не пишем»
    (`not_writing.py`, решение владельца 13.10) — как стоп-лист: цепочка кончается; первое
    письмо не в коридоре, собрано до правки лида или проверено выдуманным при живом — «не
    готово». Ответ в переписке — не письмо цепочки."""
    if message.step >= ANSWER_STEP:
        return None
    off = excluded(dialog.lead.email, dialog.host)
    if off is not None:
        return NotWritingError(f"{what}: {off} — письма продаж этому лиду не уходят")
    why = _first_problem(dialog, message) if message.step == FIRST_STEP else None
    return None if why is None else NotReadyError(f"{what} не уходит: {why}")


def _text_refused(message: MessageModel, what: str, problem: str) -> SendError:
    """Текст письма не уходит: у добивки — её не собрать ни нынешней цепочкой, ни прежним
    текстом (текст собран здесь же, `followup`), и цепочка останавливается; у остальных —
    «не готово», письмо ждёт в очереди."""
    if FIRST_STEP < message.step < ANSWER_STEP:
        return FollowupStoppedError(
            f"{what}: добивку {followup_texts.NOT_BUILT} — {problem}; цепочка остановлена"
        )
    return NotReadyError(f"{what} не уходит: {problem}")


async def answer(session: AsyncSession, thread_id: int, body: str, what: str) -> str:
    """Ответ лиду в переписке до заведения письма (`stages.answer_text`): продажи подключены,
    у переписки есть лид, ему можно писать; текст — с подписью и физическим адресом из
    «Отправителя» в конце (`letter.answered`). Цепочка не нужна: ответ — не её шаг.

    Метрики Ahrefs проверяются в итоговом тексте, как у сборки очереди — в собранном письме:
    подпись и адрес из настроек — тоже текст письма, а текст ответа почта проверила до них."""
    found = await connection.check(session, what)
    dialog = await linked(session, thread_id)
    if dialog is None:
        raise SendError(f"{what}: у переписки нет лида продаж — её начала не сборка очереди продаж")
    await _writable(session, dialog, what)
    found = found.in_language(dialog.link.language)  # подпись и адрес — языка переписки
    text = letter.answered(body, found)
    if (leak := guards.metrics_leak(text)) is not None:
        raise NotReadyError(
            f"{what} не уходит: в ответе с подписью и адресом из настроек метрики Ahrefs "
            f"({leak}) — правила Ahrefs это запрещают; уберите их из текста ответа или из "
            "подписи и адреса на экране «Продажи» → «Отправитель»"
        )
    problem = letter.answer_problem(text, found)
    if problem is not None:
        raise NotReadyError(f"{what} не уходит: {problem}")
    return text


async def connected(session: AsyncSession) -> bool:
    """Подключены ли продажи — на уровне направления, без цепочки отдельного лида."""
    return not await missing(session)


async def missing(session: AsyncSession) -> list[str]:
    """Чего не хватает продажам, словами человека (`core/stages.SalesLacking`); пусто — подключены.
    Мост спрашивает это вместо `connected`, и отказ пачке называет причину, как отказ одному
    письму. Правило одно у отправки, сборки и экрана — `connection.missing`."""
    return await connection.missing(session)


async def batch(what: str) -> None:
    """Пачка продаж (`core/stages.check_batch`): пока включена автоотправка — отказ словами,
    письма уходят проходом по одному с ящика (`autosend.py`); выключена — пачка как была."""
    if autosend.switched_on():
        raise SalesNotConnectedError(what, autosend.BY_ITSELF, words=autosend.BATCH_OFF)


async def followup(session: AsyncSession, thread_id: int | None, step: int) -> SalesFollowup:
    """Текст добивки шага `step` (шаг письма, с нуля) в переписке продаж `thread_id`: собранный
    с первым письмом той же версией цепочки или шаблон шага; его не собрать — сохранённый при
    сборке прежний (`followup_texts.choose`). Нет и его — текст как есть: проверка отправки
    (`check`) его не пустит, и проход добивок кончит цепочку с причиной словами (решение
    владельца 13.10). До решения такая добивка была «пока нельзя» — срок плюс час, без конца."""
    what = f"Добивка шага {step} в переписке №{thread_id}"
    found = await connection.check(session, what)
    dialog = await linked(session, thread_id)
    if dialog is None:
        raise SalesNotConnectedError(what, "у переписки нет лида продаж — её начала не сборка")
    number = letter.template_step(step)
    ready = await _ready_chain(session, dialog.link, what)
    if number not in ready.steps:  # полная цепочка — три шага; четвёртого письма не бывает
        raise SalesNotConnectedError(what, f"шага {number} у цепочки нет")
    values = letter.values_of(dialog.lead, dialog.host)
    found = found.in_language(dialog.link.language)  # подпись и адрес — языка цепочки
    check_text = followup_texts.unbuilt(found)
    chosen = followup_texts.choose(dialog.link.followups, ready, number, values, check_text)
    if chosen.earlier:
        logger.info(
            "продажи: %s уходит сохранённым прежним текстом — нынешней цепочкой не собрать: %s",
            what,
            chosen.problem,
        )
    return SalesFollowup(body=letter.signed(chosen.body, found))


async def threads_to(session: AsyncSession, email: str) -> list[int]:
    """Переписки продаж, чьи письма уходят на этот адрес (`core/stages.sales_threads_to`): лида с
    ним — письма уходят адресу лида (`recipient`), — и те, где ответ ушёл коллеге лида с этим
    адресом (`colleague_of`). Стоп-лист снимает по ним письма адресу, а сверка событий доставки
    узнаёт письмо (`letters/events`): без переписок коллеги отказ и жалоба на ответ ему были бы
    «событием о чужом письме»."""
    address = email.strip().lower()
    leads = await session.scalars(
        select(SalesThreadModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(func.lower(SalesLeadModel.email) == address)
    )
    answered = await session.execute(
        select(SalesThreadModel, SalesLeadModel, DomainModel.host)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
        .join(MessageModel, MessageModel.thread_id == SalesThreadModel.thread_id)
        .join(ReplyModel, ReplyModel.id == MessageModel.answers_reply_id)
        .where(func.lower(ReplyModel.from_email) == address)
    )
    dialogs = (Linked(*row) for row in answered.tuples())
    colleague = {dialog.link.thread_id for dialog in dialogs if colleague_of(address, dialog)}
    return sorted({*leads, *colleague})


async def addresses(session: AsyncSession, thread_ids: Sequence[int]) -> dict[int, str]:
    """Адрес лида каждой переписки продаж (`core/stages.sales_addresses`) — `lead_addresses`."""
    return await lead_addresses.by_thread(session, thread_ids)


async def policy(session: AsyncSession) -> MailPolicy:
    """Политика почты продаж (`core/stages.mail_policy`): окно, сигналы, сторож, темп — из настроек.

    Сторож — только у подключённых продаж: выключенные продажи с ушедшими письмами и ящиками
    дали бы ложное «ящик молчит» или «отправить некому» — молчат они по выключателю.
    """
    found = replace(sales_policy(), pace=autosend.pace())
    return replace(found, watch=False) if await connection.missing(session) else found
