"""Тревога «письма без исхода» называет у каждого письма свою причину (29.10.2026).

Вебхук жив, а письмо висит: прежде тревога говорила только «платформа ещё не сказала, дошло ли
оно», и причину искали в журнале платформы. Теперь у письма — его последняя отсрочка
(`letters/deferrals.py`), и тревога называет её словами: «№404 — сервер получателя не отвечает
(порт 25, тайм-аут), 14 попыток с 10:26 по Москве». Тревогу читают в Telegram, поэтому часы —
по Москве.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.delivery_trouble import Trouble
from backend.features.ops.alarms import Alarm
from backend.features.ops.silence import alarms
from backend.features.outreach.repository import EVERY_STAGE
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_silence_watchdog import _letter

#: 16:30 по Москве.
NOW = datetime(2026, 10, 29, 13, 30, tzinfo=UTC)
#: 10:26 по Москве.
SINCE = datetime(2026, 10, 29, 7, 26, tzinfo=UTC)
TIMEOUT = "error dialing remote address: dial tcp 192.0.2.10:25: i/o timeout"


def _deferred(
    letter: MessageModel, *, since: datetime, attempts: int, reason: str, kind: Trouble
) -> None:
    letter.deferred_since = since
    letter.deferred_at = since + timedelta(hours=5, minutes=56)
    letter.deferred_attempts = attempts
    letter.deferred_reason = reason
    letter.deferred_kind = kind.value


async def _delivered_later(session: AsyncSession, number: int) -> None:
    """Письмо направления, доставленное после висящих, — вебхук жив."""
    later = await _letter(
        session, status=MessageStatus.DELIVERED, sent_at=NOW - timedelta(hours=7), number=number
    )
    later.delivered_at = NOW - timedelta(hours=7)


async def _pending_alarm(session: AsyncSession) -> Alarm:
    await session.flush()
    found = await alarms(session, stages=EVERY_STAGE, now=NOW)
    return next(alarm for alarm in found if alarm.code == "delivery-pending:donors")


async def _pending(session: AsyncSession) -> str:
    return (await _pending_alarm(session)).detail


async def test_the_404_case_names_its_reason(session: AsyncSession) -> None:
    stuck = await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=8))
    _deferred(stuck, since=SINCE, attempts=14, reason=TIMEOUT, kind=Trouble.UNREACHABLE)
    await _delivered_later(session, 1)

    detail = await _pending(session)

    assert detail.startswith(
        f"Письмо №{stuck.id} ушло больше 6 часов назад, а исхода нет: сервер получателя "
        "не отвечает (порт 25, тайм-аут), 14 попыток с 10:26 по Москве. "
    )
    assert "вебхук событий работает" in detail


async def test_each_letter_has_its_own_reason(session: AsyncSession) -> None:
    letters = [
        await _letter(
            session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=12 - n), number=n
        )
        for n in range(5)
    ]
    _deferred(letters[0], since=SINCE, attempts=14, reason=TIMEOUT, kind=Trouble.UNREACHABLE)
    _deferred(
        letters[1],
        since=SINCE - timedelta(days=1),
        attempts=1,
        reason="452 4.2.2 The email account that you tried to reach is over quota",
        kind=Trouble.MAILBOX_FULL,
    )
    await _delivered_later(session, 9)

    detail = await _pending(session)

    first, second, third = (letter.id for letter in letters[:3])
    assert detail.startswith(
        f"Писем без исхода больше 6 часов: 5. №{first} — сервер получателя не отвечает "
        "(порт 25, тайм-аут), 14 попыток с 10:26 по Москве; "
        f"№{second} — ящик получателя переполнен (4.2.2), попытка в 28.10 10:26 по Москве; "
        f"№{third} — платформа о нём ещё ничего не сообщила; и ещё 2. "
    )


async def test_without_any_deferral_the_words_stay(session: AsyncSession) -> None:
    """Отсрочек не было — прежние слова: платформа о письме ещё ничего не сказала."""
    stuck = await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=8))
    await _delivered_later(session, 1)

    detail = await _pending(session)

    assert detail.startswith(
        f"Письмо №{stuck.id} ушло больше 6 часов назад, а платформа ещё не сказала, дошло ли оно"
    )


async def test_two_letters_are_both_named(session: AsyncSession) -> None:
    first = await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=9))
    second = await _letter(
        session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=8), number=1
    )
    _deferred(
        second,
        since=SINCE,
        attempts=3,
        reason="remote error: tls: handshake failure",
        kind=Trouble.TLS,
    )
    await _delivered_later(session, 2)

    detail = await _pending(session)

    assert detail.startswith(
        f"Писем без исхода больше 6 часов: 2. №{first.id} — платформа о нём ещё ничего не "
        f"сообщила; №{second.id} — сбой шифрования связи с сервером получателя (TLS), "
        "3 попытки с 10:26 по Москве. "
    )


class TestConcern:
    """Повод у висящих писем — по возрасту и виду причины: платформа повторяет до 72 часов,
    а у домена без почтового сервера и при отказе нашему адресу ждать нечего."""

    async def test_a_waiting_server_is_no_cause(self, session: AsyncSession) -> None:
        stuck = await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=8))
        _deferred(stuck, since=SINCE, attempts=14, reason=TIMEOUT, kind=Trouble.UNREACHABLE)
        await _delivered_later(session, 1)

        alarm = await _pending_alarm(session)

        assert alarm.concern is not None
        assert not alarm.concern.cause
        assert "исход придёт сам" in alarm.concern.words
        assert "исход придёт сам" not in alarm.detail  # фраза — в пометке, а не дважды

    async def test_past_the_retry_window_is_a_cause(self, session: AsyncSession) -> None:
        """Платформа повторяет 72 часа: письмо без исхода дольше — исход сам не придёт."""
        stuck = await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=80))
        _deferred(
            stuck,
            since=NOW - timedelta(hours=80),
            attempts=30,
            reason=TIMEOUT,
            kind=Trouble.UNREACHABLE,
        )
        await _delivered_later(session, 1)

        concern = (await _pending_alarm(session)).concern

        assert concern is not None
        assert concern.cause
        assert concern.words == (
            "Есть повод для беспокойства — письмо без исхода дольше 72 часов, платформа его уже "
            "не повторяет и исход сам не придёт: посмотреть исход письма в журнале писем "
            "платформы (Activity)."
        )

    async def test_a_domain_without_mail_server_is_a_cause(self, session: AsyncSession) -> None:
        stuck = await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=8))
        _deferred(
            stuck,
            since=SINCE,
            attempts=4,
            reason="unable to get mx info: failed to get IPs from PTR record",
            kind=Trouble.NO_MAIL_SERVER,
        )
        await _delivered_later(session, 1)

        concern = (await _pending_alarm(session)).concern

        assert concern is not None
        assert concern.cause
        assert "нет почтового сервера" in concern.words
        assert "проверить адрес получателя" in concern.words

    async def test_our_address_refused_is_a_cause(self, session: AsyncSession) -> None:
        """Отказ нашему адресу или IP — у любого из висящих, не только у первых трёх."""
        letters = [
            await _letter(
                session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=12 - n), number=n
            )
            for n in range(5)
        ]
        _deferred(
            letters[4],
            since=SINCE,
            attempts=2,
            reason="450 4.7.25 Client host rejected: cannot find your hostname",
            kind=Trouble.BLOCKED,
        )
        await _delivered_later(session, 9)

        concern = (await _pending_alarm(session)).concern

        assert concern is not None
        assert concern.cause
        assert "отклоняет наш адрес или IP" in concern.words
        # Письмо пятое, и причины в тревоге названы только у первых трёх: без номера не найти.
        assert f"(№{letters[4].id})" in concern.words
