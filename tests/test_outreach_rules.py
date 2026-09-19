"""Правила рассылки без сервера: разгон и состояние диалога.

Оба правила стоят денег, если ошибиться: разгон — репутации домена,
состояние диалога — решений, которые по нему принимают.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.features.core.domain import MessageStatus, ReplyKind, SenderStatus, Stage
from backend.features.core.models.outreach import MessageModel, ReplyModel, SenderModel
from backend.features.outreach.senders import (
    WARMUP_FIRST_DAY,
    disable,
    enable,
    warmup_state,
)
from backend.features.outreach.threads import ThreadState, summarize

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _sender(**extra: object) -> SenderModel:
    values: dict[str, object] = {
        "domain": "mail.example.test",
        "email": "outreach@mail.example.test",
        "stage": Stage.DONORS,
        "daily_cap": 20,
        "sent_today": 0,
        "status": SenderStatus.FREE,
        "enabled": True,
    }
    values.update(extra)
    return SenderModel(**values)


def _message(status: MessageStatus, *, sent: bool = True) -> MessageModel:
    return MessageModel(
        campaign_id=1,
        domain_id=1,
        step=0,
        status=status,
        idempotency_key="k",
        sent_at=NOW if sent else None,
    )


def _reply(kind: ReplyKind, *, price: Decimal | None = None) -> ReplyModel:
    reply = ReplyModel(thread_id=1, kind=kind, raw_body="текст", price_white=price)
    reply.created_at = NOW
    return reply


class TestWarmup:
    def test_first_day_is_small(self) -> None:
        sender = _sender(warmup_started_at=NOW)

        state = warmup_state(sender, now=NOW)

        assert state.day == 1
        assert state.allowance == WARMUP_FIRST_DAY
        assert not state.finished

    def test_allowance_grows_with_days(self) -> None:
        sender = _sender(warmup_started_at=NOW - timedelta(days=2))

        assert warmup_state(sender, now=NOW).allowance > WARMUP_FIRST_DAY

    def test_allowance_never_exceeds_daily_cap(self) -> None:
        sender = _sender(warmup_started_at=NOW - timedelta(days=100))

        state = warmup_state(sender, now=NOW)

        assert state.allowance == sender.daily_cap
        assert state.finished

    def test_no_warmup_means_full_cap(self) -> None:
        """Домен, который никогда не разгоняли, — это домен из прошлой
        жизни сервиса: не выдумываем ему разгон задним числом."""
        state = warmup_state(_sender(warmup_started_at=None), now=NOW)

        assert state.allowance == 20
        assert state.day == 0


class TestSwitching:
    def test_enabling_restarts_warmup(self) -> None:
        """Главное правило экрана: включённый заново домен начинает
        с начала. Полный кап сразу — это добить репутацию, которая и так
        пошатнулась, а она не восстанавливается."""
        sender = _sender(
            enabled=False,
            status=SenderStatus.PAUSED,
            warmup_started_at=NOW - timedelta(days=30),
            sent_today=17,
            pause_reason="доля отказов",
        )

        enable(sender, now=NOW)

        assert sender.enabled
        assert sender.warmup_started_at == NOW
        assert warmup_state(sender, now=NOW).allowance == WARMUP_FIRST_DAY
        assert sender.sent_today == 0
        assert sender.pause_reason is None

    def test_disabling_keeps_the_reason(self) -> None:
        sender = _sender()

        disable(sender, "доля отказов 7%", now=NOW)

        assert not sender.enabled
        assert sender.status is SenderStatus.PAUSED
        assert sender.pause_reason == "доля отказов 7%"
        assert sender.paused_at == NOW


class TestThreadState:
    def test_auto_reply_is_not_an_answer(self) -> None:
        """«Я в отпуске до понедельника» не переводит диалог в «ответил»:
        иначе половина цепочек оборвётся ни на чём."""
        summary = summarize([_message(MessageStatus.DELIVERED)], [_reply(ReplyKind.AUTO_REPLY)])

        assert summary.state is ThreadState.WAITING

    def test_human_answer_moves_the_thread(self) -> None:
        summary = summarize([_message(MessageStatus.DELIVERED)], [_reply(ReplyKind.HUMAN)])

        assert summary.state is ThreadState.REPLIED

    def test_price_beats_answer(self) -> None:
        """Цена сильнее просто ответа: ради неё всё и затевалось."""
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, price=Decimal("250"))],
        )

        assert summary.state is ThreadState.PRICED
        assert summary.price_white == Decimal("250")

    def test_unsubscribe_beats_everything(self) -> None:
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, price=Decimal("250")), _reply(ReplyKind.UNSUBSCRIBE)],
        )

        assert summary.state is ThreadState.UNSUBSCRIBED

    def test_bounce_is_not_an_answer_but_stops_the_chain(self) -> None:
        summary = summarize([_message(MessageStatus.BOUNCED)], [_reply(ReplyKind.BOUNCE)])

        assert summary.state is ThreadState.BOUNCED

    def test_nothing_sent_yet(self) -> None:
        summary = summarize([_message(MessageStatus.QUEUED, sent=False)], [])

        assert summary.state is ThreadState.QUEUED
        assert summary.messages_sent == 0
