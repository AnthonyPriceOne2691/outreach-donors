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
from backend.features.outreach.threads import ThreadState, summarize, superseded_by

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _sender(**extra: object) -> SenderModel:
    values: dict[str, object] = {
        "domain": "mail.example.test",
        "email": "outreach@mail.example.test",
        "stage": Stage.DONORS,
        "daily_cap": 20,
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


def _reply(
    kind: ReplyKind,
    *,
    price: Decimal | None = None,
    confidence: float | None = None,
    reviewed: bool = False,
    placement: str | None = None,
) -> ReplyModel:
    reply = ReplyModel(
        thread_id=1,
        kind=kind,
        raw_body="текст",
        price_white=price,
        confidence=confidence,
        reviewed_at=NOW if reviewed else None,
        placement=placement,
    )
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
            pause_reason="доля отказов",
        )

        enable(sender, now=NOW)

        assert sender.enabled
        assert sender.warmup_started_at == NOW
        assert warmup_state(sender, now=NOW).allowance == WARMUP_FIRST_DAY
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
        """Разобранный и подтверждённый ответ без цены — это «ответил»:
        работы по нему больше нет."""
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, confidence=0.95)],
        )

        assert summary.state is ThreadState.REPLIED

    def test_unparsed_answer_waits_for_a_person(self) -> None:
        """Ответ, по которому надо принять решение руками, не должен
        выглядеть как «ответил»: очередь работы прячется внутри слова,
        которое звучит как «всё хорошо»."""
        summary = summarize([_message(MessageStatus.DELIVERED)], [_reply(ReplyKind.HUMAN)])

        assert summary.state is ThreadState.NEEDS_REVIEW

    def test_price_beats_answer(self) -> None:
        """Цена сильнее просто ответа: ради неё всё и затевалось."""
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, price=Decimal("250"), confidence=0.95)],
        )

        assert summary.state is ThreadState.PRICED
        assert summary.price_white == Decimal("250")

    def test_unsure_price_is_not_a_received_price(self) -> None:
        """Диалог с неуверенным разбором не должен выглядеть законченным:
        список врал бы именно там, где по нему принимают решения."""
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, price=Decimal("250"), confidence=0.4)],
        )

        assert summary.state is ThreadState.NEEDS_REVIEW

    def test_confirmed_by_a_person_is_a_received_price(self) -> None:
        """Подтверждение человека сильнее любой уверенности модели."""
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, price=Decimal("250"), confidence=0.4, reviewed=True)],
        )

        assert summary.state is ThreadState.PRICED

    def test_price_column_is_the_settled_price_not_the_first_sum(self) -> None:
        """Боевой прогон 06.10: в «Диалогах» стояло 250 $ из ответа, ждущего
        человека, а в карточке донора — 150 $ из следующего, подтверждённого.
        Колонка берёт последнюю принятую цену — как карточка."""
        waiting = _reply(ReplyKind.HUMAN, price=Decimal("250"), confidence=0.6)
        settled = _reply(ReplyKind.HUMAN, price=Decimal("150"), confidence=0.95)
        settled.created_at = NOW + timedelta(hours=1)
        later_waiting = _reply(ReplyKind.HUMAN, price=Decimal("300"), confidence=0.5)
        later_waiting.created_at = NOW + timedelta(hours=2)

        summary = summarize([_message(MessageStatus.DELIVERED)], [waiting, settled, later_waiting])

        assert summary.price_white == Decimal("150")

    def test_of_two_settled_prices_the_later_one(self) -> None:
        first = _reply(ReplyKind.HUMAN, price=Decimal("200"), confidence=0.95)
        second = _reply(ReplyKind.HUMAN, price=Decimal("180"), confidence=0.4, reviewed=True)
        second.created_at = NOW + timedelta(days=1)

        summary = summarize([_message(MessageStatus.DELIVERED)], [second, first])

        assert summary.price_white == Decimal("180")

    def test_only_unconfirmed_sum_is_no_price_yet(self) -> None:
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, price=Decimal("250"), confidence=0.4)],
        )

        assert summary.price_white is None

    def test_decline_is_a_finished_answer(self) -> None:
        """«Не продаём» — законченный ответ, как цена: работы по нему нет."""
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, confidence=0.9, placement="declines")],
        )

        assert summary.state is ThreadState.DECLINED

    def test_free_guest_post_is_its_own_state(self) -> None:
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, confidence=0.9, placement="free")],
        )

        assert summary.state is ThreadState.FREE

    def test_unsure_decline_still_waits(self) -> None:
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, confidence=0.0, placement="declines")],
        )

        assert summary.state is ThreadState.NEEDS_REVIEW

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


