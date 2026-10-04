"""Запросы под приём ответов.

Собраны здесь по той же причине, что у остальной рассылки: ни конвейер,
ни веб-слой не должны знать, из скольких таблиц складывается «этот ответ
относится к тому письму».
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.preference import DEAD
from backend.features.core.domain import (
    ContactSource,
    MessageStatus,
    ReplyKind,
    Stage,
    SuppressionReason,
    ThreadStatus,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.replies import robots
from backend.features.replies.binding import Unbound
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Incoming, masked_for_log

logger = logging.getLogger(__name__)

#: Сколько адресов письма хранить. Наш — первым (конверт идёт раньше «кому»
#: и копии, `mime.from_form`); рассылка на сотню адресов в копии — не ответ
#: донора, и её копия ответу ни к чему.
MAX_TO_ADDRESSES = 10


def _kept_addresses(incoming: Incoming) -> list[str]:
    """Адреса письма в том виде, в каком их хранит ответ: наш первым,
    не больше `MAX_TO_ADDRESSES`, каждый — в длину колонки адреса."""
    return [address[:255] for address in incoming.to[:MAX_TO_ADDRESSES]]


class UnknownReplyError(ValueError):
    """Ответа с таким номером нет."""


class NotAPriceError(ValueError):
    """Ответ рекламодателя: цены площадки в нём нет, подтверждать нечего."""


class LeadError(ValueError):
    """Лидом этот ответ не взять: он не лид или уже в работе."""


@dataclass(frozen=True, slots=True)
class Addressee:
    """Наше письмо и всё, что нужно, чтобы применить последствия."""

    message: MessageModel
    thread: ThreadModel | None
    domain_id: int
    host: str
    stage: Stage


class ReplyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- приём ---

    async def taken(self, inbound_message_id: str) -> ReplyModel | None:
        """Ответ, которым это письмо уже принято. `None` — письмо новое.

        Провайдер доставляет вебхуки «хотя бы один раз» и повторяет их
        при сбое; без этой проверки повтор дал бы второй ответ, второй
        разбор и второй платный вызов модели. Сам ответ, а не «да»: повтору
        нужно знать, шёл ли у него разбор цены (`parse_never_ran`).
        """
        if not inbound_message_id:
            return None
        found: ReplyModel | None = await self._session.scalar(
            select(ReplyModel).where(ReplyModel.inbound_message_id == inbound_message_id)
        )
        return found

    async def parse_never_ran(self, reply: ReplyModel) -> bool:
        """Ждёт ли ответ разбора цены, который так и не шёл.

        Разбор оставляет у ответа снимок модели (`model_parse`) всегда —
        и когда цена нашлась, и когда модель отказала: без снимка ответ
        не разбирался ни разу. Решение человека разбор заменяет: взятый
        им ответ модели не отдаётся. Так выглядит ответ, чей разбор
        не встал в очередь — очередь лежала в минуту вебхука.
        """
        if reply.kind is not ReplyKind.HUMAN or reply.model_parse is not None:
            return False
        if reply.reviewed_at is not None:
            return False
        return await self.stage_of(reply) is Stage.DONORS

    async def ours_by_internet_message_id(
        self, message_ids: Sequence[str]
    ) -> list[tuple[str, int]]:
        """Наши письма по их `Message-ID` — запасной путь привязки.

        Ищется наш собственный идентификатор, а не номер письма у платформы:
        в заголовках ответа донор возвращает то, что видел у себя в ящике, —
        токен `<…@…>`, — и сравнение точное, как у метки.
        """
        if not message_ids:
            return []
        rows = await self._session.execute(
            select(MessageModel.internet_message_id, MessageModel.id).where(
                MessageModel.internet_message_id.in_(list(message_ids))
            )
        )
        return [(str(found), message_id) for found, message_id in rows.all()]

    async def addressee(self, message_id: int) -> Addressee | None:
        """Кому мы писали этим письмом."""
        rows = await self._session.execute(
            select(MessageModel, DomainModel.host, CampaignModel.stage)
            .join(DomainModel, DomainModel.id == MessageModel.domain_id)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .where(MessageModel.id == message_id)
        )
        found = rows.first()
        if found is None:
            return None

        message, host, stage = found
        thread = (
            await self._session.get(ThreadModel, message.thread_id)
            if message.thread_id is not None
            else None
        )
        return Addressee(
            message=message,
            thread=thread,
            domain_id=message.domain_id,
            host=host,
            stage=stage,
        )

    async def stage_of(self, reply: ReplyModel) -> Stage | None:
        """Этап рассылки, на письмо которой ответили. `None` — ответ ни к чему
        не привязан. Порядок тот же, что у `domain_of`: диалог переживает
        удаление письма."""
        if reply.thread_id is not None:
            stage = await self._session.scalar(
                select(CampaignModel.stage)
                .join(ThreadModel, ThreadModel.campaign_id == CampaignModel.id)
                .where(ThreadModel.id == reply.thread_id)
            )
            if stage is not None:
                return stage
        if reply.message_id is None:
            return None
        found: Stage | None = await self._session.scalar(
            select(CampaignModel.stage)
            .join(MessageModel, MessageModel.campaign_id == CampaignModel.id)
            .where(MessageModel.id == reply.message_id)
        )
        return found

    async def domain_of(self, reply: ReplyModel) -> int | None:
        """Чей это донор.

        Сначала по диалогу, потом по письму. Порядок важен: связь письма
        стирается при его удалении (`ondelete="SET NULL"`), а диалог
        остаётся — и ответ, потерявший письмо, всё равно принадлежит
        своему донору.

        Найдено живым прогоном: подтверждение разбора срабатывало,
        а цена в карточку донора не попадала, потому что искали только
        через письмо.
        """
        if reply.thread_id is not None:
            thread = await self._session.get(ThreadModel, reply.thread_id)
            if thread is not None:
                return thread.domain_id

        if reply.message_id is not None:
            message = await self._session.get(MessageModel, reply.message_id)
            if message is not None:
                return message.domain_id

        return None

    # --- последствия ---

    async def suppress(self, email: str) -> None:
        """Адрес в стоп-лист. Донора целиком добавляет человек.

        Без этапа: отписка закрывает адрес на обоих этапах, как со страницы
        отписки и по жалобе. До 04.10.2026 сюда писался этап кампании, и
        отписавшийся донор оставался открыт для писем рекламодателям —
        читатели стоп-листа берут «пусто или свой этап» (находка «Продаж»).

        Срок не ставится и поставить его тут нечем: отписка бессрочна.
        Записи со сроком заводит только человек с экрана, и причины
        ему доступны другие — «вручную» и «поставщик».
        """
        rows = await self._session.execute(
            select(SuppressionModel.id).where(SuppressionModel.email == email)
        )
        if rows.first() is not None:
            return
        self._session.add(
            SuppressionModel(
                email=email,
                reason=SuppressionReason.UNSUBSCRIBED,
                created_by="приём ответов",
            )
        )

    async def mark_contact_dead(self, contact_id: int | None) -> None:
        """Пометить адрес мёртвым (`preference.DEAD`, оценка 0).

        Одной отметки мало, и до 28.09.2026 её одной и не хватало: мёртвый
        адрес с оценкой 0 оставался лучшим, а не дошедшее письмо — «уже
        писали». Следующий адрес открывает сборка писем: мёртвый адрес она
        не берёт, а донора, у которого все письма не дошли и никто
        не ответил, снова собирает — на следующий адрес (`letters/attempts.py`).
        """
        if contact_id is None:
            return
        contact = await self._session.get(ContactModel, contact_id)
        if contact is not None:
            contact.verification_status = DEAD
            contact.verification_score = 0

    async def remember_answering_address(
        self, *, domain_id: int, email: str, now: datetime | None = None
    ) -> ContactModel | None:
        """Адрес, с которого ответили, — к контактам донора и как
        предпочтительный: дальше пишем тому, кто отвечает.

        **Адрес робота не запоминается** (`robots`): письмо от noreply без
        служебных фраз считается ответом человека — его увидит человек, —
        но предпочтительным адресом стал бы робот, и следующее письмо
        донору ушло бы в ящик, который письма выбрасывает. `None` —
        адрес не запомнен.
        """
        if robots.robot(email):
            logger.info(
                "приём: ответили с робота %s — адресом домена №%s он не становится",
                masked_for_log(email),
                domain_id,
            )
            return None
        moment = now or datetime.now(UTC)
        rows = await self._session.execute(
            select(ContactModel)
            .where(ContactModel.domain_id == domain_id)
            .where(ContactModel.email == email)
        )
        contact = rows.scalars().first()
        if contact is None:
            contact = ContactModel(domain_id=domain_id, email=email, source=ContactSource.MANUAL)
            self._session.add(contact)
            await self._session.flush()
        contact.last_replied_at = moment
        return contact

    async def store_price(
        self,
        *,
        domain_id: int,
        price: Decimal,
        currency: str | None,
        now: datetime | None = None,
    ) -> None:
        """Перенести цену в карточку донора.

        Цена кладётся в той валюте, в которой её назвали: конвертации
        в сервисе нет, и подписать евро долларами значит записать
        неверное число.
        """
        rows = await self._session.execute(
            select(DonorModel).where(DonorModel.domain_id == domain_id)
        )
        donor = rows.scalars().first()
        if donor is None:
            return
        donor.last_price = price
        donor.last_price_currency = currency
        donor.last_price_at = now or datetime.now(UTC)

    async def record_seller_answer(
        self,
        *,
        domain_id: int,
        answer: str,
        reply_id: int | None,
        now: datetime | None = None,
    ) -> None:
        """Ответ самого донора — на домен, рядом с вердиктом судьи.

        Вердикт судьи и решение человека не трогаются: по расхождению с ними
        считается, как часто отбор ошибается в главном для гест-постинга.
        """
        domain = await self._session.get(DomainModel, domain_id)
        if domain is None:
            return
        domain.seller_answer = answer
        domain.seller_answer_at = now or datetime.now(UTC)
        domain.seller_answer_reply_id = reply_id

    async def mark_replied(self, thread_id: int | None) -> None:
        """Диалог отвечен человеком.

        Статус читает гейт молчания отбора (`runs/exclusions._silent`):
        ответивший донор не должен выпасть из новых прогонов на год как
        «писали, не ответил». До 28.09.2026 статус не ставил никто — гейт
        опирался на то, чего не бывало, а тест ставил статус руками
        в фикстуре и этого не видел.

        Отписку ответ не перебивает: она сильнее, и диалог, закрытый кнопкой
        отписки, отвеченным не становится.
        """
        if thread_id is None:
            return
        thread = await self._session.get(ThreadModel, thread_id)
        if thread is not None and thread.status is ThreadStatus.OPEN:
            thread.status = ThreadStatus.REPLIED

    async def stop_chain(self, thread_id: int | None) -> int:
        """Остановить цепочку: ни одного следующего письма этому донору.

        Гасится и то, что стоит в очереди, и **срок у уже отправленного**:
        добивка не лежит в очереди заранее, она рождается по сроку. Пока
        срок цел, ответивший донор получит следующее письмо — то самое
        неуважение, ради запрета которого правило и написано.

        Возвращает, сколько писем это затронуло. Ноль — обычное дело:
        до добивок доходит меньшинство диалогов.
        """
        if thread_id is None:
            return 0
        rows = await self._session.execute(
            select(MessageModel).where(MessageModel.thread_id == thread_id)
        )
        stopped = 0
        for message in rows.scalars().all():
            if message.status is MessageStatus.QUEUED:
                message.status = MessageStatus.STOPPED
                message.next_action_at = None
                stopped += 1
            elif message.next_action_at is not None:
                message.next_action_at = None
                stopped += 1
        return stopped

    async def mark_bounced(self, message: MessageModel) -> None:
        """Пометить наше письмо не дошедшим.

        Статус двигается только вперёд: подтверждение доставки, пришедшее
        с опозданием, не должно перебивать отказ.
        """
        if message.status in (MessageStatus.SENT, MessageStatus.DELIVERED):
            message.status = MessageStatus.BOUNCED

    # --- запись ответа ---

    def add(
        self,
        incoming: Incoming,
        *,
        kind: ReplyKind,
        thread_id: int | None,
        message_id: int | None,
        found: Extracted | None,
        unbound: Unbound | None = None,
    ) -> ReplyModel:
        """Записать ответ вместе с исходным текстом.

        Разобранное приходит одним значением, а не восемью полями: восемь
        полей рядом означают, что одно из них однажды забудут передать,
        и письмо ляжет в базу без цены, которую из него достали.

        Адреса, на которые письмо пришло, пишутся у каждого ответа, а не только
        у непривязанного: по ним видно, на какой домен ответов и с какой меткой
        он пришёл, — это и есть ответ на вопрос «почему не привязался».
        """
        reply = ReplyModel(
            thread_id=thread_id,
            message_id=message_id,
            kind=kind,
            raw_body=incoming.text,
            inbound_message_id=incoming.message_id or None,
            from_email=incoming.from_email[:255],
            subject=incoming.subject[:512],
            to_addresses=_kept_addresses(incoming),
            unbound_reason=unbound,
            price_white=found.price_white if found else None,
            price_grey=found.price_grey if found else None,
            currency=found.currency if found else None,
            payment_methods=list(found.payment_methods) if found else None,
            confidence=found.confidence if found else None,
        )
        self._session.add(reply)
        return reply

    async def reply(self, reply_id: int) -> ReplyModel:
        found = await self._session.get(ReplyModel, reply_id)
        if found is None:
            raise UnknownReplyError(f"Ответа №{reply_id} нет")
        return found

    async def confirm(
        self,
        reply: ReplyModel,
        *,
        by: str,
        price_white: Decimal | None,
        price_grey: Decimal | None,
        currency: str | None,
        payment_methods: list[str] | None,
        now: datetime | None = None,
    ) -> None:
        """Подтвердить разбор руками.

        **Подтверждение человека сильнее любой уверенности модели.**
        Уверенность при этом не трогаем: она осталась тем, что сказала
        модель, и переписать её значило бы стереть след — потом никто
        не проверит, часто ли модель ошибается.

        **Ответ рекламодателя подтвердить нельзя.** Цена из него легла бы
        в карточку донора, если сайт заодно донор: его расход стал бы ценой
        площадки. Отказ — до записи, а не после.
        """
        if await self.stage_of(reply) is Stage.ADVERTISERS:
            raise NotAPriceError(
                f"Ответ №{reply.id} — от рекламодателя: это лид, а не цена площадки, "
                "и в карточку донора он не ложится. Вести его в переписке"
            )
        reply.price_white = price_white
        reply.price_grey = price_grey
        reply.currency = currency
        reply.payment_methods = payment_methods or None
        reply.reviewed_by = by[:128]
        reply.reviewed_at = now or datetime.now(UTC)

    async def take_lead(
        self, reply: ReplyModel, *, by: str, now: datetime | None = None
    ) -> datetime:
        """Взять ответ рекламодателя в работу.

        Лид — не цена: разбирать в нём нечего, решение одно — кто его ведёт.
        Кладётся туда же, где у донора подтверждение разбора (`reviewed_*`):
        «ждёт человека» у обоих этапов считается по одному полю.

        **Взятый второй раз — отказ, а не тихое «ещё раз взят».** Двое,
        открывших один лид, должны узнать друг о друге до письма клиенту,
        а не после.
        """
        if reply.kind is not ReplyKind.HUMAN or await self.stage_of(reply) is not Stage.ADVERTISERS:
            raise LeadError(
                f"Ответ №{reply.id} — не лид: лидом становится ответ человека на оффер "
                "рекламодателю. Ответ донора разбирают как цену"
            )
        if reply.reviewed_at is not None:
            raise LeadError(
                f"Лид по ответу №{reply.id} уже в работе: взял {reply.reviewed_by} "
                f"{reply.reviewed_at:%d.%m.%Y %H:%M} UTC"
            )
        moment = now or datetime.now(UTC)
        reply.reviewed_by = by[:128]
        reply.reviewed_at = moment
        return moment
