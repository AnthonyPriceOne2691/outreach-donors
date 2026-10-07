"""Отправка одного письма из очереди.

Порядок шагов здесь — не стиль, а список оплаченных чужим опытом правил.

**Стоп-лист проверяется перед каждой отправкой, без исключений.** Не при
сборке очереди: между сборкой и нажатием кнопки проходят часы, и человек
за это время мог отписаться. Проверка при сборке остаётся — она экономит
вызовы модели, — но решает эта.

**Факт отправки пишется в базу до вызова почты, а не после.**
Иначе повтор после сбоя шлёт второе письмо тому же донору, а это жалоба
на спам. Поэтому письмо сначала переводится в «отправляется» и
фиксируется, и только потом уходит наружу.

**Перевод в «отправляется» — захват, а не запись** (ревью 28.09.2026).
Проверка «письмо в очереди» и перевод — один условный `UPDATE … RETURNING`:
чтением и записью два одновременных нажатия оба видели «в очереди» и оба
отдавали письмо почте. Тот же приём, что у захвата добивки (`followups.py`).

**Свой `Message-ID` пишется той же фиксацией** (`identity.py`). Обрыв
связи после того, как платформа приняла письмо, иначе оставил бы ушедшее
письмо без якоря: добивки не легли бы в его ветку, а ответ без метки
не нашёл бы его. Идентификатор и адрес ответа собираются ещё раньше —
до «отправляется»: отказ здесь означает настройку, а не почту, и письмо
должно остаться в очереди, а не повиснуть в «отправляется», не отправив
наружу ни байта.

**Отказ почты и обрыв связи — разные исходы.** Платформа сказала «нет» —
письмо точно не ушло, его можно вернуть в очередь. Связь оборвалась —
неизвестно, ушло или нет, и письмо остаётся в «отправляется»: человек
разберётся по журналу платформы. Вернуть его в очередь значило бы
отправить второе.

**Ящик выбирается в момент отправки**, а не при сборке: очередь общая,
и вставший ящик не должен блокировать свою часть. Это про первые письма:
добивка и ответ уходят только с ящика своей переписки и только своим путём
(`mailbox.py`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from backend.features.letters import compose, identity, mailbox, reply_to, settle, unsubscribe
from backend.features.letters.transport import Mail, Outgoing, Transport, TransportError, of_stage

logger = logging.getLogger(__name__)


class SendError(RuntimeError):
    """Письмо не отправлено. Сообщение называет причину и что делать."""


class NotQueuedError(SendError):
    """Письмо не в очереди: уже ушло, остановлено или отправляется."""


class SuppressedError(SendError):
    """Донор или адрес в стоп-листе."""


class RejectedDonorError(SuppressedError):
    """Донора отклонили после сборки письма. Для цепочки — как стоп-лист:
    писать ему больше не будем."""


class RemovedAdvertiserError(SuppressedError):
    """Рекламодателя сняли после сборки письма: стоп-лист поставщиков или
    общий отсеял его задним числом. Для цепочки — как стоп-лист."""


class UndecidedDonorError(SendError):
    """Донора вернули в «предложен» после сборки письма: пока человек
    решает, писать нельзя, но и обрывать разговор из-за этого нельзя."""


class NotReadyError(SendError):
    """В письме остались незаполненные обязательные места."""


class NoSenderError(SendError):
    """Сегодня писать некому: все ящики выключены или выбрали дневной лимит.
    У письма переписки — её ящик не пишет, и письмо ждёт его."""


class OwnPathError(SendError):
    """Добивка или ответ пришли не своим путём: без ящика переписки не уходят."""


@dataclass(frozen=True, slots=True)
class SendOutcome:
    """Чем кончилась отправка."""

    message_id: int
    sender_email: str
    provider_message_id: str
    #: Ушло ли письмо на самом деле. У нулевого транспорта — нет.
    real: bool


@dataclass(frozen=True, slots=True)
class OwnHeaders:
    """Что письмо получает от нас, а не от шаблона. Оба — на домене отправителя."""

    #: Наш `Message-ID`: якорь цепочки и запасной путь привязки ответа.
    message_id: str
    #: Адрес «куда отвечать» с меткой. Пусто — только у нулевого транспорта.
    reply_to: str | None


def own_headers(message_id: int, *, sender_email: str, real: bool) -> OwnHeaders:
    """Свой `Message-ID` и адрес ответа для письма `message_id` с ящика `sender_email`.

    Адрес ответа настоящей отправке обязателен: без метки ответ с чужого
    адреса не привязывается ни к чему и выглядит как «донор не ответил».
    Нулевому транспорту — нет: он никуда не пишет, и требовать ради него
    настроенный секрет значило бы не дать посмотреть экран.

    Путь один у письма донору, добивки и пробного письма себе (`probe.py`):
    иначе пробное письмо проверяло бы не то, что уйдёт донору.
    """
    try:
        own_id = identity.new_message_id(message_id, sender_email=sender_email)
    except identity.SenderAddressError as exc:
        raise SendError(str(exc)) from exc
    try:
        address: str | None = reply_to.address_for(message_id, sender_email=sender_email)
    except reply_to.ReplyAddressError as exc:
        if real:
            raise SendError(str(exc)) from exc
        logger.warning("письма: письмо №%s уходит без адреса ответа (%s)", message_id, exc)
        address = None
    return OwnHeaders(message_id=own_id, reply_to=address)


def check_ready(body: str, *, what: str) -> None:
    """Громкая метка в тексте — отказ, а не предупреждение.

    Проверяется текст, а не нынешние настройки: письмо уходит таким,
    каким его утвердил человек. Отказ — словами для экрана; `what` —
    о каком письме речь, остальное объяснение одно на все письма.
    """
    unset = compose.unset_in(body)
    if unset:
        raise NotReadyError(f"{what} пока не отправить: {compose.unset_reason(unset)}")


@dataclass(frozen=True, slots=True)
class _Target:
    """Письмо вместе с тем, что нужно для отправки."""

    message: MessageModel
    host: str
    email: str
    #: Кому и от чьего имени — ответ этапа (`stages.recipient`).
    to: stages.Recipient
    #: Ящик и `Message-ID` прежней попытки — до захвата. Пусто у нового письма;
    #: у возвращённого человеком из «отправляется» — след попытки, которая
    #: могла уйти (`unknown_outcome.py`).
    before: tuple[int | None, str | None] = (None, None)


class Sending:
    """Отправка письма — транспортом, общим на все письма, или транспортом этапа
    письма (`ByStage`): у направления бывает своя учётка почтовой платформы."""

    def __init__(
        self, session: AsyncSession, transport: Mail, *, now: datetime | None = None
    ) -> None:
        self._session = session
        self._transports = transport
        self._now = now

    def _moment(self) -> datetime:
        return self._now or datetime.now(UTC)

    async def send(
        self,
        message_id: int,
        *,
        author_id: int | None = None,
        from_sender_id: int | None = None,
        in_reply_to: str | None = None,
    ) -> SendOutcome:
        """Отправить письмо из очереди.

        `from_sender_id` — ящик переписки, названный её путём: так уходят
        добивка и ответ. Переписку ведёт тот ящик, что её начал, и менять его
        на середине разговора значит попасть в спам и запутать собеседника.
        Без него письмо переписки не уходит вовсе (`mailbox.py`).
        """
        target = await self._target(message_id)
        try:  # не собрался транспорт этапа — отказ отправки, а не падение прохода
            transport = of_stage(self._transports, target.to.stage.value)
        except TransportError as exc:
            raise SendError(f"Почта этапа «{target.to.stage.value}» не собрана: {exc}") from exc
        await self._check_suppression(target)
        await self._check_review(target)
        check_ready(target.message.body or "", what=f"Письмо №{target.message.id}")

        sender = await self._sender(target, from_sender_id)
        own = own_headers(target.message.id, sender_email=sender.email, real=transport.real)

        # Факт отправки — в базе до вызова почты, и захватом: второй
        # одновременный запрос получает отказ здесь, а не письмо донору.
        await self._claim(target, sender, own)

        provider_id = await self._hand_over(target, sender, own, transport, in_reply_to=in_reply_to)
        await self._settle(target, sender, transport, provider_id=provider_id, author_id=author_id)
        return SendOutcome(
            message_id=target.message.id,
            sender_email=sender.email,
            provider_message_id=provider_id,
            real=transport.real,
        )

    # --- шаги ---

    async def _target(self, message_id: int) -> _Target:
        rows = await self._session.execute(
            select(MessageModel, DomainModel.host, ContactModel.email, CampaignModel.stage)
            .join(DomainModel, DomainModel.id == MessageModel.domain_id)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .outerjoin(ContactModel, ContactModel.id == MessageModel.contact_id)
            .where(MessageModel.id == message_id)
        )
        found = rows.first()
        if found is None:
            raise SendError(f"Письма №{message_id} нет")

        message, host, email, stage = found
        if message.status is not MessageStatus.QUEUED:
            raise NotQueuedError(
                f"Письмо №{message_id} в состоянии «{message.status.value}», а не в очереди. "
                "Отправить можно только то, что ещё ждёт отправки"
            )
        to = await stages.recipient(self._session, message, stage, email, f"Письмо №{message_id}")
        if not to.email:
            raise SendError(
                f"У письма №{message_id} нет адреса получателя: контакт удалён после сборки "
                "очереди. Письмо стоит убрать и собрать очередь заново"
            )
        return _Target(
            message=message,
            host=host,
            email=to.email,
            to=to,
            before=(message.sender_id, message.internet_message_id),
        )

    async def _claim(self, target: _Target, sender: SenderModel, own: OwnHeaders) -> None:
        """Перевести письмо в «отправляется» — только если оно всё ещё в очереди.

        Статус из `_target` мог устареть: между чтением и записью успевает
        второй запрос — двойной щелчок, экран и консоль, проход добивок.
        Условие проверяет база, в том же `UPDATE`: второй ждёт блокировку
        строки, после фиксации первого видит «отправляется» и строку не берёт.
        Фиксация своя: между ней и почтой ничего не должно остаться
        незаписанным — и свой Message-ID в первую очередь.
        """
        claimed = await self._session.execute(
            update(MessageModel)
            .where(
                MessageModel.id == target.message.id,
                MessageModel.status == MessageStatus.QUEUED,
            )
            .values(
                status=MessageStatus.SENDING,
                sender_id=sender.id,
                internet_message_id=own.message_id,
            )
            .returning(MessageModel.id)
            # Прочитанное письмо получает новые значения, только если строка
            # захвачена. Сессия не сбрасывает прочитанное при фиксации, и без
            # этого возврат в очередь при отказе почты не записался бы — для
            # базы «не изменилось», и письмо застряло бы в «отправляется».
            .execution_options(synchronize_session="fetch")
        )
        if claimed.first() is None:
            raise NotQueuedError(
                f"Письмо №{target.message.id} уже не в очереди: его секундой раньше "
                "взяла другая отправка. Второе письмо тому же донору не уходит — "
                "чем кончилась первая, видно в очереди писем"
            )
        await self._session.commit()

    async def _check_review(self, target: _Target) -> None:
        """Решение человека проверяется при отправке, а не только при сборке.

        Между сборкой и отправкой человек может передумать: принять, вернуть
        в «предложен», отклонить (Anthony, 24.09.2026). Без этой проверки
        письмо, собранное для принятого, ушло бы отклонённому. Рекламодатели
        Этапа 2 рассмотрения донора не проходят, у них своя проверка; можно ли
        писать лиду продаж — проверка модуля продаж (`stages.check_sales`).
        """
        if target.to.stage is Stage.ADVERTISERS:
            await self._check_advertiser(target)
            return
        if target.to.stage is Stage.SALES:  # лиду можно писать, письмо цело (`stages.SalesMail`)
            await stages.check_sales(self._session, target.message)
            return
        stages.donor_path(target.to.stage)
        review = await self._session.scalar(
            select(DonorModel.review).where(DonorModel.domain_id == target.message.domain_id)
        )
        if review == "accepted":
            return
        if review == "rejected":
            raise RejectedDonorError(
                f"Донора {target.host} отклонили после сборки письма №{target.message.id} — "
                "писать ему нельзя. Письмо стоит убрать из очереди"
            )
        raise UndecidedDonorError(
            f"Донора {target.host} вернули на рассмотрение после сборки письма "
            f"№{target.message.id}. Сначала решить на экране прогона"
        )

    async def _check_advertiser(self, target: _Target) -> None:
        """Рекламодатель ещё рекламодатель.

        Перевод кандидатов снимает тех, кого стоп-лист поставщиков или общий
        отсеял задним числом: донор стал партнёром — и его рекламодатели
        теперь чужие клиенты. Письмо, собранное до этого, уйти не должно,
        а добивка — тем более: для цепочки это как стоп-лист.
        """
        still = await self._session.scalar(
            select(AdvertiserModel.id).where(AdvertiserModel.domain_id == target.message.domain_id)
        )
        if still is None:
            raise RemovedAdvertiserError(
                f"Рекламодателя {target.host} сняли после сборки письма №{target.message.id}: "
                "его отсеял стоп-лист поставщиков или общий. Письмо стоит убрать из очереди"
            )

    async def _check_suppression(self, target: _Target) -> None:
        """Стоп-лист на двух уровнях: адрес блокирует себя, донор — все свои
        адреса. Требование «больше не пишите» сильнее отписки от адреса."""
        rows = await self._session.execute(
            select(SuppressionModel)
            .where(
                or_(
                    SuppressionModel.domain_id == target.message.domain_id,
                    SuppressionModel.email == target.email,
                )
            )
            .where(
                or_(
                    SuppressionModel.stage.is_(None),
                    SuppressionModel.stage == target.to.stage,
                )
            )
            .where(SuppressionModel.in_force(datetime.now(UTC)))
        )
        found = rows.scalars().first()
        if found is not None:
            raise SuppressedError(
                f"Донору {target.host} писать нельзя: стоп-лист, причина «{found.reason.value}». "
                "Письмо стоит убрать из очереди"
            )

    async def _sender(self, target: _Target, from_sender_id: int | None) -> SenderModel:
        """Ящик письма (`mailbox.py`): первое — любой свободный ящик этапа,
        добивка и ответ — только ящик своей переписки."""
        choice = await mailbox.choose(
            self._session,
            target.message,
            stage=target.to.stage,
            thread_sender_id=from_sender_id,
            now=self._moment(),
        )
        if choice.sender is not None:
            return choice.sender
        if choice.wrong_path:
            raise OwnPathError(choice.refusal)
        raise NoSenderError(choice.refusal)

    async def _hand_over(
        self,
        target: _Target,
        sender: SenderModel,
        own: OwnHeaders,
        transport: Transport,
        *,
        in_reply_to: str | None = None,
    ) -> str:
        """Отдать письмо почте. Отказ возвращает письмо в очередь."""
        outgoing = Outgoing(
            message_id=target.message.id,
            to=target.email,
            from_email=sender.email,
            from_name=target.to.from_name(compose.values_for(host=target.host)["sender_name"]),
            reply_to=own.reply_to,
            subject=target.message.subject or "",
            body=target.message.body or "",
            internet_message_id=own.message_id,
            in_reply_to=in_reply_to,
            # Та же ссылка, что стоит в тексте письма: разойдись они —
            # кнопка почты отписывала бы не того, кому написали.
            unsubscribe_url=unsubscribe.url_for(target.message.domain_id),
        )
        try:
            return await transport.send(outgoing)
        except TransportError as exc:
            # Почта сказала «нет» — эта попытка точно не ушла, и письмо можно
            # вернуть в очередь без риска отправить второе. Ящик и идентификатор —
            # прежние, а не этой попытки: повтор может пойти с другого домена,
            # а прежняя попытка, если была, могла уйти — по её следу событие
            # платформы запишет письмо ушедшим (`unknown_outcome.py`).
            target.message.status = MessageStatus.QUEUED
            target.message.sender_id, target.message.internet_message_id = target.before
            await self._session.commit()
            raise SendError(str(exc)) from exc
        except Exception:
            # Связь оборвалась: ушло или нет — неизвестно. Письмо остаётся
            # в «отправляется», потому что возврат в очередь означал бы
            # второе письмо тому же донору.
            logger.exception(
                "письма: письмо №%s осталось в состоянии «отправляется» — "
                "исход вызова почты неизвестен, разбирать по журналу платформы",
                target.message.id,
            )
            await self._session.commit()
            raise

    async def _settle(
        self,
        target: _Target,
        sender: SenderModel,
        transport: Transport,
        *,
        provider_id: str,
        author_id: int | None,
    ) -> None:
        """Записать, что письмо ушло, — тем же путём, что и исход, выясненный
        позже событием платформы или человеком (`settle.py`)."""
        witness = settle.Witness(
            host=target.host,
            email=target.email,
            sender_email=sender.email,
            transport=transport.name,
            real=transport.real,
        )
        await settle.record_sent(
            self._session,
            target.message,
            moment=self._moment(),
            provider_id=provider_id,
            author_id=author_id,
            witness=witness,
        )
        await self._session.commit()
