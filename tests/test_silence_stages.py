"""Тишина почты по этапам (`ops/stage_silence.py`, ревью стыков 12.10.2026).

Три правила сторожа тишины — доставка, ответы, добивки — считали письма и ответы всех этапов
одной тревогой, а лента говорит о коде один раз. Чужая тишина прятала свою поломку: сроки
продаж, которые проход добивок не берёт, пока продажи не подключены, через час зажигали «Добивки
стоят», и остановка добивок доноров новой тревоги уже не давала; ответ лида прятал сломанный
приём ответов доноров; молчание вебхука одного направления — молчание другого.

Каждое правило проверяется с двух сторон: тишина этапа — своя тревога с кодом и этапом; шум и
тишина другого этапа её не прячут и не поднимают. Продажи — подставным модулем моста: сторож
спрашивает у него только «подключены ли» и политику почты.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, ReplyKind, SenderStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from backend.features.core.stages import CURRENT, MailPolicy
from backend.features.ops import alarm_feed, silence
from backend.features.ops.alarm_feed import Feed
from backend.features.ops.alarms import Alarm
from backend.features.ops.stage_silence import REPLY_SILENCE_MIN_DELIVERED
from backend.features.outreach.repository import EVERY_STAGE
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_mail_watch_seams import FakeRedis

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NO_SALES = frozenset({Stage.DONORS, Stage.ADVERTISERS})
DONOR_BOX = "anna@mail-donors.example.test"
SALES_BOX = "hello@mail-sales.example.test"


@dataclass
class _Sales:
    """Модуль продаж в том, о чём его спрашивает сторож: подключены ли и политика почты."""

    on: bool
    asked: int = 0

    async def connected(self, _session: AsyncSession) -> bool:
        self.asked += 1
        return self.on

    async def policy(self, _session: AsyncSession) -> MailPolicy:
        return CURRENT


def _sales(monkeypatch: pytest.MonkeyPatch, *, on: bool) -> _Sales:
    found = _Sales(on=on)
    monkeypatch.setattr(stages._SALES, "load", lambda: found)
    return found


@pytest.fixture
def connected(monkeypatch: pytest.MonkeyPatch) -> _Sales:
    return _sales(monkeypatch, on=True)


@pytest.fixture
def not_connected(monkeypatch: pytest.MonkeyPatch) -> _Sales:
    """Продажи не подключены (`SALES_ENABLED=false` и т. п.): проход добивок их сроки не берёт."""
    return _sales(monkeypatch, on=False)


async def _letters(
    session: AsyncSession, stage: Stage, count: int = 1, **fields: object
) -> list[MessageModel]:
    """Первые письма рассылки этапа — каждое своему сайту."""
    campaign = CampaignModel(stage=stage, name=f"Тишина {stage.value}", status="running")
    session.add(campaign)
    await session.flush()
    found = []
    for number in range(count):
        domain = DomainModel(host=f"site{number}-{stage.value}-{campaign.id}.example.test")
        session.add(domain)
        await session.flush()
        letter = MessageModel(
            campaign_id=campaign.id,
            domain_id=domain.id,
            step=0,
            subject="Hi",
            body="Hi",
            idempotency_key=f"silence:{stage.value}:{campaign.id}:{number}:0",
            **fields,
        )
        session.add(letter)
        found.append(letter)
    await session.flush()
    return found


async def _box(session: AsyncSession, email: str, stage: Stage) -> None:
    """Ящик направления — его домен наш, и ответ на него — ответ этапа."""
    session.add(
        SenderModel(
            domain=email.split("@", 1)[1],
            email=email,
            stage=stage,
            daily_cap=20,
            status=SenderStatus.FREE,
            enabled=True,
        )
    )
    await session.flush()


async def _codes(stages_seen: frozenset[Stage] = EVERY_STAGE, *, session: AsyncSession) -> set:
    return {alarm.code for alarm in await silence.alarms(session, stages=stages_seen)}


def _late(hours: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


# --- добивки ---------------------------------------------------------------------------------


async def test_sales_deadlines_of_unconnected_sales_raise_no_alarm(
    session: AsyncSession, not_connected: _Sales
) -> None:
    """Проход добивок сроки неподключённых продаж не берёт — это устройство, а не поломка."""
    await _letters(session, Stage.SALES, status=MessageStatus.SENT, next_action_at=_late(3))

    codes = await _codes(session=session)

    assert not {code for code in codes if code.startswith("followups-stuck")}


async def test_a_stall_of_each_stage_is_its_own_alarm(
    session: AsyncSession, connected: _Sales
) -> None:
    await _letters(session, Stage.SALES, status=MessageStatus.SENT, next_action_at=_late(3))
    await _letters(session, Stage.DONORS, status=MessageStatus.SENT, next_action_at=_late(3))

    found = await silence.alarms(session, stages=EVERY_STAGE)

    assert {(alarm.code, alarm.stage) for alarm in found if "followups" in alarm.code} == {
        ("followups-stuck:donors", Stage.DONORS),
        ("followups-stuck:sales", Stage.SALES),
    }


async def test_the_donor_stall_after_the_sales_one_is_told_anew(
    session: AsyncSession, connected: _Sales, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Лента говорит о коде один раз: при общем коде остановка добивок доноров после продаж
    не дала бы ни слова. Код этапа — своё «тревога»."""
    said = _telegram(monkeypatch)
    feed = Feed("silence-stages")
    await _letters(session, Stage.SALES, status=MessageStatus.SENT, next_action_at=_late(3))
    await feed.tell(await silence.alarms(session, stages=EVERY_STAGE))
    await _letters(session, Stage.DONORS, status=MessageStatus.SENT, next_action_at=_late(3))

    await feed.tell(await silence.alarms(session, stages=EVERY_STAGE))

    assert [text.split(".")[0] for text in said] == [
        "тревога: Добивки стоят — направление «sales»",
        "тревога: Добивки стоят — направление «donors»",
    ]


