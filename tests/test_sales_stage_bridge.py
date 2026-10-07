"""Мост почты к модулю продаж — срез 4.6b, общая часть: модуль продаж подставной.

Почта модуль продаж не импортирует (контракт `mail-does-not-know-sales`): о письме продаж
она спрашивает мост `core/stages.py`, а модуль продаж подключает себя сам
(`register_sales`). Здесь вместо него — подставной с выдуманными адресом, именем и
текстом добивки: общая отправка и проход добивок обязаны вести письмо продаж его ответами,
а без регистрации — прежний отказ 1.1b словами, транспорт этапа не спрошен. Мир — тот же,
что у 1.1b (`tests/test_sales_stage_mail.py`): домен письма продаж — принятый донор с
адресом сайта, ящики есть у обоих этапов, транспорт подставной.

Поломка модуля — не его отказ словами — почте не достаётся: мост переводит её в «не
подключены» с причиной, и общий проход добивок, пачка и кнопка её переживают.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, SenderStatus, Stage
from backend.features.core.models.outreach import MessageModel, SenderModel
from backend.features.core.stages import (
    SALES_NOT_CONNECTED,
    Recipient,
    SalesFollowup,
    SalesNotConnectedError,
)
from backend.features.letters import followups
from backend.features.letters.building import followup_key, idempotency_key
from backend.features.letters.sending import SendError, Sending, SuppressedError
from backend.features.letters.transport import Outgoing
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_mail_accounts import _ByStage
from tests.test_mail_identity import Recording
from tests.test_sales_stage_mail import LEAD, NOW, _donor_chain, sales_world, sender

#: Выдуманные ответы подставного модуля продаж.
LEAD_EMAIL = "jane@lead.example.test"
SENDER_NAME = "Made-up Sales Sender"
SALES_BOX = "sales@mail-sales.example.test"


@dataclass
class FakeSalesMail:
    """Подставной модуль продаж: отвечает выдуманным и помнит, о чём его спросили."""

    connected_now: bool = True
    body: str = "A made-up reminder."
    refusal: SendError | None = None
    #: Какой ответ модуля ломается и чем: исключением Python, упавшим запросом к базе или
    #: отказом почты, который этому ответу не подходит.
    broken: str | None = None
    broken_by: str = "runtime"
    asked: list[str] = field(default_factory=list)

    async def _maybe_break(self, session: AsyncSession, answer: str) -> None:
        if answer != self.broken:
            return
        if self.broken_by == "database":
            await session.execute(text("SELECT 1 FROM made_up_table_of_the_sales_module"))
        if self.broken_by == "refusal":
            raise SuppressedError(f"выдуманный отказ модуля: {answer}")
        raise RuntimeError(f"выдуманная поломка модуля: {answer}")

    async def recipient(
        self, session: AsyncSession, message: MessageModel, _what: str
    ) -> Recipient:
        self.asked.append(f"recipient {message.id}")
        await self._maybe_break(session, "recipient")
        return Recipient(Stage.SALES, LEAD_EMAIL, SENDER_NAME)

    async def check(self, session: AsyncSession, message: MessageModel) -> None:
        self.asked.append(f"check {message.id}")
        await self._maybe_break(session, "check")
        if self.refusal is not None:
            raise self.refusal

    async def connected(self, session: AsyncSession) -> bool:
        await self._maybe_break(session, "connected")
        return self.connected_now

    async def followup(
        self, session: AsyncSession, thread_id: int | None, step: int
    ) -> SalesFollowup:
        self.asked.append(f"followup {thread_id}:{step}")
        await self._maybe_break(session, "followup")
        return SalesFollowup(body=self.body)


@pytest.fixture
def unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Модуль продаж к почте не подключён; после теста — регистрация, какая была."""
    monkeypatch.setattr(stages._SALES, "load", None)


@pytest.fixture
def fake(unregistered: None) -> FakeSalesMail:
    """Подставной модуль продаж подключён тем же входом, что настоящий."""
    found = FakeSalesMail()
    stages.register_sales(lambda: found)
    return found


def _transports() -> _ByStage:
    return _ByStage(sales=Recording(), donors=Recording(), advertisers=Recording())


def _seen(source: _ByStage) -> list[Outgoing]:
    transport = source.transports["sales"]
    assert isinstance(transport, Recording)
    return list(transport.seen)


