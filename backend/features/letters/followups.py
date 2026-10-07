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
на паузе не заменяется другим: срок переносится, цепочка ждёт. Уходит
добивка только этим проходом: в общей очереди писем её нет, и пачка
её не берёт (`mailbox.py`).

**Свой лейн, а не дневной кап.** Дневной кап ящика считает первые
письма; добивки идут своим часовым потолком (решение 21.09.2026).
Иначе backlog первых писем голодит цепочки, а цепочки съедают квоту
новых доноров. Часовой, а не дневной, — чтобы сотня подошедших добивок
не ушла пачкой за минуту: почтовая платформа смотрит на скорость.

**Этап без цепочки не захватывается.** Шаблон добивки выбирается по этапу
уже после захвата, и этап без шаблонов терял бы добивку: срок погашен,
письма нет. Поэтому захват берёт этапы с цепочкой файлами (`template.CHAINED`)
и продажи, когда они подключены (текст их добивки — ответ модуля продаж через
мост `core/stages.py`); подошедшие добивки остальных проход называет вслух и
не трогает — срок цел и дождётся подключения этапа.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core import stages, window
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.core.stages import SALES_NOT_CONNECTED, SalesNotConnectedError
from backend.features.letters import compose, guards, template
from backend.features.letters.building import attempt_of, followup_key, idempotency_key
from backend.features.letters.chain import CHAINABLE, FIRST_STEP, MAX_STEPS
from backend.features.letters.sending import (
    NoSenderError,
    NotQueuedError,
    SendError,
    Sending,
    SuppressedError,
)
from backend.features.letters.transport import ByStage, MaybeSentError, Transport

logger = logging.getLogger(__name__)


class FollowupError(RuntimeError):
    """Добивку отправить нельзя, и причина названа."""


def _due(moment: datetime) -> tuple[ColumnElement[bool], ...]:
    """Подошедшая добивка: письмо ушло, срок наступил, шаг не последний."""
    return (
        MessageModel.status.in_(CHAINABLE),
        MessageModel.next_action_at.is_not(None),
        MessageModel.next_action_at <= moment,
        MessageModel.step < MAX_STEPS - 1,
    )


def _chained(chained: tuple[Stage, ...]) -> ColumnElement[bool]:
    """Письмо рассылки этапа, у которого есть цепочка (`chained`).

    Подзапросом, а не соединением: захват блокирует строку письма, и
    соединение заперло бы заодно строку рассылки — под всеми её письмами.
    """
    campaigns = select(CampaignModel.id).where(CampaignModel.stage.in_(chained))
    return MessageModel.campaign_id.in_(campaigns)


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
    """Наш `Message-ID` первого письма — по нему добивка ложится в его ветку.
    Пусто — у первого письма своего идентификатора нет (ушло до него)."""
    attempt: int = 1
    """Номер попытки цепочки — из ключа письма, после которого пришёл срок
    (`building.attempt_of`). У того он свой от первого письма потока: каждая
    добивка получает номер предыдущего письма, а первая — первого."""
    previous_key: str = ""  # ключ письма, после которого пришёл срок: у продаж в нём контакт


