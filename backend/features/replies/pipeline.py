"""Путь входящего письма: от вебхука до строк в таблице.

Порядок шагов и есть содержание файла. Каждый следующий зависит
от предыдущего, и ни один не молчит о своём исходе.

    повтор? → привязка → чем был ответ → запись → последствия
    ... и отдельно, задачей очереди: разбор цены

**Повтор проверяется первым.** Провайдер доставляет события «хотя бы
один раз»; без этой проверки повтор дал бы второй ответ, второй разбор
и второй платный вызов модели.

**Приём и разбор разведены, и это не удобство.** Вызов модели идёт
секундами, а платформа повторяет вебхук по таймауту — платный разбор
внутри запроса означал бы повторные списания там, где сеть подтормозила.
Вебхук делает всё дешёвое и отвечает; цену разбирает очередь.

**Модель зовётся только для ответов людей.** Разбирать цену в отказе
доставки или в автоответчике — платить за заведомо пустой результат.

**Непривязанное сохраняется.** Ответ, который не удалось соотнести, —
это не мусор, а потерянный донор. Молча отброшенный, он выглядит как
«донор не ответил», и причину будут искать в лестнице контактов.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import usage
from backend.features.core.domain import ReplyKind
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies import binding, classify, outcome
from backend.features.replies.extract import ExtractClient, Extracted
from backend.features.replies.inbound import Incoming
from backend.features.replies.repository import Addressee, ReplyRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Accepted:
    """Чем кончился приём одного письма."""

    reply_id: int | None
    kind: ReplyKind | None
    way: binding.BindingWay
    #: Название сработавшего правила. Пусто — не сработало ни одно.
    rule: str | None = None
    duplicate: bool = False
    bound: bool = False
    needs_review: bool = False
    review_reason: str | None = None
    #: Разбор цены ещё впереди: он идёт задачей очереди.
    parse_pending: bool = False

    @property
    def as_report(self) -> dict[str, object]:
        """Короткий итог для лога и для ответа вебхуку."""
        return {
            "ответ": self.reply_id,
            "вид": self.kind.value if self.kind else None,
            "привязка": self.way.value,
            "правило": self.rule,
            "повтор": self.duplicate,
            "ждёт человека": self.needs_review,
        }


@dataclass(frozen=True, slots=True)
class Parsed:
    """Чем кончился разбор одного ответа."""

    reply_id: int
    confidence: float
    stored_price: bool
    needs_review: bool
    review_reason: str | None
    tokens_spent: int


class Inbox:
    """Приём входящих. Только дешёвое: привязка, вид ответа, запись."""

    def __init__(self, session: AsyncSession, *, now: datetime | None = None) -> None:
        self._session = session
        self._repo = ReplyRepository(session)
        self._now = now

    def _moment(self) -> datetime:
        return self._now or datetime.now(UTC)

    async def accept(self, incoming: Incoming) -> Accepted:
        """Принять одно входящее письмо. Модель здесь не зовётся."""
        if await self._repo.already_taken(incoming.message_id):
            logger.info("приём: письмо %s уже принято — повтор вебхука", incoming.message_id)
            return Accepted(reply_id=None, kind=None, way=binding.BindingWay.NONE, duplicate=True)

        bound, addressee = await self._bind(incoming)
        verdict = classify.classify(incoming)

        reply = self._repo.add(
            incoming,
            kind=verdict.kind,
            thread_id=addressee.message.thread_id if addressee else None,
            message_id=addressee.message.id if addressee else None,
            found=None,
        )
        await self._session.flush()

        if addressee is None:
            logger.warning(
                "приём: ответ №%s не привязан ни к одному письму (тема «%s»)",
                reply.id,
                incoming.subject[:80],
            )
            return Accepted(
                reply_id=reply.id,
                kind=verdict.kind,
                way=bound.way,
                rule=verdict.rule,
                needs_review=True,
                review_reason="ответ не привязан к письму",
            )

        # Решения, не зависящие от цены: остановка цепочки, стоп-лист,
        # отметка мёртвого адреса, запоминание отвечающего.
        consequences = outcome.decide(verdict.kind, None)
        await self._apply(consequences, incoming=incoming, addressee=addressee)

        return Accepted(
            reply_id=reply.id,
            kind=verdict.kind,
            way=bound.way,
            rule=verdict.rule,
            bound=True,
            needs_review=consequences.needs_review,
            review_reason=consequences.review_reason,
            parse_pending=verdict.kind is ReplyKind.HUMAN,
        )

    # --- шаги ---

    async def _bind(self, incoming: Incoming) -> tuple[binding.Binding, Addressee | None]:
        """Привязка вместе с проверкой, что письмо ещё существует.

        Метка может указывать на письмо, которого нет: так бывает после
        чистки базы. Ответ тогда не теряем, но привязку не выдумываем.
        """
        known = await self._repo.ours_by_provider_id(binding.thread_ids(incoming))
        bound = binding.bind(incoming, by_provider_id=known)
        if bound.message_id is None:
            return bound, None

        addressee = await self._repo.addressee(bound.message_id)
        if addressee is None:
            logger.warning("приём: письмо №%s из метки не найдено", bound.message_id)
            return binding.Binding(message_id=None, way=binding.BindingWay.NONE), None
        return bound, addressee

    async def _apply(
        self,
        consequences: outcome.Consequences,
        *,
        incoming: Incoming,
        addressee: Addressee,
    ) -> None:
        """Применить последствия. Решение принято в `outcome`, здесь только
        запросы к базе.

        Списком, а не цепочкой `if`: список видно целиком, и добавить
        в него последствие — это одна строка, а не ещё одна ветка.
        """
        moment = self._moment()
        steps: tuple[tuple[bool, Callable[[], Awaitable[None]]], ...] = (
            (consequences.stop_chain, lambda: self._stop_chain(addressee)),
            (consequences.suppress_email, lambda: self._suppress(incoming, addressee)),
            (consequences.mark_contact_dead, lambda: self._mark_dead(addressee)),
            (
                consequences.remember_answering_address,
                lambda: self._remember(incoming, addressee, moment),
            ),
        )
        for needed, action in steps:
            if needed:
                await action()

    async def _stop_chain(self, addressee: Addressee) -> None:
        stopped = await self._repo.stop_chain(addressee.message.thread_id)
        if stopped:
            logger.info("приём: отменено добивок — %s", stopped)

    async def _suppress(self, incoming: Incoming, addressee: Addressee) -> None:
        await self._repo.suppress(incoming.from_email, stage=addressee.stage)

    async def _mark_dead(self, addressee: Addressee) -> None:
        await self._repo.mark_contact_dead(addressee.message.contact_id)
        await self._repo.mark_bounced(addressee.message)

    async def _remember(self, incoming: Incoming, addressee: Addressee, moment: datetime) -> None:
        await self._repo.remember_answering_address(
            domain_id=addressee.domain_id, email=incoming.from_email, now=moment
        )


class Parser:
    """Разбор цены. Отдельно от приёма: это платный вызов модели, и место
    ему в очереди задач, а не внутри вебхука."""

    def __init__(
        self,
        session: AsyncSession,
        extractor: ExtractClient,
        *,
        now: datetime | None = None,
    ) -> None:
        self._session = session
        self._extractor = extractor
        self._repo = ReplyRepository(session)
        self._now = now

    async def parse(self, reply_id: int) -> Parsed:
        """Разобрать один ответ и применить последствия цены."""
        reply = await self._repo.reply(reply_id)
        if reply.kind is not ReplyKind.HUMAN:
            # Разбирать нечего, и это не ошибка: задача могла быть
            # поставлена до того, как вид ответа уточнили.
            return Parsed(reply_id, 0.0, False, False, None, 0)

        found = await self._extractor.extract(_as_incoming(reply))
        if found.tokens_spent:
            usage.record(self._session, operation="reply_parse", units=found.tokens_spent)
        _write_back(reply, found)

        consequences = outcome.decide(reply.kind, found)
        if consequences.store_price and reply.message_id is not None:
            await self._store_price(reply.message_id, found)

        logger.info(
            "разбор: ответ №%s, уверенность %.2f, цена в базу — %s",
            reply_id,
            found.confidence,
            "да" if consequences.store_price else "нет",
        )
        return Parsed(
            reply_id=reply_id,
            confidence=found.confidence,
            stored_price=consequences.store_price,
            needs_review=consequences.needs_review,
            review_reason=consequences.review_reason,
            tokens_spent=found.tokens_spent,
        )

    async def _store_price(self, message_id: int, found: Extracted) -> None:
        addressee = await self._repo.addressee(message_id)
        price = found.price_white if found.price_white is not None else found.price_grey
        if addressee is None or price is None:
            return
        await self._repo.store_price(
            domain_id=addressee.domain_id,
            price=price,
            currency=found.currency,
            now=self._now or datetime.now(UTC),
        )


def _write_back(reply: ReplyModel, found: Extracted) -> None:
    """Разобранное — к ответу, рядом с исходным текстом.

    Кладётся всегда, в том числе когда уверенности не хватило: человек
    должен видеть, что именно предложила модель, иначе проверять ему
    нечего.
    """
    reply.price_white = found.price_white
    reply.price_grey = found.price_grey
    reply.currency = found.currency
    reply.payment_methods = list(found.payment_methods) or None
    reply.confidence = found.confidence


def _as_incoming(reply: ReplyModel) -> Incoming:
    """Сохранённый ответ обратно во входящее письмо — ровно настолько,
    насколько это нужно разбору: текст и тема.

    Разбирается то, что лежит в базе, а не то, что пришло в вебхуке:
    между приёмом и разбором проходит время, и единственный текст,
    за который мы отвечаем, — сохранённый.
    """
    return Incoming(
        message_id=reply.inbound_message_id or "",
        to=(),
        from_email=reply.from_email or "",
        subject=reply.subject or "",
        text=reply.raw_body,
    )
