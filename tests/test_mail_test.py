"""Пробное письмо себе (`outreach mail-test`): что уходит платформе и что
остаётся в базе.

Платформа поддельная (`httpx.MockTransport`) — наружу тесты не пишут, —
а база настоящая: пробное письмо обещает не оставить в ней ни строки,
и проверяется это пересчётом всех таблиц до и после команды.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from backend.api.unsubscribe import routes as unsubscribe_routes
from backend.cli.mail_test import EXIT_NOT_SENT, EXIT_OK, run_mail_test
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import SenderStatus, Stage
from backend.features.core.models.outreach import ReplyModel, SenderModel
from backend.features.letters import unsubscribe
from backend.features.letters.probe import PROBE_DOMAIN_ID, compose_probe
from backend.features.letters.sendgrid import PROVIDER_ID_HEADER, SendGridTransport
from backend.features.letters.transport import NullTransport
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox
from backend.shared.database.base import Base
from backend.shared.sliding_window import SlidingWindow
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import FILLED, make_sender

TO = "me@ours.test"
SENDER = "anna@mail-a.example"
PROVIDER_ID = "W8Rx7c2bQvuh3sVUkwV8Hw"
OUR_ID = re.compile(r"<[0-9a-f]{32}\.m0@mail-a\.example>")


class Platform:
    """Поддельная платформа: запоминает запросы и отвечает «принято»."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(json.loads(request.content))
        return httpx.Response(202, headers={PROVIDER_ID_HEADER: PROVIDER_ID})


@pytest.fixture
async def platform(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[Platform, SendGridTransport]]:
    """Боевой транспорт на поддельной платформе, предохранитель — свой ящик.

    Список разрешённых задаётся настройкой, а не транспорту напрямую:
    так его видит и транспорт, и сообщение команды о предохранителе.
    """
    monkeypatch.setattr(outreach_cfg, "ALLOWED_RECIPIENTS", (TO,))
    fake = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        yield fake, SendGridTransport(api_key="sg-test-key", http=http)


async def _rows(session: AsyncSession) -> dict[str, int]:
    """Сколько строк в каждой таблице схемы."""
    counts: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        counts[table.name] = int(await session.scalar(select(func.count()).select_from(table)) or 0)
    return counts


