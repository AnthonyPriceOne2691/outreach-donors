"""Окно получателя у добивок доноров и рекламодателей (`letters/followup_window.py`, 16.11.2026).

Добивка донору или рекламодателю в субботу по их часам не уходит и не теряется: её срок — открытие
окна в понедельник, 09:00 по часам получателя, плюс сдвиг (генератор с зерном). Пояс — правилом
страны первых писем автоотправки: донору — страна его трафика, рекламодателю по ссылке — страна
аудитории донора его ссылки, неизвестна — запасной пояс. Завал выходных уходит в понедельник по
одной добивке с ящика, а не одной минутой. У продаж окно — политика этапа: его судят здесь, до
сборки текста, ответом моста (17.11.2026, `tests/test_mail_watch_quiet.py`), а нет у этапа окна —
только отправка (`tests/test_sales_send_window.py`). Ящики и адреса — выдуманные, почта — нулевая.
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core import stages, window
from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import followup_window
from backend.features.letters.followup_report import PassReport
from backend.features.letters.followups import Chain, Claimed, send_due
from backend.features.letters.transport import NullTransport
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_followups_pace import GAP, _gaps, _on_one_box
from tests.test_letters_advertisers import donor_of_the_link, sent_offer

#: Суббота 26.09.2026, 12:00 UTC: первые письма ушли в понедельник 21.09, добивки подошли во вторник.
SATURDAY = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
#: Сдвиг от открытия окна — первый из генератора с зерном (`window.JITTER`), не дальше получаса.
SHIFT = timedelta(minutes=30) * random.Random(7).random()


@pytest.fixture(autouse=True)
def _office_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    """Окно по умолчанию — пн–пт 09:00–18:00 по часам получателя, запасной пояс — Берлин; сдвиг —
    генератором с зерном."""
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_DAYS", frozenset(range(5)))
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_OPENS", time(9))
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_CLOSES", time(18))
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_ZONE", "Europe/Berlin")
    monkeypatch.setattr(window, "JITTER", random.Random(7))


def _monday_nine(zone: str) -> datetime:
    """Понедельник 28.09.2026, 09:00 по часам пояса `zone` — в UTC."""
    return datetime(2026, 9, 28, 9, 0, tzinfo=ZoneInfo(zone)).astimezone(UTC)


async def _donor_in(session: AsyncSession, letter: MessageModel, country: str | None) -> None:
    await session.execute(
        update(DonorModel).where(DonorModel.domain_id == letter.domain_id).values(geo=country)
    )


class TestSaturday:
    @pytest.mark.parametrize(
        ("country", "zone"),
        [("jp", "Asia/Tokyo"), (None, "Europe/Berlin")],
        ids=["jp", "неизвестна"],
    )
    async def test_a_donor_followup_waits_for_monday_nine_by_the_donors_clock(
        self,
        session: AsyncSession,
        filled_legal: None,
        caplog: pytest.LogCaptureFixture,
        country: str | None,
        zone: str,
    ) -> None:
        """Пояс донора — страна его трафика; страны нет — запасной пояс, и добивка не ждёт вечно."""
        (first,) = await _on_one_box(session, "donor.example.test")
        await _donor_in(session, first, country)

        with caplog.at_level(logging.INFO, logger=followup_window.__name__):
            report = await send_due(session, transport=NullTransport(), limit=5, now=SATURDAY)

        assert (report.sent, report.postponed, report.outside) == (0, 1, 1)
        assert "отложено 1 (из них до окна получателя — 1)" in report.as_report
        await session.refresh(first)
        assert first.next_action_at == _monday_nine(zone) + SHIFT
        assert first.next_action_at < _monday_nine(zone) + timedelta(minutes=30)
        assert (
            "donor.example.test — отложена: вне окна получателя (пн–пт 09:00–18:00 по его часам), "
            f"уйдёт не раньше пн 28.09 09:{SHIFT.seconds // 60:02d} ({zone})"
        ) in caplog.text

    async def test_an_advertiser_followup_waits_by_the_clock_of_its_links_audience(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Рекламодатель по ссылке купил место там, где его покупатели: пояс — страна аудитории
        донора его ссылки, а не запасной."""
        first = await sent_offer(session, followup_days=[1, 2])
        (await donor_of_the_link(session)).geo = "us"
        await session.flush()

        report = await send_due(session, transport=NullTransport(), limit=5, now=SATURDAY)

        assert (report.sent, report.outside) == (0, 1)
        await session.refresh(first)
        assert first.next_action_at == _monday_nine("America/New_York") + SHIFT


