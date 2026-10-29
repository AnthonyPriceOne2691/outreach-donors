"""Таблица доноров с ценами, присланная с экрана: CSV или XLSX — в строки ввода.

Загрузка доноров списком (29.10.2026, тест-группа №2: «нужен инструмент массового заноса
доноров — до 10 000 за выгрузку»): до этого донора с ценой заводили по одному кнопкой
«Завести донора вручную». Здесь только чтение — какие колонки, номер строки файла, ячейка как
написана. Правила записи — те же, что у кнопки (`bulk_entry.py` → `manual_price.py`,
`price_refusal.py`).

**CSV читает общий читатель таблиц из рук человека** (`contacts.sweep_input.records_of`):
UTF-8 со словами, что делать, если файл в другой кодировке, разделитель — по первой строке,
номер — строки файла. XLSX — первый видимый лист, значения, а не формулы (`data_only`); ZIP
мерится по оглавлению до распаковки (`attachment_formats.zip_size`): таблица-бомба на гигабайты
съела бы память сервера.

**Колонки — по заголовку** (`NAMES`): домен, цена, валюта, откуда цена; серая цена и способы
оплаты — если есть, как у формы. Заголовка нет — по порядку, как в шаблоне (`TEMPLATE`): домен,
цена, валюта, откуда цена. Пустая валюта — та же, что у формы по умолчанию.

**Кривая строка стоит строки, а не файла** — её назовёт отчёт номером. Файл целиком не
читается, только если не прочитать ничего: не та кодировка, не таблица, нет колонки домена или
цены, строк больше `MAX_ROWS`.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from pathlib import PurePath
from typing import Any

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from backend.features.contacts.sweep_input import HOST_COLUMNS, host_from_cell, records_of
from backend.features.replies.attachment_formats import MAX_PARTS, MAX_UNPACKED, zip_size

#: Строк с донорами за загрузку — так и просили: «до 10 000 за выгрузку».
MAX_ROWS = 10_000
#: Файл — не больше: прокси сервиса пропускает тело до 8 МБ (`deploy/proxy/outreach.conf`),
#: а десять тысяч строк CSV — около полумегабайта.
MAX_BYTES = 7 * 1024 * 1024
#: Имя файла в словах отказа — не длиннее: имя приходит от клиента как есть.
_NAME_CHARS = 80


class Column(StrEnum):
    """Колонки загрузки — те же поля, что у формы «Завести донора вручную»."""

    HOST = "host"
    PRICE = "price"
    CURRENCY = "currency"
    NOTE = "note"
    GREY = "grey"
    PAYMENT = "payment"


#: Имена колонок в заголовке, без регистра. Домен — общими именами читателя таблиц и своими.
NAMES: dict[Column, tuple[str, ...]] = {
    Column.HOST: (*HOST_COLUMNS, "донор"),
    Column.PRICE: ("цена", "price", "белая цена", "white price", "стоимость", "cost"),
    Column.CURRENCY: ("валюта", "currency", "код валюты"),
    Column.NOTE: ("откуда цена", "откуда", "источник", "source", "note", "заметка", "комментарий"),
    Column.GREY: ("серая цена", "grey price", "gray price"),
    Column.PAYMENT: ("способы оплаты", "оплата", "payment", "payment methods"),
}
#: Порядок колонок без заголовка — и шаблон, который скачивают с экрана.
ORDER = (Column.HOST, Column.PRICE, Column.CURRENCY, Column.NOTE)
TEMPLATE = (
    ("домен", "цена", "валюта", "откуда цена"),
    ("example.com", "150", "USD", "прайс агентства"),
)


class BulkFileError(ValueError):
    """Файл не прочитать целиком: текст говорит почему и что сделать."""


@dataclass(frozen=True, slots=True)
class Line:
    """Строка таблицы: номер строки файла и ячейки по колонкам, как написаны."""

    number: int
    cells: dict[Column, str]

    def get(self, column: Column) -> str:
        return self.cells.get(column, "")


def template_csv() -> str:
    """Шаблон загрузки — CSV UTF-8 с заголовком и строкой-примером."""
    out = io.StringIO()
    csv.writer(out).writerows(TEMPLATE)
    return out.getvalue()


def read_table(data: bytes, name: str) -> list[Line]:
    """Строки доноров из файла. Отказ целиком — `BulkFileError` словами."""
    shown = _shown_name(name)
    if len(data) > MAX_BYTES:
        raise BulkFileError(
            f"файл {shown} больше {MAX_BYTES // 2**20} МБ — разделите список на несколько файлов"
        )
    records = _xlsx_records(data, shown) if _is_xlsx(data, name) else _csv_records(data, shown)
    if not records:
        raise BulkFileError(f"в файле {shown} нет ни одной строки")
    columns, header = _columns(records[0][1])
    body = records[1:] if header else records
    if not body:
        raise BulkFileError(f"в файле {shown} только заголовок — строк с донорами нет")
    if len(body) > MAX_ROWS:
        raise BulkFileError(
            f"в файле {shown} строк с донорами {len(body)} — за загрузку не больше {MAX_ROWS}: "
            "разделите список на несколько файлов"
        )
    return [
        Line(number, {column: _cell(row, index) for column, index in columns.items()})
        for number, row in body
    ]


def _shown_name(name: str) -> str:
    """Имя файла для слов отказа: последнее звено пути, одной строкой, не длиннее предела."""
    clean = " ".join(PurePath(name or "").name.split())[:_NAME_CHARS]
    return f"«{clean}»" if clean else "без имени"


def _is_xlsx(data: bytes, name: str) -> bool:
    suffix = PurePath(name or "").suffix.lower()
    if suffix == ".xls":
        raise BulkFileError(
            "старый формат Excel (.xls) не читается — сохраните файл как .xlsx или CSV UTF-8"
        )
    return suffix == ".xlsx" or data.startswith(b"PK\x03\x04")


def _csv_records(data: bytes, shown: str) -> list[tuple[int, list[str]]]:
    try:
        return records_of(data, shown)
    except csv.Error as exc:
        raise BulkFileError(f"файл {shown} не читается как CSV: {exc}") from exc
    except ValueError as exc:  # не UTF-8 — уже словами общего читателя
        raise BulkFileError(str(exc)) from exc


def _xlsx_records(data: bytes, shown: str) -> list[tuple[int, list[str]]]:
    """Первый видимый лист: непустые строки с номерами строк листа. Читается не дальше
    `MAX_ROWS + 2` непустых строк — заголовок и одна лишняя скажут «строк больше предела»."""
    try:
        parts, unpacked = zip_size(data)
    except zipfile.BadZipFile as exc:
        raise BulkFileError(f"файл {shown} не читается как таблица Excel (.xlsx)") from exc
    if parts > MAX_PARTS or unpacked > MAX_UNPACKED:
        raise BulkFileError(
            f"файл {shown} распаковывается в {unpacked // 2**20} МБ — так список доноров не "
            "выглядит; сохраните его как CSV UTF-8"
        )
    try:
        book = openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=True, keep_links=False
        )
    except (InvalidFileException, KeyError, OSError, zipfile.BadZipFile) as exc:
        raise BulkFileError(f"файл {shown} не читается как таблица Excel (.xlsx)") from exc
    try:
        return _sheet_records(book)
    finally:
        book.close()


def _sheet_records(book: Any) -> list[tuple[int, list[str]]]:
    shown = [sheet for sheet in book.worksheets if sheet.sheet_state == "visible"]
    if not shown:
        return []
    sheet = shown[0]
    # Размеры листа, записанные в файле, бывают неверны (`attachment_formats._sheet`).
    sheet.reset_dimensions()
    records: list[tuple[int, list[str]]] = []
    for number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        cells = [_value(value) for value in row]
        if any(cells):
            records.append((number, cells))
            if len(records) > MAX_ROWS + 1:
                break
    return records


def _value(value: object) -> str:
    """Ячейка листа — строкой, как её вписал бы человек: 150, а не 150.0."""
    match value:
        case None:
            return ""
        case bool():
            return str(value).upper()
        case float() if value.is_integer():
            return str(int(value))
        case float():
            return f"{value:.15g}"
        case datetime() | date() | time():
            return value.isoformat()
        case _:
            return " ".join(str(value).split())


def _columns(head: list[str]) -> tuple[dict[Column, int], bool]:
    """Где какая колонка и заголовок ли первая строка. Домен в первой ячейке — данные,
    колонки по порядку шаблона; иначе заголовок, и домен с ценой в нём обязательны."""
    names = [" ".join(cell.strip().lower().rstrip(":").split()) for cell in head]
    found: dict[Column, int] = {}
    for column, known in NAMES.items():
        index = next((names.index(name) for name in known if name in names), None)
        if index is not None:
            found[column] = index
    if Column.HOST not in found and head and host_from_cell(head[0]):
        return {column: index for index, column in enumerate(ORDER)}, False
    if Column.HOST not in found:
        raise BulkFileError(
            "не нашлось колонки с доменом: назовите её «домен» — как в шаблоне. "
            f"В первой строке: {', '.join(cell for cell in head if cell.strip()) or 'пусто'}"
        )
    if Column.PRICE not in found:
        raise BulkFileError("не нашлось колонки с ценой: назовите её «цена» — как в шаблоне")
    return found, True


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""
