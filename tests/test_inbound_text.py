"""Приём без базы и без сети: байты письма в текст, HTML в строки, форма в поля.

Каждая проверка здесь — случай, на котором приём уже терял цену: русский
в windows-1251 кракозябрами, евро из windows-1252 управляющим символом,
ответ из одного HTML пустым, сырое письмо в quoted-printable — «=E2=82=AC»
вместо «€».
"""

from __future__ import annotations

import base64
import json

import pytest
from backend.api.replies.download import download_headers
from backend.features.replies import attachments as files_policy
from backend.features.replies.charsets import decode, decode_fields
from backend.features.replies.classify import classify
from backend.features.replies.form_data import FormError, read_form
from backend.features.replies.html_text import text_from_html
from backend.features.replies.inbound import Attachment, storable
from backend.features.replies.mime import KEPT_HEADERS, file_name, from_form, parse_headers
from backend.features.replies.quoting import written_by_hand
from backend.features.replies.raw_mime import read_raw
from tests.inbound_forms import BOUNDARY, multipart


class TestCharsets:
    @pytest.mark.parametrize(
        ("text", "label"),
        [
            ("Здравствуйте! Стоимость статьи — 5000 руб.", "windows-1251"),
            ("Der Preis beträgt 300 € – „dofollow“.", "windows-1252"),
            ("価格は1記事あたり300ドルです。", "iso-2022-jp"),
            ("Cena artykułu: 400 zł", "iso-8859-2"),
            ("Цена 5000 ₽, оплата на карту", "utf-8"),
        ],
    )
    def test_named_encoding_is_read(self, text: str, label: str) -> None:
        got, problem = decode(text.encode(label), label)

        assert got == text
        assert problem is None

    def test_latin1_label_is_read_as_windows_1252(self) -> None:
        """Почта пишет «iso-8859-1», а шлёт windows-1252: 0x80 у неё —
        евро, и прочитанный буквально он стал бы управляющим символом."""
        got, problem = decode("300 € – „dofollow“".encode("cp1252"), "iso-8859-1")

        assert got == "300 € – „dofollow“"
        assert problem is None

    def test_latin1_bytes_outside_windows_1252_stay_latin1(self) -> None:
        """Байт, которого в windows-1252 нет, читается по названной кодировке."""
        got, _ = decode(b"a\x81b", "iso-8859-1")

        assert got == "a\x81b"

    def test_ascii_label_over_utf8_bytes_is_read_as_utf8(self) -> None:
        got, problem = decode("Preis: 200 €".encode(), "us-ascii")

        assert got == "Preis: 200 €"
        assert problem is None

    def test_unknown_label_falls_back_to_utf8_and_says_so(self) -> None:
        got, problem = decode("Цена".encode(), "x-klingon")

        assert got == "Цена"
        assert problem is not None
        assert "x-klingon" in problem

    @pytest.mark.parametrize("label", ["base64", "zlib", "undefined"])
    def test_label_that_is_not_a_text_encoding_does_not_crash(self, label: str) -> None:
        """Метку пишет отправитель, то есть кто угодно."""
        got, problem = decode("Цена".encode(), label)

        assert got == "Цена"
        assert problem is not None

    def test_wrong_bytes_are_replaced_and_reported(self) -> None:
        got, problem = decode(b"\xff\xfe price", "utf-8")

        assert got.endswith(" price")
        assert "�" in got
        assert problem is not None
        assert "utf-8" in problem

    def test_fields_take_their_encodings_from_charsets(self) -> None:
        got = decode_fields(
            {
                "text": "Цена 5000 руб.".encode("cp1251"),
                "subject": "Тема".encode(),
                "headers": b"Message-ID: <a@b.test>",
                "charsets": json.dumps({"text": "windows-1251", "subject": "UTF-8"}).encode(),
            }
        )

        assert got["text"] == "Цена 5000 руб."
        assert got["subject"] == "Тема"
        assert got["headers"] == "Message-ID: <a@b.test>"

    def test_missing_charset_on_non_utf8_text_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        got = decode_fields({"text": "Цена".encode("cp1251")})

        assert "�" in got["text"]
        assert "«text»" in caplog.text


