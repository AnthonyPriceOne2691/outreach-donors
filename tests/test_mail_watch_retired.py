"""Сторож почты: ящик выбыл, а письма переписок ждут его; тревога — не позже 15 минут.

ТЗ: «Если ящик перестал отправлять, тревога приходит через 15 минут». Выбывший ящик —
выключен, на паузе, удалён или его домен на паузе (правило отправки
`letters/mailbox.silence_of`) — раньше из проверки исключался: его ждущие добивки и ответы
тревоги не давали, кроме случая «на паузе все». Теперь — тревога по смене состояния у этапа со
сторожем в политике (у продаж; модуль подставной), у доноров шума нет. Срок — порог молчания и
проход сторожа под часами теста; провайдеров сторож спрашивает не чаще, чем раньше.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from backend.config import ahrefs as ahrefs_cfg
from backend.config import alerts as alerts_cfg
from backend.config import serp as serp_cfg
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderModel,
    SendingDomainModel,
    ThreadModel,
)
from backend.features.core.stages import MailPolicy
from backend.features.letters.chain import ANSWER_STEP
from backend.features.ops import alarm_feed, mail_watch
from backend.features.ops.alarms import Alarm
from backend.features.outreach import senders
from backend.features.outreach.repository import EVERY_STAGE
from backend.workers import reaper
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_mail_watch import NOW, _letter
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import sender

BOX = "hello@mail-sales.example.test"
#: Второй ящик пишет — иначе к тревоге о выбывшем добавилась бы «все на паузе».
WRITING = "other@mail-sales2.example.test"
#: Причина паузы — словами правила мягких сигналов.
WHY = "отказов 3 в окне 50 писем — порог 5.0%: пауза"


@pytest.fixture(autouse=True)
def watched(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Продажи со сторожем в политике — подставным модулем, как в `tests/test_mail_watch.py`."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(watch=True))
    stages.register_sales(lambda: found)
    return found


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(alarm_feed, "send_alert", send)
    return said


async def _boxes(session: AsyncSession) -> SenderModel:
    """Ящик продаж, вставший на паузу три минуты назад, и второй — пишущий."""
    box = await sender(session, BOX, Stage.SALES)
    senders.disable(box, WHY, now=NOW - timedelta(minutes=3))
    await sender(session, WRITING, Stage.SALES)
    return box


async def _followup(session: AsyncSession, box: SenderModel | None, n: int, due: datetime) -> None:
    """Письмо, ушедшее с ящика `box`, и срок его добивки `due`."""
    await _letter(
        session,
        Stage.SALES,
        box,
        n,
        status=MessageStatus.SENT,
        sent_at=NOW - timedelta(days=3),
        next_action_at=due,
    )


async def _found(session: AsyncSession, at: datetime = NOW) -> list[Alarm]:
    return await mail_watch.alarms(session, at, stages=EVERY_STAGE)


def _silent(box: SenderModel) -> str:
    """Слова отказа отправки письму переписки выключенного ящика (`mailbox.silence_of`)."""
    return (
        f"Ящик {box.email} сейчас не пишет (выключен: {WHY}), а переписку ведёт он. С другого "
        "ящика письмо не уйдёт — оно ждёт, пока ящик не включат на экране «Домены рассылки»"
    )


async def test_a_paused_box_with_waiting_followups_is_told_once_and_its_return_once(
    session: AsyncSession, telegram: list[str]
) -> None:
    """Добивка подошла, вторая ждёт срока — тревога сразу, одна; ждущих стало больше — не
    повторяется; ящик включили — «прошло» после двух проходов без тревоги."""
    box = await _boxes(session)
    await _followup(session, box, 1, NOW - timedelta(minutes=1))
    await _followup(session, box, 2, NOW + timedelta(days=4))
    feed = alarm_feed.Feed()

    [alarm] = await _found(session)
    await feed.tell([alarm])
    await _followup(session, box, 3, NOW + timedelta(days=5))
    await feed.tell(await _found(session, NOW + timedelta(minutes=2)))
    senders.enable(box, now=NOW + timedelta(minutes=3))
    for minutes in (4, 6):
        await feed.tell(await _found(session, NOW + timedelta(minutes=minutes)))

    detail = (
        "Писем переписки ждут его: 2 — добивок 2 (срок уже наступил у 1), ответов 0. "
        + _silent(box)
    )
    assert (alarm.code, alarm.title, alarm.detail, alarm.stage) == (
        f"retired-box:{BOX}",
        f"Ящик {BOX} выбыл",
        detail,
        Stage.SALES,
    )
    assert telegram == [f"тревога: Ящик {BOX} выбыл. {detail}", f"прошло: Ящик {BOX} выбыл"]


async def test_a_disabled_box_and_a_deleted_one_are_retired_too(session: AsyncSession) -> None:
    """Выключен — без паузы в статусе; удалён — у письма пустой ящик, адреса уже не назвать."""
    off = await sender(session, BOX, Stage.SALES)
    off.enabled = False
    await sender(session, WRITING, Stage.SALES)
    await _followup(session, off, 1, NOW + timedelta(days=2))
    await _followup(session, None, 2, NOW - timedelta(hours=2))

    found = {alarm.code: alarm for alarm in await _found(session)}

    assert list(found) == [f"retired-box:{BOX}", "retired-box:deleted:sales"]
    assert "(выключен)" in found[f"retired-box:{BOX}"].detail
    deleted = found["retired-box:deleted:sales"]
    assert deleted.title == "Ящик переписки удалён — направление «sales»"
    assert deleted.detail == (
        "Писем переписки ждут его: 1 — добивок 1 (срок уже наступил у 1), ответов 0. Ящика "
        "первого письма нет: им начата переписка, а его удалили. С другого ящика письмо "
        "переписки не уйдёт — оно ждёт"
    )


async def test_a_queued_answer_waits_for_the_box_of_its_thread(session: AsyncSession) -> None:
    """Ответ в очереди уходит только с ящика первого письма своей переписки (`answers.py`)."""
    box = await _boxes(session)
    domain = DomainModel(host="lead-answer.example.test")
    campaign = CampaignModel(stage=Stage.SALES, name="Ответ", status="running")
    session.add_all([domain, campaign])
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    for step, status, sender_id in (
        (0, MessageStatus.DELIVERED, box.id),
        (ANSWER_STEP, MessageStatus.QUEUED, None),
    ):
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                thread_id=thread.id,
                domain_id=domain.id,
                sender_id=sender_id,
                step=step,
                status=status,
                idempotency_key=f"sales:answer-watch:{step}",
            )
        )
    await session.flush()

    [alarm] = await _found(session)

    assert alarm.code == f"retired-box:{BOX}"
    assert alarm.detail.startswith(
        "Писем переписки ждут его: 1 — добивок 0 (срок уже наступил у 0), ответов 1."
    )


async def test_a_box_on_a_paused_domain_is_retired_in_the_words_of_the_sending(
    session: AsyncSession,
) -> None:
    box = await sender(session, BOX, Stage.SALES)
    await sender(session, WRITING, Stage.SALES)
    session.add(
        SendingDomainModel(
            domain=box.domain,
            stage=Stage.SALES,
            daily_limit=5,
            paused_at=NOW - timedelta(minutes=7),
            pause_reason="жалоба на спам",
        )
    )
    await _followup(session, box, 1, NOW + timedelta(days=1))

    [alarm] = await _found(session)

    assert alarm.code == f"retired-box:{BOX}"
    assert alarm.detail.endswith(
        f"Домен {box.domain} на паузе (жалоба на спам), а переписку ведёт ящик {BOX} на нём. С "
        "другого ящика письмо не уйдёт — оно ждёт, пока домен не снимут с паузы "
        "(outreach sending-domain --resume)"
    )


async def test_a_retired_box_with_nothing_waiting_is_no_alarm(session: AsyncSession) -> None:
    """Ящик выключили, а его переписки кончились — ждать некому, и шума нет."""
    box = await _boxes(session)
    await _letter(
        session,
        Stage.SALES,
        box,
        1,
        status=MessageStatus.DELIVERED,
        sent_at=NOW - timedelta(days=9),
    )

    assert await _found(session) == []


async def test_donors_get_no_retired_alarm(session: AsyncSession) -> None:
    """У доноров сторожа в политике нет — выбывший ящик с добивками шума не добавляет."""
    box = await sender(session, "anna@mail-donors.example.test", Stage.DONORS)
    senders.disable(box, "выключено вручную", now=NOW)
    await _letter(
        session,
        Stage.DONORS,
        box,
        1,
        status=MessageStatus.SENT,
        sent_at=NOW - timedelta(days=3),
        next_action_at=NOW - timedelta(minutes=1),
    )

    assert await _found(session) == []


# --- срок: не позже 15 минут ------------------------------------------------------------------

#: Проход, где провайдеры не ответили до таймаутов, и тревога в Telegram — худший проход.
SLOW_PASS = timedelta(seconds=ahrefs_cfg.TIMEOUT_S + serp_cfg.TIMEOUT_S + alerts_cfg.TIMEOUT_SEC)
DEADLINE = timedelta(minutes=15)


def test_the_numbers_keep_the_alarm_within_fifteen_minutes() -> None:
    """Худший случай «ящик молчит»: порог, пауза между проходами и медленный проход. Опрос
    провайдеров — не чаще раза в два прохода: медленный проход в окне тревоги один."""
    worst = (
        timedelta(minutes=mail_watch.QUIET_MINUTES)
        + timedelta(seconds=reaper.WATCHDOG_INTERVAL_SEC)
        + SLOW_PASS
    )

    assert worst <= DEADLINE
    assert reaper.PROVIDERS_INTERVAL_SEC >= 2 * reaper.WATCHDOG_INTERVAL_SEC


@pytest.mark.parametrize("phase", [0.0, 1.0, 61.0, reaper.WATCHDOG_INTERVAL_SEC - 1])
async def test_a_quiet_box_is_told_within_fifteen_minutes(
    session: AsyncSession, phase: float
) -> None:
    """Ящик перестал отправлять в минуту, когда подошла добивка (`stop`). Проходы сторожа — раз
    в `WATCHDOG_INTERVAL_SEC` с любой фазой; первая тревога — с запасом на медленный проход."""
    box = await sender(session, BOX, Stage.SALES)
    stop = NOW
    await _followup(session, box, 1, stop)
    passes = [stop + timedelta(seconds=phase + k * reaper.WATCHDOG_INTERVAL_SEC) for k in range(15)]

    told = [
        at for at in passes if f"quiet-box:{BOX}" in {a.code for a in await _found(session, at)}
    ]

    assert told
    assert told[0] - stop <= DEADLINE - SLOW_PASS


async def test_a_retired_box_is_told_on_the_first_pass(session: AsyncSession) -> None:
    """Ящик выбыл — тревога первым же проходом, не дожидаясь порога молчания."""
    box = await _boxes(session)
    await _followup(session, box, 1, NOW + timedelta(days=3))

    codes = [alarm.code for alarm in await _found(session, NOW - timedelta(minutes=3))]

    assert codes == [f"retired-box:{BOX}"]


async def test_providers_are_asked_no_more_often_than_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проход сторожа чаще — опрос провайдеров нет: первый проход процесса и раз в десять
    минут, между ними — прежний ответ."""
    clock = [0.0]
    asked: list[float] = []
    down = Alarm(code="provider-unreachable", title="Провайдер не отвечает", detail="Ahrefs")

    async def probe() -> Alarm | None:
        asked.append(clock[0])
        return down if len(asked) == 1 else None

    monkeypatch.setattr(reaper, "probe_providers", probe)
    providers = reaper.Providers(clock=lambda: clock[0])
    answers: list[Alarm | None] = []
    for k in range(7):
        clock[0] = k * reaper.WATCHDOG_INTERVAL_SEC
        answers.append(await providers.probe())

    assert asked == [0.0, reaper.PROVIDERS_INTERVAL_SEC]
    assert answers == [down] * 5 + [None, None]
