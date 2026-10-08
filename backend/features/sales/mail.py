"""Письмо продаж в общей почте — ответы модуля продаж на вопросы моста `core/stages.py`.

Почта (`letters/`) модуль продаж не импортирует (контракт `mail-does-not-know-sales`):
о письме продаж она спрашивает мост (протокол `stages.SalesMail`), мост — этот модуль:
он подключён к мосту при загрузке пакета продаж (`sales/__init__.py`). Своей отправки, своих добивок
и своего балансировщика у продаж нет: письмо уходит общей отправкой (`letters/sending.py`),
пачкой — общим `send_queue`, добивки — общим проходом (`letters/followups.py`), ящик —
общим `sender_rules.pick` по этапу продаж. Здесь — только то, что знают продажи:

- `recipient` — кому и от чьего имени, до выбора учётки этапа: подключены ли продажи
  (`connection.py`), адрес лида по явной связи диалога (`sales_threads`), имя в From
  из «Отправителя», готова ли цепочка набора, которым написано первое письмо; пояса
  получателя для окна (`policy.zones_of`: лида, его страны);
- `check` — ветка продаж проверки перед отправкой (вместо отказа 1.1b в
  `sending._check_review`): лид не снят, не передан телемаркетологу
  (`handoff.handed_off` — переданному цепочка не идёт), не в стоп-листе; первое письмо —
  в коридоре отличия; подпись и адрес на месте, подстановок без значения нет
  (`letter.problem`);
- `connected` — подключены ли продажи: проход добивок берёт их сроки только тогда, иначе
  срок цел и проход называет его вслух;
- `followup` — текст добивки: шаг цепочки того же набора и языка, что у первого письма,
  подстановки лида, подпись и адрес. Тему даёт первое письмо — у шаблона добивки её нет;
- `policy` — политика почты продаж (`policy.sales_policy`): окно получателя, мягкие
  сигналы ящика, сторож.

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

from sqlalchemy import func, or_, select
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
from backend.features.letters import compose
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.sending import NotReadyError, SendError, SuppressedError
from backend.features.letters.uniqueness import corridor_verdict
from backend.features.sales import chain, connection, handoff, letter, sender
from backend.features.sales.cleaning import ASKED_NOT_TO_WRITE
from backend.features.sales.intake import host_key
from backend.features.sales.models import (
    LeadStatus,
    SalesLeadModel,
    SalesStoplistModel,
    SalesThreadModel,
)
from backend.features.sales.policy import sales_policy, zones_of

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
    found = (
        await session.execute(
            select(SalesThreadModel, SalesLeadModel, DomainModel.host)
            .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
            .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
            .where(SalesThreadModel.thread_id == thread_id)
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
    дальше (`letters/batch.py`), как на стоп-листе одного лида."""
    found = await connection.check(session, what)
    dialog = await linked(session, message.thread_id)
    if dialog is None:
        raise SendError(
            f"{what}: у письма продаж нет лида — его собрала не сборка очереди продаж. "
            "Письмо стоит убрать из очереди"
        )
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
    сам просил не писать."""
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
            or_(
                SuppressionModel.stage.is_(None),
                SuppressionModel.stage == Stage.SALES,
                SuppressionModel.reason.in_(ASKED_NOT_TO_WRITE),
            ),
            SuppressionModel.in_force(now or datetime.now(UTC)),
        )
        .limit(1)
    )
    return None if reason is None else f"стоп-лист, причина «{reason.value}»"


async def _writable(session: AsyncSession, dialog: Linked, number: int) -> None:
    """Лиду ещё можно писать: не снят, не передан, не в стоп-листе."""
    lead = dialog.lead
    if lead.status is not LeadStatus.READY:
        raise LeadStoppedError(
            f"Лида {lead.email} сняли после сборки письма №{number} (сейчас «{lead.status.value}») "
            "— писать ему нельзя"
        )
    if await handoff.handed_off(session, lead.id):
        raise LeadStoppedError(
            f"Лид {lead.email} передан телемаркетологу — письма продаж ему больше не идут, "
            "цепочка остановлена"
        )
    if (why := await stopped_by(session, lead, dialog.host)) is not None:
        raise LeadStoppedError(f"Лиду {lead.email} писать нельзя: {why}")


async def check(session: AsyncSession, message: MessageModel) -> None:
    """Проверка письма продаж перед отправкой — ветка продаж `sending._check_review`."""
    what = f"Письмо №{message.id}"
    dialog = await linked(session, message.thread_id)
    if dialog is None:
        raise SendError(f"{what}: у письма продаж нет лида — письмо стоит убрать из очереди")
    await _writable(session, dialog, message.id)
    if message.step == FIRST_STEP:
        verdict = corridor_verdict(message.uniqueness_pct or 0.0)
        if verdict is not None:
            raise NotReadyError(
                f"{what} не уходит: {verdict} — письмо продаж уходит только в коридоре, "
                "соберите очередь заново"
            )
    problem = letter.problem(message.body or "", await sender.read(session))
    if problem is not None:
        raise NotReadyError(f"{what} не уходит: {problem}")


async def connected(session: AsyncSession) -> bool:
    """Подключены ли продажи — на уровне направления, без цепочки отдельного лида."""
    return not await connection.missing(session)


async def followup(session: AsyncSession, thread_id: int | None, step: int) -> SalesFollowup:
    """Текст добивки шага `step` (шаг письма, с нуля) в переписке продаж `thread_id`."""
    what = f"Добивка шага {step} в переписке №{thread_id}"
    found = await connection.check(session, what)
    dialog = await linked(session, thread_id)
    if dialog is None:
        raise SalesNotConnectedError(what, "у переписки нет лида продаж — её начала не сборка")
    number = letter.template_step(step)
    template = (await _ready_chain(session, dialog.link, what)).steps.get(number)
    if template is None:  # полная цепочка — три шага; четвёртого письма не бывает
        raise SalesNotConnectedError(what, f"шага {number} у цепочки нет")
    values = letter.values_of(dialog.lead, dialog.host)
    body = letter.signed(compose.assemble(compose.render(template.letter, values), {}).body, found)
    problem = letter.problem(body, found)
    if problem is not None:  # письма ещё нет — «пока нельзя», а не поломка модуля
        raise SalesNotConnectedError(what, problem, words="не собрана")
    return SalesFollowup(body=body)


async def policy(session: AsyncSession) -> MailPolicy:
    """Политика почты продаж (`core/stages.mail_policy`): окно, мягкие сигналы, сторож — из настроек.

    Сторож — только у подключённых продаж: выключенные продажи с ушедшими письмами и ящиками
    дали бы ложное «ящик молчит» или «отправить некому» — молчат они по выключателю.
    """
    found = sales_policy()
    return replace(found, watch=False) if await connection.missing(session) else found
