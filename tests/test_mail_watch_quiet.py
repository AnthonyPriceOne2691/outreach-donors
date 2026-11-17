"""«Ящик молчит» — о поломке, а не об ожидании по замыслу (17.11.2026).

17.11 в 03:11 по Москве сторож назвал молчащими все 15 ящиков продаж, а в 03:21 сказал «прошло». У
250 добивок продаж в полночь UTC наступил срок, окно получателей (Алматы) было закрыто до утра, а
проход добивок откладывал их по 20 за проход — и больше десяти минут остальные выглядели подошедшими,
которых никто не забирает. Здесь — настоящий проход добивок раз в минуту и сторож почты раз в две
минуты под часами теста:

- ночь: 250 подошедших вне окна, проход с потолком 20 — тревоги нет, все отложены первым проходом и
  без сборки текста; отложенные в потолке прохода, как было до правки, дают ту самую тревогу;
- день: пауза ящика между добивками до 22,5 минуты — тревоги нет;
- проход не идёт — тревога через 10 минут, не раньше;
- почта отказывает ящику — тревога через 10 минут после отказа, даже если ждёт одно письмо; отказ,
  после которого ящик написал, и отказ одному письму — не тишина ящика.

Модуль продаж подставной (окно пн–пт 09:00–18:00, получатель в Алматы, сторож включён), почта —
нулевая или подставная, ящики и адреса — выдуманные.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core import stages, window
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderModel,
    ThreadModel,
)
from backend.features.core.stages import MailPolicy
from backend.features.core.window import Pace, SendWindow
from backend.features.letters import followup_refusal, followups
from backend.features.letters.followup_pace import Gaps
from backend.features.letters.transport import (
    LetterRefusalError,
    NullTransport,
    Outgoing,
    PlatformRefusalError,
    Transport,
)
from backend.features.ops import mail_watch, stage_silence
from backend.features.outreach.repository import EVERY_STAGE
from backend.workers.followups import BATCH
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import sender

#: Рабочие часы получателя у продаж — как в настройках по умолчанию.
OFFICE = SendWindow(days=frozenset(range(5)), start=time(9), end=time(18))
ALMATY = "Asia/Almaty"
#: Вторник 17.11.2026, полночь UTC: в Алматы ночь, окно откроется в 09:00 по их часам.
NIGHT = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
#: Тот же вторник, 06:00 UTC: в Алматы день, окно открыто.
DAY = datetime(2026, 11, 17, 6, 0, tzinfo=UTC)
#: Пауза ящика между добивками — 15 минут ±50%, разброс всегда верхний: 22,5 минуты.
LONGEST = Gaps(Pace(timedelta(minutes=15), 0.5), draw=lambda _box, _letter: 1.0)
QUIET = "quiet-box:"


@pytest.fixture(autouse=True)
def sales(monkeypatch: pytest.MonkeyPatch, filled_legal: None) -> FakeSalesMail:
    """Продажи подключены: окно пн–пт 09:00–18:00 по часам получателя в Алматы, сторож включён;
    потолки добивок ящика — по умолчанию, сдвиг от открытия окна — генератором с зерном."""
    monkeypatch.setattr(stages._SALES, "load", None)
    monkeypatch.setattr(window, "JITTER", random.Random(17))
    monkeypatch.setattr(outreach_cfg, "FOLLOWUP_PER_SENDER_PER_HOUR", 10)
    monkeypatch.setattr(outreach_cfg, "FOLLOWUP_PER_SENDER_PER_DAY", 20)
    found = FakeSalesMail(rules=MailPolicy(window=OFFICE, watch=True), zones=(ALMATY,))
    stages.register_sales(lambda: found)
    return found


async def _threads(
    session: AsyncSession, *, boxes: int, letters: int, due: datetime
) -> list[SenderModel]:
    """Ящики продаж и переписки по кругу между ними: первое письмо ушло неделю назад, срок
    добивки у всех — `due`."""
    found = [
        await sender(session, f"sales{n}@mail-sales{n}.example.test", Stage.SALES)
        for n in range(boxes)
    ]
    campaign = CampaignModel(name="Продажи", stage=Stage.SALES, status="running")
    domains = [DomainModel(host=f"lead{n}.example.test") for n in range(letters)]
    session.add_all([campaign, *domains])
    await session.flush()
    threads = [ThreadModel(domain_id=domain.id, campaign_id=campaign.id) for domain in domains]
    session.add_all(threads)
    await session.flush()
    session.add_all(
        MessageModel(
            campaign_id=campaign.id,
            thread_id=thread.id,
            domain_id=domain.id,
            step=0,
            status=MessageStatus.SENT,
            subject="A question about your team",
            body="Hi,\n\nshort question.",
            idempotency_key=f"sales:lead{n}.example.test:0",
            sender_id=found[n % boxes].id,
            sent_at=due - timedelta(days=7),
            internet_message_id=f"<first-{n}@lead{n}.example.test>",
            next_action_at=due,
        )
        for n, (domain, thread) in enumerate(zip(domains, threads, strict=True))
    )
    await session.flush()
    return found


async def _alarms(session: AsyncSession, moment: datetime) -> list[str]:
    """Коды тревог почты в минуту `moment`: «ящик молчит» и «добивки стоят»."""
    found = [
        *await mail_watch.alarms(session, moment, stages=EVERY_STAGE),
        *await stage_silence.alarms(session, moment, stages=EVERY_STAGE),
    ]
    return [alarm.code for alarm in found]


async def _minutes(
    session: AsyncSession,
    transport: Transport,
    start: datetime,
    minutes: int,
    *,
    passes: bool = True,
    deferred: int = followups.DEFERRED,
) -> dict[int, list[str]]:
    """Проход добивок раз в минуту (`passes`) и сторож раз в две — `minutes` минут от `start`:
    минута от начала → тревоги, которые сторож видел в неё."""
    seen: dict[int, list[str]] = {}
    for minute in range(minutes + 1):
        moment = start + timedelta(minutes=minute)
        if passes:
            await followups.send_due(
                session,
                transport=transport,
                limit=BATCH,
                now=moment,
                gaps=LONGEST,
                deferred=deferred,
            )
        if minute % 2 == 0:
            seen[minute] = await _alarms(session, moment)
    return seen


def _quiet(seen: dict[int, list[str]]) -> dict[int, list[str]]:
    """Минуты, в которые сторож назвал ящик молчащим, — и какие ящики."""
    found = {
        minute: [code for code in codes if code.startswith(QUIET)] for minute, codes in seen.items()
    }
    return {minute: codes for minute, codes in found.items() if codes}


async def _followups(session: AsyncSession) -> int:
    """Строк добивок в базе: заведена — значит, текст собирали."""
    return int(
        await session.scalar(select(func.count(MessageModel.id)).where(MessageModel.step == 1)) or 0
    )


class Mail(NullTransport):
    """Нулевая почта, которая отказывает первым `refusals` письмам — любому письму
    (`PlatformRefusalError`) или этому (`letter`, `LetterRefusalError`), — а дальше принимает и
    считает принятые."""

    def __init__(self, refusals: int = 0, *, letter: bool = False) -> None:
        self.refusals, self.letter, self.taken = refusals, letter, 0

    async def send(self, outgoing: Outgoing) -> str:
        if self.refusals > 0:
            self.refusals -= 1
            if self.letter:
                raise LetterRefusalError("Платформа отказала этому письму (400)")
            raise PlatformRefusalError("Платформа отказала (403)", reason="учётке отказано")
        self.taken += 1
        return await super().send(outgoing)


#: Почта, которая отказывает ящику, сколько её ни спрашивай.
FOREVER = 1_000


class TestNight:
    async def test_250_followups_outside_the_window_raise_no_alarm(
        self, session: AsyncSession, sales: FakeSalesMail
    ) -> None:
        """Ночь 17.11 по шагам: проход раз в минуту с потолком 20, сторож раз в две. Все 250
        откладываются первым проходом — до открытия окна плюс сдвиг, — и без сборки текста: строк
        добивок нет, текста у модуля продаж не спрашивали. Тревог нет ни одной."""
        await _threads(session, boxes=15, letters=250, due=NIGHT)

        first = await followups.send_due(
            session, transport=NullTransport(), limit=BATCH, now=NIGHT, gaps=LONGEST
        )
        seen = await _minutes(session, NullTransport(), NIGHT + timedelta(minutes=1), 30)

        assert (first.handed, first.postponed, first.outside) == (0, 250, 250)
        assert _quiet(seen) == {}
        assert all(codes == [] for codes in seen.values()), seen
        assert await _followups(session) == 0
        assert not [asked for asked in sales.asked if asked.startswith("followup")]
        opens = window.opening(OFFICE, ZoneInfo(ALMATY), NIGHT)
        dates = await session.scalars(
            select(MessageModel.next_action_at).where(MessageModel.step == 0)
        )
        assert all(opens <= due < opens + OFFICE.spread for due in dates if due is not None)

    async def test_the_old_count_gives_the_alarm_of_17_11(self, session: AsyncSession) -> None:
        """Тот же мир, но отложенные — в потолке прохода, как до правки (`deferred=0`): к десятой
        минуте полсотни добивок ещё не отложены, и сторож называет молчащими все 15 ящиков."""
        await _threads(session, boxes=15, letters=250, due=NIGHT)

        seen = await _minutes(session, NullTransport(), NIGHT, 14, deferred=0)

        loud = _quiet(seen)
        assert min(loud) == mail_watch.QUIET_MINUTES
        assert len(loud[mail_watch.QUIET_MINUTES]) == 15


class TestDay:
    async def test_the_pause_of_the_box_between_letters_is_not_silence(
        self, session: AsyncSession
    ) -> None:
        """Пять добивок одного ящика подошли разом; ящик отдаёт по одной и отдыхает 22,5 минуты —
        дольше порога тишины. Отдыхающему ящику срок переносится на конец паузы, и за полтора часа
        сторож молчит, а добивки уходят по одной."""
        await _threads(session, boxes=1, letters=5, due=DAY)
        transport = Mail()

        seen = await _minutes(session, transport, DAY, 90)

        assert _quiet(seen) == {}
        assert all(codes == [] for codes in seen.values()), seen
        sent = await session.scalars(
            select(MessageModel.sent_at)
            .where(MessageModel.step == 1)
            .order_by(MessageModel.sent_at)
        )
        went = [moment - DAY for moment in sent if moment is not None]
        assert went == [timedelta(minutes=m) for m in (0, 23, 46, 69)]
        assert transport.taken == 4


class TestBreakage:
    async def test_the_pass_that_does_not_go_is_an_alarm_in_ten_minutes(
        self, session: AsyncSession
    ) -> None:
        """Проход добивок не идёт: подошедшие ждут, ящик молчит — тревога на десятой минуте, до
        неё — тишина. Срок ТЗ — 15 минут."""
        [box] = await _threads(session, boxes=1, letters=3, due=DAY)

        seen = await _minutes(session, NullTransport(), DAY, 14, passes=False)

        loud = _quiet(seen)
        assert min(loud) == mail_watch.QUIET_MINUTES <= 15
        assert loud[mail_watch.QUIET_MINUTES] == [f"{QUIET}{box.email}"]

    async def test_the_mail_refusing_the_box_is_an_alarm_in_ten_minutes(
        self, session: AsyncSession
    ) -> None:
        """Почта отказывает ящику, а ждёт его одно письмо: срок добивки после отказа — через час, но
        ждёт она ящик. Тревога — через 10 минут после отказа, не раньше; почта снова принимает —
        добивка уходит повтором через час, и тишины нет."""
        [box] = await _threads(session, boxes=1, letters=1, due=DAY)
        refusing = Mail(FOREVER)

        seen = await _minutes(session, refusing, DAY, 14)

        loud = _quiet(seen)
        assert min(loud) == mail_watch.QUIET_MINUTES <= 15
        assert loud[mail_watch.QUIET_MINUTES] == [f"{QUIET}{box.email}"]
        refused = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
        assert refused is not None
        assert (refused.status, refused.sender_id) == (MessageStatus.QUEUED, None)
        assert (refused.failure_reason or "").startswith(followup_refusal.MARK)

        accepting = Mail()
        later = await _minutes(session, accepting, DAY + timedelta(hours=1), 14)

        assert accepting.taken == 1
        assert _quiet(later) == {}
        await session.refresh(refused)
        assert (refused.status, refused.failure_reason) == (MessageStatus.SENT, None)

    async def test_a_refusal_after_which_the_box_wrote_is_not_silence(
        self, session: AsyncSession
    ) -> None:
        """Почта отказала одной добивке ящика, следующая той же минутой ушла: ящик пишет, отказанная
        ждёт своего повтора, а не ящика, — тревоги нет."""
        await _threads(session, boxes=1, letters=2, due=DAY)
        once = Mail(1)

        seen = await _minutes(session, once, DAY, 20)

        assert once.taken == 1
        assert _quiet(seen) == {}

    async def test_a_refusal_of_one_letter_is_not_silence_of_the_box(
        self, session: AsyncSession
    ) -> None:
        """Почта отказала этому письму — адресату или содержимому (`LetterRefusalError`): ящик тут ни
        при чём, следа у добивки нет, и «ящик молчит» было бы неправдой."""
        await _threads(session, boxes=1, letters=1, due=DAY)

        seen = await _minutes(session, Mail(FOREVER, letter=True), DAY, 20)

        assert _quiet(seen) == {}
        refused = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
        assert refused is not None
        assert refused.failure_reason is None