# --- ответы ----------------------------------------------------------------------------------


async def _delivered_donor_letters(session: AsyncSession) -> list[MessageModel]:
    """Пачка доставленных писем доноров — достаточно, чтобы ждать ответа."""
    return await _letters(
        session, Stage.DONORS, REPLY_SILENCE_MIN_DELIVERED,
        status=MessageStatus.DELIVERED, sent_at=_late(24),
    )  # fmt: skip


async def test_a_lead_reply_does_not_hide_the_donor_silence(
    session: AsyncSession, connected: _Sales
) -> None:
    await _delivered_donor_letters(session)
    [lead] = await _letters(session, Stage.SALES, status=MessageStatus.DELIVERED, sent_at=_late(24))
    thread = ThreadModel(domain_id=lead.domain_id, campaign_id=lead.campaign_id)
    session.add(thread)
    await session.flush()
    session.add(
        ReplyModel(
            thread_id=thread.id,
            message_id=lead.id,
            kind=ReplyKind.HUMAN,
            raw_body="Tell me more",
            from_email="ceo@lead.example.test",
        )
    )
    await session.flush()

    found = await silence.alarms(session, stages=EVERY_STAGE)

    assert ("reply-silence:donors", Stage.DONORS) in {(a.code, a.stage) for a in found}


@pytest.mark.parametrize(
    ("address", "quiet"),
    [
        ("anna+m7.0123456789@replies.mail-donors.example.test", True),  # поддомен ответов доноров
        ("anna@mail-donors.example.test", True),  # ящик доноров
        ("hello@mail-sales.example.test", False),  # ящик продаж — не их приём
        ("someone@elsewhere.example.test", False),  # не наш адрес — этапа нет
    ],
    ids=["поддомен-доноров", "ящик-доноров", "ящик-продаж", "чужой"],
)
async def test_an_unbound_reply_counts_for_the_stage_of_its_address(
    session: AsyncSession, connected: _Sales, address: str, quiet: bool
) -> None:
    """Ответ без письма доказывает, что приём работает, — у этапа домена, на который пришёл."""
    await _box(session, DONOR_BOX, Stage.DONORS)
    await _box(session, SALES_BOX, Stage.SALES)
    await _delivered_donor_letters(session)
    session.add(
        ReplyModel(
            kind=ReplyKind.HUMAN, raw_body="Hi", from_email="x@site.example.test",
            to_addresses=[address],
        )
    )  # fmt: skip
    await session.flush()

    codes = await _codes(session=session)

    assert ("reply-silence:donors" not in codes) is quiet


