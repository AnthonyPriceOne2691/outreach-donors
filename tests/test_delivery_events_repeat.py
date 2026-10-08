"""Повтор пачки событий доставки не удваивает журнал здоровья ящика (ревью стыков, B1).

Платформа доставляет события «хотя бы один раз» и шлёт пачку снова на любой не-2xx и
таймаут. Статус письма, стоп-лист и парковка идемпотентны монотонностью, а журнал здоровья —
строка на сигнал: повтор удваивал его, и ящик продаж терял лимит раньше времени. Номер
события у платформы (`sg_event_id`) отсеивает повтор: проверка «уже в журнале» — до записи,
гонку двух доставок держит уникальный индекс в точке сохранения.

Условия ревью — по тесту на каждое, номер условия в имени теста:
1. номер необязателен: без него (или с негодным) событие идёт как раньше, пачка принята;
2. проверка «уже в журнале» стоит до записи: повтор не пробует вставить строку;
3. гонка двух доставок одной пачки — «слышано» в точке сохранения, а не 500 на всю пачку;
4. та же пачка дважды — строка на событие, лимит срезан один раз; без номера — как раньше;
5. доноры: их ящики выходят из `listen` раньше журнала, пачка доноров — как раньше.

Модуль продаж и подпись платформы — подставные, сети нет; база настоящая, гонка — на двух
настоящих соединениях со встречей перед записью (как `tests/test_send_race.py`).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.api import deps
from backend.api.app import create_app
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel, SenderHealthModel, SenderModel
from backend.features.core.stages import MailPolicy
from backend.features.letters.events import DeliveryEvent
from backend.features.outreach import health
from backend.features.outreach.health import Heard, SoftSignals
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import Connection, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_delivery_events import _keypair, _sign
from tests.test_sales_model import ROOT, _migration
from tests.test_sales_soft_signals import NOW, _box, _journal
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_send_race import MEETING, committed_sessions

URL = "/api/events/delivery"
#: Ключ `sg_event_id` в событии отсутствует (а не `null`).
MISSING = object()
REVISION = ROOT / "backend/migrations/versions/260e2efdd0c6_sender_health_event_id.py"


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Подпись платформы — своей парой ключей; модуль продаж подключён, пороги по умолчанию."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(soft=SoftSignals(), watch=True))
    stages.register_sales(lambda: found)
    private, public = _keypair()
    monkeypatch.setattr("backend.config.outreach.EVENTS_PUBLIC_KEY", public)
    return private


def _event(kind: str, letter: int, number: object = MISSING, **extra: Any) -> dict[str, Any]:
    """Событие платформы о письме `letter`: время — `NOW`, номер — как дан."""
    row = {"event": kind, "message_id": str(letter), "timestamp": int(NOW.timestamp()), **extra}
    return row if number is MISSING else {**row, "sg_event_id": number}


async def _deliver(client: AsyncClient, private: Any, batch: list[dict[str, Any]]) -> Response:
    """Пачка событий с подписью платформы."""
    payload = json.dumps(batch).encode()
    stamp = str(int(time.time()))
    headers = {
        "X-Twilio-Email-Event-Webhook-Signature": _sign(private, payload, stamp),
        "X-Twilio-Email-Event-Webhook-Timestamp": stamp,
        "Content-Type": "application/json",
    }
    return await client.post(URL, content=payload, headers=headers)


async def _letter(session: AsyncSession, box: SenderModel) -> int:
    """Последнее письмо ящика: события — о нём."""
    found = await session.scalar(
        select(MessageModel.id)
        .where(MessageModel.sender_id == box.id)
        .order_by(MessageModel.sent_at.desc())
        .limit(1)
    )
    assert found is not None
    return int(found)


async def _rows(session: AsyncSession, box_id: int) -> list[tuple[str, str | None]]:
    """Журнал ящика: вид строки и номер события."""
    found = await session.execute(
        select(SenderHealthModel.kind, SenderHealthModel.event_id)
        .where(SenderHealthModel.sender_id == box_id)
        .order_by(SenderHealthModel.id)
    )
    return [(kind, number) for kind, number in found.all()]


# --- 1: номер необязателен ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        (MISSING, None),
        (None, None),
        ("", None),
        ("   ", None),
        (4817, None),
        ({"id": "sg"}, None),
        ("x" * 101, None),
        (" sg-1-padded ", "sg-1-padded"),
        ("y" * 100, "y" * 100),
    ],
)
async def test_1_event_number_is_optional_and_an_odd_one_fails_nothing(
    client: AsyncClient, session: AsyncSession, key: Any, raw: object, kept: str | None
) -> None:
    """Нет номера, он не строка, пустой или длиннее колонки журнала — событие идёт как раньше,
    без номера: пачка принята, строка сигнала записана."""
    box = await _box(session, Stage.SALES, total=1)

    answer = await _deliver(client, key, [_event("deferred", await _letter(session, box), raw)])

    assert (answer.status_code, answer.json()["accepted"]) == (200, True), answer.text
    assert await _rows(session, box.id) == [("deferred", kept)]


# --- 2: проверка «уже в журнале» — до записи ------------------------------------------------


@contextmanager
def _journal_inserts(session: AsyncSession) -> Iterator[list[str]]:
    """Запросы `INSERT INTO sender_health`, ушедшие в базу соединением теста."""
    tried: list[str] = []

    def spy(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO SENDER_HEALTH"):
            tried.append(statement)

    target = session.bind.sync_connection  # type: ignore[union-attr]
    event.listen(target, "before_cursor_execute", spy)
    try:
        yield tried
    finally:
        event.remove(target, "before_cursor_execute", spy)


async def test_2_repeat_is_known_before_the_write(
    client: AsyncClient, session: AsyncSession, key: Any
) -> None:
    """Повтор узнаётся проверкой «уже в журнале» до записи: вторая доставка не пробует вставить
    строку. Уникальный индекс — страховка гонки, а не путь каждого повтора."""
    box = await _box(session, Stage.SALES, total=1)
    letter = await _letter(session, box)
    batch = [_event("deferred", letter, "sg-2-a"), _event("blocked", letter, "sg-2-b")]
    first = await _deliver(client, key, batch)

    with _journal_inserts(session) as tried:
        again = await _deliver(client, key, batch)

    assert (first.status_code, again.status_code) == (200, 200), again.text
    assert tried == [], "повтор пробовал записать строку журнала"
    assert await _rows(session, box.id) == [("deferred", "sg-2-a"), ("blocked", "sg-2-b")]


# --- 3: гонка двух доставок одной пачки -----------------------------------------------------


def _meet_after_the_check(monkeypatch: pytest.MonkeyPatch, number: str) -> None:
    """Обе доставки спросили журнал о событии `number` — только теперь пишут: ни одна ещё
    не записала, обе слышат «нет», и гонка воспроизводится каждый раз."""
    barrier = asyncio.Barrier(2)
    known = health._known

    async def check_then_meet(session: AsyncSession, event_id: str | None) -> bool:
        found = await known(session, event_id)
        if event_id == number:
            await asyncio.wait_for(barrier.wait(), timeout=MEETING)
        return found

    monkeypatch.setattr(health, "_known", check_then_meet)


async def _two_boxes(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int, int]:
    """Ящик продаж и ящик доноров, по письму у каждого, — зафиксированы."""
    async with factory() as session:
        sales = await _box(session, Stage.SALES, total=1)
        donors = await _box(session, Stage.DONORS, total=1)
        found = sales.id, await _letter(session, sales), await _letter(session, donors)
        await session.commit()
        return found


async def test_3_two_deliveries_of_one_batch_at_once_answer_200_and_write_once(
    key: Any, jwt_secret: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Платформа не дождалась 200 и прислала ту же пачку, пока первая доставка шла. Обе прошли
    проверку «уже в журнале» — чужой незафиксированной строки не видно, — и вставка проигравшей
    падает на уникальном индексе. Её откатывает своя точка сохранения: событие слышано, пачка
    идёт дальше (события продаж за ним и событие донора), ответ — 200, а не 500 на всю пачку."""
    async with committed_sessions() as factory:
        box, letter, donor_letter = await _two_boxes(factory)
        _meet_after_the_check(monkeypatch, "sg-3-1")
        batch = [_event("deferred", letter, f"sg-3-{n}") for n in (1, 2, 3)]
        batch.append(_event("bounce", donor_letter, "sg-3-donor"))

        async def per_request() -> AsyncIterator[AsyncSession]:
            async with factory() as session:
                yield session

        app = create_app()
        app.dependency_overrides[deps.db_session] = per_request
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            answers = await asyncio.gather(*(_deliver(client, key, batch) for _ in range(2)))

        assert [answer.status_code for answer in answers] == [200, 200], [a.text for a in answers]
        assert sorted(answer.json()["bounced"] for answer in answers) == [0, 1]
        async with factory() as session:
            assert await _rows(session, box) == [
                *(("deferred", f"sg-3-{n}") for n in (1, 2, 3)),
                ("limit_cut", None),
            ]
            donor = await session.get(MessageModel, donor_letter)
            assert donor is not None
            assert donor.status is MessageStatus.BOUNCED


