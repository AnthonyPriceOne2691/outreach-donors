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
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.api.unsubscribe import routes as unsubscribe_routes
from backend.cli.mail_test import EXIT_NOT_SENT, EXIT_OK, run_mail_test
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import SenderStatus, Stage, SuppressionReason
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import ReplyModel, SenderModel
from backend.features.letters import stoplist, unsubscribe
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


class TestTheStopList:
    """Проба судится стоп-листом, как настоящая отправка (`probe.check_stoplist`).

    Команда — единственный путь к почте мимо `Sending.send`, и до 28.10.2026 адрес
    ограничивал только предохранитель: на боевой учётке его список пуст, и проба ушла
    бы адресу из стоп-листа. Здесь предохранитель включён и адрес в нём (`platform`) —
    отказывает стоп-лист, а не он."""

    async def _refused(
        self,
        session: AsyncSession,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
        *,
        to: str = TO,
    ) -> str:
        """Проба не ушла: код отказа, платформа не звана, в базе ни строки. Что напечатано."""
        fake, transport = platform
        await make_sender(session, SENDER)
        before = await _rows(session)

        code = await run_mail_test(session, transport, to=to, sender=None, stage=Stage.DONORS)

        assert code == EXIT_NOT_SENT
        assert fake.seen == []
        assert await _rows(session) == before
        return capsys.readouterr().out

    async def test_address_in_the_stoplist_is_refused(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Отписка ответом — запись на адрес без этапа (`replies/repository.suppress`)."""
        session.add(
            SuppressionModel(
                email=TO, reason=SuppressionReason.UNSUBSCRIBED, created_by="приём ответов"
            )
        )
        await session.flush()

        printed = await self._refused(session, platform, capsys)

        assert (
            f"Пробное письмо не отправлено: адрес {TO} в стоп-листе на всех этапах "
            "(причина «unsubscribed»): писать на него нельзя. Пробное письмо — себе" in printed
        )

    async def test_domain_entry_holds_every_address_of_the_domain(
        self,
        session: AsyncSession,
        filled_legal: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Домен адреса — тем ключом, которым его заводит экран (`stoplist.site_of`): ссылка
        на сайт заводит корень, и запись держит адрес на поддомене почты."""
        await stoplist.add(
            session,
            "https://www.Example.org/contacts",
            reason=SuppressionReason.MANUAL,
            author="тест",
        )
        fake = Platform()
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
            # Адрес в списке транспорта: не пускает его стоп-лист, а не предохранитель.
            allowed = SendGridTransport(
                api_key="sg-test-key", http=http, allowlist=("@mail.example.org",)
            )
            printed = await self._refused(
                session, (fake, allowed), capsys, to="Me@Mail.Example.org"
            )

        assert (
            "домен example.org в стоп-листе на всех этапах (причина «manual»): запись на домен "
            "держит все его адреса, и me@mail.example.org тоже" in printed
        )

    async def test_entry_on_the_address_is_named_before_the_domain(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        for target in ("ours.test", TO):
            await stoplist.add(session, target, reason=SuppressionReason.MANUAL, author="тест")

        printed = await self._refused(session, platform, capsys)

        assert f"адрес {TO} в стоп-листе" in printed
        assert "домен ours.test" not in printed

    @pytest.mark.parametrize("stage", [None, Stage.DONORS], ids=["без-этапа", "этап-пробы"])
    async def test_entry_without_stage_or_of_the_probe_stage_holds(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
        stage: Stage | None,
    ) -> None:
        await stoplist.add(
            session, "ours.test", reason=SuppressionReason.SUPPLIER, stage=stage, author="тест"
        )

        printed = await self._refused(session, platform, capsys)

        scope = "на всех этапах" if stage is None else "этапа donors"
        assert f"домен ours.test в стоп-листе {scope} (причина «supplier»)" in printed

    async def test_entry_of_another_stage_does_not_hold(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
    ) -> None:
        fake, transport = platform
        await make_sender(session, SENDER)
        await stoplist.add(
            session, TO, reason=SuppressionReason.MANUAL, stage=Stage.ADVERTISERS, author="тест"
        )

        code = await run_mail_test(session, transport, to=TO, sender=None, stage=Stage.DONORS)

        assert code == EXIT_OK
        assert fake.seen[0]["personalizations"][0]["to"] == [{"email": TO}]

    async def test_expired_entry_holds_nobody(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
    ) -> None:
        """Срок вышел — запись видна в списке, но письма не держит (`SuppressionModel.in_force`)."""
        fake, transport = platform
        await make_sender(session, SENDER)
        domain = DomainModel(host="ours.test")
        session.add(domain)
        await session.flush()
        session.add(
            SuppressionModel(
                domain_id=domain.id,
                reason=SuppressionReason.SUPPLIER,
                expires_at=datetime.now(UTC) - timedelta(days=1),
                created_by="тест",
            )
        )
        await session.flush()

        code = await run_mail_test(session, transport, to=TO, sender=None, stage=Stage.DONORS)

        assert code == EXIT_OK
        assert fake.seen

    async def test_host_beyond_the_dns_limit_is_judged_by_the_address_alone(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Домена с таким ключом в базе не бывает: команда не падает, а судит дальше — и
        чужой адрес не пускает предохранитель."""
        printed = await self._refused(session, platform, capsys, to=f"me@{'a' * 250}.test")

        assert "не в списке разрешённых" in printed
        assert "стоп-лист" not in printed


class TestTheStageSafetyList:
    """Предохранитель называется по учётке этапа — тем списком и тем именем,
    по которым судит отправка (`cfg.mail_account`). До 07.10.2026 команда
    называла общий список строкой, а у этапа бывает свой
    (`OUTREACH_ADVERTISERS_ALLOWED_RECIPIENTS`): в выводе стоял бы не тот
    список и не та строка, которую править."""

    OWN = "OUTREACH_ADVERTISERS_ALLOWED_RECIPIENTS"
    MAX = "max@mail-b.example"

    async def test_stage_without_own_list_names_the_shared_one(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(self.OWN, raising=False)
        monkeypatch.delenv("OUTREACH_ADVERTISERS_SENDGRID_API_KEY", raising=False)
        _, transport = platform

        code = await run_mail_test(
            session, transport, to=TO, sender=self.MAX, stage=Stage.ADVERTISERS
        )

        assert code == EXIT_OK
        assert (
            "Предохранитель включён (OUTREACH_ALLOWED_RECIPIENTS): письмо уйдёт, только если "
            f"адрес в списке — {TO}." in capsys.readouterr().out
        )

    async def test_own_list_of_the_stage_is_the_one_named(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Общий список при этом задан (`platform`) — назван должен быть свой."""
        monkeypatch.setenv(self.OWN, "Stage2@Ours.test")
        fake = Platform()
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
            # Транспорт этапа — как его собирает команда (`build_transport(stage=…)`).
            transport = SendGridTransport(
                api_key="sg-test-key", http=http, account=outreach_cfg.mail_account("advertisers")
            )
            code = await run_mail_test(
                session, transport, to="stage2@ours.test", sender=self.MAX, stage=Stage.ADVERTISERS
            )

        assert code == EXIT_OK
        assert fake.seen, "письмо на ящик из своего списка этапа должно было уйти"
        printed = capsys.readouterr().out
        assert (
            f"Предохранитель включён ({self.OWN}): письмо уйдёт, только если адрес в списке — "
            "stage2@ours.test." in printed
        )
        assert "OUTREACH_ALLOWED_RECIPIENTS" not in printed

    async def test_empty_own_list_is_named_as_the_switched_off_catch(
        self,
        session: AsyncSession,
        filled_legal: None,
        platform: tuple[Platform, SendGridTransport],
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(self.OWN, "")
        _, transport = platform

        await run_mail_test(session, transport, to=TO, sender=self.MAX, stage=Stage.ADVERTISERS)

        assert f"Предохранитель снят ({self.OWN} пуст)" in capsys.readouterr().out


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