# --- доставка --------------------------------------------------------------------------------


async def test_each_webhook_silence_is_its_own_alarm(
    session: AsyncSession, connected: _Sales
) -> None:
    for stage in (Stage.DONORS, Stage.SALES):
        await _letters(
            session, stage, status=MessageStatus.SENT, sent_at=_late(8), provider_message_id="sg-1"
        )

    found = await silence.alarms(session, stages=EVERY_STAGE)

    assert {(a.code, a.stage) for a in found if "delivery" in a.code} == {
        ("delivery-silence:donors", Stage.DONORS),
        ("delivery-silence:sales", Stage.SALES),
    }


async def test_sales_letters_of_unconnected_sales_are_not_counted(
    session: AsyncSession, not_connected: _Sales
) -> None:
    for stage in (Stage.DONORS, Stage.SALES):
        await _letters(
            session, stage, status=MessageStatus.SENT, sent_at=_late(8), provider_message_id="sg-2"
        )

    codes = await _codes(session=session)

    assert {code for code in codes if code.startswith("delivery")} == {"delivery-silence:donors"}


# --- видимость -------------------------------------------------------------------------------


async def test_without_the_right_sales_are_neither_counted_nor_asked(
    session: AsyncSession, connected: _Sales
) -> None:
    await _letters(
        session, Stage.SALES, status=MessageStatus.SENT, sent_at=_late(8), provider_message_id="s"
    )

    codes = await _codes(NO_SALES, session=session)

    assert not {code for code in codes if code.endswith(":sales")}
    assert connected.asked == 0


@pytest.fixture
def nobody_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def answered() -> Alarm | None:
        return None

    monkeypatch.setattr("backend.api.watchdog.routes.probe_providers", answered)


@pytest.mark.usefixtures("nobody_down")
async def test_the_screen_shows_the_sales_silence_only_with_the_right(
    client: AsyncClient,
    session: AsyncSession,
    connected: _Sales,
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    await _letters(
        session, Stage.SALES, status=MessageStatus.SENT, sent_at=_late(8), provider_message_id="s"
    )
    await session.commit()
    await make_user(
        "outsider@team.example.test", role=UserRole.OPERATOR, permissions={"sales": False}
    )
    await make_user("seller@team.example.test", role=UserRole.OPERATOR)
    outsider = bearer(await sign_in("outsider@team.example.test"))
    seller = bearer(await sign_in("seller@team.example.test"))

    hidden = (await client.get("/api/watchdog", headers=outsider)).json()["alarms"]
    shown = (await client.get("/api/watchdog", headers=seller)).json()["alarms"]

    assert [one["code"] for one in hidden] == []
    assert [(one["code"], one["stage"]) for one in shown] == [("delivery-silence:sales", "sales")]


# --- прежние коды после выкатки ----------------------------------------------------------------


def _telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Что лента сказала — по порядку; Telegram принимает всё."""
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(alarm_feed, "send_alert", send)
    return said


async def test_codes_without_a_stage_pass_after_the_rollout(
    session: AsyncSession, not_connected: _Sales, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Прежний процесс сторожа сказал о тревогах без этапа; новый их больше не видит — лента
    говорит «прошло» о них, как о любой кончившейся тревоге, а действующая тишина — новым кодом."""
    said = _telegram(monkeypatch)
    redis = FakeRedis()
    monkeypatch.setattr(alarm_feed, "connection", lambda: redis)
    feed = Feed("silence-stages-rollout")
    redis.hashes[alarm_feed.told_key(feed.name)] = {
        "followups-stuck": "Добивки стоят",
        "delivery-silence": "Платформа молчит о доставке",
    }
    await _letters(session, Stage.DONORS, status=MessageStatus.SENT, next_action_at=_late(3))

    for _ in range(alarm_feed.QUIET_PASSES):
        await feed.tell(await silence.alarms(session, stages=EVERY_STAGE))

    assert [text.split(".")[0] for text in said] == [
        "тревога: Добивки стоят — направление «donors»",
        "прошло: Добивки стоят",
        "прошло: Платформа молчит о доставке",
    ]
    assert set(redis.hashes[alarm_feed.told_key(feed.name)]) == {"followups-stuck:donors"}