async def test_3_only_the_event_index_counts_as_heard(
    session: AsyncSession, key: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Точка сохранения прощает только уникальный индекс номера события. Другой отказ базы
    (здесь ящик удалён, пока событие шло) — исключением, как до правки, а не «слышано»."""
    box = await _box(session, Stage.SALES, total=0)
    known = health._known

    async def box_gone(s: AsyncSession, event_id: str | None) -> bool:
        await s.execute(text("DELETE FROM senders WHERE id = :id"), {"id": box.id})
        return await known(s, event_id)

    monkeypatch.setattr(health, "_known", box_gone)

    with pytest.raises(IntegrityError, match="sender_id"):
        await health.listen(
            session,
            box.id,
            DeliveryEvent("deferred", None, "x@to.example.test", event_id="sg-3x"),
            NOW,
        )


# --- 4: та же пачка дважды ------------------------------------------------------------------


async def test_4_same_batch_twice_writes_each_event_once_and_cuts_the_limit_once(
    client: AsyncClient, session: AsyncSession, key: Any
) -> None:
    box = await _box(session, Stage.SALES, total=1)
    letter = await _letter(session, box)
    batch = [_event("deferred", letter, f"sg-4-{n}") for n in (1, 2)]
    batch.append(_event("bounce", letter, "sg-4-3", type="blocked"))

    for _ in range(2):
        answer = await _deliver(client, key, batch)
        assert answer.status_code == 200, answer.text

    assert await _journal(session, box) == ["deferred", "deferred", "blocked", "limit_cut"]
    once = int(box.daily_cap * SoftSignals().cut)
    assert await health.cuts(session, Stage.SALES, NOW) == {box.id: once}
    assert [number for _, number in await _rows(session, box.id)] == [
        "sg-4-1",
        "sg-4-2",
        "sg-4-3",
        None,  # снижение лимита — производная строка, номера события у неё нет
    ]


async def test_4_without_numbers_a_repeat_goes_as_before(
    client: AsyncClient, session: AsyncSession, key: Any
) -> None:
    """Без номера отсеять повтор нечем — как до правки: строки повтора пишутся снова."""
    box = await _box(session, Stage.SALES, total=1)
    letter = await _letter(session, box)

    for _ in range(2):
        answer = await _deliver(client, key, [_event("deferred", letter)] * 2)
        assert answer.status_code == 200, answer.text

    assert await _journal(session, box) == ["deferred"] * 3 + ["limit_cut", "deferred"]


# --- 5: доноры ------------------------------------------------------------------------------


async def test_5_donor_box_leaves_listen_before_the_journal(
    session: AsyncSession, key: Any
) -> None:
    """Ящик доноров выходит из `listen` с `ruled=False` раньше журнала — даже с номером, который
    журнал знает: его судит прежнее правило парковки, как судило."""
    sales = await _box(session, Stage.SALES, total=1)
    donors = await _box(session, Stage.DONORS, total=1)
    session.add(SenderHealthModel(sender_id=sales.id, kind="deferred", at=NOW, event_id="sg-5"))
    await session.flush()
    bounce = DeliveryEvent("bounce", await _letter(session, donors), "x@y.z", event_id="sg-5")

    assert await health.listen(session, donors.id, bounce, NOW) == Heard(ruled=False)


@pytest.mark.parametrize("numbered", [True, False])
async def test_5_donor_batch_twice_goes_the_old_way(
    client: AsyncClient, session: AsyncSession, key: Any, numbered: bool
) -> None:
    """С номерами и без — одно и то же: журнала у доноров нет, парковка — прежним правилом по
    доле отказов за всю жизнь ящика (4 из 60 — пауза с первой доставки)."""
    box = await _box(session, Stage.DONORS, total=60, bounced=3)
    letter = await _letter(session, box)
    numbers = ("sg-5-1", "sg-5-2") if numbered else (MISSING, MISSING)
    batch = [_event("bounce", letter, numbers[0]), _event("deferred", letter, numbers[1])]

    answers = [await _deliver(client, key, batch) for _ in range(2)]

    assert [answer.status_code for answer in answers] == [200, 200]
    said = [(answer.json()["bounced"], answer.json()["paused"]) for answer in answers]
    assert said == [(1, [box.email]), (0, [])]
    assert await _journal(session, box) == []


# --- ревизия --------------------------------------------------------------------------------


def _revision_cycle(connection: Connection) -> tuple[object, object]:
    """Ревизия ещё раз, в процессе: подъём сьюта идёт подпроцессом, покрытие его не видит."""
    migration = _migration(REVISION)
    there = text(
        "SELECT to_regclass('uq_sender_health_event') IS NOT NULL AND EXISTS (SELECT 1 FROM "
        "information_schema.columns WHERE table_name = 'sender_health' AND column_name = 'event_id')"
    )
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        gone = connection.scalar(there)
        migration.upgrade()
    return gone, connection.scalar(there)


async def test_the_event_number_revision_goes_down_and_up(session: AsyncSession) -> None:
    assert await (await session.connection()).run_sync(_revision_cycle) == (False, True)