class TestWeekdays:
    async def test_the_window_is_the_recipients_own(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Понедельник 01:00 UTC: в Токио 10:00 — добивка уходит, в Берлине 03:00 — ждёт 09:00.
        Окно у каждой добивки — по часам её получателя, а не по нашим."""
        (tokyo,) = await _on_one_box(session, "tokyo.example.test")
        (berlin,) = await _on_one_box(session, "berlin.example.test")
        await _donor_in(session, tokyo, "jp")
        early = _monday_nine("Asia/Tokyo") + timedelta(hours=1)

        report = await send_due(session, transport=NullTransport(), limit=5, now=early)

        assert (report.sent, report.outside) == (1, 1)
        await session.refresh(berlin)
        assert berlin.next_action_at == _monday_nine("Europe/Berlin") + SHIFT
        went = await session.scalar(select(MessageModel.domain_id).where(MessageModel.step == 1))
        assert went == tokyo.domain_id

    async def test_the_weekend_backlog_goes_out_one_by_one_on_monday(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Три добивки ящика подошли за выходные: в субботу все три ждут окна — понедельник, первые
        полчаса после 09:00, — а в понедельник уходят по одной с паузой ящика, не одной минутой."""
        letters = await _on_one_box(
            session, "one.example.test", "two.example.test", "three.example.test"
        )
        opens = _monday_nine("Europe/Berlin")

        saturday = await send_due(
            session, transport=NullTransport(), limit=5, now=SATURDAY, gaps=_gaps()
        )

        assert (saturday.sent, saturday.outside) == (0, 3)
        for letter in letters:
            await session.refresh(letter)
            assert letter.next_action_at is not None
            assert opens <= letter.next_action_at < opens + timedelta(minutes=30)

        start = opens + timedelta(minutes=30)
        passes = [
            await send_due(session, transport=NullTransport(), limit=5, now=moment, gaps=_gaps())
            for moment in (start, start + GAP, start + 2 * GAP)
        ]

        assert [(found.sent, found.resting) for found in passes] == [(1, 2), (1, 1), (1, 0)]
        sent = await session.scalars(
            select(MessageModel.sent_at)
            .where(MessageModel.step == 1)
            .order_by(MessageModel.sent_at)
        )
        assert list(sent) == [start, start + GAP, start + 2 * GAP]


async def test_sales_followups_without_a_window_in_the_policy_are_not_judged_here(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """У продаж окно — политика этапа (`stages.mail_policy`): модуль продаж к мосту не подключён —
    окна у этапа нет, и здесь добивка продаж не задерживается и в отчёт не попадает, что бы ни
    показывали часы. Отказ — словами отправки."""
    monkeypatch.setattr(stages._SALES, "load", None)
    domain = DomainModel(host="lead.example.test")
    campaign = CampaignModel(name="Продажи", stage=Stage.SALES, status="draft")
    session.add_all([domain, campaign])
    await session.flush()
    previous = MessageModel(
        campaign_id=campaign.id, domain_id=domain.id, step=0, idempotency_key="sales:lead:0"
    )
    session.add(previous)
    await session.flush()
    claimed = Claimed(
        previous_id=previous.id,
        thread_id=None,
        campaign_id=campaign.id,
        domain_id=domain.id,
        contact_id=None,
        sender_id=None,
        step=1,
        host=domain.host,
        anchor=None,
    )
    report = PassReport()

    held = await followup_window.holds(session, Chain(session, now=SATURDAY), claimed, report)

    assert held is False
    assert report == PassReport()