class TestHtml:
    GMAIL = (
        '<div dir="ltr">Hi Anna,<br>Our price is <b>$200</b> per post.</div><br>'
        '<div class="gmail_quote"><div class="gmail_attr">On Mon, Sep 28, 2026 Anna Ro '
        "&lt;anna@ours.test&gt; wrote:<br></div>"
        '<blockquote class="gmail_quote">Hi, what&#39;s the price?<br>'
        "To stop hearing from us, unsubscribe here</blockquote></div>"
    )

    def test_gmail_quote_is_marked_and_cut(self) -> None:
        text = text_from_html(self.GMAIL)

        assert "> On Mon, Sep 28, 2026" in text
        assert written_by_hand(text) == "Hi Anna,\nOur price is $200 per post."

    def test_quoted_unsubscribe_does_not_make_an_unsubscribe(self) -> None:
        """В цитате — наше письмо со словом «unsubscribe» в юридическом блоке."""
        incoming = from_form({"from": "editor@site.test", "html": self.GMAIL})

        assert classify(incoming).kind.value == "human"

    def test_apple_cite_blockquote_is_a_quote(self) -> None:
        html = (
            '<div>Price:&nbsp;300&nbsp;€</div><div><br><blockquote type="cite">'
            "<div>On Sep 28, 2026, at 10:04, Anna wrote:</div><div>price?</div>"
            "</blockquote></div>"
        )

        assert written_by_hand(text_from_html(html)) == "Price: 300 €"

    def test_outlook_quote_starts_at_its_marker_and_runs_to_the_end(self) -> None:
        """У Outlook цитата — не внутри метки, а всё, что после неё."""
        html = (
            "<div class=WordSection1><p class=MsoNormal>Hello,</p>"
            "<p class=MsoNormal>&nbsp;</p><p class=MsoNormal>Price 250 EUR</p></div>"
            '<div id="appendonsend"></div><hr>'
            '<div id="divRplyFwdMsg"><b>From:</b> Anna Ro<br><b>Sent:</b> Monday</div>'
            "<div>unsubscribe here</div>"
        )

        text = text_from_html(html)

        assert text.startswith("Hello,\n\nPrice 250 EUR\n> From: Anna Ro")
        assert written_by_hand(text) == "Hello,\n\nPrice 250 EUR"

    @pytest.mark.parametrize(
        "quote",
        [
            '<div class="OutlookMessageHeader">-----Original Message-----</div><p>old</p>',
            '<div class="yahoo_quoted"><div>On Monday, Anna wrote:</div><div>old</div></div>',
            '<div id="divRplyFwdMsg">From: Anna</div><div>old</div>',
        ],
    )
    def test_known_quote_markers(self, quote: str) -> None:
        assert written_by_hand(text_from_html(f"<p>Price 99 USD</p>{quote}")) == "Price 99 USD"

    def test_scripts_styles_and_head_are_dropped(self) -> None:
        html = (
            "<html><head><title>t</title><style>p{color:red}</style></head>"
            "<body><script>alert(1)</script><p>Price 50</p><!-- note --></body></html>"
        )

        assert text_from_html(html) == "Price 50"

    def test_entities_spaces_lists_and_tables(self) -> None:
        html = (
            "<p>A &amp; B&nbsp;&mdash;  <b> 120 </b>  USD</p>"
            "<table><tr><td>Guest post</td><td>$200</td></tr></table>"
            "<ul><li>PayPal</li><li>Wise</li></ul>"
        )

        assert text_from_html(html) == "A & B — 120 USD\nGuest post\t$200\n- PayPal\n- Wise"

    def test_deep_nesting_does_not_hit_the_recursion_limit(self) -> None:
        html = "<div>" * 5000 + "Price 70" + "</div>" * 5000

        assert text_from_html(html) == "Price 70"


def _raw(*lines: str, body: bytes = b"") -> bytes:
    return "\r\n".join(lines).encode() + b"\r\n\r\n" + body


