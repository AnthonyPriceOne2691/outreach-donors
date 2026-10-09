"""Разборщики форматов: что из файла становится текстом — и где чтение останавливается.

Здесь читается в этом же процессе (`attachment_formats.read`): так видно сам
разбор. Путь целиком — вид файла по байтам, отдельный процесс и его таймаут —
в `test_attachment_text.py`.

Файл чужой, поэтому проверяется не только «прочитал», но и «не прочитал лишнего»:
формула не исполняется, скрытый лист не читается, бомба не распаковывается,
а любое падение разборщика — строка словами, а не исключение наружу.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from typing import Any

import pytest
from backend.features.replies import attachment_formats
from backend.features.replies.attachment_formats import CUT, MAX_CHARS, MAX_MEMORY, read
from backend.features.replies.attachment_text import FileText, Kind
from tests.attachment_files import docx, locked, paragraph, pdf, table, xlsx, zipped


def text_of(kind: Kind, data: bytes) -> str:
    found = read(kind, data)
    assert found.text is not None, found.note
    return found.text


class TestPdf:
    def test_pages_are_read_in_order(self) -> None:
        found = read(Kind.PDF, pdf("Guest post: 150 EUR", "Link insertion: 80 EUR"))

        assert found == FileText(text="Guest post: 150 EUR\nLink insertion: 80 EUR")

    def test_only_the_first_thirty_pages_are_read(self) -> None:
        found = read(Kind.PDF, pdf(*(f"page {number}." for number in range(1, 32))))

        assert found.text is not None
        assert "page 30." in found.text
        assert "page 31." not in found.text
        assert found.note == "прочитаны первые 30 страниц из 31"

    def test_password_locked_pdf_is_said_not_read(self) -> None:
        found = read(Kind.PDF, locked(pdf("Guest post: 150 EUR"), user_password="secret"))

        assert found == FileText(note="PDF защищён паролем — не читается, файл можно скачать")

    def test_lock_on_printing_only_does_not_stop_reading(self) -> None:
        """Пустой пароль пользователя — запрет печати и правки, а не чтения."""
        found = read(Kind.PDF, locked(pdf("Guest post: 150 EUR"), user_password=""))

        assert found.text == "Guest post: 150 EUR"

    def test_pages_without_text_are_called_a_scan(self) -> None:
        found = read(Kind.PDF, pdf("", ""))

        assert found.text is None
        assert found.note is not None
        assert found.note.startswith("в PDF нет текста — похоже, страницы отсканированы")


class TestSpreadsheet:
    def test_rows_are_cells_through_tabs_without_empty_rows(self) -> None:
        book = xlsx(
            {"Прайс": [["Услуга", "Цена"], ["Guest post", 150], [None, None], ["Link", 80]]}
        )

        assert text_of(Kind.XLSX, book) == (
            "[лист «Прайс»]\nУслуга\tЦена\nGuest post\t150\nLink\t80"
        )

    @pytest.mark.parametrize(
        ("value", "number_format", "shown"),
        [
            (150, '"$"#,##0.00', "$ 150"),
            (150, "$#,##0", "$ 150"),
            (80.5, '#,##0.00\\ "€"', "80.5 €"),
            (1200, "[$€-407] #,##0", "€ 1200"),
            (900, '#,##0 "руб."', "900 руб."),
            (300, '_-* #,##0.00\\ "₽"_-;\\-* #,##0.00\\ "₽"_-', "300 ₽"),
            (99, '_("$"* #,##0_);_("$"* \\(#,##0\\)', "$ 99"),
            (0.1 + 0.2, "General", "0.3"),
            (7, "[Red]0", "7"),
        ],
    )
    def test_number_carries_the_currency_of_its_format(
        self, value: float, number_format: str, shown: str
    ) -> None:
        """Число — с валютой, которую человек видит в Excel: без неё цена из
        прайса ушла бы в ручную очередь «валюта не названа»."""
        book = xlsx({"P": [["Guest post", value]]}, formats={"P!B1": number_format})

        assert text_of(Kind.XLSX, book) == f"[лист «P»]\nGuest post\t{shown}"

    def test_date_is_a_date(self) -> None:
        book = xlsx({"P": [["Valid until", datetime(2026, 12, 31)]]})

        assert text_of(Kind.XLSX, book) == "[лист «P»]\nValid until\t2026-12-31"

    def test_formula_is_neither_run_nor_shown(self) -> None:
        """Формула — код. Читается посчитанное Excel значение, а у книги, где его
        нет, — пусто, но не текст формулы."""
        book = xlsx({"P": [["Guest post", 150, "=B1*100"]]})

        text = text_of(Kind.XLSX, book)
        assert "=B1" not in text
        assert text.endswith("Guest post\t150")

    def test_hidden_sheet_is_not_read_and_that_is_said(self) -> None:
        book = xlsx({"Prices": [["Guest post", 150]], "Costs": [["our cost", 5]]}, hidden=["Costs"])

        found = read(Kind.XLSX, book)

        assert found.text == "[лист «Prices»]\nGuest post\t150"
        assert found.note == "скрытых листов не читали: 1"

    def test_only_the_first_ten_sheets_are_read(self) -> None:
        book = xlsx({f"S{number}": [[f"sheet {number}"]] for number in range(1, 12)})

        found = read(Kind.XLSX, book)

        assert found.text is not None
        assert "sheet 10" in found.text
        assert "sheet 11" not in found.text
        assert found.note == "прочитаны первые 10 листов из 11"

    def test_only_the_first_rows_of_a_sheet_are_read(self) -> None:
        book = xlsx({"Long": [[number] for number in range(1, 2002)]})

        found = read(Kind.XLSX, book)

        assert found.text is not None
        assert found.text.endswith("\n1999\n2000")
        assert found.note == "лист «Long»: прочитаны первые 2000 строк"

    def test_sheet_without_values_gives_no_text(self) -> None:
        found = read(Kind.XLSX, xlsx({"Empty": [[None, None]]}))

        assert found == FileText(note="в таблице нет ни одного значения")

    def test_wrong_dimension_in_the_file_does_not_hide_rows(self) -> None:
        """Размер листа пишет программа, сохранившая файл, и пишет его неверно:
        «A1» у листа на сотню строк. Чтение по нему дало бы одну ячейку."""
        book = xlsx({"P": [["Service", "Price"], ["Guest post", 150]]})
        parts = _parts(book)
        sheet = parts["xl/worksheets/sheet1.xml"].decode()
        parts["xl/worksheets/sheet1.xml"] = sheet.replace('ref="A1:B2"', 'ref="A1"').encode()

        assert text_of(Kind.XLSX, zipped(parts)) == "[лист «P»]\nService\tPrice\nGuest post\t150"


def _parts(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        return {name: opened.read(name) for name in opened.namelist()}


class TestDocument:
    def test_paragraphs_and_tables_in_order(self) -> None:
        body = (
            paragraph("Our ra", "tes for 2026")
            + table(["Guest post", "150", "USD"], ["Link insertion", "80", "USD"])
            + paragraph("Thanks!")
        )

        assert text_of(Kind.DOCX, docx(body)) == (
            "Our rates for 2026\nGuest post\t150\tUSD\nLink insertion\t80\tUSD\nThanks!"
        )

    def test_text_box_is_read_once(self) -> None:
        """Надпись Word лежит дважды — рисунком и запасной копией."""
        box = "<w:txbxContent>" + paragraph("Sale: 99 USD") + "</w:txbxContent>"
        body = (
            "<w:p><w:r><mc:AlternateContent>"
            f'<mc:Choice Requires="wps"><w:drawing>{box}</w:drawing></mc:Choice>'
            f"<mc:Fallback><w:pict>{box}</w:pict></mc:Fallback>"
            "</mc:AlternateContent></w:r></w:p>"
        )

        assert text_of(Kind.DOCX, docx(body)) == "Sale: 99 USD"

    def test_deleted_text_and_tab_stops_are_not_text(self) -> None:
        """Удалённое правкой — не то, что донор прислал; позиции табуляции
        в свойствах абзаца — разметка, а не знаки текста."""
        body = (
            '<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>'
            "<w:del><w:r><w:delText>999 USD</w:delText></w:r></w:del>"
            '<w:r><w:t xml:space="preserve">Price</w:t></w:r><w:r><w:tab/><w:t>150 USD</w:t></w:r>'
            "</w:p>"
            "<w:sdt><w:sdtContent>" + paragraph("from a form field") + "</w:sdtContent></w:sdt>"
        )

        assert text_of(Kind.DOCX, docx(body)) == "Price\t150 USD\nfrom a form field"

    def test_entities_are_never_expanded(self) -> None:
        """«Миллиард смешков»: разметка с DTD не разбирается вовсе."""
        prolog = '<!DOCTYPE d [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'

        found = read(Kind.DOCX, docx(paragraph("&b;"), prolog=prolog))

        assert found == FileText(
            note="не удалось прочитать файл: DTDForbidden", failure="DTDForbidden"
        )


class TestZipCeilings:
    @pytest.mark.parametrize("kind", [Kind.XLSX, Kind.DOCX])
    def test_unpacked_size_is_judged_before_unpacking(
        self, kind: Kind, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Пятьдесят один мегабайт нулей сжимаются в пятьдесят килобайт. Отказ —
        по оглавлению: до разборщика дело не доходит."""
        monkeypatch.setattr(attachment_formats.openpyxl, "load_workbook", _never)
        monkeypatch.setattr(attachment_formats, "parse_xml", _never)
        bomb = zipped({"word/document.xml": b"<a/>", "xl/zeros.bin": b"\0" * (51 * 1024 * 1024)})
        assert len(bomb) < 1024 * 1024

        found = read(kind, bomb)

        assert found == FileText(
            note="распакованный файл больше 50 МБ — так прайс не выглядит; файл можно скачать"
        )

    def test_too_many_parts_are_not_unpacked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(attachment_formats.openpyxl, "load_workbook", _never)
        crowd = zipped({f"xl/part{number}.xml": b"<a/>" for number in range(1001)})

        found = read(Kind.XLSX, crowd)

        assert found == FileText(
            note="в файле больше 1000 частей — так прайс не выглядит; файл можно скачать"
        )


