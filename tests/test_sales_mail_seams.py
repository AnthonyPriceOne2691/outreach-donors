"""Ревью стыков почты и модуля продаж (A1–A4): мост, проход добивок, пачка.

Договор моста (`core/stages.py`) держат `test_sales_stage_bridge.py` (подставной модуль) и
`test_sales_mail_contract.py` (настоящий). Здесь — то, чего там не было:

- A1: ответ модуля, уронивший запрос к базе на отправке (адрес, проверка), не ломает
  транзакцию почты — письмо ждёт, сессия жива; сбой транзакции самой почты — её ошибка;
- A2: проход добивок спрашивает «подключены ли» один раз, а не на каждую добивку;
- A3: неполная цепочка одного набора (гипотеза и язык) — отказ одному письму: пачка считает
  его и идёт дальше, а не встаёт целиком, как на «продажи не подключены»;
- A4: отказ пачке называет, чего не хватает продажам, как отказ одному письму;
- сверх списка: «осталось в очереди» после пачки — без потолка пачки;
- обработка ошибок модуля: «пока нельзя» у добивки и «нет лида» — словами, без поломки.

Правки общего кода здесь не делаются: их тесты помечены `xfail(strict=True)` с причиной.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage, UserRole
from backend.features.core.models.outreach import MessageModel
from backend.features.core.stages import SALES_NOT_CONNECTED, SalesNotConnectedError
from backend.features.letters import followups
from backend.features.letters.batch import send_queue
from backend.features.letters.sending import NotReadyError, Sending
from backend.features.sales import chain, chain_text
from backend.features.sales import mail as sales_mail
from backend.features.sales.models import SalesLeadModel, SalesThreadModel
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_send import FIRST_DUE, JANE, OLGA, _first_sent, _queued, _seen, _transports
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import NOW, sales_world

#: Лид на русском — его письмо собирается русской цепочкой того же общего набора.
IVAN = "ivan@acme.example.test"


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Подставной модуль продаж тем же входом, что настоящий; после теста — прежний."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail()
    stages.register_sales(lambda: found)
    return found


# --- A1: сбой базы в ответе модуля на отправке ------------------------------------------------


@pytest.mark.parametrize("broken", ["recipient", "check"])
async def test_a_module_answer_that_breaks_the_base_leaves_the_mail_transaction_whole(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail, broken: str
) -> None:
    """Запрос модуля упал в базе посреди отправки: «не подключены» с причиной, письмо в очереди,
    а транзакция почты цела — следующий запрос той же сессии идёт (мутант «без точки
    сохранения» роняет его: «current transaction is aborted»)."""
    fake.broken, fake.broken_by = broken, "database"
    found = await sales_world(session)

    with pytest.raises(SalesNotConnectedError, match="made_up_table_of_the_sales_module"):
        await Sending(session, _transports(), now=NOW).send(found.letter.id)

    status = await session.scalar(
        select(MessageModel.status).where(MessageModel.id == found.letter.id)
    )
    assert status is MessageStatus.QUEUED


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код core/stages._asked: точка сохранения ставится внутри try — сбой самой "
        "транзакции почты (прерванной раньше или упавшей базы) выдаётся за «ошибку модуля "
        "продаж»; правка — PR «общее» (ревью стыков R1, A1)"
    ),
)
async def test_an_aborted_mail_transaction_is_the_mail_failure_not_the_module_one(
    session: AsyncSession, fake: FakeSalesMail, caplog: pytest.LogCaptureFixture
) -> None:
    """Транзакция почты уже прервана своим упавшим запросом: «подключены ли» поднимает ошибку
    базы как есть, модуль не спрошен, журнал не пишет «ошибка модуля продаж»."""
    with pytest.raises(DBAPIError):
        await session.execute(text("SELECT 1 FROM made_up_table_of_the_mail_itself"))

    with caplog.at_level(logging.WARNING, logger=stages.__name__), pytest.raises(DBAPIError):
        await stages.sales_connected(session)

    assert "ошибка модуля продаж" not in caplog.text


# --- A2: проход добивок спрашивает модуль раз за проход ---------------------------------------


async def test_the_pass_asks_whether_sales_are_connected_once(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Две добивки продаж в одном проходе — вопрос «подключены ли» один: каждый вопрос —
    точка сохранения и чтение настроек, и ответ прохода один на весь проход."""
    source, _ = await _first_sent(session, world, JANE, OLGA)
    asked: list[str] = []
    real = sales_mail.connected

    async def counted(found: AsyncSession) -> bool:
        asked.append("connected")
        return await real(found)

    monkeypatch.setattr(sales_mail, "connected", counted)

    report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert (report.sent, asked) == (2, ["connected"])


# --- A3: неполная цепочка одного набора — отказ одному письму ---------------------------------


async def _two_languages(session: AsyncSession, world: w.World) -> None:
    """Лиды одной гипотезы на двух языках, письма собраны; затем английскую цепочку общего
    набора сделали неполной — выключили вторую добивку."""
    await w.lead(session, world.hypothesis_id, IVAN, name="Ivan", language="ru")
    await _queued(session, world)  # JANE, английский
    off = chain_text.step_template(step=3, language="en", body=w.FOLLOW_BODY[3], active=False)
    await chain.save(session, off, hypothesis_id=None, author="тест", author_id=None)


