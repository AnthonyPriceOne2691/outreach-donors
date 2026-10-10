"""Список диалогов и числа меню — без текстов писем и ответов (аудит 10.10.2026).

Список «Диалогов» и числа «Обзора» и меню грузили переписки целиком: тела писем,
тексты ответов с адресами и снимками разбора. На тысячах диалогов продаж это сотни
мегабайт в процессе сервера на каждый показ, а числа меню спрашиваются раз в минуту
с каждой открытой вкладки. Теперь из базы приходит то, что читает правило состояния.

Проверяется с двух сторон. На мире, где есть каждое состояние диалога каждого этапа,
строка списка совпадает с шапкой карточки — карточка читает переписку целиком тем же
правилом, — а числа совпадают и с карточками, и с выписанными руками. И запросы списка
и чисел не берут ни тел, ни адресов: только поля правила и начало текста там, где
правило его читает.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.api.threads.schemas import ThreadCard
from backend.features.core.domain import (
    ContactSource,
    MessageStatus,
    ReplyKind,
    Stage,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.ops.overview import WAITS_FOR_PERSON, overview, work
from backend.features.outreach.repository import (
    EVERY_STAGE,
    OutreachRepository,
    ThreadMark,
    ThreadRow,
)
from backend.features.outreach.threads import ThreadState
from backend.features.replies.inbound import MAX_TEXT_CHARS
from backend.features.replies.outcome import names_a_sum
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

#: Автоответ с ценой: сумму в нём ищет правило — по тексту.
PRICED_AUTO = "I am out of the office until Monday. Our sponsored post rate is $150."
_TAIL = " Our rate is 150 USD"
_PAD = "Я в отпуске до понедельника. "
_BEFORE = (_PAD * (MAX_TEXT_CHARS // len(_PAD) + 1))[: MAX_TEXT_CHARS - len(_TAIL)]
#: Сумма кончается ровно на последнем разбираемом знаке (`inbound.MAX_TEXT_CHARS`), а текст
#: до неё — кириллицей, по два байта на знак: начало, отрезанное на знак раньше или по
#: байтам, суммы бы не увидело.
EDGE_SUM = f"{_BEFORE}{_TAIL}\nХорошего дня."
#: Сумма за границей разбираемого: её правило не видит ни в тексте целиком, ни в начале.
FAR_SUM = "I am out of the office this week. " * 700 + "Our sponsored post rate is $150."
#: Ответ человека в сто пятьдесят тысяч знаков — его текст правилу не нужен.
LONG = "Thank you, we can publish it next week. " * 3750

#: Вид разобран модулем продаж, человек не нужен (`outcome.sales_review`).
SALES_SETTLED = {
    "stage": "sales",
    "kind": "not_interested",
    "route": "closed",
    "waits": False,
    "reason": "не интересно — диалог закрыт",
}
#: Вид разобран, но путь — человек: ответ ждёт.
SALES_WAITS = {
    "stage": "sales",
    "kind": "question",
    "route": "agent",
    "waits": True,
    "reason": "задал вопрос: ответит агент; пока — человек",
}
#: Модуль продаж закрыл адрес по словам лида (`outcome.sales_closed_address`).
SALES_CLOSED = {
    "stage": "sales",
    "kind": "unsubscribe",
    "route": "unsubscribe",
    "waits": False,
    "reason": "просит не писать — адрес закрыт",
}

#: Снимок разбора цены у ответа донора — правилу состояния он не нужен.
DONOR_PARSE = {"prompt_version": "test", "notes": ["цена зависит от темы"], "offers": []}

HUMAN, AUTO = ReplyKind.HUMAN, ReplyKind.AUTO_REPLY
DELIVERED = (MessageStatus.DELIVERED,)
SURE = {"kind": HUMAN, "confidence": 0.95}


@dataclass(frozen=True)
class Dialog:
    """Диалог мира: этап, письма, ответы и состояние, которого от него ждут."""

    stage: Stage
    letters: tuple[MessageStatus, ...]
    replies: tuple[dict[str, Any], ...]
    state: ThreadState
    #: Чей диалог; пусто — свой домен по имени диалога.
    host: str | None = None
    #: Решение по домену: диалоги не принятого донора в «ответили» не входят.
    review: str | None = "accepted"


WORLD: dict[str, Dialog] = {
    "queued": Dialog(Stage.DONORS, (MessageStatus.QUEUED,), (), ThreadState.QUEUED),
    "waiting": Dialog(
        Stage.DONORS, (MessageStatus.DELIVERED, MessageStatus.QUEUED), (), ThreadState.WAITING
    ),
    "bounced": Dialog(
        Stage.DONORS, (MessageStatus.BOUNCED,), ({"kind": ReplyKind.BOUNCE},), ThreadState.BOUNCED
    ),
    "stopped": Dialog(
        Stage.DONORS, (MessageStatus.SENT, MessageStatus.STOPPED), (), ThreadState.STOPPED
    ),
    "replied": Dialog(Stage.DONORS, DELIVERED, (SURE,), ThreadState.REPLIED),
    "unsure": Dialog(
        Stage.DONORS,
        DELIVERED,
        (
            {
                "kind": HUMAN,
                "confidence": 0.4,
                "price_white": Decimal(300),
                "currency": "USD",
                "model_parse": DONOR_PARSE,
            },
        ),
        ThreadState.NEEDS_REVIEW,
    ),
    "auto-sum": Dialog(
        Stage.DONORS,
        DELIVERED,
        ({"kind": AUTO, "raw_body": PRICED_AUTO},),
        ThreadState.NEEDS_REVIEW,
    ),
    "auto-sum-at-edge": Dialog(
        Stage.DONORS, DELIVERED, ({"kind": AUTO, "raw_body": EDGE_SUM},), ThreadState.NEEDS_REVIEW
    ),
    "auto-sum-too-far": Dialog(
        Stage.DONORS, DELIVERED, ({"kind": AUTO, "raw_body": FAR_SUM},), ThreadState.WAITING
    ),
    "priced": Dialog(
        Stage.DONORS,
        DELIVERED,
        (
            {"kind": AUTO, "raw_body": "On leave."},
            {**SURE, "price_white": Decimal(250), "currency": "EUR", "raw_body": LONG},
        ),
        ThreadState.PRICED,
    ),
    # Второй адрес того же донора: «ответили» считает донора, а не адрес.
    "priced-other-address": Dialog(
        Stage.DONORS,
        DELIVERED,
        ({**SURE, "price_white": Decimal(260), "price_grey": Decimal(200), "currency": "EUR"},),
        ThreadState.PRICED,
        host="priced.example.test",
    ),
    "priced-auto-confirmed": Dialog(
        Stage.DONORS,
        DELIVERED,
        (
            {
                "kind": AUTO,
                "raw_body": PRICED_AUTO,
                "price_white": Decimal(150),
                "currency": "USD",
                "reviewed_at": NOW,
            },
        ),
        ThreadState.PRICED,
    ),
    "priced-rejected-donor": Dialog(
        Stage.DONORS,
        DELIVERED,
        ({**SURE, "price_white": Decimal(90), "currency": "USD"},),
        ThreadState.PRICED,
        review="rejected",
    ),
    "declined": Dialog(
        Stage.DONORS, DELIVERED, ({**SURE, "placement": "declines"},), ThreadState.DECLINED
    ),
    "free": Dialog(Stage.DONORS, DELIVERED, ({**SURE, "placement": "free"},), ThreadState.FREE),
    "unsubscribed": Dialog(
        Stage.DONORS, DELIVERED, ({"kind": ReplyKind.UNSUBSCRIBE},), ThreadState.UNSUBSCRIBED
    ),
    "lead": Dialog(Stage.ADVERTISERS, DELIVERED, ({"kind": HUMAN},), ThreadState.LEAD),
    "lead-taken": Dialog(
        Stage.ADVERTISERS, DELIVERED, ({"kind": HUMAN, "reviewed_at": NOW},), ThreadState.LEAD_TAKEN
    ),
    # Сумма в автоответе рекламодателя — его расход, а не цена: человека не ждёт.
    "advertiser-auto-sum": Dialog(
        Stage.ADVERTISERS,
        DELIVERED,
        ({"kind": AUTO, "raw_body": PRICED_AUTO},),
        ThreadState.WAITING,
    ),
    "sales-unsorted": Dialog(Stage.SALES, DELIVERED, ({"kind": HUMAN},), ThreadState.SALES_PENDING),
    "sales-waits": Dialog(
        Stage.SALES,
        DELIVERED,
        ({"kind": HUMAN, "model_parse": SALES_WAITS},),
        ThreadState.SALES_PENDING,
    ),
    "sales-settled": Dialog(
        Stage.SALES,
        DELIVERED,
        ({"kind": HUMAN, "model_parse": SALES_SETTLED},),
        ThreadState.REPLIED,
    ),
    "sales-closed": Dialog(
        Stage.SALES,
        DELIVERED,
        ({"kind": HUMAN, "model_parse": SALES_CLOSED},),
        ThreadState.UNSUBSCRIBED,
    ),
    "sales-away": Dialog(
        Stage.SALES, DELIVERED, ({"kind": AUTO, "raw_body": PRICED_AUTO},), ThreadState.WAITING
    ),
}

#: «Ждут человека»: цены — `unsure`, `auto-sum`, `auto-sum-at-edge`; лид — `lead`;
#: продажи — `sales-unsorted`, `sales-waits`.
WAITING_PRICES, WAITING_LEADS, WAITING_ALL = 3, 1, 6
#: Ответившие доноры: `replied`, `unsure`, `auto-sum`, `auto-sum-at-edge`,
#: `priced-auto-confirmed`, `priced` — двумя адресами, один раз, — `declined`, `free`.
#: Отклонённый домен — не донор и в счёт не входит.
REPLIED_DONORS = 8


async def _world(session: AsyncSession) -> dict[str, int]:
    """Завести мир и ответ без диалога. Номера диалогов — по именам."""
    campaigns = {stage: CampaignModel(stage=stage, name=f"Мир {stage.value}") for stage in Stage}
    session.add_all(campaigns.values())
    domains: dict[str, DomainModel] = {}
    ids: dict[str, int] = {}
    for name, dialog in WORLD.items():
        host = dialog.host or f"{name}.example.test"
        if host not in domains:
            domains[host] = await make_donor(session, host, review=dialog.review)
        domain, campaign = domains[host], campaigns[dialog.stage]
        thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
        session.add(thread)
        await session.flush()
        ids[name] = thread.id
        for step, status in enumerate(dialog.letters):
            session.add(
                MessageModel(
                    campaign_id=campaign.id,
                    thread_id=thread.id,
                    domain_id=domain.id,
                    step=step,
                    status=status,
                    subject="Advertising rates",
                    body=f"Good afternoon! {LONG}",
                    sent_at=None if status is MessageStatus.QUEUED else NOW - timedelta(days=step),
                    idempotency_key=f"lean:{name}:{step}",
                )
            )
        for number, reply in enumerate(dialog.replies):
            session.add(
                ReplyModel(
                    **{"raw_body": "Hello.", **reply},
                    thread_id=thread.id,
                    from_email=f"editor@{host}",
                    subject="Re: Advertising rates",
                    to_addresses=["anna@mail.example.test"],
                    created_at=NOW - timedelta(hours=len(dialog.replies) - number),
                )
            )
    # С адресом — один диалог: строка списка несёт адрес собеседника.
    contact = ContactModel(
        domain_id=domains["replied.example.test"].id,
        email="editor@replied.example.test",
        source=ContactSource.PAGE,
    )
    session.add(contact)
    await session.flush()
    replied = await session.get(ThreadModel, ids["replied"])
    assert replied is not None
    replied.contact_id = contact.id
    # Ответ, не привязанный ни к одному письму, — не диалог и в счёт диалогов не входит.
    session.add(ReplyModel(kind=HUMAN, raw_body="Who is this?", unbound_reason="no_label"))
    await session.flush()
    return ids


async def _cards(session: AsyncSession, ids: dict[str, int]) -> dict[int, ThreadRow]:
    """Шапки карточек: карточка читает переписку целиком — письма и ответы моделями —
    и сводит её тем же правилом. Так список и числа считались до 10.10.2026."""
    repository = OutreachRepository(session)
    return {number: (await repository.thread(number)).row for number in ids.values()}


@contextmanager
def _statements(session: AsyncSession) -> Iterator[list[str]]:
    """Запросы, ушедшие в базу соединением теста."""
    sent: list[str] = []

    def spy(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        sent.append(statement)

    target = session.bind.sync_connection  # type: ignore[union-attr]
    event.listen(target, "before_cursor_execute", spy)
    try:
        yield sent
    finally:
        event.remove(target, "before_cursor_execute", spy)


_COLUMN = re.compile(r"\b(messages|replies)\.(\w+)")


def _columns(statements: list[str]) -> dict[str, set[str]]:
    """Какие колонки писем и ответов называют запросы. Начало текста у автоответа
    донору (`left(replies.raw_body, …)`) и снимок у ответа продаж (`THEN
    replies.model_parse`) не в счёт: их правило читает, и только у этих ответов."""
    named: dict[str, set[str]] = {"messages": set(), "replies": set()}
    for statement in statements:
        rest = statement.replace("left(replies.raw_body,", "").replace(
            "THEN replies.model_parse", ""
        )
        for table, column in _COLUMN.findall(rest):
            named[table].add(column)
    return named


#: Что списку и числам берут у письма и ответа: поля правила состояния
#: (`threads.LetterFacts`, `threads.ReplyFacts`) и номер диалога.
LETTER_FIELDS = {"thread_id", "status", "sent_at"}
REPLY_FIELDS = {
    "thread_id",
    "id",
    "kind",
    "created_at",
    "confidence",
    "reviewed_at",
    "price_white",
    "price_grey",
    "currency",
    "placement",
}
#: Тексты и адреса — то, что весит. Ни списку, ни числам «Обзора» они не нужны.
TEXTS = {
    "subject",
    "body",
    "raw_body",
    "from_email",
    "to_addresses",
    "offers",
    "payment_methods",
    "model_parse",
}


def test_world_holds_every_state_and_the_edges_hold() -> None:
    """Мир покрывает каждое состояние, а суммы у границы разбираемого стоят там,
    где правило их видит и не видит."""
    assert {dialog.state for dialog in WORLD.values()} == set(ThreadState)
    assert len(EDGE_SUM) > MAX_TEXT_CHARS
    assert names_a_sum(EDGE_SUM)
    assert not names_a_sum(EDGE_SUM[: MAX_TEXT_CHARS - 1])
    assert len(EDGE_SUM[:MAX_TEXT_CHARS].encode()) > MAX_TEXT_CHARS
    assert not names_a_sum(FAR_SUM)
    assert names_a_sum(FAR_SUM[MAX_TEXT_CHARS:])


async def test_list_row_is_the_card_head(session: AsyncSession) -> None:
    """Строка списка — та же, что шапка карточки: адрес, рассылка, этап, состояние,
    счёт писем, время и цена. И состояние — то, которого ждёт мир."""
    ids = await _world(session)

    listed = await OutreachRepository(session).threads(stages=EVERY_STAGE)
    cards = await _cards(session, ids)

    assert [row.thread.id for row in listed] == sorted(ids.values(), reverse=True)
    for row in listed:
        card = cards[row.thread.id]
        assert (row.host, row.contact_email, row.campaign_name, row.stage, row.summary) == (
            card.host,
            card.contact_email,
            card.campaign_name,
            card.stage,
            card.summary,
        ), row.host
    assert {row.thread.id: row.summary.state for row in listed} == {
        ids[name]: dialog.state for name, dialog in WORLD.items()
    }


async def test_counted_state_is_the_card_state(session: AsyncSession) -> None:
    ids = await _world(session)

    marks = await OutreachRepository(session).states(stages=EVERY_STAGE)
    cards = await _cards(session, ids)

    assert len(marks) == len(ids)
    assert {mark.thread_id: mark for mark in marks} == {
        number: ThreadMark(
            thread_id=number,
            domain_id=card.thread.domain_id,
            stage=card.stage,
            state=card.summary.state,
        )
        for number, card in cards.items()
    }


async def test_menu_and_overview_count_what_the_cards_say(session: AsyncSession) -> None:
    ids = await _world(session)
    cards = await _cards(session, ids)

    menu = await work(session, stages=EVERY_STAGE)
    view = await overview(session, now=NOW)

    states = [card.summary.state for card in cards.values()]
    assert menu.threads == sum(state in WAITS_FOR_PERSON for state in states) == WAITING_ALL
    assert view.waiting.prices == states.count(ThreadState.NEEDS_REVIEW) == WAITING_PRICES
    assert view.waiting.leads == states.count(ThreadState.LEAD) == WAITING_LEADS
    assert view.donors.replied == REPLIED_DONORS
    assert view.unbound_replies == 1


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


async def test_screens_get_the_same_answers(
    session: AsyncSession, client: AsyncClient, operator_token: str
) -> None:
    """По HTTP: строка списка — та же, что шапка карточки, полями прежней формы;
    число меню — то же, что «Ждут человека»."""
    await _world(session)
    await session.commit()
    headers = bearer(operator_token)

    listed = (await client.get("/api/threads", headers=headers)).json()
    menu = (await client.get("/api/overview/work", headers=headers)).json()

    assert len(listed) == len(WORLD)
    for row in listed:
        assert set(row) == set(ThreadCard.model_fields)
        one = await client.get(f"/api/threads/{row['id']}", headers=headers)
        assert one.json()["card"] == row
    assert menu == {"run": 0, "threads": WAITING_ALL, "forms": 0, "advertisers": 0}


async def test_list_and_counts_take_no_texts(session: AsyncSession) -> None:
    """Запросы списка и чисел берут у писем и ответов только поля правила: ни тем,
    ни тел, ни адресов. И моделей писем и ответов в сессии не остаётся — их не строят."""
    await _world(session)
    session.expunge_all()
    repository = OutreachRepository(session)

    with _statements(session) as sent:
        listed = await repository.threads(stages=EVERY_STAGE)
        await repository.states(stages=EVERY_STAGE)

    assert len(listed) == len(WORLD)
    named = _columns(sent)
    assert named["messages"] <= LETTER_FIELDS, named["messages"] - LETTER_FIELDS
    assert named["replies"] <= REPLY_FIELDS, named["replies"] - REPLY_FIELDS
    # Начало текста и снимок разбора берут оба запроса ответов — списка и чисел —
    # и только в своих формах: иначе проверка выше смотрела бы мимо них.
    assert sum("left(replies.raw_body," in statement for statement in sent) == 2
    assert sum("THEN replies.model_parse" in statement for statement in sent) == 2
    assert not [kept for kept in session.identity_map.values() if isinstance(kept, MessageModel)]
    assert not [kept for kept in session.identity_map.values() if isinstance(kept, ReplyModel)]


async def test_text_and_snapshot_come_only_where_the_rule_reads_them(
    session: AsyncSession,
) -> None:
    """Начало текста — только у автоответа донору и не длиннее разбираемого, отрезанное
    по знакам, а не по байтам; снимок разбора — только у ответа продаж. У остальных
    ответов вместо них пусто: ответ человека в сто пятьдесят тысяч знаков списку
    не стоит ничего."""
    ids = await _world(session)

    replies = await OutreachRepository(session)._listed_replies(stages=EVERY_STAGE)

    def taken(name: str) -> list[tuple[str, dict[str, Any] | None]]:
        return sorted(
            ((reply.raw_body, reply.model_parse) for reply in replies[ids[name]]),
            key=lambda pair: pair[0],
        )

    assert taken("priced") == [("", None), ("On leave.", None)]
    assert taken("auto-sum-at-edge") == [(EDGE_SUM[:MAX_TEXT_CHARS], None)]
    assert taken("auto-sum-too-far") == [(FAR_SUM[:MAX_TEXT_CHARS], None)]
    assert taken("unsure") == [("", None)]
    assert taken("advertiser-auto-sum") == taken("sales-away") == [("", None)]
    assert taken("sales-settled") == [("", SALES_SETTLED)]
    assert taken("sales-unsorted") == [("", None)]


async def test_overview_and_menu_take_no_texts(session: AsyncSession) -> None:
    """Ни один запрос «Обзора» и чисел меню не берёт текстов и адресов писем и ответов."""
    await _world(session)

    with _statements(session) as sent:
        await work(session, stages=EVERY_STAGE)
        await overview(session, now=NOW)

    named = _columns(sent)
    assert not named["messages"] & TEXTS, named["messages"] & TEXTS
    assert not named["replies"] & TEXTS, named["replies"] & TEXTS


# --- без права «Продажи» (решение Anthony 10.10.2026, П2) -----------------------------------

#: Этапы учётки без права «Продажи» (`access.permissions.visible_stages`).
NO_SALES = frozenset({Stage.DONORS, Stage.ADVERTISERS})
#: Ждут человека в продажах: `sales-unsorted`, `sales-waits`.
WAITING_SALES = 2


async def test_without_sales_the_list_and_the_numbers_are_the_rest_of_the_world(
    session: AsyncSession,
) -> None:
    """Сужает база: строки и числа без продаж — ровно те же, что у всех, за вычетом переписок
    продаж, тем же правилом и с теми же состояниями. Письма продаж в сводке не нулями —
    этапа там нет вовсе."""
    await _world(session)
    repository = OutreachRepository(session)

    everyone = await repository.threads(stages=EVERY_STAGE)
    listed = await repository.threads(stages=NO_SALES)
    marks = await repository.states(stages=NO_SALES)
    menu = await work(session, stages=NO_SALES)
    view = await overview(session, now=NOW, stages=NO_SALES)

    rest = [(row.thread.id, row.summary) for row in everyone if row.stage is not Stage.SALES]
    assert [(row.thread.id, row.summary) for row in listed] == rest
    assert {mark.thread_id for mark in marks} == {number for number, _ in rest}
    assert menu.threads == WAITING_ALL - WAITING_SALES
    assert (view.waiting.prices, view.waiting.leads) == (WAITING_PRICES, WAITING_LEADS)
    assert view.donors.replied == REPLIED_DONORS
    assert set(view.letters) == NO_SALES


async def test_without_sales_the_queries_still_take_no_texts(session: AsyncSession) -> None:
    """Сужение — условием в базе, и запросы списка и чисел по-прежнему без тел и адресов.
    Видны все этапы — условия на этап нет вовсе: запросы те же, что до П2."""
    await _world(session)
    session.expunge_all()
    repository = OutreachRepository(session)

    with _statements(session) as narrowed:
        await repository.threads(stages=NO_SALES)
        await repository.states(stages=NO_SALES)
    with _statements(session) as everyone:
        await repository.threads(stages=EVERY_STAGE)
        await repository.states(stages=EVERY_STAGE)

    named = _columns(narrowed)
    assert named["messages"] <= LETTER_FIELDS, named["messages"] - LETTER_FIELDS
    assert named["replies"] <= REPLY_FIELDS, named["replies"] - REPLY_FIELDS
    assert all("campaigns.stage IN" in statement for statement in narrowed)
    assert not any("campaigns.stage IN" in statement for statement in everyone)
