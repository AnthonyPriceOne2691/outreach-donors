"""Письмо отклонённого лида — с очереди в момент отклонения; вернулся лид — письмо пишется заново.

Лида отклонила очистка (перепроверка «готов»: второй `unknown`) или снял стоп-лист продаж, а его
первое письмо уже стоит в очереди. До правки письмо висело с отказом «стоп-лист» на каждом проходе
автоотправки, а очередь из одних таких писем поднимала тревогу «отказ каждому письму окна». Теперь
оно снимается тут же, словами отказа лида, — тем же местом, что снимает письмо после трёх отказов
платформы (`off_queue.take_off`, решение владельца 13.10). Лид вернулся (запись стоп-листа сняли, и
очистка его пропустила) — сборка пишет ему письмо заново на том же месте. Не пишет — письмо, которое
могло уйти прежней попыткой, и письмо, снятое после отказов платформы.

База настоящая, почта — нулевой транспорт с памятью, модель — подделка коридора, проверяльщик —
подделка без сети. Адреса и тексты выдуманы.
"""

from __future__ import annotations

import pytest
from backend.config import sales as sales_cfg
from backend.features.contacts.mx import MailRoute
from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.compose import Rendered
from backend.features.letters.rewrite import Personalization, RewriteResult
from backend.features.sales import (
    autosend,
    autosend_refusals,
    cleaning,
    off_queue,
    queue,
    stoplist,
    unconfirmed,
)
from backend.features.sales.autosend import Stop
from backend.features.sales.models import LeadStatus, SalesLeadModel, SalesStoplistModel
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_send import _seen, _transports
from tests.test_sales_unconfirmed import NOW as CLEANED
from tests.test_sales_unconfirmed import WEEK, Hunter, _clean, _recheck_written

NOW = w.NOW
JANE = "jane@acme.example.test"
CLOSED = f"{off_queue.LEAD_CLOSED}: стоп-лист продаж: адрес {JANE}"


@pytest.fixture(autouse=True)
def _month_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Месячный потолок проверок задан с запасом: здесь не о нём."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "100000")


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Домен адреса принимает почту: ступень MX — своими тестами."""

    async def route(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.MX

    monkeypatch.setattr(cleaning, "mail_route", route)


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def _queued(session: AsyncSession, world: w.World) -> MessageModel:
    """Лид Jane «готов», её первое письмо собрано и ждёт в очереди."""
    await w.lead(session, world.hypothesis_id, JANE)
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=1)
    letter = await session.scalar(select(MessageModel))
    assert letter is not None
    return letter


async def _closed_and_back(session: AsyncSession) -> None:
    """Адрес Jane — в стоп-лист продаж (лид снят), запись сняли (лид — на очистку), очистка
    пропустила его снова."""
    await stoplist.add(session, stoplist.read_text(JANE), author="тест", note=None)
    entry = await session.scalar(select(SalesStoplistModel))
    assert entry is not None
    await stoplist.remove(session, entry.id, author_id=None)
    await cleaning.clean(session, FixtureVerifier(), now=NOW)


async def _lead(session: AsyncSession) -> SalesLeadModel:
    found = await session.scalar(select(SalesLeadModel).where(SalesLeadModel.email == JANE))
    assert found is not None
    return found


# --- письмо снятого лида — с очереди в момент снятия --------------------------------------------


async def test_a_lead_rejected_on_its_recheck_takes_its_queued_letter_off_the_queue(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Письмо собрано при выдуманном проверяльщике; живой ответил `unknown` дважды — очистка
    отклоняет лида и в ту же минуту снимает его письмо словами отказа. Проход автоотправки видит
    пустую очередь, а не «отказ каждому письму окна»."""
    lead = await _recheck_written(session, world, monkeypatch, sent=False)
    letter = await session.scalar(select(MessageModel))
    assert letter is not None

    await _clean(session, world, Hunter(), CLEANED + WEEK)
    found = await autosend.run(session, _transports(), now=CLEANED + WEEK)

    await session.refresh(lead)
    await session.refresh(letter)
    assert (lead.status, lead.rejection_reason) == (LeadStatus.REJECTED, "unconfirmed")
    assert (letter.status, letter.failure_reason) == (
        MessageStatus.STOPPED,
        off_queue.closed(unconfirmed.TWICE),
    )
    assert (found.stop, found.refused) == (Stop.EMPTY, {})


