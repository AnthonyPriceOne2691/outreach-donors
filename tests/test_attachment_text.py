"""Вложение в текст целиком: вид файла по байтам, отдельный процесс и его таймаут.

Вид решают байты, а не имя и не тип отправителя: «price.pdf» бывает картинкой,
а «price.xls», выгруженный сайтом, — страницей HTML. Всё, что не читается, —
строка словами, которую человек увидит на экране вместо текста.

Чтение идёт настоящим отдельным процессом: подделка процесса проверила бы
подделку. Чтобы проверить таймаут и падение, подменяется только команда —
путь запуска, ожидания и убийства остаётся тем же, что в проде.
"""

from __future__ import annotations

import logging
import sys
import time

import pytest
from backend.features.replies import attachment_text
from backend.features.replies.attachment_text import (
    ARCHIVE,
    PICTURE,
    PROTECTED,
    FileText,
    Kind,
    UnreadableError,
    extension_of,
    kind_of,
    read_text,
)
from backend.features.replies.inbound import Attachment
from tests.attachment_files import JPEG, OLE, PNG, RAR, docx, paragraph, pdf, xlsx, zipped

PRICE_PDF = pdf("Guest post: 150 EUR")
PRICE_BOOK = xlsx({"Prices": [["Guest post", 150]]}, formats={"Prices!B1": '"$"#,##0'})


class TestWhatTheFileIs:
    @pytest.mark.parametrize(
        ("name", "data", "kind"),
        [
            ("price.pdf", PRICE_PDF, Kind.PDF),
            # Байты главнее имени: PDF под чужим именем — всё равно PDF.
            ("price.txt", PRICE_PDF, Kind.PDF),
            ("scan.pdf", b"\r\n" + PRICE_PDF, Kind.PDF),
            ("price.xlsx", PRICE_BOOK, Kind.XLSX),
            # Таблица и документ — по частям внутри ZIP, а не по расширению.
            ("price.pdf", PRICE_BOOK, Kind.XLSX),
            ("price", PRICE_BOOK, Kind.XLSX),
            ("rates.docx", docx(paragraph("150 USD")), Kind.DOCX),
            ("rates.zip", docx(paragraph("150 USD")), Kind.DOCX),
            ("rates.csv", b"Guest post,150", Kind.CSV),
            ("rates.tsv", b"Guest post\t150", Kind.CSV),
            ("rates.TXT.", b"Guest post 150", Kind.TXT),
            ("rates.htm", b"Guest post <b>150</b>", Kind.HTML),
            # Выгрузка сайта «в Excel» — часто страница HTML с таблицей.
            ("export.xls", b"\xef\xbb\xbf\n<HTML><table><tr><td>150</td></tr></table>", Kind.HTML),
            ("price.txt", "Цена 150 €".encode("utf-16"), Kind.TXT),
        ],
    )
    def test_kind_comes_from_the_bytes(self, name: str, data: bytes, kind: Kind) -> None:
        assert kind_of(name, data) is kind

    @pytest.mark.parametrize(
        ("name", "data", "said"),
        [
            ("price.pdf", PNG, PICTURE),
            ("scan.jpg", JPEG, PICTURE),
            ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", PICTURE),
            ("prices.zip", zipped({"prices.txt": b"150 USD"}), ARCHIVE),
            ("prices.rar", RAR, ARCHIVE),
            ("old.xls", OLE, "старый формат Excel (.xls) не читается — файл можно скачать"),
            ("old.doc", OLE, "старый формат Word (.doc) не читается — файл можно скачать"),
            ("locked.xlsx", OLE, PROTECTED),
            ("deck.pptx", zipped({"ppt/presentation.xml": b"<p/>"}), "«.pptx» не читается — файл можно скачать"),
            ("rates.pdf", b"Guest post 150", "файл назван «.pdf», но внутри не PDF — не читается, файл можно скачать"),
            ("rates.txt", b"Guest\x00post", "файл назван «.txt», но внутри двоичные данные, а не текст — файл можно скачать"),
            ("rates", b"Guest post 150", "файл без расширения не читается — файл можно скачать"),
            ("rates.rtf", b"{\\rtf1 Guest post 150}", "«.rtf» не читается — файл можно скачать"),
            ("rates.txt", b"", "файл пустой — читать нечего"),
            ("broken.xlsx", b"PK\x03\x04 not really a zip", "файл повреждён: оглавление ZIP не читается — файл можно скачать"),
        ],
    )  # fmt: skip
    def test_what_is_not_read_is_said_in_words(self, name: str, data: bytes, said: str) -> None:
        with pytest.raises(UnreadableError) as refused:
            kind_of(name, data)

        assert str(refused.value) == said

    def test_type_named_by_the_sender_decides_nothing(self) -> None:
        """Отправитель назвал картинку PDF — читается как картинка, то есть никак."""
        assert read_text("price.pdf", "application/pdf", PNG) == FileText(note=PICTURE)

    def test_extension_is_measured_like_the_one_of_a_dangerous_file(self) -> None:
        """Одна мера расширения: у отказа опасному файлу и у чтения текста."""
        assert extension_of("прайс.exe. ") == Attachment(name="прайс.exe. ", size=1).extension
        assert extension_of("прайс.exe. ") == ".exe"


