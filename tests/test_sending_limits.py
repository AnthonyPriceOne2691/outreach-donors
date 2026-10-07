"""Лимиты домена и направления — фильтр до выбора ящика (Ф4, срез 4.5a): причина
доходит до итога пачки, счёт — первые письма, как у разгона; у Этапов 1–2 всё как было.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.cli import sending_domains
from backend.config import outreach as cfg
from backend.config import storage
from backend.features.core.domain import MessageStatus, Stage, UserRole
from backend.features.core.models.outreach import MessageModel, SenderModel, SendingDomainModel
from backend.features.letters import batch, followups
from backend.features.letters.transport import NullTransport
from backend.features.outreach import limits
from httpx import AsyncClient
from sqlalchemy import Connection, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN, bearer, make_donor, make_sender
from tests.test_api_outreach import _sent_letters
from tests.test_letters_queue import _build
from tests.test_sales_model import ROOT, _migration
from tests.test_send_race import committed_sessions
from tests.thread_letters import conversation

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _box(box_id: int, domain: str, stage: Stage = Stage.SALES) -> SenderModel:
    return SenderModel(
        id=box_id, domain=domain, email=f"b{box_id}@{domain}", stage=stage, enabled=True
    )


def _row(domain: str, *, stage: Stage = Stage.SALES, **kw: object) -> SendingDomainModel:
    return SendingDomainModel(**{"domain": domain, "stage": stage, "daily_limit": 3, **kw})


def _screen(
    boxes: list[SenderModel], sent: dict[int, int], *rows: SendingDomainModel, **kw: object
) -> limits.Screened:
    """Фильтр продаж в минуту `NOW`; `stage` и `direction_limit` — доводами."""
    options: dict[str, object] = {"stage": Stage.SALES, "direction_limit": None, **kw}
    found = {row.domain: row for row in rows}
    return limits.screen(boxes, sent_today=sent, domains=found, now=NOW, **options)  # type: ignore[arg-type]


def test_a1_domain_limit_is_shared_by_all_its_boxes() -> None:
    """Два ящика на домене с лимитом 3: третий первый — и домен исчерпан для обоих."""
    boxes = [_box(1, "a.example.test"), _box(2, "a.example.test"), _box(3, "b.example.test")]

    before = _screen(boxes, {1: 1, 2: 1}, _row("a.example.test"))
    after = _screen(boxes, {1: 2, 2: 1}, _row("a.example.test"))

    assert [box.id for box in before.fit] == [1, 2, 3]
    assert [box.id for box in after.fit] == [3]
    assert after.refused == dict.fromkeys(
        (1, 2), "домен исчерпан на сегодня: a.example.test — 3 из 3"
    )


def test_direction_limit_stops_every_box_of_the_stage_and_only_it() -> None:
    boxes = [
        _box(1, "a.example.test"),
        _box(2, "b.example.test"),
        _box(3, "c.example.test", Stage.DONORS),
    ]

    sales = _screen(boxes, {1: 2, 2: 1, 3: 9}, direction_limit=3)
    donors = _screen(boxes, {1: 2, 2: 1, 3: 9}, stage=Stage.DONORS)

    assert sales.fit == ()
    assert sales.why() == "у направления кончился дневной лимит (3 из 3 первых писем)"
    assert [box.id for box in donors.fit] == [3]


@pytest.mark.parametrize(
    ("row", "words"),
    [
        (
            _row("a.example.test", paused_at=NOW, pause_reason="жалоба"),
            "на паузе: a.example.test — жалоба",
        ),
        (
            _row("a.example.test", young_until=NOW + timedelta(days=2)),
            "на выдержке: a.example.test — до 09.10 12:00 UTC",
        ),
        (
            _row("a.example.test", stage=Stage.DONORS),
            "записан за другим направлением: a.example.test — donors",
        ),
        (_row("a.example.test", young_until=NOW - timedelta(minutes=1)), None),
    ],
)
def test_paused_young_or_foreign_domain_is_named(
    row: SendingDomainModel, words: str | None
) -> None:
    found = _screen([_box(1, "a.example.test"), _box(2, "b.example.test")], {}, row)

    assert found.refused == ({1: f"домен {words}"} if words else {})
    assert [box.id for box in found.fit] == ([2] if words else [1, 2])


async def _queue(session: AsyncSession, letters: int, boxes: list[str], cap: int = 20) -> None:
    """Очередь доноров и ящики Этапа 1 — на одном домене или на разных."""
    for n in range(letters):
        await make_donor(session, f"donor{n}.example.test", email=f"info@donor{n}.example.test")
    for email in boxes:
        await make_sender(session, email, cap=cap)
    await _build(session)
    await session.commit()


class TestBatch:
    async def test_a1_batch_stops_with_the_domain_in_words(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _queue(session, 5, ["a@mail.example.test", "b@mail.example.test"])
        session.add(_row("mail.example.test", stage=Stage.DONORS))
        # Добивка с ящика домена сегодня лимит не ест: счёт — первые письма, как у разгона.
        first = await session.scalar(select(MessageModel))
        assert first is not None
        followup = {"step": 1, "status": MessageStatus.SENT, "sent_at": datetime.now(UTC)}
        session.add(
            MessageModel(
                **followup,
                campaign_id=first.campaign_id,
                domain_id=first.domain_id,
                sender_id=await session.scalar(select(SenderModel.id)),
                idempotency_key="donors:followup.example.test:1",
            )
        )
        await session.commit()

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert (report.sent, report.left) == (3, 2)
        assert report.stopped is not None
        assert "домен исчерпан на сегодня: mail.example.test — 3 из 3" in report.stopped

    async def test_direction_limit_stops_the_batch_in_words(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _queue(session, 4, ["a@mail-a.example.test", "b@mail-b.example.test"])
        monkeypatch.setitem(cfg.DIRECTION_LIMITS, "donors", 2)

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert (report.sent, report.left) == (2, 2)
        assert report.stopped is not None
        assert "у направления кончился дневной лимит (2 из 2 первых писем)" in report.stopped

    async def test_stages_1_2_without_rows_stop_with_the_old_words(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _queue(session, 3, ["a@mail.example.test"], cap=2)

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert (report.sent, report.left) == (2, 1)
        assert report.stopped == (
            "Сегодня писать некому: все ящики либо выключены, либо выбрали дневной лимит. "
            "Письмо остаётся в очереди — завтра лимит откроется заново"
        )


async def test_a_followup_waits_for_its_own_box_even_when_another_is_free(
    session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Условие d6: ящик переписки исчерпан, свободный есть — добивка ждёт, не пересаживается."""
    talk = await conversation(session, sent_at=NOW - timedelta(days=8))
    await make_sender(session, "free@mail-free.example.test")
    monkeypatch.setattr(cfg, "FOLLOWUP_PER_SENDER_PER_HOUR", 0)
    due = talk.first.next_action_at
    assert due is not None

    report = await followups.send_due(session, transport=NullTransport(), limit=5, now=due)

    assert (report.sent, report.postponed) == (0, 1)
    assert await session.scalar(select(MessageModel.id).where(MessageModel.step == 1)) is None


