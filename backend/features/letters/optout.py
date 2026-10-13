"""Последствия отписки: кому больше не пишем и что делать с начатым.

Отписка нажатием кнопки и отписка письмом — **разной ширины, и это
решение, а не недосмотр.** Ответ «unsubscribe» приходит с конкретного
ящика, и в стоп-лист идёт адрес: писал человек, говорил за себя.
Кнопку в письме нажимает тот, кому мы написали как сайту, и закрывает
она сайт целиком — иначе цепочка продолжится на втором найденном
адресе того же донора, и это будет рассылка в обход отписки.

**Письмо продаж адресовано человеку, а не сайту** (решение владельца 12.10.2026). Кнопка
и One-Click на нём закрывают адрес этого человека — без этапа, то есть для всех направлений, —
его переписка продаж становится «отписался», а домен в стоп-лист не встаёт: переписки и письма
доноров и рекламодателей на сайте не трогаются (`unsubscribe_label`). Жалоба на письмо продаж
закрывает то же и ещё компанию для продаж — запись на домен с этапом продаж: больше никому
в ней продажи не пишут, а снимает такая запись только письма продаж (`complained`,
`stoplist.stop_pending`). Этап — по письму метки (`label_letter`), как у подписи страницы
(`api/unsubscribe/signer.py`). Письма доноров и рекламодателей — как раньше: сайт целиком.

Адрес человека знает модуль продаж, а почта его не импортирует — спрашивает мост
(`core/stages.py`): кому ушло письмо (`stages.recipient`), а если мост не отвечает (продажи
не подключены, поломка) — какой из адресов сайта ведёт к переписке письма
(`stages.sales_lead_threads`). Не узнать и так — значит, адреса человека нет среди адресов
сайта и письма доноров и рекламодателей до него не дойдут; тогда для продаж закрывается
компания, как при жалобе, и журнал говорит это громко (`_person`).

**Повторное нажатие ничего не ломает.** Донор жмёт кнопку дважды,
почтовый клиент открывает ссылку заранее, человек возвращается на
страницу через неделю — во всех случаях ответ один: «больше не пишем».
Вторая строка в стоп-листе при этом не заводится.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import stages
from backend.features.core.domain import Stage, SuppressionReason, ThreadStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel, ThreadModel
from backend.features.letters import settle
from backend.features.letters.stoplist import stop_pending
from backend.features.letters.unsubscribe import Label

logger = logging.getLogger(__name__)

#: Как подписана строка стоп-листа, заведённая жалобой на спам (`events.py`).
COMPLAINT = "жалоба на спам"


@dataclass(frozen=True, slots=True)
class OptOut:
    """Что стало с донором после нажатия."""

    host: str
    #: Впервые или он уже был в стоп-листе. Страница показывает одно и то
    #: же, но в журнале разница видна: повторы — это предпросмотр ссылок
    #: почтовыми клиентами, и по ним судят, не отписывает ли нас робот.
    first_time: bool
    #: Сколько писем сняли с очереди и сроков.
    stopped: int


async def label_letter(session: AsyncSession, label: Label) -> MessageModel | None:
    """Письмо метки — если метка его называет и письмо того же сайта, что метка: метку
    подписывал и не этот сервис (стенд, база из копии), и чужое письмо она не раскрывает."""
    if label.letter_id is None:
        return None
    letter = await session.get(MessageModel, label.letter_id)
    return letter if letter is not None and letter.domain_id == label.domain_id else None


async def unsubscribe_label(session: AsyncSession, label: Label, *, source: str) -> OptOut | None:
    """Нажатие кнопки отписки с метки: письмо продаж — адрес человека, остальные — сайт
    метки целиком (`unsubscribe_domain`). `None` — сайта метки нет."""
    letter = await label_letter(session, label)
    if letter is not None and await settle.stage_of(session, letter) is Stage.SALES:
        return await _unsubscribe_person(session, letter, source=source)
    return await unsubscribe_domain(session, label.domain_id, source=source)


async def unsubscribe_domain(
    session: AsyncSession, domain_id: int, *, source: str
) -> OptOut | None:
    """Донор в стоп-лист целиком. `None` — такого донора нет.

    Строка стоп-листа без этапа: отписка действует и на второй этап
    тоже. Человек, попросивший больше не писать, не давал согласия
    на письмо в другой роли.
    """
    domain = await session.get(DomainModel, domain_id)
    if domain is None:
        return None

    rows = await session.execute(
        select(SuppressionModel.id).where(
            SuppressionModel.domain_id == domain_id,
            SuppressionModel.stage.is_(None),
        )
    )
    first_time = rows.first() is None
    if first_time:
        session.add(
            SuppressionModel(
                domain_id=domain_id,
                reason=SuppressionReason.UNSUBSCRIBED,
                created_by=source,
            )
        )

    stopped = await _stop_everything(session, domain_id)
    return OptOut(host=domain.host, first_time=first_time, stopped=stopped)


async def complained(session: AsyncSession, letter: MessageModel, email: str) -> int:
    """Жалоба на письмо продаж с адреса `email`: адрес — в стоп-лист без этапа, компания —
    с этапом продаж, назначенное им снято, переписка письма — «отписался». Сколько снято.
    Доноров и рекламодателей сайта жалоба на письмо продаж не трогает."""
    address = email.strip().lower()
    stopped = 0
    if address:
        await _forbid(session, email=address)
        stopped += await stop_pending(session, email=address)
    await _forbid(session, domain_id=letter.domain_id, stage=Stage.SALES)
    stopped += await stop_pending(session, domain_id=letter.domain_id, stage=Stage.SALES)
    await _unsubscribed(session, letter.thread_id)
    return stopped


async def _unsubscribe_person(
    session: AsyncSession, letter: MessageModel, *, source: str
) -> OptOut | None:
    """Отписка с письма продаж: адрес человека — в стоп-лист без этапа, назначенное ему снято,
    переписка письма — «отписался»; адреса не узнать — компания закрыта для продаж."""
    domain = await session.get(DomainModel, letter.domain_id)
    if domain is None:
        return None
    reason = SuppressionReason.UNSUBSCRIBED
    email = await _person(session, letter)
    if email is not None:
        first = await _forbid(session, email=email, reason=reason, source=source)
        stopped = await stop_pending(session, email=email)
    else:
        logger.warning(
            "отписка: письмо продаж №%s — адрес человека не узнать: компания %s закрыта "
            "для продаж, доноров и рекламодателей отписка не тронула",
            letter.id,
            domain.host,
        )
        first = await _forbid(
            session, domain_id=letter.domain_id, stage=Stage.SALES, reason=reason, source=source
        )
        stopped = await stop_pending(session, domain_id=letter.domain_id, stage=Stage.SALES)
    await _unsubscribed(session, letter.thread_id)
    return OptOut(host=domain.host, first_time=first, stopped=stopped)


async def _person(session: AsyncSession, letter: MessageModel) -> str | None:
    """Адрес человека, которому ушло письмо продаж: кому письмо — у моста (`stages.recipient`);
    мост не ответил — адрес сайта, чей лид — переписка письма (`stages.sales_lead_threads`).
    `None` — не узнать: среди адресов сайта адреса лида нет."""
    what = f"Отписка с письма продаж №{letter.id}"
    try:
        found = await stages.recipient(session, letter, Stage.SALES, None, what)
    except stages.MailRefusalError as exc:
        logger.info("отписка: %s — мост продаж не назвал адресата: %s", what, exc)
    else:
        if found.email:
            return found.email.strip().lower()
    addresses = await session.scalars(
        select(ContactModel.email)
        .where(ContactModel.domain_id == letter.domain_id)
        .order_by(ContactModel.id)
    )
    for address in addresses.all():
        threads = await stages.sales_lead_threads(session, address)
        if threads and letter.thread_id in threads:
            return address.strip().lower()
    return None


async def _forbid(
    session: AsyncSession,
    *,
    email: str | None = None,
    domain_id: int | None = None,
    stage: Stage | None = None,
    reason: SuppressionReason = SuppressionReason.COMPLAINED,
    source: str = COMPLAINT,
) -> bool:
    """Бессрочная запись стоп-листа на адрес `email` или, без него, на домен `domain_id` —
    с этапом `stage`, если такой ещё нет. `True` — заведена.

    Уже держит бессрочная запись на того же адресата без этапа или с тем же этапом: запись
    со сроком кончится, а отписка и жалоба бессрочны."""
    target = (
        SuppressionModel.email == email
        if email is not None
        else SuppressionModel.domain_id == domain_id
    )
    same: ColumnElement[bool] = SuppressionModel.stage.is_(None)
    if stage is not None:
        same = or_(same, SuppressionModel.stage == stage)
    held = await session.scalar(
        select(SuppressionModel.id)
        .where(target, same, SuppressionModel.expires_at.is_(None))
        .limit(1)
    )
    if held is not None:
        return False
    session.add(
        SuppressionModel(
            email=email,
            domain_id=None if email is not None else domain_id,
            stage=stage,
            reason=reason,
            created_by=source,
        )
    )
    return True


async def _unsubscribed(session: AsyncSession, thread_id: int | None) -> None:
    """Переписка письма продаж — «отписался»: с этим человеком разговор кончен."""
    thread = await session.get(ThreadModel, thread_id) if thread_id is not None else None
    if thread is not None:
        thread.status = ThreadStatus.UNSUBSCRIBED


async def _stop_everything(session: AsyncSession, domain_id: int) -> int:
    """Снять всё назначенное и пометить диалоги отписавшимися.

    Снятие писем и сроков — общее со стоп-листом (`stoplist.stop_pending`):
    два экземпляра одного правила разъехались бы на первой правке, и тише
    всех разошёлся бы тот, который реже зовут.
    """
    stopped = await stop_pending(session, domain_id=domain_id)
    threads = await session.execute(select(ThreadModel).where(ThreadModel.domain_id == domain_id))
    for thread in threads.scalars().all():
        thread.status = ThreadStatus.UNSUBSCRIBED
    return stopped
