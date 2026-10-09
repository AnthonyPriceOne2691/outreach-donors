"""Файл к нашему ответу: какой уходит с письмом, какой нет и что сказано человеку.

Антивируса нет, поэтому правило строгое (`letters/outgoing_files.py`): уходит только
то, что узнаётся по содержимому и сходится с расширением, а внутри разрешённого —
без исполняемого. Здесь же — что наш предел файлов на письмо стоит под пределом
платформы: письмо больше её предела не уходит вовсе, и узнали бы мы это отказом
на живом ответе.
"""

from __future__ import annotations

import io
import math
import re
import unicodedata
import zipfile

import pytest
from backend.features.letters import outgoing_files, sendgrid
from backend.features.letters.answers import MAX_BODY
from backend.features.letters.outgoing_files import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_LETTER_BYTES,
    MAX_NAME,
    OutgoingFileError,
    check,
    check_letter,
    clean_name,
)

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
EXE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00 This program cannot be run"


def package(*parts: str) -> bytes:
    """Zip-пакет с названными частями — так устроены DOCX и XLSX."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for part in parts:
            archive.writestr(part, "<x/>")
    return buffer.getvalue()


DOCX = package("[Content_Types].xml", "_rels/.rels", "word/document.xml")
XLSX = package("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/worksheets/sheet1.xml")
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TestWhatGoes:
    @pytest.mark.parametrize(
        ("name", "data", "content_type"),
        [
            ("Прайс 2026.pdf", PDF, "application/pdf"),
            ("Media kit.docx", DOCX, DOCX_TYPE),
            ("rates.xlsx", XLSX, XLSX_TYPE),
            ("rates.csv", "site;price\nexample;120 €\n".encode(), "text/csv"),
            ("notes.txt", "Цена — 120 €, оплата PayPal.\n".encode(), "text/plain"),
            ("banner.png", PNG, "image/png"),
            ("photo.jpg", JPEG, "image/jpeg"),
            ("photo.jpeg", JPEG, "image/jpeg"),
            ("ПРАЙС.PDF", PDF, "application/pdf"),
        ],
    )
    def test_allowed_file_goes_with_our_type(
        self, name: str, data: bytes, content_type: str
    ) -> None:
        checked = check(name, data)

        assert (checked.name, checked.content_type, checked.size) == (
            name,
            content_type,
            len(data),
        )

    def test_text_with_a_bom_and_a_plain_angle_bracket_is_text(self) -> None:
        """«<3» — не разметка: отказ — только началу страницы или картинки SVG."""
        checked = check("thanks.txt", b"\xef\xbb\xbf<3 thanks for the reply")

        assert checked.content_type == "text/plain"

    def test_pdf_with_a_json_name_is_not_a_script(self) -> None:
        """Имя в PDF кончается разделителем: `/JSON` — не `/JS`."""
        assert check("data.pdf", PDF.replace(b"/Type", b"/JSON")).content_type == "application/pdf"


class TestWhatDoesNot:
    @pytest.mark.parametrize(
        "name",
        ["setup.exe", "page.html", "logo.svg", "archive.zip", "run.js", "old.doc", "old.xls"],
    )
    def test_type_outside_the_list_is_refused_and_the_list_is_named(self, name: str) -> None:
        with pytest.raises(OutgoingFileError) as refused:
            check(name, b"anything")

        assert str(refused.value) == (
            f"«{name}»: такие файлы с письмом не уходят — можно "
            "PDF, DOCX, XLSX, CSV, TXT, PNG, JPG (JPEG)"
        )

    @pytest.mark.parametrize(
        ("name", "data", "words"),
        [
            ("price.pdf", EXE, "по содержимому это не PDF"),
            ("price.pdf", PNG, "по содержимому это не PDF"),
            ("banner.png", JPEG, "по содержимому это не картинка PNG"),
            ("photo.jpg", PNG, "по содержимому это не картинка JPEG"),
            ("rates.xlsx", DOCX, "по содержимому это не таблица Excel"),
            ("kit.docx", XLSX, "по содержимому это не документ Word"),
            ("kit.docx", package("word/document.xml"), "по содержимому это не документ Word"),
            ("kit.docx", package("[Content_Types].xml", "readme.txt"), "не документ Word"),
            ("kit.docx", EXE, "по содержимому это не документ Word"),
            # Склейка: программа, к концу которой приписан пакет, — zip нашёл бы
            # опись с конца и принял бы её.
            ("kit.docx", EXE + DOCX, "по содержимому это не документ Word"),
            ("kit.docx", b"PK\x03\x04 broken package", "по содержимому это не документ Word"),
        ],
    )
    def test_extension_and_content_must_agree(self, name: str, data: bytes, words: str) -> None:
        with pytest.raises(OutgoingFileError, match=words) as refused:
            check(name, data)

        assert str(refused.value).startswith(f"«{name}»: ")

    @pytest.mark.parametrize(
        "part",
        [
            "word/vbaProject.bin",
            "word/vbaData.xml",
            "word/activeX/activeX1.xml",
            "word/embeddings/oleObject1.bin",
        ],
    )
    def test_office_document_with_active_content_is_refused(self, part: str) -> None:
        with pytest.raises(OutgoingFileError, match="с макросами или встроенными объектами"):
            check("kit.docx", package("[Content_Types].xml", "word/document.xml", part))

    def test_spreadsheet_with_macros_is_refused(self) -> None:
        with pytest.raises(OutgoingFileError, match="таблица Excel с макросами"):
            check(
                "rates.xlsx", package("[Content_Types].xml", "xl/workbook.xml", "xl/vbaProject.bin")
            )

    @pytest.mark.parametrize(
        "active",
        [
            b"/OpenAction << /S /JavaScript /JS (app.alert(1)) >>",
            b"/AA << /O << /JS 5 0 R >> >>",
            b"/S /Launch /F (cmd.exe)",
            b"/Type /EmbeddedFile /Length 42",
            b"/RichMedia << >>",
        ],
    )
    def test_pdf_with_scripts_launch_or_embedded_files_is_refused(self, active: bytes) -> None:
        data = PDF.replace(b"/Type /Catalog", b"/Type /Catalog " + active)

        with pytest.raises(OutgoingFileError, match="скрипты, запуск программ или вложенные файлы"):
            check("kit.pdf", data)

    @pytest.mark.parametrize(
        ("name", "data", "words"),
        [
            ("rates.csv", "сайт;цена\n".encode("cp1251"), "таблица CSV не в кодировке UTF-8"),
            # Excel «текст Юникода» — UTF-16: латиница в нём идёт через нулевой байт.
            ("notes.txt", "Price list".encode("utf-16"), "нулевые байты"),
            ("notes.txt", b"price\x00list", "нулевые байты — по содержимому это не текст"),
            ("notes.txt", b"\xef\xbb\xbf  <!DOCTYPE html><script>x</script>", "разметка HTML"),
            ("notes.txt", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "разметка HTML или SVG"),
            ("rates.csv", b"<?xml version='1.0'?><svg/>", "разметка HTML или SVG"),
            ("notes.txt", b"\n<HTML><body>hi</body></HTML>", "разметка HTML или SVG"),
        ],
    )
    def test_text_must_be_utf8_text_and_not_markup(
        self, name: str, data: bytes, words: str
    ) -> None:
        with pytest.raises(OutgoingFileError, match=words):
            check(name, data)

    def test_empty_file_is_refused(self) -> None:
        with pytest.raises(
            OutgoingFileError, match=re.escape("«price.pdf» пустой — прикладывать нечего")
        ):
            check("price.pdf", b"")


class TestSize:
    def test_file_at_the_limit_goes(self) -> None:
        data = PDF + b"0" * (MAX_FILE_BYTES - len(PDF))

        assert check("big.pdf", data).size == MAX_FILE_BYTES

    def test_one_byte_over_the_limit_is_refused_with_the_limit_named(self) -> None:
        data = PDF + b"0" * (MAX_FILE_BYTES + 1 - len(PDF))

        with pytest.raises(OutgoingFileError) as refused:
            check("big.pdf", data)

        assert str(refused.value) == (
            "«big.pdf» больше предела 10 МБ на файл — уменьшите его или пришлите ссылкой в тексте"
        )

    def test_five_files_go_and_the_sixth_is_refused(self) -> None:
        check_letter(MAX_FILES, MAX_FILES)

        with pytest.raises(OutgoingFileError) as refused:
            check_letter(MAX_FILES + 1, MAX_FILES + 1)

        assert str(refused.value) == (
            "К письму — не больше 5 файлов, а приложено 6: уберите лишние"
        )

    def test_files_of_a_letter_together_are_capped(self) -> None:
        check_letter(3, MAX_LETTER_BYTES)

        with pytest.raises(OutgoingFileError) as refused:
            check_letter(3, MAX_LETTER_BYTES + 512 * 1024)

        assert str(refused.value) == (
            "Файлы письма вместе — 20,5 МБ, больше предела 20 МБ на письмо: уберите лишние"
        )

    def test_our_letter_limit_is_under_the_platform_limit_after_encoding(self) -> None:
        """Платформа считает письмо целиком: файлы в base64 (треть сверху) с переносом
        строки каждые 76 знаков, текст ответа (до 4 байт на знак, тоже в base64),
        заголовки письма и частей. Всё вместе обязано быть меньше её предела."""
        files = math.ceil(MAX_LETTER_BYTES / 3) * 4 * 78 / 76
        text = math.ceil(MAX_BODY * 4 / 3) * 4 * 78 / 76
        headers = 16 * 1024 + MAX_FILES * 1024

        assert files + text + headers < sendgrid.MAX_MESSAGE_BYTES


class TestName:
    @pytest.mark.parametrize(
        ("raw", "clean"),
        [
            ("C:\\fakepath\\прайс.pdf", "прайс.pdf"),
            ("../../etc/passwd.txt", "passwd.txt"),
            ("pri\x00ce\r\n.pdf", "price.pdf"),
            # Разворот строки (U+202E): «invoice», разворот, «gpj.pdf» — на экране «invoicefdp.jpg».
            ("invoice\u202egpj.pdf", "invoicegpj.pdf"),
            ("zero\u200bwidth.pdf", "zerowidth.pdf"),
            ('a<b>:c"d|e?f*.pdf', "a_b__c_d_e_f_.pdf"),
            ("price.pdf. . ", "price.pdf"),
            ("  price list .xlsx  ", "price list.xlsx"),
            (".pdf", "attachment.pdf"),
            ("Price.PDF", "Price.PDF"),
        ],
    )
    def test_name_is_base_visible_and_keeps_its_extension(self, raw: str, clean: str) -> None:
        assert clean_name(raw) == clean

    def test_decomposed_letters_are_composed(self) -> None:
        """Файлы с Mac приходят с «й» из двух знаков: длина и вид имени — по составленным."""
        decomposed = unicodedata.normalize("NFD", "отчёт за май.pdf")

        assert clean_name(decomposed) == "отчёт за май.pdf"

    def test_long_name_is_cut_to_the_limit_with_the_extension_whole(self) -> None:
        name = clean_name("п" * 300 + ".xlsx")

        assert len(name) == MAX_NAME
        assert name == "п" * (MAX_NAME - 5) + ".xlsx"

    @pytest.mark.parametrize("raw", ["README", "price.", None, ""])
    def test_name_without_an_extension_is_refused(self, raw: str | None) -> None:
        with pytest.raises(OutgoingFileError, match="у файла нет расширения"):
            clean_name(raw)


def test_every_allowed_extension_is_named_in_the_refusal() -> None:
    """Список в отказе и таблица типов — одно и то же: разойдись они, человек
    искал бы тип, которого нет, или не знал бы о том, что есть."""
    named = outgoing_files.ALLOWED.replace("(", "").replace(")", "").replace(",", "").split()

    assert sorted(extension.upper() for extension in outgoing_files._KINDS) == sorted(named)
