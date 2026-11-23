"""Проход автоотправки не шлёт письмо, оставшееся шаблоном (`letters/batch._sendable`).

23.11.2026 автокруг собрал письма шаблоном (у модели не было денег), и при включённой
автоотправке они ушли бы сами одним текстом. Проверяется то, ради чего фильтр: проход пропускает
шаблонное письмо со словами, что сделать, и шлёт остальные; поправленное руками шаблонное уходит;
кнопка шлёт как прежде — решает человек; проход из одних шаблонных стоит не по делу, и тревога
о нём одна, со словами; срок очереди их не считает; письма продаж фильтр не судит.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, QueueAutosendModel
from backend.features.letters import autosend, autosend_alarm, batch, forecast, review
from backend.features.letters.transport import NullTransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_sender
from tests.test_letters_rewrite_queue import HOSTS, Model, _build, _donors

#: Шаблоном — первые два письма очереди, третье переписано моделью.
TEMPLATED = HOSTS[:2]


@pytest.fixture(autouse=True)
def _always_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Окно получателя — круглые сутки: проверки здесь не об окне."""
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_DAYS", frozenset(range(7)))
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_OPENS", time.min)
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_CLOSES", time.max)


def _now() -> datetime:
    return datetime.now(UTC)


async def _queue(session: AsyncSession, hosts: tuple[str, ...] = HOSTS[:3]) -> None:
    """Очередь доноров: письма `TEMPLATED` собраны, пока модель отказывала, остальные — переписаны.
    Ящиков столько, сколько писем: проход отдаёт по письму с ящика."""
    await _donors(session, hosts)
    for n in range(len(hosts)):
        await make_sender(session, f"outreach{n}@mail{n}.example.test")
    await _build(session, Model(refuse=TEMPLATED))


async def _statuses(session: AsyncSession) -> dict[str, MessageStatus]:
    rows = await session.execute(
        select(DomainModel.host, MessageModel.status)
        .join(MessageModel, MessageModel.domain_id == DomainModel.id)
        .execution_options(populate_existing=True)
    )
    return dict(rows.tuples().all())


async def _started(session: AsyncSession) -> QueueAutosendModel:
    mode, _ = await autosend.start(session, Stage.DONORS, "links", by=None, now=_now())
    await session.commit()
    return mode


@pytest.mark.usefixtures("filled_legal")
class TestThePass:
    async def test_the_pass_skips_template_letters_and_sends_the_rest(
        self, session: AsyncSession
    ) -> None:
        await _queue(session)
        mode = await _started(session)

        passed = await autosend.run(session, NullTransport(), mode, now=_now())

        assert await _statuses(session) == {
            HOSTS[0]: MessageStatus.QUEUED,
            HOSTS[1]: MessageStatus.QUEUED,
            HOSTS[2]: MessageStatus.SENT,
        }
        assert passed.state == f"ушло 1, {batch.TEMPLATED} — 2"
        assert passed.stall is None, "ушло письмо — проход идёт, тревоги нет"

    async def test_a_template_letter_edited_by_hand_goes(self, session: AsyncSession) -> None:
        """Правка руками — решение человека, даже слово в слово: такое письмо проход шлёт."""
        await _queue(session, HOSTS[:1])
        [letter] = (await session.scalars(select(MessageModel))).all()
        await AccessRepository(session).record(
            AuditAction.USER_UPDATED,
            target=f"message:{letter.id}",
            details={"действие": review.EDITED},
            stage=Stage.DONORS,
        )
        await session.commit()
        mode = await _started(session)

        passed = await autosend.run(session, NullTransport(), mode, now=_now())

        assert passed.state == "ушло 1"
        assert await _statuses(session) == {HOSTS[0]: MessageStatus.SENT}

    async def test_only_template_letters_stall_the_pass_and_say_what_to_do(
        self, session: AsyncSession
    ) -> None:
        await _queue(session, TEMPLATED)
        mode = await _started(session)

        passed = await autosend.run(session, NullTransport(), mode, now=_now())

        assert passed.state == f"ушло 0, {batch.TEMPLATED} — 2"
        assert passed.stall == passed.state
        assert "«Переписать шаблонные»" in passed.state
        assert mode.stopped_at is None, "очередь не пуста — режим ждёт человека"