# --- без регистрации: прежний отказ 1.1b ------------------------------------------------------


async def test_without_the_module_a_sales_letter_is_refused_as_before(
    session: AsyncSession, filled_legal: None, unregistered: None
) -> None:
    world = await sales_world(session)
    source = _transports()

    with pytest.raises(SalesNotConnectedError) as refused:
        await Sending(session, source, now=NOW).send(world.letter.id)

    assert str(refused.value) == f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED}"
    assert source.asked == []
    await session.refresh(world.letter)
    assert world.letter.status is MessageStatus.QUEUED


async def test_without_the_module_sales_deadlines_wait_and_are_not_claimed(
    session: AsyncSession, filled_legal: None, unregistered: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Без модуля продаж проход называет ждущие сроки вслух на каждом проходе, как в 1.1b."""
    world = await sales_world(session, status=MessageStatus.SENT, due=NOW - timedelta(days=1))

    with caplog.at_level(logging.WARNING, logger=followups.__name__):
        report = await followups.send_due(session, transport=_transports(), limit=5, now=NOW)

    assert (report.sent, report.waiting) == (0, 1)
    await session.refresh(world.letter)
    assert world.letter.next_action_at == NOW - timedelta(days=1)
    assert f"1 подошли, срок не погашен — {SALES_NOT_CONNECTED}" in caplog.text


@pytest.mark.parametrize("stage", [Stage.DONORS, Stage.ADVERTISERS])
async def test_bridge_gives_donors_and_advertisers_their_contact_untouched(
    session: AsyncSession, fake: FakeSalesMail, stage: Stage
) -> None:
    message = MessageModel(id=1, step=0)

    found = await stages.recipient(session, message, stage, "editor@site.example.test", "Письмо №1")

    assert found == Recipient(stage, "editor@site.example.test", None)
    assert fake.asked == []


@pytest.mark.parametrize(("connected", "refused"), [(True, False), (False, True)])
async def test_queue_send_asks_the_module_whether_sales_are_connected(
    session: AsyncSession, fake: FakeSalesMail, connected: bool, refused: bool
) -> None:
    """Отправка очереди пачкой спрашивает тот же мост: модуль говорит «нет» — отказ
    словами до задачи; «да» — пачка этапа продаж ставится."""
    fake.connected_now = connected
    what = "Очередь писем не отправлена"

    if refused:
        with pytest.raises(SalesNotConnectedError, match=f"^{what}: {SALES_NOT_CONNECTED}$"):
            await stages.check_connected(session, Stage.SALES, what)
    else:
        await stages.check_connected(session, Stage.SALES, what)


async def test_queue_send_without_the_module_is_refused_as_before(
    session: AsyncSession, unregistered: None
) -> None:
    with pytest.raises(SalesNotConnectedError, match=SALES_NOT_CONNECTED):
        await stages.check_connected(session, Stage.SALES, "Очередь писем не отправлена")


# --- модуль подключён: письмо идёт его ответами --------------------------------------------


async def test_sales_letter_goes_to_the_address_and_in_the_name_the_module_gives(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail
) -> None:
    """Адрес — ответ модуля продаж, а не строка `contacts` домена (у домена мира она есть)."""
    world = await sales_world(session)
    source = _transports()

    await Sending(session, source, now=NOW).send(world.letter.id)

    [outgoing] = _seen(source)
    assert (outgoing.to, outgoing.from_name, outgoing.from_email) == (
        LEAD_EMAIL,
        SENDER_NAME,
        SALES_BOX,
    )
    assert source.asked == ["sales"]
    assert fake.asked == [f"recipient {world.letter.id}", f"check {world.letter.id}"]
    await session.refresh(world.letter)
    assert world.letter.status is MessageStatus.SENT


async def test_refusal_of_the_module_check_stops_the_letter_before_the_mailbox(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail
) -> None:
    fake.refusal = SuppressedError("выдуманному лиду писать нельзя")
    world = await sales_world(session)
    source = _transports()

    with pytest.raises(SuppressedError, match="выдуманному лиду писать нельзя"):
        await Sending(session, source, now=NOW).send(world.letter.id)

    assert _seen(source) == []
    await session.refresh(world.letter)
    assert (world.letter.status, world.letter.sender_id) == (MessageStatus.QUEUED, None)


async def test_not_connected_answer_of_the_module_is_said_before_the_transport(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(_session: AsyncSession, _message: MessageModel, what: str) -> Recipient:
        raise SalesNotConnectedError(what, "нет выдуманной учётки")

    monkeypatch.setattr(fake, "recipient", refuse)
    world = await sales_world(session)
    source = _transports()

    with pytest.raises(SalesNotConnectedError) as refused:
        await Sending(session, source, now=NOW).send(world.letter.id)

    assert str(refused.value) == (
        f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED} — нет выдуманной учётки"
    )
    assert source.asked == []


# --- добивки продаж: общий проход, текст модуля --------------------------------------------


async def _first_letter_sent(session: AsyncSession) -> tuple[MessageModel, SenderModel]:
    world = await sales_world(session, status=MessageStatus.SENT, due=NOW - timedelta(minutes=1))
    box = await session.scalar(select(SenderModel).where(SenderModel.email == SALES_BOX))
    assert box is not None
    return world.letter, box


async def test_sales_followup_goes_in_the_same_thread_with_the_text_of_the_module(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail
) -> None:
    first, box = await _first_letter_sent(session)
    await sender(session, "other@mail-sales2.example.test", Stage.SALES)
    source = _transports()

    report = await followups.send_due(session, transport=source, limit=5, now=NOW)

    assert (report.sent, report.waiting, report.postponed) == (1, 0, 0)
    [outgoing] = _seen(source)
    assert outgoing.subject == first.subject
    assert outgoing.in_reply_to == first.internet_message_id
    assert (outgoing.from_email, outgoing.to, outgoing.body) == (
        box.email,
        LEAD_EMAIL,
        "A made-up reminder.",
    )
    step = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
    assert step is not None
    assert (step.thread_id, step.idempotency_key) == (first.thread_id, f"sales:{LEAD}:1")
    assert f"followup {first.thread_id}:1" in fake.asked


async def test_sales_deadline_waits_while_the_module_says_not_connected(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail, caplog: pytest.LogCaptureFixture
) -> None:
    """Модуль подключён к мосту, но говорит «не подключены»: срок цел и посчитан в отчёте прохода,
    а предупреждения в журнале нет — иначе оно шумело бы каждый час, пока продажи выключены."""
    fake.connected_now = False
    first, _ = await _first_letter_sent(session)

    with caplog.at_level(logging.WARNING, logger=followups.__name__):
        report = await followups.send_due(session, transport=_transports(), limit=5, now=NOW)

    assert (report.sent, report.waiting) == (0, 1)
    await session.refresh(first)
    assert first.next_action_at == NOW - timedelta(minutes=1)
    assert fake.asked == []
    assert "срок не погашен" not in caplog.text


async def test_followup_waiting_for_a_box_goes_with_the_text_of_today(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail
) -> None:
    """Добивка не ушла — ящик первого письма на паузе — и ждёт строкой в базе; модуль продаж
    тем временем отвечает другим текстом (сменили подпись): уходит нынешний."""
    _, box = await _first_letter_sent(session)
    box.status = SenderStatus.PAUSED
    await session.flush()
    first_try = await followups.send_due(session, transport=_transports(), limit=5, now=NOW)
    box.status = SenderStatus.FREE
    fake.body = "Another made-up reminder."
    source = _transports()

    second_try = await followups.send_due(
        session, transport=source, limit=5, now=NOW + followups.POSTPONE
    )

    assert (first_try.postponed, second_try.sent) == (1, 1)
    assert [outgoing.body for outgoing in _seen(source)] == ["Another made-up reminder."]
    rows = await session.scalars(select(MessageModel.id).where(MessageModel.step == 1))
    assert len(list(rows)) == 1


# --- модуль упал: почта получает только отказ словами ----------------------------------------


@pytest.mark.parametrize("broken_by", ["runtime", "database", "refusal"])
@pytest.mark.parametrize(("broken", "counts"), [("connected", (1, 0, 1)), ("followup", (1, 1, 0))])
async def test_a_broken_sales_module_does_not_stop_the_pass_for_donors(
    session: AsyncSession,
    filled_legal: None,
    fake: FakeSalesMail,
    caplog: pytest.LogCaptureFixture,
    broken: str,
    counts: tuple[int, int, int],
    broken_by: str,
) -> None:
    """Проход добивок общий: поломка модуля продаж его не роняет. Донорская добивка уходит,
    срок продаж цел — «подключены ли» упало: срок не взят; текст добивки не собрался: срок
    возвращён на час, — причина в журнале. Упавший запрос модуля не ломает транзакцию почты;
    отказ почты, который добивке до письма не подходит (стоп-лист), — тоже «не подключены»."""
    fake.broken, fake.broken_by = broken, broken_by
    due = NOW - timedelta(days=2)
    world = await sales_world(session, status=MessageStatus.SENT, due=due)
    donor = await _donor_chain(session, due=NOW - timedelta(days=1))

    with caplog.at_level(logging.WARNING, logger=stages.__name__):
        report = await followups.send_due(session, transport=_transports(), limit=5, now=NOW)

    assert (report.sent, report.postponed, report.waiting) == counts
    await session.refresh(world.letter)
    await session.refresh(donor)
    later = {"connected": due, "followup": NOW + followups.POSTPONE}
    assert world.letter.next_action_at == later[broken]
    assert donor.next_action_at is None  # донорская ушла, срок следующей — у её добивки
    assert "ошибка модуля продаж" in caplog.text


@pytest.mark.parametrize("broken", ["recipient", "check"])
async def test_a_broken_sales_module_is_said_in_words_and_the_letter_waits(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail, broken: str
) -> None:
    """Поломка модуля на отправке — «не подключены» с причиной: кнопка получит 409, пачка
    встанет словами, а не «связь с почтой оборвалась»; письмо в очереди, ничего не ушло."""
    fake.broken = broken
    world = await sales_world(session)
    source = _transports()

    with pytest.raises(SalesNotConnectedError) as refused:
        await Sending(session, source, now=NOW).send(world.letter.id)

    assert str(refused.value) == (
        f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED} — выдуманная поломка модуля: {broken}"
    )
    assert isinstance(refused.value.__cause__, RuntimeError)
    assert _seen(source) == []
    await session.refresh(world.letter)
    assert (world.letter.status, world.letter.sender_id) == (MessageStatus.QUEUED, None)


async def test_queue_send_with_a_broken_module_is_refused_in_words(
    session: AsyncSession, fake: FakeSalesMail
) -> None:
    fake.broken = "connected"
    what = "Очередь писем не отправлена"

    with pytest.raises(SalesNotConnectedError, match=f"^{what}: {SALES_NOT_CONNECTED}$"):
        await stages.check_connected(session, Stage.SALES, what)


# --- ключ добивки: из ключа предыдущего письма ---------------------------------------------


@pytest.mark.parametrize(
    ("previous", "step", "expected"),
    [
        (
            "sales:acme.example.test:jane@acme.example.test:0",
            1,
            "sales:acme.example.test:jane@acme.example.test:1",
        ),
        (
            "sales:a2.example.test:a2@a2.example.test:1",
            2,
            "sales:a2.example.test:a2@a2.example.test:2",
        ),
        (
            "sales:acme.example.test:jane@acme.example.test:0:a2",
            1,
            "sales:acme.example.test:jane@acme.example.test:1:a2",
        ),
        ("donors:site.example.test:0", 1, "donors:site.example.test:1"),
        ("donors:site.example.test:1:a3", 2, "donors:site.example.test:2:a3"),
    ],
)
def test_followup_key_inherits_the_first_letter_key(
    previous: str, step: int, expected: str
) -> None:
    """У продаж в ключе контакт (`{этап}:{домен}:{адрес}:{шаг}`) — адреса у почты нет, его
    знает ключ первого письма; номер попытки остаётся на месте."""
    assert followup_key(previous, step) == expected


def test_followup_key_of_a_donor_is_the_same_as_before() -> None:
    """Для доноров вывод из прежнего ключа совпадает с прежним правилом."""
    for attempt in (1, 2):
        first = idempotency_key(
            stage=Stage.DONORS, host="site.example.test", step=0, attempt=attempt
        )
        assert followup_key(first, 1) == idempotency_key(
            stage=Stage.DONORS, host="site.example.test", step=1, attempt=attempt
        )
