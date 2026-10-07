"""Путь входящего письма: от вебхука до строк в таблице.

Порядок шагов и есть содержание файла. Каждый следующий зависит
от предыдущего, и ни один не молчит о своём исходе.

    повтор? → привязка → чем был ответ → запись → последствия
    ... и отдельно, задачей очереди: разбор цены

**Повтор проверяется первым.** Провайдер доставляет события «хотя бы
один раз»; без этой проверки повтор дал бы второй ответ, второй разбор
и второй платный вызов модели. Но повтор — ещё и единственный шанс
поставить разбор, который не встал в очередь: вебхук сохранил ответ,
а очередь в ту минуту лежала. Такой ответ повтор отдаёт разбору снова
(`Accepted.to_parse`), иначе он ждал бы разбора вечно.

**Приём и разбор разведены, и это не удобство.** Вызов модели идёт
секундами, а платформа повторяет вебхук по таймауту — платный разбор
внутри запроса означал бы повторные списания там, где сеть подтормозила.
Вебхук делает всё дешёвое и отвечает; цену разбирает очередь.

**Модель зовётся только для ответов людей — и только доноров.** Разбирать
цену в отказе доставки или в автоответчике — платить за заведомо пустой
результат; разбирать её в ответе рекламодателя — записать его расход
ценой площадки (`outcome.ADVERTISER_LEAD`), в ответе лида продаж — то же
самое: его вид разбирает очередь продаж (`Accepted.to_sales`). Автоответ с суммой
в валюте модели тоже не отдаётся — его цену смотрит человек (`outcome.AUTO_REPLY_WITH_SUM`).
И один ответ разбирается один раз: разобранный или решённый человеком
модели второй раз не уходит.

**Мёртвый ящик сам называет следующий адрес** («пишите на editor@…»):
на домене донора он сразу ложится в контакты, на чужом — ждёт человека
(`redirect`).

**Непривязанное сохраняется — вместе с причиной.** Ответ, который не удалось
соотнести, — это не мусор, а потерянный донор. Молча отброшенный, он выглядит
как «донор не ответил», и причину будут искать в лестнице контактов. Почему
не привязан, решает `binding` в минуту приёма, и это ложится к ответу рядом
с адресами, на которые он пришёл: вкладка «Не привязаны» (`unbound`)
показывает и то и другое.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import assert_never

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import usage
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies import binding, classify, outcome, redirect
from backend.features.replies import extract as extract_mod
from backend.features.replies.attachments import ReplyFiles
from backend.features.replies.extract import ExtractClient, Extracted
from backend.features.replies.inbound import Incoming
from backend.features.replies.repository import Addressee, ReplyRepository
from backend.features.replies.unbound import explain

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Accepted:
    """Чем кончился приём одного письма."""

    reply_id: int | None
    kind: ReplyKind | None
    way: binding.BindingWay
    #: Почему не привязан — то же, что легло к ответу. Пусто у привязанного.
    unbound: binding.Unbound | None = None
    #: Название сработавшего правила. Пусто — не сработало ни одно.
    rule: str | None = None
    duplicate: bool = False
    bound: bool = False
    needs_review: bool = False
    review_reason: str | None = None
    #: Разбор цены ещё впереди: он идёт задачей очереди.
    parse_pending: bool = False
    #: Повтор вебхука застал ответ, чей разбор цены так и не шёл: номер
    #: этого ответа. Разбор ставится снова — иначе он не пошёл бы никогда.
    unparsed: int | None = None
    #: Ответ уходит очереди продаж (`outcome.to_sales_queue`): вид разбирает модуль продаж.
    sales_pending: bool = False
    #: Повтор вебхука застал ответ продаж, чей разбор вида так и не шёл.
    sales_again: int | None = None
    #: Что назвал автоответ мёртвого ящика и что с этим сделано.
    forwarding: redirect.Redirect = field(default_factory=redirect.Redirect)

    @property
    def to_parse(self) -> int | None:
        """Какой ответ отдать разбору цены: только что принятый или застрявший."""
        if self.parse_pending and self.reply_id is not None:
            return self.reply_id
        return self.unparsed

    @property
    def to_sales(self) -> int | None:
        """Какой ответ отдать очереди продаж: только что принятый или застрявший."""
        if self.sales_pending and self.reply_id is not None:
            return self.reply_id
        return self.sales_again

    @property
    def as_report(self) -> dict[str, object]:
        """Короткий итог для лога и для ответа вебхуку."""
        return {
            "ответ": self.reply_id,
            "вид": self.kind.value if self.kind else None,
            "привязка": self.way.value,
            **({"почему не привязан": self.unbound.value} if self.unbound else {}),
            "правило": self.rule,
            "повтор": self.duplicate,
            "ждёт человека": self.needs_review,
            "разбор снова": self.unparsed,
            "продажам": self.to_sales,
            **self.forwarding.as_report,
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
    #: Почему модель не звали — словами. Пусто — звали. Уходит и в итог
    #: задачи очереди: без него второй раз пришедшая задача выглядела бы
    #: разбором с нулевой уверенностью.
    skipped: str | None = None


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
        taken = await self._repo.taken(incoming.message_id)
        if taken is not None:
            return await self._repeated(incoming, taken)

        bound, addressee = await self._bind(incoming)
        verdict = classify.classify(incoming)

        reply = self._repo.add(
            incoming,
            kind=verdict.kind,
            thread_id=addressee.message.thread_id if addressee else None,
            message_id=addressee.message.id if addressee else None,
            found=None,
            unbound=bound.unbound,
        )
        await self._session.flush()
        # Файлы — сразу за ответом и в той же транзакции: другой копии письма
        # нет, и ответ, записанный без своих вложений, терял бы прайс молча.
        ReplyFiles(self._session).keep(reply.id, incoming.attachments)

        if addressee is None:
            logger.warning(
                "приём: ответ №%s не привязан ни к одному письму — %s (тема «%s»)",
                reply.id,
                bound.unbound,
                incoming.subject[:80],
            )
            return Accepted(
                reply_id=reply.id,
                kind=verdict.kind,
                way=bound.way,
                unbound=bound.unbound,
                rule=verdict.rule,
                needs_review=True,
                review_reason=f"ответ не привязан к письму: {explain(bound.unbound, incoming.to)}",
            )

        return await self._settle(reply, verdict, bound, incoming, addressee)

    async def _repeated(self, incoming: Incoming, taken: ReplyModel) -> Accepted:
        """Повтор вебхука: второго ответа не заводим.

        Но если разбор цены у принятого ответа так и не шёл, повтор отдаёт
        его разбору снова. Платформа повторяет письмо ровно тогда, когда
        вебхук ответил не 2xx, — а так он отвечает, если ответ сохранён,
        а очередь разбора в ту минуту лежала. Без этой ветки повтор видел
        «уже принято», и ответ оставался «ждёт разбора» навсегда.
        """
        unparsed = taken.id if await self._repo.parse_never_ran(taken) else None
        # Тот же случай у ответа продаж: снимка разбора вида нет, человек не решал.
        unsorted = taken.model_parse is None and taken.reviewed_at is None
        stage = await self._repo.stage_of(taken) if unsorted else None
        sales_again = taken.id if outcome.to_sales_queue(taken.kind, stage) else None
        logger.info(
            "приём: письмо %s уже принято ответом №%s — повтор вебхука%s",
            incoming.message_id,
            taken.id,
            "; разбор цены так и не шёл — ставлю снова" if unparsed else "",
        )
        return Accepted(
            reply_id=None,
            kind=None,
            way=binding.BindingWay.NONE,
            duplicate=True,
            unparsed=unparsed,
            sales_again=sales_again,
        )

    async def _settle(
        self,
        reply: ReplyModel,
        verdict: classify.Verdict,
        bound: binding.Binding,
        incoming: Incoming,
        addressee: Addressee,
    ) -> Accepted:
        """Решения, не зависящие от цены: остановка цепочки, стоп-лист,
        отметка мёртвого адреса, запоминание отвечающего — и адреса,
        которые назвал мёртвый ящик."""
        auto = verdict.kind is ReplyKind.AUTO_REPLY
        consequences = outcome.decide(
            verdict.kind,
            None,
            stage=addressee.stage,
            names_a_sum=auto and outcome.names_a_sum(incoming.text),
        )
        await self._apply(consequences, incoming=incoming, addressee=addressee)
        if verdict.kind is ReplyKind.HUMAN:
            # По виду ответа, а не по последствиям: остановку цепочки дают
            # и отказ, и отписка, а отвеченным диалог делает только человек.
            await self._repo.mark_replied(addressee.message.thread_id)
        found = await self._redirect(verdict, incoming, addressee)
        if auto and consequences.needs_review:
            logger.info("приём: ответ №%s — %s", reply.id, consequences.review_reason)

        return Accepted(
            reply_id=reply.id,
            kind=verdict.kind,
            way=bound.way,
            rule=verdict.rule,
            bound=True,
            needs_review=consequences.needs_review or found.review_reason is not None,
            review_reason=consequences.review_reason or found.review_reason,
            parse_pending=verdict.kind is ReplyKind.HUMAN
            and outcome.priced_by_model(addressee.stage),
            sales_pending=outcome.to_sales_queue(verdict.kind, addressee.stage),
            forwarding=found,
        )

    async def _redirect(
        self, verdict: classify.Verdict, incoming: Incoming, addressee: Addressee
    ) -> redirect.Redirect:
        """Мёртвый ящик сам назвал, куда писать, — взять адрес (`redirect`)."""
        if verdict.rule != classify.DEAD_MAILBOX:
            return redirect.Redirect()
        return await redirect.follow(
            self._session, incoming, domain_id=addressee.domain_id, host=addressee.host
        )

    # --- шаги ---

    async def _bind(self, incoming: Incoming) -> tuple[binding.Binding, Addressee | None]:
        """Привязка вместе с проверкой, что письмо ещё существует.

        Метка может указывать на письмо, которого нет: так бывает после
        чистки базы. Ответ тогда не теряем, но привязку не выдумываем.
        """
        known = await self._repo.ours_by_internet_message_id(binding.thread_ids(incoming))
        bound = binding.bind(incoming, by_message_id=known)
        if bound.message_id is None:
            return bound, None

        addressee = await self._repo.addressee(bound.message_id)
        if addressee is None:
            logger.warning("приём: письмо №%s из метки не найдено", bound.message_id)
            return binding.no_such_letter(), None
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
            (consequences.suppress_email, lambda: self._suppress(incoming)),
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

    async def _suppress(self, incoming: Incoming) -> None:
        await self._repo.suppress(incoming.from_email)

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
        skipped = await self._not_for_model(reply)
        if skipped is not None:
            return skipped

        # Потолок модели: исключение уходит задаче, и она ставит разбор заново
        # на начало следующих суток UTC (`workers/jobs._parse_or_postpone`) —
        # ответ не теряется и не судится без модели.
        await usage.ensure_llm_within_cap(self._session)
        found = await self._extractor.extract(_as_incoming(reply))
        if found.tokens_spent:
            usage.record(self._session, operation="reply_parse", units=found.tokens_spent)
        _write_back(reply, found)

        consequences = outcome.decide(reply.kind, found)
        if consequences.store_price:
            await self._store_price(reply, found)
            await self._seller_answer(reply, extract_mod.PLACEMENT_SELLS)
        if consequences.store_declines:
            await self._seller_answer(reply, extract_mod.PLACEMENT_DECLINES)
        if consequences.store_sells:
            await self._seller_answer(reply, extract_mod.PLACEMENT_SELLS)
        if consequences.store_free:
            await self._seller_answer(reply, extract_mod.PLACEMENT_FREE)

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

    async def _not_for_model(self, reply: ReplyModel) -> Parsed | None:
        """Ответ, который модели не отдаётся, — с итогом без неё. `None` — отдаётся."""
        if reply.kind is not ReplyKind.HUMAN:
            # Разбирать нечего, и это не ошибка: задача могла быть
            # поставлена до того, как вид ответа уточнили.
            return _without_model(reply, "не ответ человека")
        if reply.model_parse is not None or reply.reviewed_at is not None:
            # Задача пришла второй раз — повтор вебхука, ручная постановка.
            # Второй вызов модели стоил бы денег и затёр бы и снимок первого
            # разбора, по которому калибруется модель, и решение человека.
            logger.info("разбор: ответ №%s уже разобран или решён — модель не зову", reply.id)
            return _without_model(reply, "уже разобран или решён человеком")
        match await self._repo.stage_of(reply):
            case Stage.DONORS | None:
                return None
            case Stage.ADVERTISERS:
                skipped, why = "ответ рекламодателя", outcome.ADVERTISER_LEAD
            case Stage.SALES:
                skipped, why = "ответ продаж", outcome.SALES_WAITING
            case unknown:
                assert_never(unknown)
        # Приём такой разбор не ставит; пришла задача — значит, её
        # поставили в обход, и платить за неё модели незачем.
        logger.warning("разбор: ответ №%s — %s, отказ", reply.id, why)
        return _without_model(reply, skipped, why=why)

    async def _seller_answer(self, reply: ReplyModel, answer: str) -> None:
        domain_id = await self._repo.domain_of(reply)
        if domain_id is None:
            return
        await self._repo.record_seller_answer(
            domain_id=domain_id, answer=answer, reply_id=reply.id, now=self._now
        )

    async def _store_price(self, reply: ReplyModel, found: Extracted) -> None:
        """Донор ищется по диалогу, а при его отсутствии — по письму:
        связь письма стирается при его удалении, диалог остаётся."""
        domain_id = await self._repo.domain_of(reply)
        price = found.price_white if found.price_white is not None else found.price_grey
        if domain_id is None or price is None:
            return
        await self._repo.store_price(
            domain_id=domain_id,
            price=price,
            currency=found.currency,
            offers=reply.offers,
            now=self._now or datetime.now(UTC),
        )


def _without_model(reply: ReplyModel, skipped: str, *, why: str | None = None) -> Parsed:
    """Итог разбора, в котором модель не звали: ждёт ли ответ человека —
    по тому же правилу, что экран, а не «нет» по умолчанию."""
    waiting = why is not None or outcome.waiting_for_review(
        reply.kind, reply.confidence, reviewed=reply.reviewed_at is not None
    )
    return Parsed(
        reply_id=reply.id,
        confidence=reply.confidence or 0.0,
        stored_price=False,
        needs_review=waiting,
        review_reason=why,
        tokens_spent=0,
        skipped=skipped,
    )


def _write_back_placement(reply: ReplyModel, found: Extracted) -> None:
    reply.placement = found.placement
    reply.model_parse = found.snapshot()


def _write_back(reply: ReplyModel, found: Extracted) -> None:
    """Разобранное — к ответу, рядом с исходным текстом.

    Кладётся всегда, в том числе когда уверенности не хватило: человек
    должен видеть, что именно предложила модель, иначе проверять ему
    нечего.
    """
    reply.price_white = found.price_white
    reply.price_grey = found.price_grey
    reply.currency = found.currency
    # Пустой список, а не пусто: `[]` — «разобран, цен не названо», а пусто
    # остаётся у ответов, разобранных до того, как список появился.
    reply.offers = [offer.as_json() for offer in found.offers]
    reply.payment_methods = list(found.payment_methods) or None
    reply.confidence = found.confidence
    _write_back_placement(reply, found)


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