class TestReadingInItsOwnProcess:
    def test_spreadsheet_is_read(self) -> None:
        found = read_text("price.xlsx", None, PRICE_BOOK)

        assert found == FileText(text="[лист «Prices»]\nGuest post\t$ 150")

    def test_pdf_is_read(self) -> None:
        assert read_text("price.pdf", "application/pdf", PRICE_PDF).text == "Guest post: 150 EUR"

    def test_failure_of_the_reader_comes_back_as_words(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Документ без закрывающего тега: разборщик в процессе падает, а сюда
        приходит строка словами и тип ошибки — в журнал полем, а не текстом."""
        broken = zipped({"word/document.xml": b"<w:document><w:body>"})

        with caplog.at_level(logging.WARNING, logger=attachment_text.__name__):
            found = read_text("rates.docx", None, broken)

        assert found == FileText(note="не удалось прочитать файл: ParseError", failure="ParseError")
        record = next(r for r in caplog.records if r.getMessage().endswith("не прочитал файл"))
        assert (record.attachment, record.kind, record.error) == (
            "rates.docx",
            "docx",
            "ParseError",
        )

    def test_hung_reading_is_killed_and_said(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(
            attachment_text,
            "_command",
            lambda _kind: [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        started = time.monotonic()

        with caplog.at_level(logging.WARNING, logger=attachment_text.__name__):
            found = read_text("rates.txt", None, b"Guest post 150", timeout=0.5)

        assert time.monotonic() - started < 10
        assert found == FileText(
            note="файл не прочитан за 0.5 с — так долго прайс не читается; файл можно скачать",
            failure="timeout",
        )
        assert any(r.getMessage().endswith("процесс убит") for r in caplog.records)

    @pytest.mark.parametrize(
        ("script", "code"),
        [("import sys; sys.exit(3)", 3), ("print('не JSON')", 0)],
    )
    def test_process_that_gives_no_answer_is_said(
        self, monkeypatch: pytest.MonkeyPatch, script: str, code: int
    ) -> None:
        monkeypatch.setattr(
            attachment_text, "_command", lambda _kind: [sys.executable, "-c", script]
        )

        found = read_text("rates.txt", None, b"Guest post 150")

        assert found == FileText(
            note=f"не удалось прочитать файл: процесс чтения не дал ответа (код {code})",
            failure="crash",
        )

    def test_process_that_cannot_start_is_said(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(attachment_text, "_command", lambda _kind: ["/нет/такого/питона"])

        found = read_text("rates.txt", None, b"Guest post 150")

        assert found == FileText(
            note="не удалось прочитать файл: FileNotFoundError", failure="start"
        )

    def test_process_sees_neither_keys_nor_the_database(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Файл чужой: даже если разборщик на нём дырявый, ключей у процесса нет."""
        monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
        monkeypatch.setenv("STORAGE_DSN", "postgresql://db.example.test/outreach")
        script = (
            "import json, os, sys; "
            "sys.stdout.write(json.dumps({'text': ' '.join(sorted(os.environ)), "
            "'note': None, 'failure': None}))"
        )
        monkeypatch.setattr(
            attachment_text, "_command", lambda _kind: [sys.executable, "-c", script]
        )

        found = read_text("rates.txt", None, b"Guest post 150")

        assert found.failure is None
        assert "LLM_API_KEY" not in (found.text or "")
        assert "STORAGE_DSN" not in (found.text or "")

    def test_command_writes_nothing_and_carries_no_bytes_of_the_file(self) -> None:
        command = attachment_text._command(Kind.PDF)

        assert command[:4] == [sys.executable, "-B", "-s", "-m"]
        assert command[-1] == "pdf"
