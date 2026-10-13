"""Разгон ящика — от первого письма после старта, а не от самого старта (ревью стыков с продажами).

Ступень дневного потолка считалась от старта разгона (`warmup_started_at`), а его ставят
заведение ящика и «Включить заново»: ящик, заведённый за неделю до первого письма, начинал
сразу с верхней ступени. Теперь ступень — от первого письма ящика после старта
(`warmup_first_sent_at`). Его ставит запись «ушло» (`letters/settle.py`, одна на все пути
письма) условным `UPDATE`: один раз за разгон и только письмом, ушедшим не раньше старта.
«Включить заново» отметку снимает. Ревизия заполняет отметку по уже ушедшим письмам.

База настоящая, почта — нулевой транспорт.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from backend.features.letters import batch
from backend.features.letters.transport import NullTransport
from backend.features.outreach import senders
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender
from tests.migration_helpers import load_migration
from tests.test_letters_queue import _build

#: Ступени разгона — выдуманные: тест не зависит от настроек окружения.
STEPS = (3, 7, 11, 20)
REVISION = "07bcb0eef6a1_sender_warmup_first_letter.py"
#: Старт разгона — некруглый, не дата-обязательство.
START = datetime(2026, 10, 1, 9, 17, 43, tzinfo=UTC)
LIMIT_SPENT = (
    "Сегодня писать некому: все ящики либо выключены, либо выбрали дневной лимит. "
    "Письмо остаётся в очереди — завтра лимит откроется заново"
)


@pytest.fixture(autouse=True)
def steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(senders, "WARMUP_STEPS", STEPS)
    monkeypatch.setattr(senders, "WARMUP_STEP_DAYS", 2)


async def _box(session: AsyncSession, email: str, *, started: datetime | None) -> SenderModel:
    """Ящик со стартом разгона `started`, прочитанный из базы, — как его видит отправка."""
    box = await make_sender(session, email)
    box.warmup_started_at = started
    await session.flush()
    await session.refresh(box)
    return box


async def _mark(session: AsyncSession, box: SenderModel) -> datetime | None:
    """Отметка ящика из базы, а не из сессии."""
    found: datetime | None = await session.scalar(
        text("SELECT warmup_first_sent_at FROM senders WHERE id = :id"), {"id": box.id}
    )
    return found


async def test_a_box_added_a_week_before_its_first_letter_sends_the_first_step(
    session: AsyncSession, filled_legal: None
) -> None:
    """Пачка с ящика, заведённого неделю назад и ни разу не писавшего: первая ступень, а не
    верхняя. Первое письмо ставит отметку — время его ухода; слова отказа — прежние: ящик
    включён и выбрал лимит, завтра он откроется."""
    for n in range(5):
        await make_donor(session, f"donor{n}.example.test", email=f"info@donor{n}.example.test")
    box = await _box(session, "a@mail.example.test", started=datetime.now(UTC) - timedelta(days=7))
    await _build(session)
    await session.commit()

    report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

    assert (report.sent, report.left, report.stopped) == (STEPS[0], 5 - STEPS[0], LIMIT_SPENT)
    first = await session.scalar(
        select(func.min(MessageModel.sent_at)).where(MessageModel.sender_id == box.id)
    )
    assert first is not None
    assert await _mark(session, box) == first
    assert senders.warmup_state(box).day == 1


async def test_the_mark_is_the_first_letter_after_the_start_and_only_it(
    session: AsyncSession,
) -> None:
    """Письмо, отданное почте до старта (исход неизвестен, записан позже), разгона не
    начинает; следующие письма отметку не двигают; у ящика без разгона её нет; отметка
    прежнего разгона (раньше старта) уступает первому письму нового."""
    box = await _box(session, "a@mail-a.example.test", started=START)
    finished = await _box(session, "b@mail-b.example.test", started=None)
    restarted = await _box(session, "c@mail-c.example.test", started=START)
    restarted.warmup_first_sent_at = START - timedelta(days=30)
    await session.flush()

    await senders.mark_first_letter(session, box.id, moment=START - timedelta(minutes=1))
    before_the_start = await _mark(session, box)
    for later in (timedelta(minutes=5), timedelta(hours=2)):
        await senders.mark_first_letter(session, box.id, moment=START + later)
    await senders.mark_first_letter(session, finished.id, moment=START)
    await senders.mark_first_letter(session, restarted.id, moment=START + timedelta(hours=1))
    await senders.mark_first_letter(session, None, moment=START)  # ящик письма удалён

    assert before_the_start is None
    assert [await _mark(session, one) for one in (box, finished, restarted)] == [
        START + timedelta(minutes=5),
        None,
        START + timedelta(hours=1),
    ]
    # Сессия видит то же, что база: разгон следующего письма считается по её объекту.
    assert box.warmup_first_sent_at == START + timedelta(minutes=5)


async def test_enabling_again_starts_the_steps_from_the_next_letter(session: AsyncSession) -> None:
    """«Включить заново» на десятый день: отметка снята — первая ступень, пока ящик не
    напишет; дни разгона считаются от его первого письма после включения."""
    box = await _box(session, "a@mail.example.test", started=START)
    box.warmup_first_sent_at = START
    switched_on = START + timedelta(days=9)

    senders.enable(box, now=switched_on)
    await session.flush()
    idle = senders.warmup_state(box, now=switched_on + timedelta(days=3))
    await senders.mark_first_letter(session, box.id, moment=switched_on + timedelta(days=4))
    writing = senders.warmup_state(box, now=switched_on + timedelta(days=6))

    assert (idle.day, idle.allowance) == (1, STEPS[0])
    assert await _mark(session, box) == switched_on + timedelta(days=4)
    assert (writing.day, writing.allowance) == (3, STEPS[1])


# --- ревизия ---------------------------------------------------------------------------------


def _revision(connection: Connection, *steps: str) -> None:
    """Шаги ревизии на соединении теста — в его транзакции."""
    migration = load_migration(REVISION)
    with Operations.context(MigrationContext.configure(connection)):
        for step in steps:
            getattr(migration, step)()


async def _sent(session: AsyncSession, box: SenderModel, host: str, at: datetime) -> None:
    """Ушедшее с ящика `box` в минуту `at` первое письмо сайту `host`."""
    campaign = CampaignModel(stage=Stage.DONORS, name=f"Ревизия {host}", status="running")
    domain = DomainModel(host=host)
    session.add_all([campaign, domain])
    await session.flush()
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            domain_id=domain.id,
            sender_id=box.id,
            step=0,
            status=MessageStatus.SENT,
            sent_at=at,
            idempotency_key=f"test:{host}",
        )
    )
    await session.flush()


async def _column(session: AsyncSession) -> bool:
    found = await session.scalar(
        text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'senders' AND column_name = 'warmup_first_sent_at'"
        )
    )
    return bool(found)


async def test_the_revision_marks_each_box_by_its_letters_and_goes_down_and_up(
    session: AsyncSession,
) -> None:
    """Писал после старта — первое такое письмо (а не письмо до старта); после старта не
    писал — пусто, первая ступень; разгона нет — пусто, как было."""
    writing = await _box(session, "a@mail-a.example.test", started=START)
    silent = await _box(session, "b@mail-b.example.test", started=START)
    finished = await _box(session, "c@mail-c.example.test", started=None)
    await _sent(session, writing, "w1.example.test", START - timedelta(days=1))
    await _sent(session, writing, "w2.example.test", START + timedelta(days=3))
    await _sent(session, writing, "w3.example.test", START + timedelta(days=2, minutes=7))
    await _sent(session, silent, "s1.example.test", START - timedelta(hours=1))
    await _sent(session, finished, "f1.example.test", START)
    await session.commit()
    connection = await session.connection()

    await connection.run_sync(_revision, "downgrade")
    after_downgrade = await _column(session)
    await connection.run_sync(_revision, "upgrade")

    assert (after_downgrade, await _column(session)) == (False, True)
    assert [await _mark(session, box) for box in (writing, silent, finished)] == [
        START + timedelta(days=2, minutes=7),
        None,
        None,
    ]