async def test_a_letter_taken_by_a_sending_meanwhile_is_left_to_it(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо секундой раньше взяла отправка («отправляется») — снятие лида его не трогает:
    «остановлено» поверх врало бы о письме, которое ушло. Исход решит событие платформы."""
    letter = await _queued(session, world)
    await session.execute(
        update(MessageModel)
        .where(MessageModel.id == letter.id)
        .values(status=MessageStatus.SENDING)
        .execution_options(synchronize_session=False)
    )

    await stoplist.add(session, stoplist.read_text(JANE), author="тест", note=None)

    await session.refresh(letter)
    assert (letter.status, letter.failure_reason) == (MessageStatus.SENDING, None)
    assert (await _lead(session)).status is LeadStatus.REJECTED


async def test_the_one_place_of_taking_off_leaves_a_letter_a_sending_took_a_second_ago(
    session: AsyncSession, world: w.World
) -> None:
    """Одно место снятия (`off_queue.take_off`: письмо снятого лида и письмо после трёх отказов
    платформы) снимает только письмо, которое всё ещё в очереди. Прочитано «в очереди», а секундой
    раньше его взяла отправка — не трогается: «остановлено» врало бы о письме, которое уходит."""
    letter = await _queued(session, world)
    await session.execute(
        update(MessageModel)
        .where(MessageModel.id == letter.id)
        .values(status=MessageStatus.SENDING)
        .execution_options(synchronize_session=False)
    )

    taken = await off_queue.take_off(session, letter, off_queue.closed("выдуманная причина"))

    found = await session.execute(
        select(MessageModel.status, MessageModel.failure_reason).where(MessageModel.id == letter.id)
    )
    assert (taken, *found.one()) == (False, MessageStatus.SENDING, None)


# --- лид вернулся — письмо пишется заново на том же месте ----------------------------------------


async def test_a_lead_back_from_the_stoplist_gets_its_letter_written_again_in_the_same_place(
    session: AsyncSession, world: w.World
) -> None:
    """Запись стоп-листа сняли, очистка пропустила лида снова — сборка пишет его первое письмо
    заново на том же месте (тот же номер, ключ и диалог, причина снятия стёрта), и оно уходит."""
    letter = await _queued(session, world)
    thread_id, key = letter.thread_id, letter.idempotency_key
    await stoplist.add(session, stoplist.read_text(JANE), author="тест", note=None)
    await session.refresh(letter)
    taken = (letter.status, letter.failure_reason)
    await _closed_and_back(session)
    rewriter = w.CorridorRewriter()

    report = await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=5)
    source = _transports()
    found = await autosend.run(session, source, now=NOW)

    assert taken == (MessageStatus.STOPPED, CLOSED)
    assert (await _lead(session)).status is LeadStatus.READY
    assert (report.prepared, report.refreshed, len(rewriter.seen)) == (0, 1, 1)
    assert found.sent == {letter.id: w.SALES_BOX}
    assert [outgoing.to for outgoing in _seen(source)] == [JANE]
    await session.refresh(letter)
    assert (letter.status, letter.failure_reason) == (MessageStatus.SENT, None)
    assert (letter.thread_id, letter.idempotency_key) == (thread_id, key)
    assert list(await session.scalars(select(MessageModel.id))) == [letter.id]


class _ClosingRewriter(w.CorridorRewriter):
    """Модель пишет письмо — а лида тем временем снимают (стоп-лист пополнили с экрана)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__()
        self.session = session

    async def rewrite(
        self, rendered: Rendered, about: Personalization, *, hint: str | None = None
    ) -> RewriteResult:
        await self.session.execute(
            update(SalesLeadModel)
            .where(SalesLeadModel.email == JANE)
            .values(status=LeadStatus.REJECTED)
        )
        return await super().rewrite(rendered, about, hint=hint)


async def test_a_build_begun_before_the_lead_was_closed_does_not_bring_its_letter_back(
    session: AsyncSession, world: w.World
) -> None:
    """Сборка взяла вернувшегося лида и пишет его письмо, а лида тем временем снова сняли: снятое
    письмо остаётся снятым — сборка не ставит в очередь письмо лида, которому писать нельзя, и в
    отчёте так и говорит: лида сняли, пока письмо собирали (`off_queue.still_ready`)."""
    letter = await _queued(session, world)
    await stoplist.add(session, stoplist.read_text(JANE), author="тест", note=None)
    await _closed_and_back(session)

    report = await queue.build(
        session, _ClosingRewriter(session), hypothesis_id=world.hypothesis_id, limit=5
    )

    assert (report.prepared, report.refreshed) == (0, 0)
    assert dict(report.waiting) == {off_queue.WITHDRAWN: 1}
    await session.refresh(letter)
    assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, CLOSED)


async def test_a_letter_that_may_have_gone_is_not_written_again(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо вернули в очередь после неизвестного исхода — у него наш `Message-ID` прежней
    попытки: она могла дойти. Лида сняли и вернули — сборка такое письмо заново не пишет и модель
    не зовёт: это было бы второе первое письмо тому же человеку."""
    letter = await _queued(session, world)
    letter.internet_message_id = "<made-up-earlier-attempt@mail-sales.example.test>"
    await session.flush()
    await _closed_and_back(session)
    rewriter = w.CorridorRewriter()

    report = await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=5)

    assert (report.prepared, report.refreshed, rewriter.seen) == (0, 0, [])
    await session.refresh(letter)
    assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, CLOSED)


async def test_a_letter_taken_off_after_platform_refusals_is_not_written_again(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо, снятое после трёх отказов платформы, сняла платформа, а не лид: тем же местом
    снятия (`off_queue.take_off`), но сборка его заново не пишет — лид «готов», а письма нет."""
    letter = await _queued(session, world)
    said = autosend_refusals.words(3, "Платформа отказала (400): выдуманный отказ")
    assert await off_queue.take_off(session, letter, said)
    rewriter = w.CorridorRewriter()

    report = await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=5)

    assert (report.prepared, report.refreshed, rewriter.seen) == (0, 0, [])
    await session.refresh(letter)
    assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, said)


async def test_a_long_reason_is_cut_to_the_column_and_keeps_its_head(
    session: AsyncSession, world: w.World
) -> None:
    """Слова отказа длиннее колонки (`messages.failure_reason`, 256) режутся, а начало остаётся:
    по нему сборка узнаёт письмо снятого лида и пишет его заново, когда лид вернётся."""
    letter = await _queued(session, world)

    await off_queue.of_leads(session, {(await _lead(session)).id: "слово " * 100})

    await session.refresh(letter)
    assert letter.failure_reason is not None
    assert len(letter.failure_reason) == off_queue.REASON_LENGTH == 256
    assert letter.failure_reason.startswith(f"{off_queue.LEAD_CLOSED}: слово слово")
