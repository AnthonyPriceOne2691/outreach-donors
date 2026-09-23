"""Цепочка писем: когда уходит добивка и кому её больше не слать.

Три письма вместо одного — требование рассылки, а не удобство: на первое
отвечает меньшинство, и цепочка кончается там, где донор либо ответил,
либо явно попросил замолчать.

    первое письмо ушло → срок добивки на отправленной строке →
    проход забирает подошедшую, шлёт шаблон в тот же тред →
    срок следующей → ответ, отписка или отказ доставки гасят срок

Четыре решения, каждое из которых чинит свой способ навредить донору.

**Захват гасит срок до вызова почты.** `UPDATE ... RETURNING` ставит
`next_action_at = NULL` и фиксируется прежде, чем письмо уйдёт. Сбой
между захватом и почтой оставляет цепочку молчащей — это потерянная
добивка, и это несравнимо лучше, чем второе одинаковое письмо донору,
которое он справедливо пометит спамом.

**Кандидат — только отправленное и доставленное.** Ответ переводит
диалог и гасит срок, отписка и отказ доставки тоже: добивка не уходит
тому, кто уже ответил или попросил перестать.

**Добивка идёт с того же ящика, что и первое письмо.** Менять ящик
на середине переписки значит попасть в спам и запутать собеседника —
то же правило, что у ответа оператора из карточки диалога. Ящик
на паузе не заменяется другим: срок переносится, цепочка ждёт.

**Свой лейн, а не дневной кап.** Дневной кап ящика считает первые
письма; добивки идут своим часовым потолком (решение 21.09.2026).
Иначе backlog первых писем голодит цепочки, а цепочки съедают квоту
новых доноров. Часовой, а не дневной, — чтобы сотня подошедших добивок
не ушла пачкой за минуту: почтовая платформа смотрит на скорость.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import compose, guards, template
from backend.features.letters.building import idempotency_key
from backend.features.letters.chain import CHAINABLE, FIRST_STEP, MAX_STEPS
from backend.features.letters.sending import (
    NoSenderError,
    SendError,
    Sending,
    SuppressedError,
)
from backend.features.letters.transport import Transport

logger = logging.getLogger(__name__)


class FollowupError(RuntimeError):
    """Добивку отправить нельзя, и причина названа."""


@dataclass(frozen=True, slots=True)
class Claimed:
    """Захваченная добивка: что слать, кому и от чьего имени."""

    #: Письмо, после которого пришёл срок. Оно же — якорь треда.
    previous_id: int
    thread_id: int | None
    campaign_id: int
    domain_id: int
    contact_id: int | None
    sender_id: int | None
    step: int
    """Шаг, который предстоит отправить: у предыдущего письма плюс один."""
    host: str
    anchor: str | None
    """Идентификатор первого письма у почты — по нему ответ ложится в тред."""


class Chain:
    """Правила цепочки на настоящей базе."""

    def __init__(self, session: AsyncSession, *, now: datetime | None = None) -> None:
        self._session = session
        self._now = now

    def _moment(self) -> datetime:
        return self._now or datetime.now(UTC)

    async def claim(self) -> Claimed | None:
        """Забрать одну подошедшую добивку. `None` — слать сейчас некому.

        Срок гасится тем же запросом, которым строка выбирается: между
        «нашли» и «погасили» не должно быть места второму проходу.
        """
        moment = self._moment()
        pick = (
            select(MessageModel.id)
            .where(
                MessageModel.status.in_(CHAINABLE),
                MessageModel.next_action_at.is_not(None),
                MessageModel.next_action_at <= moment,
                MessageModel.step < MAX_STEPS - 1,
            )
            .order_by(MessageModel.next_action_at)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        row = (
            await self._session.execute(
                update(MessageModel)
                .where(MessageModel.id == pick)
                .values(next_action_at=None)
                .returning(
                    MessageModel.id,
                    MessageModel.thread_id,
                    MessageModel.campaign_id,
                    MessageModel.domain_id,
                    MessageModel.contact_id,
                    MessageModel.sender_id,
                    MessageModel.step,
                    MessageModel.provider_message_id,
                )
            )
        ).first()
        if row is None:
            return None

        host = await self._host(row[3])
        return Claimed(
            previous_id=row[0],
            thread_id=row[1],
            campaign_id=row[2],
            domain_id=row[3],
            contact_id=row[4],
            sender_id=row[5],
            step=row[6] + 1,
            host=host,
            anchor=await self._anchor(row[1]) or row[7],
        )

    async def restore(self, claimed: Claimed, *, delay: timedelta) -> None:
        """Вернуть срок: отправить сейчас не вышло, но цепочка жива.

        Так выглядит вставший ящик и временный отказ почты. Потерять
        добивку из-за чужой минутной беды нельзя, а слать её немедленно
        второй раз — тем более.
        """
        await self._session.execute(
            update(MessageModel)
            .where(MessageModel.id == claimed.previous_id)
            .values(next_action_at=self._moment() + delay)
        )
        await self._session.flush()

    async def compose_letter(self, claimed: Claimed) -> compose.Letter:
        """Текст добивки: шаблон шага с подстановками этого донора.

        Модель не участвует — решение 21.09.2026. Запреты те же, что
        у первого письма: метрики Ahrefs в письмо не просачиваются
        (`guards`), незаполненная подстановка видна в тексте
        и останавливает отправку.
        """
        rendered = compose.render(
            template.followup(claimed.step),
            compose.values_for(host=claimed.host, domain_id=claimed.domain_id),
        )
        letter = compose.assemble(rendered, {})
        guards.assert_no_metrics(letter.body)
        return letter

    async def materialize(self, claimed: Claimed, letter: compose.Letter) -> MessageModel:
        """Завести строку добивки. В очередь согласования она не идёт.

        Очередь писем — это место, где человек согласует первое письмо;
        добивка согласована вместе с ним, и её место в переписке,
        а не в очереди.
        """
        existing = await self._session.scalar(
            select(MessageModel).where(
                MessageModel.thread_id == claimed.thread_id,
                MessageModel.step == claimed.step,
            )
        )
        if existing is not None:
            # Прошлая попытка сорвалась на вставшем ящике или отказе
            # почты. Второй строки быть не должно: у неё тот же ключ
            # идемпотентности, и вставка просто упала бы — а цепочка
            # встала бы навсегда.
            return existing

        message = MessageModel(
            campaign_id=claimed.campaign_id,
            thread_id=claimed.thread_id,
            domain_id=claimed.domain_id,
            contact_id=claimed.contact_id,
            step=claimed.step,
            status=MessageStatus.QUEUED,
            subject=letter.subject,
            body=letter.body,
            idempotency_key=idempotency_key(
                stage=await self._stage(claimed.campaign_id),
                host=claimed.host,
                step=claimed.step,
            ),
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def sent_this_hour(self, sender_id: int) -> int:
        """Сколько добивок ушло с этого ящика за последний час."""
        since = self._moment() - timedelta(hours=1)
        total = await self._session.scalar(
            select(func.count())
            .select_from(MessageModel)
            .where(
                MessageModel.sender_id == sender_id,
                MessageModel.step > FIRST_STEP,
                MessageModel.sent_at >= since,
            )
        )
        return int(total or 0)

    async def _host(self, domain_id: int) -> str:
        host = await self._session.scalar(
            select(DomainModel.host).where(DomainModel.id == domain_id)
        )
        if not host:
            raise FollowupError(f"У домена №{domain_id} нет имени — добивку некому адресовать")
        return str(host)

    async def _stage(self, campaign_id: int) -> Stage:
        campaign = await self._session.get(CampaignModel, campaign_id)
        if campaign is None:
            raise FollowupError(f"Рассылки №{campaign_id} нет — добивка осиротела")
        return campaign.stage

    async def _anchor(self, thread_id: int | None) -> str | None:
        """Идентификатор первого письма переписки у почты.

        Берётся именно первое, а не предыдущее: у почтового клиента
        донора вся цепочка должна лежать одной веткой, и якорь у неё
        один. Предыдущее письмо дало бы лесенку из вложенных ответов.
        """
        if thread_id is None:
            return None
        return await self._session.scalar(
            select(MessageModel.provider_message_id)
            .where(
                MessageModel.thread_id == thread_id,
                MessageModel.step == FIRST_STEP,
                MessageModel.provider_message_id.is_not(None),
            )
            .order_by(MessageModel.id)
            .limit(1)
        )


@dataclass
class PassReport:
    """Что сделал один проход по цепочкам."""

    sent: int = 0
    postponed: int = 0
    """Отложено: ящик стоит или почта отказала временно. Цепочка жива."""
    stopped: int = 0
    """Остановлено: донор в стоп-листе. Больше ему не пишем."""

    @property
    def as_report(self) -> str:
        return f"отправлено {self.sent}, отложено {self.postponed}, остановлено {self.stopped}"


#: На сколько откладывается добивка, если отправить её сейчас нельзя:
#: ящик на паузе, почта отказала временно, лейн выбран. Час — потому что
#: все три причины проходят сами, и спрашивать чаще значит долбить базу.
POSTPONE = timedelta(hours=1)


async def send_due(
    session: AsyncSession,
    *,
    transport: Transport,
    limit: int,
    now: datetime | None = None,
) -> PassReport:
    """Один проход: разослать подошедшие добивки.

    `limit` — потолок прохода. Проход короткий намеренно: подошедших
    может оказаться сотня, и отправить их подряд значит выдать всплеск,
    по которому почтовая платформа судит о рассылке хуже, чем по объёму.
    """
    chain = Chain(session, now=now)
    postman = Sending(session, transport, now=now)
    report = PassReport()

    for _ in range(limit):
        claimed = await chain.claim()
        if claimed is None:
            break
        # Срок погашен надёжно до вызова почты: упасть теперь можно
        # только в сторону «добивка не ушла», но не «ушла дважды».
        await session.commit()
        await _deliver(session, chain, postman, claimed, report)

    return report


async def _deliver(
    session: AsyncSession,
    chain: Chain,
    postman: Sending,
    claimed: Claimed,
    report: PassReport,
) -> None:
    """Одна добивка: проверить лейн, собрать текст, отдать почте."""
    if claimed.sender_id is not None and await chain.sent_this_hour(claimed.sender_id) >= (
        cfg.FOLLOWUP_PER_SENDER_PER_HOUR
    ):
        await chain.restore(claimed, delay=POSTPONE)
        await session.commit()
        report.postponed += 1
        return

    message = await chain.materialize(claimed, await chain.compose_letter(claimed))
    await session.commit()

    try:
        await postman.send(message.id, from_sender_id=claimed.sender_id, in_reply_to=claimed.anchor)
    except SuppressedError as exc:
        # Донор попросил не писать между первым письмом и сроком добивки.
        # Срок уже погашен захватом — цепочка кончилась сама.
        message.status = MessageStatus.STOPPED
        await session.commit()
        report.stopped += 1
        logger.info("добивки: %s — цепочка остановлена (%s)", claimed.host, exc)
        return
    except (NoSenderError, SendError) as exc:
        await chain.restore(claimed, delay=POSTPONE)
        await session.commit()
        report.postponed += 1
        logger.warning("добивки: %s — отложена на час (%s)", claimed.host, exc)
        return

    await session.commit()
    report.sent += 1
    logger.info("добивки: %s — ушёл шаг %s", claimed.host, claimed.step)