class TestRawLetter:
    def test_single_part_quoted_printable_is_decoded(self) -> None:
        raw = _raw(
            "From: Mark <mark@donor.test>",
            "Message-ID: <c11@mail.donor.test>",
            'Content-Type: text/plain; charset="utf-8"',
            "Content-Transfer-Encoding: quoted-printable",
            body=b"Preis f=C3=BCr einen Artikel: 200 =E2=82=AC, dofollow.\r\n",
        )

        letter = read_raw(raw)

        assert letter.text.strip() == "Preis für einen Artikel: 200 €, dofollow."
        assert parse_headers(letter.headers)["message-id"] == "<c11@mail.donor.test>"

    def test_8bit_windows_1251_is_read_by_its_own_charset(self) -> None:
        raw = _raw(
            "From: a@b.test",
            "Content-Type: text/plain; charset=windows-1251",
            "Content-Transfer-Encoding: 8bit",
            body="Стоимость — 5000 руб.".encode("cp1251"),
        )

        assert read_raw(raw).text == "Стоимость — 5000 руб."

    def test_html_only_letter_gives_html(self) -> None:
        raw = _raw(
            "From: a@b.test",
            "Content-Type: text/html; charset=utf-8",
            body=b"<p>Price &euro;90</p>",
        )

        letter = read_raw(raw)

        assert letter.text == ""
        assert from_form({"from": "a@b.test"}, letter=letter).text == "Price €90"

    def test_inline_image_and_forwarded_letter_are_files(self) -> None:
        image = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
        raw = _raw(
            "From: a@b.test",
            f'Content-Type: multipart/mixed; boundary="{BOUNDARY}"',
            body=(
                f"--{BOUNDARY}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSee below.\r\n"
                f"--{BOUNDARY}\r\nContent-Type: image/png\r\nContent-Transfer-Encoding: base64\r\n"
                f"Content-Disposition: inline\r\n\r\n{image}\r\n"
                f"--{BOUNDARY}\r\nContent-Type: message/rfc822\r\n\r\n"
                "From: x@y.test\r\nSubject: old\r\n\r\nForwarded text\r\n"
                f"--{BOUNDARY}--\r\n"
            ).encode(),
        )

        letter = read_raw(raw)

        assert letter.text == "See below."
        assert [a.content_type for a in letter.attachments] == ["image/png", "message/rfc822"]
        assert letter.attachments[0].data == b"\x89PNG\r\n\x1a\n"
        assert letter.attachments[1].name == "вложение-2.eml"
        assert b"Forwarded text" in (letter.attachments[1].data or b"")


class TestForm:
    def test_fields_and_files_keep_their_exact_bytes(self) -> None:
        binary = bytes(range(256)) * 3 + b"\r\n--xYzZ\r\n\r"
        body, headers = multipart(
            [("text", "Цена".encode("cp1251")), ("html", b"")],
            [("attachment1", 'filename="price.bin"', "application/octet-stream", binary)],
        )

        form = read_form(body, headers["Content-Type"])

        assert form.fields == {"text": "Цена".encode("cp1251"), "html": b""}
        assert form.files[0].data == binary
        assert form.complete

    def test_field_above_a_megabyte_is_read_whole(self) -> None:
        big = b"<p>" + b"A" * 1_500_000 + b"</p>"
        body, headers = multipart([("html", big)])

        assert read_form(body, headers["Content-Type"]).fields["html"] == big

    def test_filename_in_rfc2231_is_decoded(self) -> None:
        body, headers = multipart(
            [],
            [("attachment1", "filename*=UTF-8''%D0%BF%D1%80%D0%B0%D0%B9%D1%81.pdf", "a/b", b"x")],
        )

        assert read_form(body, headers["Content-Type"]).files[0].filename == "прайс.pdf"

    def test_broken_off_form_keeps_what_came_whole(self, caplog: pytest.LogCaptureFixture) -> None:
        body, headers = multipart([("from", b"a@b.test"), ("text", b"Price 10")])

        form = read_form(body[:-20], headers["Content-Type"])

        assert form.fields == {"from": b"a@b.test"}
        assert not form.complete
        assert "оборвана" in caplog.text

    def test_urlencoded_form_is_read_too(self) -> None:
        form = read_form(b"from=a%40b.test&text=%D0%A6", "application/x-www-form-urlencoded")

        assert form.fields == {"from": b"a@b.test", "text": "Ц".encode()}

    @pytest.mark.parametrize("kind", ["application/json", "", "multipart/form-data"])
    def test_not_a_form_is_refused_with_a_reason(self, kind: str) -> None:
        with pytest.raises(FormError):
            read_form(b"{}", kind)