@pytest.mark.usefixtures("filled_legal")
class TestTheButton:
    @pytest.mark.parametrize("itself", [False, True], ids=["пачка", "пачка при «уходит сама»"])
    async def test_the_button_sends_template_letters_as_before(
        self, session: AsyncSession, itself: bool
    ) -> None:
        """Кнопку нажал человек — и при идущем режиме (`send_jobs`: `automatic=True`)."""
        await _queue(session, TEMPLATED)

        report = await batch.send_queue(
            session, NullTransport(), stage=Stage.DONORS, automatic=itself
        )

        assert (report.sent, dict(report.refused)) == (2, {})


@pytest.mark.usefixtures("filled_legal")
async def test_the_queue_forecast_does_not_count_template_letters(session: AsyncSession) -> None:
    """Срок очереди — без шаблонных: проход их не шлёт, и до правки они не уйдут."""
    await _queue(session)
    await _started(session)

    found = await forecast.forecast(session, Stage.DONORS, days=7, back=0)

    assert found.queued == 3
    assert found.stuck == {batch.TEMPLATED: 2}
    assert "без 2 писем, которые не уйдут, пока их не поправят" in found.eta.words


async def test_one_alarm_with_words_for_a_queue_of_template_letters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проход за проходом стоит на шаблонных — тревога одна, через 15 минут, со словами, что
    сделать; ушло письмо — одно «прошло»."""
    told: list[str] = []

    async def alert(text: str) -> bool:
        told.append(text)
        return True

    monkeypatch.setattr(autosend_alarm, "send_alert", alert)
    watch = autosend_alarm.Watch()
    title = autosend_alarm.title_of(Stage.ADVERTISERS, "links")
    stall = f"ушло 0, {batch.TEMPLATED} — 68"
    start = datetime(2026, 11, 23, 7, 5, tzinfo=UTC)

    for minutes in range(0, 31, 5):
        await watch.see(1, title, stall, start + timedelta(minutes=minutes))
    await watch.see(1, title, None, start + timedelta(minutes=31))

    assert len(told) == 2
    assert told[0].startswith(f"тревога: {title} не уходит сама дольше 15 минут — {stall}")
    assert told[1] == f"прошло: {title} снова уходит сама (стояла: {stall})"


async def test_sales_letters_are_not_judged(session: AsyncSession) -> None:
    """У писем продаж свой коридор и своя автоотправка: письмо продаж с отличием 0 проход
    не держит."""
    domain = DomainModel(host="lead.example.test")
    campaign = CampaignModel(name="Продажи", stage=Stage.SALES, status="draft")
    session.add_all([domain, campaign])
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain.id,
        step=0,
        status=MessageStatus.QUEUED,
        subject="Hello",
        body="Hi",
        uniqueness_pct=0.0,
        idempotency_key="sales:lead.example.test:lead@lead.example.test:0",
    )
    session.add(letter)
    await session.flush()
    report = batch.BatchReport()

    kept = await batch._sendable(session, Stage.SALES, [letter.id], report, paced=_Paced())
    donors = await batch._sendable(session, Stage.DONORS, [letter.id], report, paced=_Paced())

    assert kept == [letter.id]
    assert dict(report.refused) == {batch.TEMPLATED: 1}, "тот же отбор у доноров письмо держит"
    assert donors == []


class _Paced:
    """Проход автоотправки — только то, что нужно отбору писем пачки."""

    only: frozenset[int] = frozenset()

    def boxes(self) -> frozenset[int]:
        return frozenset()

    def took(self, box_id: int, letter_id: int) -> None:
        return None
