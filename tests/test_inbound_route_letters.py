"""Вебхук приёма на настоящей базе: письмо в тех формах, которые шлёт платформа.

До сих пор тесты слали форму словарём, и клиент кодировал её в UTF-8 сам.
Здесь форма собирается байт в байт (`tests/inbound_forms.py`), и каждый
случай — тот, на котором приём терял ответ донора: поле в windows-1251,
евро в windows-1252, ответ из одного HTML, поле больше мегабайта, метка
в копии или только в конверте, сырое письмо.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from backend.api.inbound import routes as inbound_routes
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.replies.quoting import written_by_hand
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.inbound_forms import (
    AUTH,
    BOUNDARY,
    HOST,
    URL,
    UTF8,
    NoQueue,
    label_for,
    letter,
    make_sent,
    multipart,
    raw_mode,
    setup_inbound,
)


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch) -> NoQueue:
    return setup_inbound(monkeypatch)


@pytest.fixture
async def sent(session: AsyncSession) -> MessageModel:
    return await make_sent(session)


async def _post(client: AsyncClient, fields: list[tuple[str, bytes]]) -> dict[str, object]:
    body, headers = multipart(fields)
    response = await client.post(URL, content=body, headers={**headers, **AUTH})
    assert response.status_code == 200, response.text
    answer: dict[str, object] = response.json()
    return answer


async def _stored(session: AsyncSession) -> ReplyModel:
    return (await session.execute(select(ReplyModel))).scalars().one()


class TestEncodings:
    @pytest.mark.parametrize(
        ("text", "charset"),
        [
            ("Hi Anna, the price is 550 USD per article.", "UTF-8"),
            ("Здравствуйте! Стоимость статьи — 5000 руб., ссылка dofollow.", "windows-1251"),
            ("Der Preis für einen Gastartikel beträgt 300 € – „dofollow“.", "windows-1252"),
            ("価格は1記事あたり300ドルです。", "iso-2022-jp"),
        ],
    )
    async def test_text_is_read_in_the_letters_own_encoding(
        self,
        client: AsyncClient,
        sent: MessageModel,
        session: AsyncSession,
        text: str,
        charset: str,
    ) -> None:
        answer = await _post(
            client,
            letter(sent, text=text.encode(charset), charsets={**UTF8, "text": charset}),
        )

        assert answer["bound"]
        assert (await _stored(session)).raw_body == text

    async def test_subject_in_its_own_encoding(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        fields = [
            (name, "Re: Цена размещения".encode("koi8-r") if name == "subject" else value)
            for name, value in letter(sent, charsets={**UTF8, "subject": "koi8-r"})
        ]

        await _post(client, fields)

        assert (await _stored(session)).subject == "Re: Цена размещения"


class TestHtmlOnly:
    async def test_price_in_an_html_only_reply_is_seen_and_the_quote_is_cut(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        html = (
            b'<div dir="ltr">Hi Anna,<br>Our price is <b>$200</b> per post.</div>'
            b'<div class="gmail_quote"><div class="gmail_attr">On Mon, Sep 28, 2026, Anna Ro '
            b'wrote:</div><blockquote class="gmail_quote">What&#39;s the price?<br>'
            b"To stop hearing from us, unsubscribe here</blockquote></div>"
        )

        answer = await _post(client, letter(sent, text=None, html=html))

        reply = await _stored(session)
        assert "$200" in written_by_hand(reply.raw_body)
        assert "unsubscribe" not in written_by_hand(reply.raw_body)
        # Цитата не потеряна — она помечена, и человек видит её в карточке.
        assert "> On Mon, Sep 28, 2026" in reply.raw_body
        assert answer["kind"] == "human"

    async def test_html_above_a_megabyte_is_taken(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        """Картинка, вставленная в текст, — полтора мегабайта HTML. Раньше
        это был отказ 400, повторы платформы и потерянный ответ."""
        html = (
            b'<div>Price: $180</div><img src="data:image/png;base64,'
            + b"A" * 1_500_000
            + b'"><div>Thanks!</div>'
        )

        await _post(client, letter(sent, text=None, html=html))

        assert (await _stored(session)).raw_body == "Price: $180\nThanks!"


class TestWhereTheLabelIs:
    async def test_label_only_in_the_copy_binds(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        answer = await _post(
            client,
            letter(
                sent,
                to=b"Editor <editor@donor.example.test>",
                envelope_to="editor@donor.example.test",
                extra=[("cc", f"Anna Ro <{label_for(sent)}>".encode())],
            ),
        )

        assert answer["bound"]
        assert (await _stored(session)).thread_id == sent.thread_id

    async def test_label_only_in_the_envelope_binds(
        self, client: AsyncClient, sent: MessageModel
    ) -> None:
        """Скрытая копия и пересылка: в «кому» и «копии» метки нет."""
        answer = await _post(client, letter(sent, to=b"Team <team@donor.example.test>"))

        assert answer["bound"]

    async def test_without_the_label_anywhere_it_is_kept_unbound(
        self, client: AsyncClient, sent: MessageModel
    ) -> None:
        answer = await _post(
            client,
            letter(sent, to=b"team@donor.example.test", envelope_to="team@donor.example.test"),
        )

        assert not answer["bound"]
        assert answer["needs_review"]


def _mime(*lines: str, body: bytes) -> bytes:
    return "\r\n".join(lines).encode() + b"\r\n\r\n" + body


class TestRawMode:
    async def test_alternative_with_an_attachment(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        email = _mime(
            f"From: Elena <editor@{HOST}>",
            f"To: {label_for(sent)}",
            "Subject: =?UTF-8?B?UmU6INCm0LXQvdCw?=",
            "Message-ID: <raw-1@mail.donor.test>",
            "MIME-Version: 1.0",
            f'Content-Type: multipart/mixed; boundary="{BOUNDARY}-m"',
            body=(
                f"--{BOUNDARY}-m\r\n"
                f'Content-Type: multipart/alternative; boundary="{BOUNDARY}-a"\r\n\r\n'
                f"--{BOUNDARY}-a\r\nContent-Type: text/plain; charset=utf-8\r\n"
                "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
                "Rate card attached, a post is 300 =E2=82=AC.\r\n"
                f"--{BOUNDARY}-a\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
                "<p>Rate card attached, a post is 300 &euro;.</p>\r\n"
                f"--{BOUNDARY}-a--\r\n"
                f"--{BOUNDARY}-m\r\nContent-Type: application/pdf\r\n"
                'Content-Disposition: attachment; filename="rates.pdf"\r\n'
                "Content-Transfer-Encoding: base64\r\n\r\n"
                "JVBERi0xLjQKJeLjz9MK\r\n"
                f"--{BOUNDARY}-m--\r\n"
            ).encode(),
        )
        fields = [(name, value) for name, value in raw_mode(sent, email) if name != "subject"]

        answer = await _post(client, fields)

        reply = await _stored(session)
        assert answer["bound"]
        assert reply.raw_body == "Rate card attached, a post is 300 €."
        assert reply.subject == "Re: Цена"
        assert reply.inbound_message_id == "<raw-1@mail.donor.test>"

    async def test_single_part_quoted_printable(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        email = _mime(
            f"From: Mark <editor@{HOST}>",
            "Message-ID: <raw-2@mail.donor.test>",
            'Content-Type: text/plain; charset="utf-8"',
            "Content-Transfer-Encoding: quoted-printable",
            body=b"Preis f=C3=BCr einen Artikel: 200 =E2=82=AC, dofollow.\r\n",
        )

        await _post(client, raw_mode(sent, email))

        assert (await _stored(session)).raw_body == "Preis für einen Artikel: 200 €, dofollow.\n"

    async def test_8bit_windows_1251(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        email = _mime(
            f"From: editor@{HOST}",
            "Message-ID: <raw-3@mail.donor.test>",
            "Content-Type: text/plain; charset=windows-1251",
            "Content-Transfer-Encoding: 8bit",
            body="Стоимость размещения — 5000 руб.".encode("cp1251"),
        )

        await _post(client, raw_mode(sent, email))

        assert (await _stored(session)).raw_body == "Стоимость размещения — 5000 руб."

    async def test_repeat_of_a_raw_letter_is_a_repeat(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        """Номер письма в сыром режиме — из самого письма: без него повтор
        вебхука стал бы вторым ответом и вторым платным разбором."""
        email = _mime(
            f"From: editor@{HOST}",
            "Message-ID: <raw-4@mail.donor.test>",
            "Content-Type: text/plain; charset=utf-8",
            body=b"250 EUR",
        )

        await _post(client, raw_mode(sent, email))
        again = await _post(client, raw_mode(sent, email))

        assert again["duplicate"]
        assert len((await session.execute(select(ReplyModel))).scalars().all()) == 1


class TestBoundaries:
    async def test_body_above_the_ceiling_is_refused_before_reading(
        self, client: AsyncClient, sent: MessageModel
    ) -> None:
        body = b"x" * (inbound_routes.MAX_REQUEST_BYTES + 1)

        response = await client.post(
            URL,
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}", **AUTH},
        )

        assert response.status_code == 413

    async def test_body_without_a_declared_size_is_not_read_past_the_ceiling(
        self, client: AsyncClient, sent: MessageModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(inbound_routes, "MAX_REQUEST_BYTES", 1000)
        body, headers = multipart(letter(sent, text=b"x" * 5000))

        async def chunks() -> AsyncIterator[bytes]:
            for start in range(0, len(body), 512):
                yield body[start : start + 512]

        response = await client.post(URL, content=chunks(), headers={**headers, **AUTH})

        assert response.status_code == 413

    async def test_body_without_a_declared_size_under_the_ceiling_is_taken(
        self, client: AsyncClient, sent: MessageModel
    ) -> None:
        body, headers = multipart(letter(sent))

        async def chunks() -> AsyncIterator[bytes]:
            yield body[:100]
            yield body[100:]

        response = await client.post(URL, content=chunks(), headers={**headers, **AUTH})

        assert response.status_code == 200, response.text
        assert response.json()["bound"]

    async def test_wrong_secret_is_refused(self, client: AsyncClient, sent: MessageModel) -> None:
        body, headers = multipart(letter(sent))

        response = await client.post(
            URL, content=body, headers={**headers, "X-Inbound-Secret": "wrong-secret"}
        )

        assert response.status_code == 403

    async def test_not_a_form_gets_200_and_a_reason(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        """Повтор того же тела ничего не изменит — значит, не 4xx."""
        response = await client.post(
            URL,
            content=json.dumps({"text": "hi"}).encode(),
            headers={"Content-Type": "application/json", **AUTH},
        )

        assert response.status_code == 200
        assert not response.json()["accepted"]
        assert "не форма" in response.json()["reason"]
        assert (await session.execute(select(ReplyModel))).first() is None

    async def test_missing_sender_header_falls_back_to_the_envelope(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        fields = [(name, b"" if name == "from" else value) for name, value in letter(sent)]

        answer = await _post(client, fields)

        assert answer["accepted"]
        assert (await _stored(session)).from_email == f"editor@{HOST}"
