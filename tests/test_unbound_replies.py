"""Ответы без письма: вебхук сохраняет — вкладка «Не привязаны» показывает.

До 28.09.2026 приём сохранял непривязанный ответ, а видеть его было негде:
`ReplyRepository.unbound()` не звал никто. Проверяется поэтому не функция,
а путь целиком: письмо уходит в настоящий вебхук формой, как её шлёт
платформа, и читается маршрутом списка — на настоящей базе. Причина
не привязки сверяется словами, которые увидит человек: код без слов
ничего не говорит ни ему, ни тесту.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from backend.cli.mail_test import EXIT_OK, run_mail_test
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import ReplyKind, Stage, UserRole
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.letters import reply_to
from backend.features.letters.probe import compose_probe
from backend.features.letters.sendgrid import PROVIDER_ID_HEADER, SendGridTransport
from backend.features.replies import unbound
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_sender
from tests.inbound_forms import (
    REPLY_HOST,
    SECRET,
    URL,
    NoQueue,
    attachment_info,
    label_for,
    letter,
    make_sent,
    multipart,
    setup_inbound,
)

LIST = "/api/replies/unbound"
PDF = b"%PDF-1.4\r\nrate card\r\n%%EOF\r\n"
ME = "me@ours.test"
SENDER = "anna@mail-a.example"
#: Что `outreach mail-test` обещает увидеть у ответа на пробное письмо.
PROMISED_REASON = "Ответ на пробное письмо"


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch) -> NoQueue:
    return setup_inbound(monkeypatch)


@pytest.fixture
async def sent(session: AsyncSession) -> MessageModel:
    return await make_sent(session)


@pytest.fixture
async def viewer(make_user: Any, sign_in: Any) -> dict[str, str]:
    """Оператор: смотреть может, как любой, у кого есть доступ к базе."""
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return bearer(await sign_in("оператор@site.com"))


def _without_thread(fields: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Письмо без заголовков цепочки: написано заново, а не ответом."""
    blob = b"Message-ID: <fresh@mail.elsewhere.test>\nSubject: Hello\n"
    return [(name, blob if name == "headers" else value) for name, value in fields]


async def _post(
    client: AsyncClient,
    fields: list[tuple[str, bytes]],
    files: list[tuple[str, str, str, bytes]] | None = None,
) -> dict[str, Any]:
    body, headers = multipart(fields, files or [])
    # Секрет — тот, что стоит сейчас: у пробного письма он свой (`filled_legal`).
    secret = {"X-Inbound-Secret": outreach_cfg.INBOUND_SECRET}
    response = await client.post(URL, content=body, headers={**headers, **secret})
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    return answer


async def _to(
    client: AsyncClient, sent: MessageModel, address: str, **extra: Any
) -> dict[str, Any]:
    """Письмо на адрес `address` — и в «кому», и в конверте."""
    fields = letter(sent, to=address.encode(), envelope_to=address, **extra)
    return await _post(client, fields)


async def _listed(client: AsyncClient, headers: dict[str, str], query: str = "") -> dict[str, Any]:
    response = await client.get(f"{LIST}{query}", headers=headers)
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    return answer


class TestWhoSees:
    async def test_without_a_pass_nobody(self, client: AsyncClient) -> None:
        response = await client.get(LIST)

        assert response.status_code == 401

    async def test_operator_with_view_sees(
        self, client: AsyncClient, viewer: dict[str, str]
    ) -> None:
        response = await client.get(LIST, headers=viewer)

        assert response.status_code == 200
        assert response.json() == {"rows": [], "total": 0, "page": 1, "limit": unbound.PAGE_SIZE}

    async def test_taken_view_closes_the_list(
        self, client: AsyncClient, make_user: Any, sign_in: Any
    ) -> None:
        await make_user("слепой@site.com", role=UserRole.OPERATOR, permissions={"view": False})
        token = await sign_in("слепой@site.com")

        response = await client.get(LIST, headers=bearer(token))

        assert response.status_code == 403
        assert "«view»" in response.json()["detail"]