class TestSupersededAnswer:
    """Проверка прода 10.10.2026: донор написал «250 $» (разбор не уверен), следом
    уточнил «150 $» (уверен, цена в карточке), а карточка переписки звала подтвердить
    250 — «Подтвердить» записал бы донору старую цену. Ответ, после которого донор
    назвал цену и она принята, разбора не ждёт: правило одно на числа и на экран."""

    def test_unsure_price_answered_by_a_later_settled_one_waits_for_nobody(self) -> None:
        unsure = _reply(ReplyKind.HUMAN, price=Decimal("250"), confidence=0.6)
        sure = _reply(ReplyKind.HUMAN, price=Decimal("150"), confidence=0.93)
        sure.created_at = NOW + timedelta(minutes=3)

        summary = summarize([_message(MessageStatus.DELIVERED)], [unsure, sure])

        assert superseded_by(unsure, [unsure, sure]) is sure
        assert superseded_by(sure, [unsure, sure]) is None
        assert summary.state is ThreadState.PRICED
        assert summary.price_white == Decimal("150")

    def test_price_confirmed_by_a_person_later_supersedes_too(self) -> None:
        """Принята — подтверждена человеком или разобрана уверенно, как у цены переписки."""
        unsure = _reply(ReplyKind.HUMAN, price=Decimal("250"), confidence=0.6)
        confirmed = _reply(ReplyKind.HUMAN, price=Decimal("150"), confidence=0.3, reviewed=True)
        confirmed.created_at = NOW + timedelta(hours=1)

        assert superseded_by(unsure, [confirmed, unsure]) is confirmed

    def test_thanks_without_a_price_after_an_unsure_one_still_waits(self) -> None:
        """«Спасибо» без цены цены не называет — старый разбор по-прежнему ждёт человека."""
        unsure = _reply(ReplyKind.HUMAN, price=Decimal("250"), confidence=0.6)
        thanks = _reply(ReplyKind.HUMAN, confidence=0.95)
        thanks.created_at = NOW + timedelta(hours=1)

        summary = summarize([_message(MessageStatus.DELIVERED)], [unsure, thanks])

        assert superseded_by(unsure, [unsure, thanks]) is None
        assert summary.state is ThreadState.NEEDS_REVIEW

    def test_unsure_answer_after_a_settled_price_waits_for_a_person(self) -> None:
        """Обратный порядок: цена принята, а следом донор написал то, что разбор
        не понял. Этот ответ экран зовёт разобрать — и числа его считают: до 10.10.2026
        цена стояла раньше «ждёт разбора», и меню такой диалог не считало. Цена
        в колонке — прежняя принятая."""
        sure = _reply(ReplyKind.HUMAN, price=Decimal("150"), confidence=0.93)
        unsure = _reply(ReplyKind.HUMAN, price=Decimal("200"), confidence=0.5)
        unsure.created_at = NOW + timedelta(days=1)

        summary = summarize([_message(MessageStatus.DELIVERED)], [sure, unsure])

        assert summary.state is ThreadState.NEEDS_REVIEW
        assert summary.price_white == Decimal("150")

    def test_unsure_answer_after_a_decline_waits_for_a_person(self) -> None:
        """«Не продаём», а следом — неуверенная цена: донор, может быть, передумал."""
        declined = _reply(ReplyKind.HUMAN, confidence=0.9, placement="declines")
        unsure = _reply(ReplyKind.HUMAN, price=Decimal("90"), confidence=0.5)
        unsure.created_at = NOW + timedelta(days=1)

        summary = summarize([_message(MessageStatus.DELIVERED)], [declined, unsure])

        assert summary.state is ThreadState.NEEDS_REVIEW


class TestAdvertiserThreadState:
    """Ответ рекламодателя — лид, а не цена: «ждёт разбора» с формой цены
    было бы неправдой, подтверждение цены для него — отказ."""

    def test_answer_is_a_lead_waiting_for_a_human(self) -> None:
        summary = summarize(
            [_message(MessageStatus.DELIVERED)], [_reply(ReplyKind.HUMAN)], Stage.ADVERTISERS
        )

        assert summary.state is ThreadState.LEAD

    def test_taken_lead_waits_for_nobody(self) -> None:
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, reviewed=True)],
            Stage.ADVERTISERS,
        )

        assert summary.state is ThreadState.LEAD_TAKEN

    def test_new_answer_after_a_taken_one_is_work_again(self) -> None:
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN, reviewed=True), _reply(ReplyKind.HUMAN)],
            Stage.ADVERTISERS,
        )

        assert summary.state is ThreadState.LEAD

    def test_unsubscribe_is_still_stronger(self) -> None:
        summary = summarize(
            [_message(MessageStatus.DELIVERED)],
            [_reply(ReplyKind.HUMAN), _reply(ReplyKind.UNSUBSCRIBE)],
            Stage.ADVERTISERS,
        )

        assert summary.state is ThreadState.UNSUBSCRIBED

    def test_auto_reply_is_still_silence(self) -> None:
        summary = summarize(
            [_message(MessageStatus.DELIVERED)], [_reply(ReplyKind.AUTO_REPLY)], Stage.ADVERTISERS
        )

        assert summary.state is ThreadState.WAITING

    def test_donor_answer_is_unchanged(self) -> None:
        """Этап 1 не должен заметить правки: тот же ответ — «ждёт разбора»."""
        summary = summarize([_message(MessageStatus.DELIVERED)], [_reply(ReplyKind.HUMAN)])

        assert summary.state is ThreadState.NEEDS_REVIEW