async def test_an_incomplete_chain_of_one_set_does_not_stop_the_batch(
    session: AsyncSession, world: w.World
) -> None:
    """Пачка встаёт, только когда следующее письмо упрётся в то же (ящиков нет, продажи не
    подключены). Неполная английская цепочка держит письмо Jane, но не письмо Ivan на
    русском: оно уходит, письмо Jane посчитано в итоге и ждёт в очереди с причиной."""
    await _two_languages(session, world)
    source = _transports()

    report = await send_queue(session, source, stage=Stage.SALES)

    assert (report.sent, dict(report.refused), report.stopped, report.left) == (
        1,
        {"письмо не готово к отправке": 1},
        None,
        1,
    )
    assert [outgoing.to for outgoing in _seen(source)] == [IVAN]


async def test_a_letter_of_an_incomplete_chain_is_refused_in_words_and_waits(
    session: AsyncSession, world: w.World
) -> None:
    """Одно письмо — тот же отказ словами: какая цепочка неполна и где её задать; письмо в
    очереди, ящик не тронут."""
    await _two_languages(session, world)
    letter = await session.scalar(
        select(MessageModel)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(SalesLeadModel.email == JANE)
    )
    assert letter is not None

    with pytest.raises(NotReadyError) as refused:
        await Sending(session, _transports(), now=w.NOW).send(letter.id)

    assert str(refused.value).startswith(f"Письмо №{letter.id} не уходит: цепочка писем продаж")
    assert "нет второй добивки" in str(refused.value)
    await session.refresh(letter)
    assert (letter.status, letter.sender_id) == (MessageStatus.QUEUED, None)


# --- A4: отказ пачке называет, чего не хватает --------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код core/stages.check_connected: у моста есть только «подключены ли» (да/нет), "
        "и отказ пачке — без перечня, чего не хватает; правка — PR «общее» (ревью стыков R1, A4)"
    ),
)
async def test_the_batch_refusal_names_what_sales_lack(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    admin_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кнопка пачки при выключенных продажах: 409 называет причину, как отказ одному письму
    (`test_sales_send.test_screen_says_in_words_why_sales_are_not_connected`)."""
    await _queued(session, world)
    await session.commit()
    monkeypatch.setattr(sales_cfg, "ENABLED", False)

    response = await client.post(
        "/api/letters/send-queue", json={"stage": "sales"}, headers=bearer(admin_token)
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"Очередь писем не отправлена: {SALES_NOT_CONNECTED} — "
        "модуль продаж выключен — включает администратор"
    )


@pytest.fixture
async def admin_token(
    make_user: Callable[..., Awaitable[Any]], sign_in: Callable[..., Awaitable[str]]
) -> str:
    await make_user("admin@sales-seams.example.test", role=UserRole.ADMIN)
    return await sign_in("admin@sales-seams.example.test")


# --- сверх списка: пачка берёт до потолка, а «осталось» считает тем же потолком ------------


async def test_the_batch_says_how_many_letters_are_really_left(
    session: AsyncSession, world: w.World
) -> None:
    """Потолок пачки — `limit` (на бою `BATCH_MAX` = 200): пять писем, потолок два — ушло два,
    осталось три, а не «два»."""
    emails = [f"lead{number}@acme.example.test" for number in range(5)]
    await _queued(session, world, *emails)

    report = await send_queue(session, _transports(), stage=Stage.SALES, limit=2)

    assert (report.sent, report.left) == (2, 3)


# --- обработка ошибок модуля: «пока нельзя» и отказ одному письму --------------------------


async def test_a_followup_whose_chain_became_incomplete_waits_and_is_no_module_failure(
    session: AsyncSession, world: w.World, caplog: pytest.LogCaptureFixture
) -> None:
    """Первое письмо ушло, затем первую добивку набора выключили: добивка «пока нельзя» —
    срок на час, причина словами цепочки, без «ошибки модуля продаж» с трассой."""
    source, [first] = await _first_sent(session, world)
    off = chain_text.step_template(step=2, language="en", body=w.FOLLOW_BODY[2], active=False)
    await chain.save(session, off, hypothesis_id=None, author="тест", author_id=None)

    with caplog.at_level(logging.INFO):
        report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert (report.sent, report.postponed) == (0, 1)
    await session.refresh(first)
    assert first.next_action_at == FIRST_DUE + followups.POSTPONE
    assert "нет первой добивки" in caplog.text
    assert "ошибка модуля продаж" not in caplog.text


async def _unlink(session: AsyncSession, letter: MessageModel) -> None:
    """Связь диалога с лидом пропала — письмо продаж без лида."""
    link = await session.get(SalesThreadModel, letter.thread_id)
    assert link is not None
    await session.delete(link)
    await session.flush()


async def test_a_sales_letter_without_its_lead_is_refused_alone_and_the_batch_goes_on(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо продаж, у которого нет лида, — отказ этому письму словами: пачка его считает и
    отправляет остальные."""
    jane, _ = await _queued(session, world, JANE, OLGA)
    await _unlink(session, jane)
    source = _transports()

    report = await send_queue(session, source, stage=Stage.SALES)

    assert (report.sent, sum(report.refused.values()), report.stopped) == (1, 1, None)
    assert [outgoing.to for outgoing in _seen(source)] == [OLGA]


async def test_a_followup_in_a_thread_without_a_sales_lead_waits_and_is_no_module_failure(
    session: AsyncSession, world: w.World, caplog: pytest.LogCaptureFixture
) -> None:
    source, [first] = await _first_sent(session, world)
    await _unlink(session, first)

    with caplog.at_level(logging.INFO):
        report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert (report.sent, report.postponed) == (0, 1)
    assert "у переписки нет лида продаж" in caplog.text
    assert "ошибка модуля продаж" not in caplog.text