class TestWhyNotBound:
    """Каждая причина — через настоящий вебхук и словами, которые увидит человек."""

    async def test_no_label_and_a_foreign_thread(
        self,
        client: AsyncClient,
        sent: MessageModel,
        viewer: dict[str, str],
        session: AsyncSession,
    ) -> None:
        taken = await _to(client, sent, "team@donor.example.test")

        # Что легло в базу — столбцами, мимо объектов сессии: их она отдала бы
        # из памяти такими, какими их записали, а не такими, какими их хранит база.
        stored = (
            await session.execute(
                select(ReplyModel.unbound_reason, ReplyModel.to_addresses).where(
                    ReplyModel.id == taken["reply_id"]
                )
            )
        ).one()
        assert tuple(stored) == ("foreign_thread", ["team@donor.example.test"])
        row = (await _listed(client, viewer))["rows"][0]
        assert row["id"] == taken["reply_id"]
        assert row["reason"] == "foreign_thread"
        assert row["reason_text"].startswith("Метки в адресе нет, а заголовки цепочки не совпали")
        # Всё, по чему донора ищут руками: от кого целиком, куда, о чём.
        assert row["from_email"] == "editor@donor.example.test"
        assert row["to"] == ["team@donor.example.test"]
        assert row["subject"] == "Re: Advertising rates"
        assert row["kind"] == ReplyKind.HUMAN.value
        assert row["text"] == "Placement is 250 EUR."
        assert row["preview"] == "Placement is 250 EUR."
        # Вебхук говорит ту же причину тому, кто разбирает его ответ.
        assert taken["reason"] == f"ответ не привязан к письму: {row['reason_text']}"

    async def test_no_label_and_no_thread_at_all(
        self, client: AsyncClient, sent: MessageModel, viewer: dict[str, str]
    ) -> None:
        address = f"anna@{REPLY_HOST}"
        fields = letter(sent, to=address.encode(), envelope_to=address)
        await _post(client, _without_thread(fields))

        row = (await _listed(client, viewer))["rows"][0]
        assert row["reason"] == "no_label"
        assert row["reason_text"].startswith("Метки в адресе нет, и заголовков цепочки тоже")
        assert row["to"] == [address]

    async def test_reply_to_the_probe_letter(
        self,
        client: AsyncClient,
        sent: MessageModel,
        viewer: dict[str, str],
        filled_legal: None,
    ) -> None:
        """Метка на письмо №0: именно так `outreach mail-test` проверяет,
        что ответы доходят, — и экран обязан сказать это его словами, а не
        «письма нет». Адрес — тот, что собирает само пробное письмо."""
        probe = compose_probe(to=ME, sender_email=SENDER, stage=Stage.DONORS, real=True)
        address = probe.outgoing.reply_to
        assert address is not None
        await _to(client, sent, address)

        row = (await _listed(client, viewer))["rows"][0]
        assert row["reason"] == "no_such_letter"
        assert row["reason_text"].startswith(f"{PROMISED_REASON}: его метка — письмо №0")
        assert row["to"] == [address]

    async def test_label_of_a_letter_that_is_gone(
        self, client: AsyncClient, sent: MessageModel, viewer: dict[str, str]
    ) -> None:
        address = f"anna+{reply_to.label_for(987_654, secret=SECRET)}@{REPLY_HOST}"
        await _to(client, sent, address)

        row = (await _listed(client, viewer))["rows"][0]
        assert row["reason"] == "no_such_letter"
        assert row["reason_text"].startswith("Метка указывает на письмо №987654, а такого")

    async def test_forged_or_foreign_signature_is_named(
        self, client: AsyncClient, sent: MessageModel, viewer: dict[str, str]
    ) -> None:
        """Подпись не сошлась — не «метки нет»: если так встают все ответы,
        сменили секрет, и чинить надо его, а не искать доноров."""
        await _to(client, sent, f"anna+m{sent.id}.0000000000@{REPLY_HOST}")

        row = (await _listed(client, viewer))["rows"][0]
        assert row["reason"] == "bad_signature"
        assert row["reason_text"].startswith(f"В адресе метка письма №{sent.id}, но подпись")

    async def test_secret_changed_after_sending(
        self,
        client: AsyncClient,
        sent: MessageModel,
        viewer: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Настоящая метка нашего письма, подписанная прежним секретом."""
        address = label_for(sent)
        monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", "another-secret-" + "x" * 20)
        await _to(client, sent, address)

        row = (await _listed(client, viewer))["rows"][0]
        assert row["reason"] == "bad_signature"


class TestWhatIsListed:
    async def test_bound_reply_is_not_there(
        self, client: AsyncClient, sent: MessageModel, viewer: dict[str, str]
    ) -> None:
        taken = await _post(client, letter(sent))
        assert taken["bound"]

        assert await _listed(client, viewer) == {
            "rows": [],
            "total": 0,
            "page": 1,
            "limit": unbound.PAGE_SIZE,
        }

    async def test_reply_that_lost_its_letter_stays_with_its_donor(
        self,
        client: AsyncClient,
        sent: MessageModel,
        session: AsyncSession,
        viewer: dict[str, str],
    ) -> None:
        """Письмо удалили — связь с ним обнулилась, а диалог остался: это ответ
        донора, он виден в переписке, а не среди потерянных."""
        session.add(
            ReplyModel(
                thread_id=sent.thread_id, message_id=None, kind=ReplyKind.HUMAN, raw_body="250 EUR"
            )
        )
        await session.commit()

        assert (await _listed(client, viewer))["total"] == 0

    async def test_reply_from_before_the_reason_was_kept(
        self, client: AsyncClient, session: AsyncSession, viewer: dict[str, str]
    ) -> None:
        session.add(ReplyModel(kind=ReplyKind.AUTO_REPLY, raw_body="I am away until Monday."))
        await session.commit()

        row = (await _listed(client, viewer))["rows"][0]
        assert row["reason"] is None
        assert row["reason_text"] == (
            "Причина не записана: ответ принят до того, как её стали сохранять."
        )
        assert row["to"] == []
        assert row["kind"] == "auto_reply"

    async def test_preview_is_what_the_person_wrote_not_our_quote(
        self, client: AsyncClient, sent: MessageModel, viewer: dict[str, str]
    ) -> None:
        text = (
            b"Hello Anna,\r\n\r\nwe take   guest posts for 300 USD.\r\n\r\n"
            b"On Mon, 28 Sep 2026 at 10:00, Anna <anna@mail-a.example> wrote:\r\n"
            b"> Good afternoon, what are your rates?\r\n"
        )
        await _to(client, sent, "team@donor.example.test", text=text)

        row = (await _listed(client, viewer))["rows"][0]
        assert row["preview"] == "Hello Anna, we take guest posts for 300 USD."
        # Раскрытая строка — текст целиком, с цитатой: его человек и читает.
        assert "what are your rates?" in row["text"]

    async def test_attachments_are_listed_and_download(
        self, client: AsyncClient, sent: MessageModel, viewer: dict[str, str]
    ) -> None:
        # Метки нет и в конверте: иначе письмо привязалось бы по ней.
        fields = letter(
            sent,
            to=b"team@donor.example.test",
            envelope_to="team@donor.example.test",
            extra=[attachment_info(("rates.pdf", "application/pdf"), ("run.exe", "app/x"))],
        )
        files = [
            ("attachment1", 'filename="rates.pdf"', "application/pdf", PDF),
            ("attachment2", 'filename="run.exe"', "application/x-msdownload", b"MZ"),
        ]
        await _post(client, fields, files)

        row = (await _listed(client, viewer))["rows"][0]
        kept, refused = row["attachments"]
        assert (kept["name"], kept["size"], kept["accepted"]) == ("rates.pdf", len(PDF), True)
        assert (refused["name"], refused["accepted"]) == ("run.exe", False)
        assert "исполняемый" in refused["reason"]
        # Скачивается тем же маршрутом, что вложение ответа в переписке.
        got = await client.get(f"/api/replies/{row['id']}/attachments/{kept['id']}", headers=viewer)
        assert got.status_code == 200
        assert got.content == PDF


class TestPages:
    @pytest.fixture
    async def many(self, session: AsyncSession) -> list[int]:
        """Двадцать пять непривязанных — страница с хвостом."""
        rows = [
            ReplyModel(kind=ReplyKind.HUMAN, raw_body=f"ответ {n}", unbound_reason="no_label")
            for n in range(25)
        ]
        session.add_all(rows)
        await session.commit()
        return [row.id for row in rows]

    async def test_newest_first_by_pages_of_twenty(
        self, client: AsyncClient, viewer: dict[str, str], many: list[int]
    ) -> None:
        first = await _listed(client, viewer)
        second = await _listed(client, viewer, "?page=2")

        newest = sorted(many, reverse=True)
        assert [row["id"] for row in first["rows"]] == newest[:20]
        assert [row["id"] for row in second["rows"]] == newest[20:]
        assert (first["total"], first["page"], first["limit"]) == (25, 1, 20)
        assert (second["total"], second["page"]) == (25, 2)

    async def test_page_past_the_end_is_empty_not_an_error(
        self, client: AsyncClient, viewer: dict[str, str], many: list[int]
    ) -> None:
        past = await _listed(client, viewer, "?page=3")

        assert past["rows"] == []
        assert past["total"] == 25

    async def test_size_is_the_servers_and_it_has_a_ceiling(
        self, client: AsyncClient, viewer: dict[str, str], many: list[int]
    ) -> None:
        small = await _listed(client, viewer, "?page=2&limit=5")
        too_big = await client.get(f"{LIST}?limit={unbound.MAX_PAGE_SIZE + 1}", headers=viewer)
        zero = await client.get(f"{LIST}?page=0", headers=viewer)

        assert [row["id"] for row in small["rows"]] == sorted(many, reverse=True)[5:10]
        assert too_big.status_code == 422
        assert zero.status_code == 422


class TestOverview:
    async def test_home_counts_them_by_the_same_rule(
        self,
        client: AsyncClient,
        sent: MessageModel,
        make_user: Any,
        sign_in: Any,
    ) -> None:
        await _post(client, letter(sent, message_id="bound"))  # привязан — не в счёт
        await _to(client, sent, "team@donor.example.test", message_id="stray-1")
        await _to(client, sent, f"anna@{REPLY_HOST}", message_id="stray-2")
        await make_user("админ@site.com", role=UserRole.ADMIN)
        admin = bearer(await sign_in("админ@site.com"))

        home = await client.get("/api/overview", headers=admin)

        assert home.status_code == 200, home.text
        assert home.json()["unbound_replies"] == 2
        assert (await _listed(client, admin))["total"] == 2


class TestMailTestPromise:
    async def test_mail_test_names_the_tab_and_the_reason(
        self,
        session: AsyncSession,
        filled_legal: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Команда пробного письма говорит, где искать ответ. До 28.09.2026 она
        обещала «экран диалогов», где непривязанных не показывал никто.

        Причина, которую она обещает, — та же строка, с которой начинается
        причина в списке (`test_reply_to_the_probe_letter`)."""
        monkeypatch.setattr(outreach_cfg, "ALLOWED_RECIPIENTS", (ME,))
        await make_sender(session, SENDER)
        accepted = httpx.MockTransport(
            lambda _request: httpx.Response(202, headers={PROVIDER_ID_HEADER: "sg-probe-1"})
        )
        async with httpx.AsyncClient(transport=accepted) as http:
            transport = SendGridTransport(api_key="sg-test-key", http=http)
            code = await run_mail_test(session, transport, to=ME, sender=None, stage=Stage.DONORS)

        assert code == EXIT_OK
        printed = " ".join(capsys.readouterr().out.split())
        assert "на экране «Диалоги», вкладка «Не привязаны»" in printed
        assert f"с причиной «{PROMISED_REASON}»" in printed