class TestScreen:
    async def test_the_senders_screen_shows_stage_domains_and_directions(
        self, client: AsyncClient, session: AsyncSession, make_user: object, sign_in: object
    ) -> None:
        box = await make_sender(session, "a@mail.example.test")
        session.add(_row("mail.example.test", stage=Stage.DONORS, daily_limit=30))
        await _sent_letters(session, box, count=2)
        await make_user("админ@site.com", role=UserRole.ADMIN)  # type: ignore[operator]
        token = await sign_in("админ@site.com")  # type: ignore[operator]

        response = await client.get("/api/senders", headers=bearer(token))

        view = response.json()
        assert (view["senders"][0]["id"], view["senders"][0]["stage"]) == (box.id, "donors")
        [domain] = view["domains"]
        assert (domain["domain"], domain["daily_limit"], domain["sent_today"]) == (
            "mail.example.test",
            30,
            2,
        )
        assert [(d["stage"], d["daily_limit"]) for d in view["directions"]] == [
            (stage.value, None) for stage in Stage
        ]


class TestConsole:
    async def test_a_domain_is_added_tuned_paused_and_resumed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(storage, "DSN", TEST_DSN)

        def args(**given: object) -> argparse.Namespace:
            base = {
                **dict.fromkeys(("stage", "daily_limit", "young_days", "pause")),
                "resume": False,
            }
            return argparse.Namespace(domain="Mail-B.example.test", **(base | given))

        async with committed_sessions() as factory:
            incomplete = await sending_domains.cmd_sending_domain(args())
            created = await sending_domains.cmd_sending_domain(args(stage="sales", daily_limit=30))
            await sending_domains.cmd_sending_domain(args(daily_limit=25, pause="жалоба"))
            async with factory() as session:
                paused = await session.scalar(select(SendingDomainModel))
            await sending_domains.cmd_sending_domain(args(resume=True, young_days=0))
            async with factory() as session:
                resumed = await session.scalar(select(SendingDomainModel))

        out = capsys.readouterr().out
        assert (incomplete, created) == (sending_domains.EXIT_INCOMPLETE, sending_domains.EXIT_OK)
        assert "заводится с --stage и --daily-limit" in out
        assert "Домен mail-b.example.test (sales): лимит 25, на паузе (жалоба)" in out
        assert paused is not None
        assert resumed is not None
        assert (paused.domain, paused.stage, paused.pause_reason) == (
            "mail-b.example.test",
            Stage.SALES,
            "жалоба",
        )
        assert (resumed.paused_at, resumed.daily_limit) == (None, 25)
        assert resumed.young_until < paused.young_until  # type: ignore[operator]


def _cycle(connection: Connection) -> tuple[object, object]:
    """Ревизия ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие его не видит."""
    migration = _migration(ROOT / "backend/migrations/versions/e054d221b2df_sending_domains.py")
    there = text("SELECT to_regclass('sending_domains') IS NOT NULL")
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        gone = connection.scalar(there)
        migration.upgrade()
    return gone, connection.scalar(there)


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    assert await (await session.connection()).run_sync(_cycle) == (False, True)
