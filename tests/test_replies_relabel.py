"""Переразбор вида лежащих ответов нынешними правилами (`replies/relabel.py`, `replies-relabel`).

Проверка прода 25.11.2026: подтверждения служб поддержки без номера заявки, пришедшие до правила
«служба обещает ответить», остались «ответом человека» и ждали его в «Диалогах с рекламодателями».
Проверяется: показ называет такой ответ и правило и ничего не пишет; запись делает его автоответом
и кладёт правило причиной (`kind_reason`, 27.11.2026); живой ответ, ответ с решением человека и
ответ продаж не трогаются.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import pytest
from backend.cli import main as cli_main
from backend.cli import replies_relabel
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.outreach import CampaignModel, ReplyModel, ThreadModel
from backend.features.replies import relabel
from backend.features.replies.pipeline import Inbox
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_letters_advertisers import NOW, SECRET, answer_to, sent_offer

#: Подтверждение службы без номера заявки — так оно лежит в базе с прода (бренд заменён).
OLD_RECEIPT = (
    "Thank you for contacting us. Your enquiry is important to us. We’ve received your email, and "
    "a member of our Customer Service Team will review your request and get back to you as soon as "
    "possible. Kind regards, The Brand Customer Service Team"
)
LIVE = "Interesting. Could you send the list of sites and the terms?"


async def _stored(session: AsyncSession, monkeypatch: pytest.MonkeyPatch, text: str) -> ReplyModel:
    """Ответ рекламодателя, принятый как ответ человека, с текстом `text` — каким он лежит в базе
    с тех пор, как правила ещё не было."""
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    got = await Inbox(session, now=NOW).accept(answer_to(await sent_offer(session), LIVE))
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    assert reply.kind is ReplyKind.HUMAN
    reply.raw_body = text
    await session.commit()
    return reply


@pytest.mark.usefixtures("filled_legal")
class TestRelabel:
    async def test_show_names_the_old_receipt_and_writes_nothing(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        reply = await _stored(session, monkeypatch, OLD_RECEIPT)
        reply_id = reply.id

        assert await replies_relabel.run(session, write=False) == 0

        said = capsys.readouterr().out
        assert f"№{reply_id} advertisers: станет автоответом — служба обещает ответить" in said
        assert "Это показ — в базе ничего не изменилось. Станут автоответом: 1." in said
        await session.refresh(reply)
        assert reply.kind is ReplyKind.HUMAN

    async def test_write_makes_it_an_auto_reply(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        reply = await _stored(session, monkeypatch, OLD_RECEIPT)

        assert await replies_relabel.run(session, write=True) == 0

        await session.refresh(reply)
        assert reply.kind is ReplyKind.AUTO_REPLY
        # Причина — правило, которое назвало ответ автоответом: заголовков в базе нет.
        assert reply.kind_reason == "служба обещает ответить"
        assert "Записано: автоответом стали 1." in capsys.readouterr().out
        assert await relabel.candidates(session) == []

    async def test_a_reply_decided_between_show_and_write_is_not_touched(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Показ нашёл ответ, а до записи человек взял его в работу: запись его не трогает
        (ревью продаж 25.11.2026 — гонка с «Взять в работу» и «В отказ»)."""
        reply = await _stored(session, monkeypatch, OLD_RECEIPT)
        reply_id = reply.id
        found = await relabel.candidates(session)
        await session.execute(
            update(ReplyModel)
            .where(ReplyModel.id == reply_id)
            .values(reviewed_by="анна@site.com", reviewed_at=datetime(2026, 9, 22, tzinfo=UTC))
        )
        await session.commit()

        async def stale(_session: object) -> list[relabel.Relabel]:
            return found

        monkeypatch.setattr(replies_relabel.relabel, "candidates", stale)

        assert await replies_relabel.run(session, write=True) == 0

        said = capsys.readouterr().out
        assert "Записано: автоответом стали 0." in said
        assert "Не тронуты: 1 — их уже решил человек." in said
        kept = await session.get(ReplyModel, reply_id)
        assert kept is not None
        await session.refresh(kept)
        assert kept.kind is ReplyKind.HUMAN
        assert kept.kind_reason is None

    async def test_a_live_answer_stays_a_human(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        await _stored(session, monkeypatch, LIVE)

        assert await relabel.candidates(session) == []
        assert await replies_relabel.run(session, write=True) == 0
        assert "Автоответов среди ответов, ждущих человека, нет." in capsys.readouterr().out

    async def test_a_reply_a_human_decided_is_left_alone(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reply = await _stored(session, monkeypatch, OLD_RECEIPT)
        reply.reviewed_by, reply.reviewed_at = "анна@site.com", datetime(2026, 9, 22, tzinfo=UTC)
        await session.commit()

        assert await relabel.candidates(session) == []

    async def test_sales_replies_are_left_to_sales(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reply = await _stored(session, monkeypatch, OLD_RECEIPT)
        thread = await session.get(ThreadModel, reply.thread_id)
        assert thread is not None
        campaign = await session.get(CampaignModel, thread.campaign_id)
        assert campaign is not None
        campaign.stage = Stage.SALES
        await session.commit()

        assert await relabel.candidates(session) == []


async def test_command_shows_by_default_and_writes_with_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[bool] = []

    async def run(_session: object, *, write: bool) -> int:
        asked.append(write)
        return 0

    monkeypatch.setattr(replies_relabel, "run", run)
    monkeypatch.setattr(replies_relabel, "check_storage", lambda: None)

    assert await replies_relabel.cmd_replies_relabel(argparse.Namespace(yes=False)) == 0
    assert await replies_relabel.cmd_replies_relabel(argparse.Namespace(yes=True)) == 0
    assert asked == [False, True]


def test_command_is_registered() -> None:
    args = cli_main.build_parser().parse_args(["replies-relabel", "--yes"])

    assert args.yes is True