def _never(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("разборщик позван на файле, который не прошёл потолки")


class TestText:
    def test_csv_cells_go_through_tabs(self) -> None:
        """Запятая между числами склеила бы их: «250,300» — одно число."""
        data = b"Service,Price,Old price\r\nGuest post,250,300\r\n\r\n,,\r\n"

        assert text_of(Kind.CSV, data) == "Service\tPrice\tOld price\nGuest post\t250\t300"

    def test_csv_from_russian_excel_reads_its_encoding_and_delimiter(self) -> None:
        data = "Услуга;Цена\r\nГостевой пост;150 €\r\n".encode("cp1251")

        assert text_of(Kind.CSV, data) == "Услуга\tЦена\nГостевой пост\t150 €"

    def test_only_the_first_rows_of_a_csv_are_read(self) -> None:
        data = "\n".join(str(number) for number in range(1, 2002)).encode()

        found = read(Kind.CSV, data)

        assert found.text is not None
        assert found.text.endswith("\n2000")
        assert found.note == "прочитаны первые 2000 строк"

    def test_csv_the_reader_refuses_is_kept_as_plain_text(self) -> None:
        """Поле длиннее предела `csv` — не таблица, но текст в нём остаётся."""
        data = b'Guest post,"' + b"x " * 70_000 + b'"\n'

        found = read(Kind.CSV, data)

        assert found.text is not None
        assert found.text.startswith('Guest post,"x x')
        assert found.note is not None
        assert found.note.startswith("таблица не разобрана как CSV — текст как есть")

    def test_text_in_utf16_loses_control_characters_and_extra_blank_lines(self) -> None:
        data = "Цена\x00 100 €\r\n\r\n\r\n\r\nвсё\x07".encode("utf-16")

        assert read(Kind.TXT, data) == FileText(text="Цена 100 €\n\nвсё")

    def test_long_text_is_cut_at_a_word_and_says_so(self) -> None:
        """Обрезанное «150» стало бы «15» — другим числом: режется по слову."""
        room = MAX_CHARS - len(CUT)
        data = ("a " * ((room - 5) // 2) + "1234567890" * 3).encode()

        found = read(Kind.TXT, data)

        assert found.text is not None
        assert len(found.text) <= MAX_CHARS
        assert found.text.endswith(f"a{CUT}")
        assert "12345" not in found.text
        assert found.note == "текст длиннее 20 000 знаков — прочитано начало"

    def test_html_gives_text_without_scripts_and_styles(self) -> None:
        data = (
            b"<html><head><style>td{}</style><script>fetch('/api/users')</script></head>"
            b"<body><table><tr><td>Guest post</td><td>150 USD</td></tr></table></body></html>"
        )

        assert read(Kind.HTML, data) == FileText(text="Guest post\t150 USD")

    def test_text_file_without_text_says_so(self) -> None:
        assert read(Kind.TXT, b" \r\n\t\n") == FileText(note="в файле нет текста")


class TestFailure:
    def test_broken_file_is_a_note_not_an_exception(self) -> None:
        found = read(Kind.XLSX, b"PK\x03\x04 this is not a workbook")

        assert found == FileText(note="не удалось прочитать файл: BadZipFile", failure="BadZipFile")

    def test_any_failure_of_a_reader_is_named_by_type_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """В строке человеку — тип ошибки, а не трасса и не текст исключения:
        текст пишет разборщик, а иногда и сам файл."""

        def broken(_data: bytes) -> FileText:
            raise RuntimeError("Traceback … /app/backend … secret")

        monkeypatch.setitem(attachment_formats._READERS, Kind.TXT, broken)

        found = read(Kind.TXT, b"Price 150")

        assert found == FileText(
            note="не удалось прочитать файл: RuntimeError", failure="RuntimeError"
        )


class TestProcessEntry:
    """Вход процесса чтения — здесь, в этом процессе: потолок памяти подменён,
    иначе он лёг бы на весь прогон тестов."""

    def test_file_on_stdin_answers_json_on_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        limits: list[tuple[int, tuple[int, int]]] = []
        monkeypatch.setattr(
            attachment_formats.resource, "setrlimit", lambda *args: limits.append(args)
        )
        stdin, stdout = _Stdin(b"Guest post 150 USD"), io.StringIO()
        monkeypatch.setattr(attachment_formats.sys, "stdin", stdin)
        monkeypatch.setattr(attachment_formats.sys, "stdout", stdout)

        attachment_formats.main(["reader", "txt"])

        assert json.loads(stdout.getvalue()) == {
            "text": "Guest post 150 USD",
            "note": None,
            "failure": None,
        }
        assert limits == [(attachment_formats.resource.RLIMIT_AS, (MAX_MEMORY, MAX_MEMORY))]

    def test_memory_ceiling_the_system_refuses_is_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refused(*_args: Any) -> None:
            raise ValueError("current limit exceeds maximum limit")

        monkeypatch.setattr(attachment_formats.resource, "setrlimit", refused)
        monkeypatch.setattr(attachment_formats.sys, "stdin", _Stdin(b"Price 1"))
        monkeypatch.setattr(attachment_formats.sys, "stdout", stdout := io.StringIO())

        attachment_formats.main(["reader", "txt"])

        assert json.loads(stdout.getvalue())["text"] == "Price 1"


class _Stdin:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)