class TestNamesAndPolicy:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("=?UTF-8?B?0L/RgNCw0LnRgS5wZGY=?=", "прайс.pdf"),
            ("C:/Users/elena/Desktop/rates.pdf", "rates.pdf"),
            ("..\\..\\boot.ini", "boot.ini"),
            ("прайс\u202efdp.exe", "прайсfdp.exe"),
            ("", "без имени"),
        ],
    )
    def test_file_name_is_readable_and_safe(self, raw: str, expected: str) -> None:
        assert file_name(raw) == expected

    @pytest.mark.parametrize("name", ["prices.exe", "PRICES.EXE", "prices.exe. ", "a.pdf.js"])
    def test_dangerous_extension_is_seen_as_the_system_sees_it(self, name: str) -> None:
        assert Attachment(name=name, size=1).dangerous

    def test_policy_keeps_twenty_and_says_why_not_more(self) -> None:
        many = [Attachment(name=f"{n}.pdf", size=1, data=b"x") for n in range(21)]

        decisions = files_policy.sort_out(many)

        assert sum(d.keep for d in decisions) == 20
        assert decisions[-1].reason is not None
        assert "больше 20 файлов" in decisions[-1].reason

    def test_policy_counts_sizes_per_file_and_per_reply(self) -> None:
        mb = 1024 * 1024
        decisions = files_policy.sort_out(
            [
                Attachment(name="big.pdf", size=11 * mb, data=b"0" * (11 * mb)),
                Attachment(name="a.pdf", size=9 * mb, data=b"0" * (9 * mb)),
                Attachment(name="b.pdf", size=9 * mb, data=b"0" * (9 * mb)),
                Attachment(name="c.pdf", size=9 * mb, data=b"0" * (9 * mb)),
            ]
        )

        assert [d.keep for d in decisions] == [False, True, True, False]
        assert "11 МБ — больше предела 10 МБ" in (decisions[0].reason or "")
        assert "25 МБ на ответ" in (decisions[3].reason or "")

    def test_named_but_missing_file_is_listed_not_kept(self) -> None:
        decision = files_policy.sort_out([Attachment(name="rates.pdf", size=None)])[0]

        assert not decision.keep
        assert "самого файла в письме не было" in (decision.reason or "")

    def test_storable_drops_nul_and_mends_surrogates(self) -> None:
        raw = "Цена".encode().decode("ascii", "surrogateescape")

        assert storable(f"a\x00b {raw} \ud800") == "ab Цена \ufffd"


def test_service_headers_are_kept_for_the_classifier() -> None:
    blob = (
        "X-Auto-Response-Suppress: All\nList-Id: <news.site.test>\n"
        "List-Unsubscribe: <mailto:u@site.test>\nReply-To: sales@site.test\nX-Other: no"
    )

    headers = parse_headers(blob)

    assert {"x-auto-response-suppress", "list-id", "list-unsubscribe", "reply-to"} <= set(
        KEPT_HEADERS
    )
    assert headers["reply-to"] == "sales@site.test"
    assert "x-other" not in headers


def test_download_headers_never_let_the_file_render() -> None:
    headers = download_headers("прайс.pdf", 7)

    assert headers["Content-Disposition"] == (
        'attachment; filename="attachment-7.pdf"; '
        "filename*=UTF-8''%D0%BF%D1%80%D0%B0%D0%B9%D1%81.pdf"
    )
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"
    assert download_headers('say "hi".pdf', 1)["Content-Disposition"].startswith(
        'attachment; filename="say _hi_.pdf";'
    )
