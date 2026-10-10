"""Сторож почты продаж и тревоги в Telegram по смене состояния (Ф4, срез 4.5b).

Ящик молчит, очередь есть — отправить некому, все ящики на паузе: тревоги у этапа со
сторожем в политике (у продаж; модуль подставной), у доноров — как было. Тревога
уходит один раз — при появлении и при уходе (после двух проходов без неё); бот не
настроен — громкая строка журнала, тоже один раз; Telegram не принял — слово повторится
следующим проходом.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from backend.config import alerts as alerts_cfg
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, SenderStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderModel,
    SendingDomainModel,
)
from backend.features.core.stages import MailPolicy
from backend.features.ops import alarm_feed, mail_watch, silence
from backend.features.ops.alarms import Alarm
from backend.features.outreach.repository import EVERY_STAGE
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import sender

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def watched(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(watch=True))
    stages.register_sales(lambda: found)
    return found


async def _letter(
    session: AsyncSession, stage: Stage, box: SenderModel | None, n: int = 0, **kw: object
) -> None:
    domain = DomainModel(host=f"lead{n}-{stage.value}.example.test")
    campaign = CampaignModel(stage=stage, name="Сторож", status="running")
    session.add_all([domain, campaign])
    await session.flush()
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            domain_id=domain.id,
            sender_id=box.id if box else None,
            step=0,
            idempotency_key=f"{stage}:watch{n}:0",
            **kw,
        )
    )
    await session.flush()


async def test_a3_a_box_with_waiting_letters_and_nothing_sent_is_quiet(
    session: AsyncSession,
) -> None:
    box = await sender(session, "hello@mail-sales.example.test", Stage.SALES)
    await _letter(
        session,
        Stage.SALES,
        box,
        status=MessageStatus.SENT,
        sent_at=NOW - timedelta(days=3),
        next_action_at=NOW - timedelta(minutes=20),
    )

    found = await mail_watch.alarms(session, NOW, stages=EVERY_STAGE)
    early = await mail_watch.alarms(session, NOW - timedelta(minutes=10), stages=EVERY_STAGE)

    assert [alarm.code for alarm in found] == [f"quiet-box:{box.email}"]
    assert early == []
    assert found[0] in await silence.alarms(session, stages=EVERY_STAGE, now=NOW)
    # С ящика ушло письмо пять минут назад — он не молчит, проход просто не дошёл.
    await _letter(
        session, Stage.SALES, box, 1, status=MessageStatus.SENT, sent_at=NOW - timedelta(minutes=5)
    )
    assert await mail_watch.alarms(session, NOW, stages=EVERY_STAGE) == []


async def test_a4_queue_and_nobody_to_send_while_all_boxes_are_paused(
    session: AsyncSession,
) -> None:
    box = await sender(session, "hello@mail-sales.example.test", Stage.SALES)
    box.status = SenderStatus.PAUSED
    await _letter(session, Stage.SALES, None, status=MessageStatus.QUEUED)

    codes = [alarm.code for alarm in await mail_watch.alarms(session, NOW, stages=EVERY_STAGE)]
    box.status = SenderStatus.FREE  # ящик пишет, но его домен на паузе — писать всё равно нечем
    session.add(
        SendingDomainModel(domain=box.domain, stage=Stage.SALES, daily_limit=5, paused_at=NOW)
    )
    paused_domain = [
        alarm.code for alarm in await mail_watch.alarms(session, NOW, stages=EVERY_STAGE)
    ]

    assert codes == ["all-paused:sales", "nobody-to-send:sales"]
    assert paused_domain == ["nobody-to-send:sales"]


async def test_donors_get_no_new_alarms(session: AsyncSession) -> None:
    box = await sender(session, "anna@mail-donors.example.test", Stage.DONORS)
    box.enabled = False
    await _letter(session, Stage.DONORS, None, status=MessageStatus.QUEUED)

    assert await mail_watch.alarms(session, NOW, stages=EVERY_STAGE) == []


async def test_a_module_without_a_policy_is_an_alarm_and_the_watch_goes_on(
    session: AsyncSession, watched: FakeSalesMail
) -> None:
    watched.broken = "policy"

    [alarm] = await mail_watch.alarms(session, NOW, stages=EVERY_STAGE)

    assert (alarm.code, alarm.title) == ("no-policy:sales", "Политика почты «sales» не получена")
    assert alarm.detail.endswith("не подключены — выдуманная поломка модуля: policy")


QUIET = Alarm(code="quiet-box:x", title="Ящик x молчит", detail="ждут его")


async def test_an_alarm_is_told_once_and_its_end_once(monkeypatch: pytest.MonkeyPatch) -> None:
    said: list[str] = []

    async def telegram(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(alarm_feed, "send_alert", telegram)
    feed = alarm_feed.Feed()

    for state in ([QUIET], [QUIET], [], []):
        await feed.tell(state)

    assert said == ["тревога: Ящик x молчит. ждут его", "прошло: Ящик x молчит"]


async def test_without_a_bot_the_journal_line_is_said_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "")
    feed = alarm_feed.Feed()

    with caplog.at_level(logging.ERROR):
        await feed.tell([QUIET])
        await feed.tell([QUIET])

    assert caplog.text.count("ТРЕВОГА НЕ ОТПРАВЛЕНА") == 1


async def test_a_refused_alarm_is_said_again_next_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([False, True])

    async def telegram(_text: str) -> bool:
        return next(answers)

    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "made-up-token")
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_CHAT_ID", "made-up-chat")
    monkeypatch.setattr(alarm_feed, "send_alert", telegram)
    feed = alarm_feed.Feed()

    await feed.tell([QUIET])
    first = dict(feed.told)
    await feed.tell([QUIET])

    assert (first, feed.told) == ({}, {QUIET.code: QUIET.title})


async def test_a_refused_end_is_said_again_next_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """«Прошло» не принято — следующий проход скажет его снова, счёт тишины не сбрасывается."""
    said: list[str] = []
    answers = iter([True, False, True])

    async def telegram(text: str) -> bool:
        said.append(text)
        return next(answers)

    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "made-up-token")
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_CHAT_ID", "made-up-chat")
    monkeypatch.setattr(alarm_feed, "send_alert", telegram)
    feed = alarm_feed.Feed()

    for state in ([QUIET], [], [], []):
        await feed.tell(state)

    assert said == [
        "тревога: Ящик x молчит. ждут его",
        "прошло: Ящик x молчит",
        "прошло: Ящик x молчит",
    ]
    assert (feed.told, feed.quiet) == ({}, {})  # о прошедшей тревоге лента не помнит ничего
