"""Перезапрос цены без отправки: состояние переписки, слова и шаблон письма.

Переписка после перезапроса снова ждёт донора — до его ответа, и ответ, пришедший
раньше перезапроса, этого не отменяет. Слова о перезапросе — свои у каждого места,
где письмо называют: карточка переписки, исход письма, отказ общей очереди. Текст
письма — шаблоном без модели, и экран знает номер его шага.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.letters import compose, guards, mailbox, template
from backend.features.letters.chain import RECHECK_STEP, kind_of
from backend.features.letters.recheck_rules import days
from backend.features.letters.template import TemplateError, ZoneKind
from backend.features.outreach.threads import (
    ThreadState,
    donor_word,
    recheck_unpriced,
    state_of,
    summarize,
)
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
FRONT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api" / "priceRecheck.ts"


def _reply(*, at: datetime, price: str | None = "90", confidence: float = 0.95) -> ReplyModel:
    reply = ReplyModel(
        thread_id=1,
        kind=ReplyKind.HUMAN,
        raw_body="Our price is $90.",
        price_white=Decimal(price) if price is not None else None,
        confidence=confidence,
        placement="sells",
    )
    reply.created_at = at
    return reply


def _letter(step: int, status: MessageStatus, *, at: datetime) -> MessageModel:
    return MessageModel(
        campaign_id=1, domain_id=1, step=step, status=status, idempotency_key="k", sent_at=at
    )


class TestThreadWaitsAgain:
    def test_asked_after_the_answer_the_thread_waits_for_the_donor(self) -> None:
        answered = _reply(at=NOW - timedelta(days=200))

        state = state_of({MessageStatus.DELIVERED}, [answered], asked_at=NOW)

        assert state is ThreadState.RECHECK

    def test_the_answer_after_the_recheck_is_an_answer_again(self) -> None:
        old, new = _reply(at=NOW - timedelta(days=200)), _reply(at=NOW + timedelta(hours=1))

        state = state_of({MessageStatus.DELIVERED}, [old, new], asked_at=NOW)

        assert state is ThreadState.PRICED

    def test_an_answer_waiting_for_a_person_is_stronger(self) -> None:
        """Работа у человека важнее ожидания донора: «ждёт разбора» остаётся."""
        unsure = _reply(at=NOW - timedelta(days=200), confidence=0.3)

        state = state_of({MessageStatus.DELIVERED}, [unsure], asked_at=NOW)

        assert state is ThreadState.NEEDS_REVIEW

    def test_without_an_answer_it_is_just_waiting(self) -> None:
        """Перезапрос уходит только ответившему; без ответа — правило ушедшего письма."""
        assert state_of({MessageStatus.SENT}, [], asked_at=NOW) is ThreadState.WAITING

    @pytest.mark.parametrize(
        ("status", "state"),
        [
            (MessageStatus.SENT, ThreadState.RECHECK),
            (MessageStatus.DELIVERED, ThreadState.RECHECK),
            # Не дошедший перезапрос донор не читал — ждать его ответа не на что.
            (MessageStatus.BOUNCED, ThreadState.PRICED),
            (MessageStatus.SENDING, ThreadState.PRICED),
        ],
    )
    def test_the_list_reads_the_recheck_letter(
        self, status: MessageStatus, state: ThreadState
    ) -> None:
        letters = [
            _letter(0, MessageStatus.DELIVERED, at=NOW - timedelta(days=201)),
            _letter(RECHECK_STEP, status, at=NOW),
        ]

        summary = summarize(letters, [_reply(at=NOW - timedelta(days=200))])

        assert summary.state is state

    def test_an_answer_without_a_number_waits_for_a_person(self) -> None:
        """«Цена та же» числа не называет: переписка ждёт человека, а не пишет «цена
        получена» старой ценой."""
        old, same = (
            _reply(at=NOW - timedelta(days=200)),
            _reply(at=NOW + timedelta(hours=1), price=None),
        )

        assert recheck_unpriced(same, NOW)
        assert not recheck_unpriced(old, NOW)
        assert state_of({MessageStatus.DELIVERED}, [old, same], asked_at=NOW) is (
            ThreadState.NEEDS_REVIEW
        )

    def test_confirmed_empty_it_is_an_answer_not_the_old_price(self) -> None:
        old = _reply(at=NOW - timedelta(days=200))
        same = _reply(at=NOW + timedelta(hours=1), price=None)
        same.reviewed_at = NOW + timedelta(hours=2)

        assert state_of({MessageStatus.DELIVERED}, [old, same], asked_at=NOW) is (
            ThreadState.REPLIED
        )

    def test_a_confirmed_price_of_an_auto_reply_is_the_donor_word(self) -> None:
        """Цена из автоответа с суммой, подтверждённая человеком, — слово донора: после неё
        перезапрос ставит переписку в ожидание, как после письма человека."""
        auto = _reply(at=NOW - timedelta(days=200))
        auto.kind = ReplyKind.AUTO_REPLY
        auto.reviewed_at = NOW - timedelta(days=199)

        assert donor_word(auto)
        assert state_of({MessageStatus.DELIVERED}, [auto], asked_at=NOW) is ThreadState.RECHECK

    def test_other_stages_do_not_wait_for_a_recheck(self) -> None:
        """Перезапрос — вопрос цены донору: у рекламодателя его не бывает, и правило
        его этапа время перезапроса не читает."""
        lead = _reply(at=NOW - timedelta(days=3))

        state = state_of({MessageStatus.SENT}, [lead], Stage.ADVERTISERS, asked_at=NOW)

        assert state is ThreadState.LEAD


class TestWords:
    def test_the_letter_is_named_as_it_is(self) -> None:
        assert kind_of(RECHECK_STEP) == "перезапрос цены"

    async def test_from_the_shared_queue_it_points_to_its_own_button(
        self, session: AsyncSession
    ) -> None:
        """Перезапрос без ящика переписки — отказ пути, и слова называют его кнопку."""
        letter = _letter(RECHECK_STEP, MessageStatus.QUEUED, at=NOW)
        letter.id = 7

        choice = await mailbox.choose(
            session, letter, stage=Stage.DONORS, thread_sender_id=None, now=NOW
        )

        assert choice.sender is None
        assert choice.wrong_path
        assert "перезапрос цены" in choice.refusal
        assert "«Перезапросить цену»" in choice.refusal

    @pytest.mark.parametrize(
        ("count", "said"),
        [(1, "1 день"), (2, "2 дня"), (5, "5 дней"), (11, "11 дней"), (14, "14 дней")],
    )
    def test_days_in_words(self, count: int, said: str) -> None:
        assert days(count) == said

    @pytest.mark.parametrize(
        ("count", "said"), [(21, "21 день"), (22, "22 дня"), (150, "150 дней")]
    )
    def test_days_in_words_past_twenty(self, count: int, said: str) -> None:
        assert days(count) == said


class TestTemplate:
    def test_every_zone_is_fixed_and_signed(self) -> None:
        """Модель сюда не зовут: вопрос о цене — условие сделки. Подпись — именем
        и физическим адресом, как у всех писем доноров (решение Anthony 11.10.2026)."""
        parsed = template.recheck()

        assert {zone.kind for zone in parsed.zones} == {ZoneKind.FIXED}
        assert parsed.zone("signature").text.splitlines()[-2:] == [
            "{{sender_name}}",
            "{{postal_address}}",
        ]
        assert parsed.placeholders() == {"host", "sender_name", "postal_address"}

    def test_a_signature_without_the_address_is_refused(self) -> None:
        """Спецификация перезапроса требует подписи так же, как у всех писем доноров
        (`template.REQUIRED_IN_ZONE`): имя и почтовый адрес — без адреса шаблон не разбирается."""
        unsigned = (
            "subject: Guest article on {{host}}: current rate?\n\n"
            "[greeting] fixed\nHi,\n\n"
            "[ask] fixed\nWhat is your current price per article?\n\n"
            "[signature] fixed\nBest regards,\n{{sender_name}}\n"
        )

        with pytest.raises(TemplateError, match="postal_address"):
            template.parse(unsigned, template.RECHECK)

    def test_the_letter_names_no_metrics_and_no_price(self) -> None:
        """Число цены в письме не стоит: у ручной цены донору не скажешь, откуда она.
        Цифры бывают только в подписи — в физическом адресе."""
        parsed = template.recheck()
        rendered = compose.render(parsed, compose.values_for(host="site.example.test", domain_id=1))

        guards.assert_no_metrics(rendered.body)
        for zone in ("greeting", "ask"):
            assert not re.search(r"\d", parsed.zone(zone).text), zone
        assert not re.search(r"\d", parsed.subject)
        assert "site.example.test" in rendered.subject

    def test_without_an_address_the_letter_is_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Адреса в настройках нет — в подписи громкая метка, и отправка такое письмо
        не пропустит: перезапрос откажет словами до заведения письма (`price_recheck._text`)."""
        monkeypatch.setattr("backend.config.outreach.SENDER_NAME", "Anna Ro")
        monkeypatch.setattr("backend.config.outreach.POSTAL_ADDRESS", "")
        rendered = compose.render(
            template.recheck(), compose.values_for(host="site.example.test", domain_id=1)
        )

        assert compose.unset_in(rendered.body) == ["ФИЗИЧЕСКИЙ АДРЕС НЕ ЗАДАН"]


def test_the_screen_knows_the_recheck_step() -> None:
    """Лента переписки называет письмо по шагу (`ThreadFeed.letterTitle`): номер шага
    у экрана — тот же, что у сервера."""
    source = FRONT.read_text(encoding="utf-8")
    found = re.search(r"export const PRICE_RECHECK_STEP = (\d+);", source)

    assert found is not None
    assert int(found.group(1)) == RECHECK_STEP
