"""Файлы для тестов чтения вложений — собираются здесь же, кодом.

Готовых двоичных файлов в дереве нет намеренно: прайс, собранный кодом,
читается на ревью глазами, и видно, что в нём лежит, — а файл из дерева
остаётся чёрным ящиком, и тест о нём говорит меньше, чем кажется.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any

import openpyxl
from pypdf import PdfReader, PdfWriter

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF"
RAR = b"Rar!\x1a\x07\x00\xcf\x90s"
#: Контейнер старого Office (OLE2): так начинаются .xls, .doc и запертый .xlsx.
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 24

_NAMESPACES = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
)


def pdf(*pages: str) -> bytes:
    """Минимальный корректный PDF: на каждой странице — строка текста Helvetica.

    Смещения в таблице xref считаются честно: разборщик, который чинит битую
    таблицу сам, на кривом файле прошёл бы тест, который проверял не то.
    """
    font = 3 + 2 * len(pages)
    kids = " ".join(f"{3 + 2 * index} 0 R" for index in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
    ]
    for index, line in enumerate(pages):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {4 + 2 * index} 0 R >>".encode()
        )
        shown = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({shown}) Tj ET".encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n%b\nendstream" % (len(stream), stream))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%b\nendobj\n" % (number, body)
    table = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        table,
    )
    return bytes(out)


def locked(document: bytes, *, user_password: str) -> bytes:
    """Тот же PDF, запертый паролем. Пустой пароль пользователя — запрет только
    печати и правки: такой файл открывается и читается без пароля."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(document)))
    writer.encrypt(
        user_password=user_password,
        owner_password="owner-only",  # pragma: allowlist secret
        algorithm="AES-256",
    )
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def xlsx(
    sheets: Mapping[str, Sequence[Sequence[Any]]],
    *,
    formats: Mapping[str, str] | None = None,
    hidden: Sequence[str] = (),
) -> bytes:
    """Книга Excel: лист — строки значений. `formats` — «Лист!B2» → формат числа."""
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(list(row))
        if title in hidden:
            sheet.sheet_state = "hidden"
    for where, number_format in (formats or {}).items():
        title, cell = where.split("!")
        book[title][cell].number_format = number_format
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def paragraph(*runs: str) -> str:
    """Абзац Word: каждый кусок — свой прогон, как Word и режет текст."""
    return (
        "<w:p>"
        + "".join(f'<w:r><w:t xml:space="preserve">{run}</w:t></w:r>' for run in runs)
        + "</w:p>"
    )


def table(*rows: Sequence[str]) -> str:
    """Таблица Word: строка — ячейки с абзацем текста."""
    cells = (
        "<w:tr>" + "".join(f"<w:tc>{paragraph(cell)}</w:tc>" for cell in row) + "</w:tr>"
        for row in rows
    )
    return "<w:tbl>" + "".join(cells) + "</w:tbl>"


def docx(body: str, *, prolog: str = "") -> bytes:
    """Документ Word из разметки тела: ровно те части, что читает разборщик."""
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>{prolog}'
        f"<w:document {_NAMESPACES}><w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    return zipped({"[Content_Types].xml": b"<Types/>", "word/document.xml": document.encode()})


def zipped(parts: Mapping[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return out.getvalue()