class Chain:
    """Правила цепочки на настоящей базе."""

    def __init__(
        self, session: AsyncSession, *, now: datetime | None = None, extra: tuple[Stage, ...] = ()
    ) -> None:
        self._session = session
        self._now = now
        #: Чьи добивки проход берёт: этапы с цепочкой файлами и `extra` (продажи, когда подключены).
        self._take = _chained((*template.CHAINED, *extra))

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
            .where(*_due(moment), self._take)
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
                    MessageModel.internet_message_id,
                    MessageModel.idempotency_key,
                )
            )
        ).first()
        if row is None:
            return None

        host = await self._host(row[3])
        anchor = await self._anchor(row[1]) or row[7]
        if anchor is None:
            # Не отказ: напоминание без ветки лучше потерянного. Но и не
            # молча — у донора оно ляжет отдельным письмом.
            logger.warning(
                "добивки: %s — у первого письма нет своего Message-ID, "
                "добивка уйдёт без заголовков цепочки",
                host,
            )
        return Claimed(
            previous_id=row[0],
            thread_id=row[1],
            campaign_id=row[2],
            domain_id=row[3],
            contact_id=row[4],
            sender_id=row[5],
            step=row[6] + 1,
            host=host,
            anchor=anchor,
            attempt=attempt_of(row[8]),
            previous_key=row[8],
        )

    async def unchained(self) -> int:
        """Сколько подошедших добивок ждут этапа без цепочки. Срок у них цел."""
        total = await self._session.scalar(
            select(func.count()).select_from(MessageModel).where(*_due(self._moment()), ~self._take)
        )
        return int(total or 0)

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
        """Текст добивки: шаблон шага этапа рассылки с подстановками адресата.

        Модель не участвует — решение 21.09.2026. Запреты те же, что
        у первого письма: метрики Ahrefs в письмо не просачиваются
        (`guards`), незаполненная подстановка видна в тексте
        и останавливает отправку.

        **Тема — у отправленного первого письма, а не у шаблона добивки.**
        Переписка одна, и у адресата письма должны лежать одной веткой.
        Тема шаблона совпадала с первым письмом, только пока оно уходило
        темой по умолчанию: тема, поправленная на экране, или тема оффера
        с площадкой, которую пересчёт обхода успел сменить, давали добивку
        отдельной веткой — о другом, чем письмо, на которое она ссылается.
        """
        stage = await self._stage(claimed.campaign_id)
        if stage is Stage.SALES:  # шаг цепочки из базы — ответ модуля продаж (`stages.SalesMail`)
            body = (
                await stages.sales_followup(self._session, claimed.thread_id, claimed.step)
            ).body
            letter = compose.Letter(subject="", body=body, plain_body=body)
        else:
            rendered = compose.render(
                template.followup(claimed.step, stage),
                compose.values_for(host=claimed.host, domain_id=claimed.domain_id),
            )
            letter = compose.assemble(rendered, {})
        guards.assert_no_metrics(letter.body)
        first = await self._first_subject(claimed.thread_id)
        return replace(letter, subject=first) if first else letter

    async def materialize(self, claimed: Claimed, letter: compose.Letter) -> MessageModel:
        """Завести строку добивки. В очередь согласования она не идёт.

        Очередь писем — это место, где человек согласует первое письмо;
        добивка согласована вместе с ним, и её место в переписке,
        а не в очереди.

        Ключ — с номером попытки цепочки: добивка второй попытки (письма
        на следующий адрес после отказа) иначе получила бы ключ добивки
        первой и не вставилась бы в базу. У продаж в ключе ещё и контакт —
        два лида одной компании, — и он берётся из ключа предыдущего письма.
        """
        stage = await self._stage(claimed.campaign_id)
        existing = await self._session.scalar(
            select(MessageModel).where(
                MessageModel.thread_id == claimed.thread_id,
                MessageModel.step == claimed.step,
            )
        )
        if existing is not None:
            # Прошлая попытка сорвалась на вставшем ящике или отказе почты. Второй
            # строки быть не должно: у неё тот же ключ идемпотентности, и вставка
            # упала бы — а цепочка встала бы навсегда. Текст продаж — нынешний:
            # подпись и адрес «Отправителя» могли смениться с прошлой попытки.
            if stage is Stage.SALES and existing.status is MessageStatus.QUEUED:
                existing.subject, existing.body = letter.subject, letter.body
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
            idempotency_key=(
                followup_key(claimed.previous_key, claimed.step)
                if stage is Stage.SALES
                else idempotency_key(
                    stage=stage, host=claimed.host, step=claimed.step, attempt=claimed.attempt
                )
            ),
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def sent_this_hour(self, sender_id: int) -> int:
        """Сколько добивок ушло с этого ящика за последний час.

        Только шаги цепочки: ответ в переписке (`chain.ANSWER_STEP`) — тоже
        письмо с этого ящика, но не добивка, и считать его здесь значило бы
        отнимать часовой запас добивок у ящика, где человек ведёт разговор
        (ревью «Продаж» #162).
        """
        since = self._moment() - timedelta(hours=1)
        total = await self._session.scalar(
            select(func.count())
            .select_from(MessageModel)
            .where(
                MessageModel.sender_id == sender_id,
                MessageModel.step > FIRST_STEP,
                MessageModel.step < MAX_STEPS,
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

    async def _first_subject(self, thread_id: int | None) -> str | None:
        """Тема первого письма переписки — та, с которой оно ушло."""
        if thread_id is None:
            return None
        subject = await self._session.scalar(
            select(MessageModel.subject)
            .where(MessageModel.thread_id == thread_id, MessageModel.step == FIRST_STEP)
            .order_by(MessageModel.id)
            .limit(1)
        )
        return subject.strip() if subject and subject.strip() else None

    async def _anchor(self, thread_id: int | None) -> str | None:
        """Наш `Message-ID` первого письма переписки.

        Берётся именно первое, а не предыдущее: у почтового клиента
        донора вся цепочка должна лежать одной веткой, и якорь у неё
        один. Предыдущее письмо дало бы лесенку из вложенных ответов.

        Именно наш идентификатор, а не номер письма у платформы: номер
        получатель не видит, и `In-Reply-To` с ним не кладёт добивку никуда.
        """
        if thread_id is None:
            return None
        return await self._session.scalar(
            select(MessageModel.internet_message_id)
            .where(
                MessageModel.thread_id == thread_id,
                MessageModel.step == FIRST_STEP,
                MessageModel.internet_message_id.is_not(None),
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
    unknown: int = 0
    """Почта не ответила после отправки: ушла ли добивка — неизвестно. Не
    повторяется: срок погашен, письмо «отправляется» до события платформы."""
    waiting: int = 0
    """Подошли, но у этапа нет цепочки: срок цел, добивка ждёт подключения этапа."""

    @property
    def as_report(self) -> str:
        said = (
            f"отправлено {self.sent}, отложено {self.postponed}, остановлено {self.stopped}, "
            f"исход неизвестен {self.unknown}"
        )
        return f"{said}, ждут этапа {self.waiting}" if self.waiting else said


#: На сколько откладывается добивка, если отправить её сейчас нельзя:
#: ящик на паузе, почта отказала временно, лейн выбран. Час — потому что
#: все три причины проходят сами, и спрашивать чаще значит долбить базу.
POSTPONE = timedelta(hours=1)


async def send_due(
    session: AsyncSession,
    *,
    transport: Transport | ByStage,
    limit: int,
    now: datetime | None = None,
) -> PassReport:
    """Один проход: разослать подошедшие добивки.

    `limit` — потолок прохода. Проход короткий намеренно: подошедших
    может оказаться сотня, и отправить их подряд значит выдать всплеск,
    по которому почтовая платформа судит о рассылке хуже, чем по объёму.
    """
    # Продажи — только подключённые: иначе срок их добивки цел и назван вслух.
    sales = (Stage.SALES,) if await stages.sales_connected(session) else ()
    chain = Chain(session, now=now, extra=sales)
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

    report.waiting = await chain.unchained()
    if report.waiting and not stages.sales_registered():
        # Вслух, пока модуль продаж к мосту не подключён: молчащая цепочка выглядит как «никто не
        # ответил». Подключён — сроки ждут его «подключены» (экран продаж), журнал не шумит каждый час.
        logger.warning(
            "добивки: %s подошли, срок не погашен — %s", report.waiting, SALES_NOT_CONNECTED
        )
    return report


async def _deliver(
    session: AsyncSession,
    chain: Chain,
    postman: Sending,
    claimed: Claimed,
    report: PassReport,
) -> None:
    """Одна добивка: проверить ящик и лейн, собрать текст, отдать почте."""
    if claimed.sender_id is None:
        # Письма теряют номер ящика, когда ящик удаляют. С другого ящика
        # добивка не уходит (`mailbox.py`): она ждёт, и проход говорит почему.
        await chain.restore(claimed, delay=POSTPONE)
        await session.commit()
        report.postponed += 1
        logger.warning(
            "добивки: %s — ящик переписки неизвестен (удалён): добивка ждёт, "
            "с другого ящика она не уйдёт",
            claimed.host,
        )
        return
    if await chain.sent_this_hour(claimed.sender_id) >= cfg.FOLLOWUP_PER_SENDER_PER_HOUR:
        await chain.restore(claimed, delay=POSTPONE)
        await session.commit()
        report.postponed += 1
        return

    try:
        # Текст продаж собирается внутри: не подключены или цепочка неполна — срок вернётся.
        message = await chain.materialize(claimed, await chain.compose_letter(claimed))
        await session.commit()
        await postman.send(message.id, from_sender_id=claimed.sender_id, in_reply_to=claimed.anchor)
    except SuppressedError as exc:
        # Донор попросил не писать между первым письмом и сроком добивки.
        # Срок уже погашен захватом — цепочка кончилась сама.
        message.status = MessageStatus.STOPPED
        await session.commit()
        report.stopped += 1
        logger.info("добивки: %s — цепочка остановлена (%s)", claimed.host, exc)
        return
    except MaybeSentError as exc:
        # Могла уйти — повтор через час был бы вторым письмом тому же человеку.
        await session.commit()
        report.unknown += 1
        logger.warning("добивки: %s — исход неизвестен, повтора не будет (%s)", claimed.host, exc)
        return
    except NotQueuedError as exc:
        # Шаг уже решён другим путём: событие платформы записало ушедшей
        # добивку, которую человек вернул в цепочку (`unknown_outcome.py`).
        # Срок не возвращается — иначе проход раз в час приходил бы за ушедшей.
        await session.commit()
        logger.info("добивки: %s — шаг %s уже не в очереди (%s)", claimed.host, claimed.step, exc)
        return
    except (NoSenderError, SendError, SalesNotConnectedError) as exc:
        # Отказ почты этапу — как вставший ящик: срок возвращается, а не
        # теряется, даже если цепочку этапа подключат раньше его писем.
        await chain.restore(claimed, delay=window.postpone(exc, POSTPONE))  # окно — до открытия
        await session.commit()
        report.postponed += 1
        logger.warning("добивки: %s — отложена (%s)", claimed.host, exc)
        return

    await session.commit()
    report.sent += 1
    logger.info("добивки: %s — ушёл шаг %s", claimed.host, claimed.step)