class TestWhatGoesOut:
    async def test_probe_carries_our_identity_and_no_tracking(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake, transport = platform
        await make_sender(session, SENDER)

        code = await run_mail_test(session, transport, to=TO, sender=None, stage=Stage.DONORS)

        assert code == EXIT_OK
        sent = fake.seen[0]
        assert sent["personalizations"][0]["to"] == [{"email": TO}]
        assert sent["from"] == {"email": SENDER, "name": FILLED["OUTREACH_SENDER_NAME"]}
        own = sent["headers"]["Message-ID"]
        assert OUR_ID.fullmatch(own)
        reply = sent["reply_to"]["email"]
        assert reply.startswith("anna+m0.")
        assert reply.endswith("@replies.mail-a.example")
        assert sent["tracking_settings"] == {
            "click_tracking": {"enable": False},
            "open_tracking": {"enable": False},
        }
        unsubscribe_page = FILLED["OUTREACH_UNSUBSCRIBE_URL"]
        assert sent["headers"]["List-Unsubscribe"].startswith(f"<{unsubscribe_page}/u0.")
        assert "example.org" in sent["subject"]
        assert "In-Reply-To" not in sent["headers"]

        printed = capsys.readouterr().out
        assert PROVIDER_ID in printed
        assert own in printed
        assert "d=mail-a.example" in printed
        assert "replies.mail-a.example" in printed
        assert "Предохранитель включён" in printed

    async def test_nothing_is_written(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
    ) -> None:
        """Ни строки письма, ни расхода капа, ни журнала: пробное письмо —
        не рассылка, и считать его рассылкой значит отнять письмо у донора."""
        fake, transport = platform
        await make_sender(session, SENDER)
        before = await _rows(session)

        await run_mail_test(session, transport, to=TO, sender=None, stage=Stage.DONORS)

        assert fake.seen, "письмо должно было уйти — иначе проверять нечего"
        assert await _rows(session) == before

    async def test_null_transport_says_the_letter_went_nowhere(
        self,
        session: AsyncSession,
        filled_legal: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Путь тот же и у нулевого транспорта, но он честно говорит, что
        письмо никуда не ушло, и не просит смотреть заголовки."""
        await make_sender(session, SENDER)
        before = await _rows(session)

        code = await run_mail_test(
            session, NullTransport(), to="me@box.example.test", sender=None, stage=Stage.DONORS
        )

        assert code == EXIT_OK
        printed = capsys.readouterr().out
        assert "НЕ ушло" in printed
        assert "null-0" in printed
        assert "Что проверить" not in printed
        assert await _rows(session) == before

    async def test_first_enabled_box_of_the_stage_is_taken(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
    ) -> None:
        fake, transport = platform
        switched_off = await make_sender(session, "off@mail-z.example")
        switched_off.enabled = False
        session.add(
            SenderModel(
                domain="mail-b.example",
                email="max@mail-b.example",
                stage=Stage.ADVERTISERS,
                daily_cap=20,
                status=SenderStatus.FREE,
                enabled=True,
            )
        )
        await make_sender(session, SENDER)

        await run_mail_test(session, transport, to=TO, sender=None, stage=Stage.DONORS)

        assert fake.seen[0]["from"]["email"] == SENDER

    async def test_named_box_and_the_advertiser_offer(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
    ) -> None:
        """Оффер рекламодателю без найденной ссылки не собирается — у пробного
        письма ссылка пробная, а адрес ответа — на домене названного ящика."""
        fake, transport = platform

        code = await run_mail_test(
            session, transport, to=TO, sender="Max@Mail-B.example", stage=Stage.ADVERTISERS
        )

        assert code == EXIT_OK
        sent = fake.seen[0]
        assert sent["from"]["email"] == "max@mail-b.example"
        assert sent["reply_to"]["email"].endswith("@replies.mail-b.example")
        assert sent["headers"]["Message-ID"].endswith(".m0@mail-b.example>")
        assert "sample anchor" in sent["content"][0]["value"]


class TestRefusals:
    async def test_no_enabled_box_says_what_to_do(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake, transport = platform

        code = await run_mail_test(session, transport, to=TO, sender=None, stage=Stage.DONORS)

        assert code == EXIT_NOT_SENT
        assert "outreach sender-add" in capsys.readouterr().out
        assert fake.seen == []

    async def test_unready_text_is_refused_as_a_real_letter_is(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Объяснение то же, что у письма донору: пробное письмо проверяет
        ровно ту отправку, что будет, а не свою."""
        monkeypatch.setattr(outreach_cfg, "SENDER_NAME", "")
        fake, transport = platform
        await make_sender(session, SENDER)

        code = await run_mail_test(session, transport, to=TO, sender=None, stage=Stage.DONORS)

        assert code == EXIT_NOT_SENT
        assert (
            "Пробное письмо пока не отправить: не задано имя отправителя — настраивается "
            "при подключении почты" in capsys.readouterr().out
        )
        assert fake.seen == []

    async def test_safety_catch_holds(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake, transport = platform
        await make_sender(session, SENDER)

        code = await run_mail_test(
            session, transport, to="stranger@elsewhere.test", sender=None, stage=Stage.DONORS
        )

        assert code == EXIT_NOT_SENT
        printed = capsys.readouterr().out
        assert "не отправлено: Адрес stranger@elsewhere.test не в списке разрешённых" in printed
        assert "Держите там свои ящики" in printed
        assert fake.seen == []


class TestWhatTheProbeLeavesBehind:
    async def test_reply_to_a_probe_is_kept_unbound(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Метка `m0` не указывает ни на одно письмо: ответ принимается
        непривязанным и виден на экране — этого достаточно, чтобы убедиться,
        что ответы доходят."""
        probe = compose_probe(to=TO, sender_email=SENDER, stage=Stage.DONORS, real=True)
        assert probe.outgoing.reply_to is not None

        got = await Inbox(session).accept(
            Incoming(
                message_id="<re-probe@ours.test>",
                to=(probe.outgoing.reply_to,),
                from_email=TO,
                subject="Re: probe",
                text="Got it, looks fine.",
                in_reply_to=probe.outgoing.internet_message_id,
            )
        )

        assert not got.bound
        assert got.needs_review
        reply = await session.get(ReplyModel, got.reply_id)
        assert reply is not None
        assert reply.thread_id is None

    async def test_unsubscribe_link_of_a_probe_unsubscribes_nobody(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Донора №0 нет: страница отвечает «ссылка не работает» и не пишет
        ничего — ни на открытие, ни на нажатие, ни на отписку в один клик."""
        # Счётчик частоты общий на процесс: чужие тесты не должны отнимать
        # у этого его две попытки.
        monkeypatch.setattr(unsubscribe_routes, "_throttle", SlidingWindow())
        label = unsubscribe.url_for(PROBE_DOMAIN_ID).rsplit("/", 1)[1]
        before = await _rows(session)

        shown = await client.get(f"/api/unsubscribe/{label}")
        clicked = await client.post(f"/api/unsubscribe/{label}")

        assert shown.status_code == clicked.status_code == 404
        assert "no longer works" in clicked.text
        assert await _rows(session) == before
