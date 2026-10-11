"""Письмо продаж в общей почте — ответы модуля продаж на вопросы моста `core/stages.py`.

Почта (`letters/`) модуль продаж не импортирует (контракт `mail-does-not-know-sales`):
о письме продаж она спрашивает мост (протокол `stages.SalesMail`), мост — этот модуль:
он подключён к мосту при загрузке пакета продаж (`sales/__init__.py`). Своей отправки, своих добивок
и своего балансировщика у продаж нет: письмо уходит общей отправкой (`letters/sending.py`),
пачкой — общим `send_queue`, добивки — общим проходом (`letters/followups.py`), ящик —
общим `sender_rules.pick` по этапу продаж. Здесь — только то, что знают продажи:

- `recipient` — кому и от чьего имени, до выбора учётки этапа: подключены ли продажи
  (`connection.py`), адрес лида по явной связи диалога (`sales_threads`), имя в From
  из «Отправителя», готова ли цепочка набора, которым написано первое письмо (ответу в
  переписке — нет: он не шаг цепочки); пояса получателя для окна (`policy.zones_of`:
  лида, его страны);
- `check` — ветка продаж проверки перед отправкой (вместо отказа 1.1b в
  `sending._check_review`): лид не снят, не передан телемаркетологу
  (`handoff.handed_off` — переданному цепочка не идёт), не в стоп-листе (`unwritable` —
  то же правило решает, писать ли агенту черновик ответа лида); первое письмо —
  в коридоре отличия, собрано после последней правки лида (`lead_edit.edited`), а адрес не
  проверен выдуманным проверяльщиком при живом (`verifier.unproven`); подпись и адрес на
  месте, подстановок без значения нет (`letter.problem`, у ответа в переписке —
  `letter.answer_problem`);
- `connected` — подключены ли продажи: проход добивок берёт их сроки только тогда, иначе
  срок цел и проход называет его вслух;
- `answer` — ответ лиду в переписке до заведения письма: продажи подключены, у переписки
  есть лид, ему можно писать; текст — с подписью и адресом из «Отправителя» в конце, и
  метрик Ahrefs в нём нет;
- `followup` — текст добивки: собранный при сборке первого письма (`followup_texts.py`,
  у переписки лида), если цепочка набора с тех пор не менялась, иначе — шаг цепочки того же
  набора и языка, что у первого письма, с подстановками лида; подпись и адрес — из
  «Отправителя». Модели здесь нет. Тему даёт первое письмо — у шаблона добивки её нет;
- `policy` — политика почты продаж (`policy.sales_policy`): окно получателя, мягкие
  сигналы ящика, сторож;
- `threads_to` — переписки лидов с этим адресом: по ним стоп-лист снимает письма продаж
  адресу (`letters/stoplist.stop_pending`) — строки `contacts` у них нет.

**Отказ — только словами почты** (договор моста): «писать больше нельзя» (лид снят,
передан, в стоп-листе) — `LeadStoppedError`, наследник стоп-листа: цепочка кончается;
«письмо не то» — `NotReadyError`: письмо стоит в очереди с причиной, пачка идёт дальше (и
первое письмо, чья цепочка набора неполна: письма других наборов уходят); «пока нельзя»
(продажи не подключены, цепочка добивки неполна, добивка не собирается) —
`SalesNotConnectedError`: пачка встаёт, срок добивки возвращается. Другое исключение мост
считает поломкой модуля. Ответы читают базу и ничего не сохраняют: мост спрашивает модуль в
своей точке сохранения.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel
from backend.features.core.stages import (
    MailPolicy,
    Recipient,
    SalesFollowup,
    SalesNotConnectedError,
)
from backend.features.letters import guards
from backend.features.letters.chain import ANSWER_STEP, FIRST_STEP
from backend.features.letters.sending import NotReadyError, SendError, SuppressedError
from backend.features.letters.uniqueness import corridor_verdict
from backend.features.sales import chain, connection, followup_texts, handoff, letter, sender
from backend.features.sales.intake import host_key
from backend.features.sales.lead_edit import EDITED, edited
from backend.features.sales.models import (
    LeadStatus,
    SalesLeadModel,
    SalesStoplistModel,
    SalesThreadModel,
)
from backend.features.sales.policy import sales_policy, zones_of
from backend.features.sales.suppressions import holds_sales
from backend.features.sales.verifier import unproven

logger = logging.getLogger(__name__)


class LeadStoppedError(SuppressedError):
    """Этому лиду писать больше нельзя: снят, передан телемаркетологу, в стоп-листе.
    Для цепочки — как стоп-лист: проход добивок её останавливает."""


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
    """Адрес лида и имя отправителя — если продажи подключены и цепочка письма полна.

    Неполная цепочка — отказ этому письму, а не этапу (`NotReadyError`): цепочка своя у набора
    письма (гипотеза и язык), и письма других наборов уходят — пачка считает это письмо и идёт
    дальше (`letters/batch.py`), как на стоп-листе одного лида. Ответу лиду в переписке
    (`ANSWER_STEP`) цепочка не нужна: он не её шаг."""
    found = await connection.check(session, what)
    dialog = await linked(session, message.thread_id)
    if dialog is None:
        raise SendError(
            f"{what}: у письма продаж нет лида — его собрала не сборка очереди продаж. "
            "Письмо стоит убрать из очереди"
        )
    if message.step < ANSWER_STEP:
        link = dialog.link
        try:
            (await chain.of_set(session, link.chain_hypothesis_id, link.language)).check_ready()
        except chain.ChainNotReadyError as exc:
            raise NotReadyError(f"{what} не уходит: {exc}") from exc
    return Recipient(
        Stage.SALES, dialog.lead.email, found.values["sender_name"], zones=zones_of(dialog.lead)
    )


async def stopped_by(
    session: AsyncSession, lead: SalesLeadModel, host: str, *, now: datetime | None = None
) -> str | None:
    """Почему этому лиду писать нельзя — стоп-лист продаж или общий; `None` — можно.

    Те же правила, что у очистки (`cleaning.py`): стоп-лист продаж держит адрес, домен
    компании и домен адреса; общий — строки без этапа, этапа продаж и те, где человек
    сам просил не писать (`suppressions.holds_sales` — одно условие на очистку и отправку)."""
    mailbox = lead.email.rpartition("@")[2]
    hosts = sorted({host, mailbox, host_key(mailbox)} - {""})
    manual = await session.scalar(
        select(func.count())
        .select_from(SalesStoplistModel)
        .where(
            or_(
                SalesStoplistModel.email == lead.email.lower(),
                SalesStoplistModel.host.in_(hosts),
            )
        )
    )
    if manual:
        return "в ручном стоп-листе продаж"
    reason = await session.scalar(
        select(SuppressionModel.reason)
        .where(
            or_(SuppressionModel.domain_id == lead.domain_id, SuppressionModel.email == lead.email),
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


def _first_problem(dialog: Linked, message: MessageModel) -> str | None:
    """Почему первое письмо не уходит — словами; `None` — уходит: оно в коридоре отличия,
    собрано после последней правки лида (`lead_edit.edited`), а адрес не проверен выдуманным
    проверяльщиком при живом (`verifier.unproven`)."""
    verdict = corridor_verdict(message.uniqueness_pct or 0.0)
    if verdict is not None:
        return f"{verdict} — письмо продаж уходит только в коридоре, соберите очередь заново"
    return EDITED if edited(dialog.lead, message) else unproven(dialog.lead)


async def check(session: AsyncSession, message: MessageModel) -> None:
    """Проверка письма продаж перед отправкой — ветка продаж `sending._check_review`. Ответ
    лиду в переписке (`ANSWER_STEP`) — не шаг цепочки: подпись в его тексте не отказ."""
    what = f"Письмо №{message.id}"
    dialog = await linked(session, message.thread_id)
    if dialog is None:
        raise SendError(f"{what}: у письма продаж нет лида — письмо стоит убрать из очереди")
    await _writable(session, dialog, what)
    if message.step == FIRST_STEP and (why := _first_problem(dialog, message)) is not None:
        raise NotReadyError(f"{what} не уходит: {why}")
    rule = letter.answer_problem if message.step >= ANSWER_STEP else letter.problem
    problem = rule(message.body or "", await sender.read(session))
    if problem is not None:
        raise NotReadyError(f"{what} не уходит: {problem}")


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
    return not await connection.missing(session)


async def followup(session: AsyncSession, thread_id: int | None, step: int) -> SalesFollowup:
    """Текст добивки шага `step` (шаг письма, с нуля) в переписке продаж `thread_id`: собранный
    с первым письмом той же версией цепочки или шаблон шага (`followup_texts.text_of`)."""
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
    text = followup_texts.text_of(dialog.link.followups, ready, number, values)
    body = letter.signed(text, found)
    problem = letter.problem(body, found)
    if problem is not None:  # письма ещё нет — «пока нельзя», а не поломка модуля
        raise SalesNotConnectedError(what, problem, words="не собрана")
    return SalesFollowup(body=body)


async def threads_to(session: AsyncSession, email: str) -> list[int]:
    """Переписки продаж, чей лид — этот адрес (`core/stages.sales_threads_to`): письма в них
    уходят адресу лида (`recipient`), и стоп-лист снимает их по этим перепискам."""
    found = await session.scalars(
        select(SalesThreadModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(func.lower(SalesLeadModel.email) == email.strip().lower())
        .order_by(SalesThreadModel.thread_id)
    )
    return list(found)


async def policy(session: AsyncSession) -> MailPolicy:
    """Политика почты продаж (`core/stages.mail_policy`): окно, мягкие сигналы, сторож — из настроек.

    Сторож — только у подключённых продаж: выключенные продажи с ушедшими письмами и ящиками
    дали бы ложное «ящик молчит» или «отправить некому» — молчат они по выключателю.
    """
    found = sales_policy()
    return replace(found, watch=False) if await connection.missing(session) else found
