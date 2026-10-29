"""Таблица доноров с ценами — чтение CSV и XLSX (`donors/bulk_file.py`).

Проверяется то, ради чего модуль: колонки находятся по заголовку и без него — по порядку
шаблона; номер — строки файла; кривая ячейка — строка, а не отказ файла; файл целиком
отвергается только словами, называющими, что не совпало, и только если не прочитать ничего;
битый лист Excel — отказ словами, а не ответ 500; лист читается значениями, как их видит
человек; шаблон читается сам собой; экран и прокси знают те же пределы.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable
from pathlib import Path

import openpyxl
import pytest
from backend.features.donors import bulk_file
from backend.features.donors.bulk_file import (
    MAX_ROWS,
    BulkFileError,
    Column,
    read_table,
    template_csv,
)

ROOT = Path(__file__).resolve().parent.parent
PROXIES = (ROOT / "deploy" / "nginx.conf", ROOT / "deploy" / "proxy" / "outreach.conf")
SCREEN = ROOT / "frontend" / "src" / "advertisers" / "bulkFile.ts"
BODY_LIMIT = re.compile(r"^\s*client_max_body_size\s+(\d+)m\s*;", re.M)


def _xlsx(rows: list[list[object]], *, hidden_first: bool = False) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    if hidden_first:
        sheet.title = "скрыт"
        sheet.append(["домен", "цена"])
        sheet.append(["hidden.example.com", 1])
        sheet.sheet_state = "hidden"
        sheet = book.create_sheet("доноры")
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _rewritten(data: bytes, part: str, change: Callable[[bytes], bytes]) -> bytes:
    """Та же книга Excel, у которой часть `part` переписана — так ломается лист."""
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            body = source.read(item.filename)
            target.writestr(item, change(body) if item.filename == part else body)
    return out.getvalue()


def test_columns_by_header_in_any_order_and_case() -> None:
    data = "Валюта;Откуда цена;ДОМЕН;Цена\nEUR;прайс;https://www.example.com/path;120\n".encode()

    [line] = read_table(data, "list.csv")

    assert line.number == 2
    assert (
        line.get(Column.HOST),
        line.get(Column.PRICE),
        line.get(Column.CURRENCY),
        line.get(Column.NOTE),
    ) == ("https://www.example.com/path", "120", "EUR", "прайс")


def test_without_header_columns_go_in_the_template_order() -> None:
    data = "example.com,150,USD,биржа\nexample.org,90\n".encode()

    [first, second] = read_table(data, "x.csv")

    assert (first.number, first.get(Column.HOST), first.get(Column.NOTE)) == (
        1,
        "example.com",
        "биржа",
    )
    assert (second.get(Column.PRICE), second.get(Column.CURRENCY)) == ("90", "")


def test_grey_price_and_payment_columns_are_read_when_present() -> None:
    data = 'домен,цена,серая цена,оплата\nexample.com,150,120,"PayPal, Wise"\n'.encode()

    [line] = read_table(data, "x.csv")

    assert (line.get(Column.GREY), line.get(Column.PAYMENT)) == ("120", "PayPal, Wise")


def test_blank_rows_are_skipped_and_numbers_are_file_lines() -> None:
    data = "домен,цена\n\nexample.com,1\n,,\nexample.org,2\n".encode()

    assert [line.number for line in read_table(data, "x.csv")] == [3, 5]


def test_the_template_reads_back_as_one_example_row() -> None:
    [line] = read_table(template_csv().encode("utf-8"), "шаблон.csv")

    assert (line.get(Column.HOST), line.get(Column.PRICE), line.get(Column.CURRENCY)) == (
        "example.com",
        "150",
        "USD",
    )


def test_a_bom_from_excel_is_not_part_of_the_header() -> None:
    data = ("﻿" + template_csv()).encode("utf-8")

    assert [line.get(Column.HOST) for line in read_table(data, "x.csv")] == ["example.com"]


def test_xlsx_first_visible_sheet_values_as_written() -> None:
    data = _xlsx(
        [["домен", "цена", "валюта"], ["example.com", 150.0, "EUR"], ["example.org", 99.5, None]],
        hidden_first=True,
    )

    lines = read_table(data, "доноры.xlsx")

    assert [(line.number, line.get(Column.HOST), line.get(Column.PRICE)) for line in lines] == [
        (2, "example.com", "150"),
        (3, "example.org", "99.5"),
    ]
    assert lines[1].get(Column.CURRENCY) == ""


def test_xlsx_is_recognised_by_content_without_the_suffix() -> None:
    data = _xlsx([["домен", "цена"], ["example.com", 1]])

    assert [line.get(Column.HOST) for line in read_table(data, "upload")] == ["example.com"]


@pytest.mark.parametrize(
    ("data", "name", "said"),
    [
        (b"", "x.csv", "файл «x.csv» пуст — в нём нет ни одной строки"),
        (b"\n\n", "x.csv", "в файле «x.csv» нет ни одной строки"),
        ("домен,цена\n".encode(), "x.csv", "только заголовок"),
        ("сайт;сумма\nexample.com;1\n".encode(), "x.csv", "нет колонки «цена» — назовите"),
        ("имя,цена\nАнна,1\n".encode(), "x.csv", "нет колонки «домен» — назовите"),
        ("домен,цена\nпример.рф,1\n".encode("cp1251"), "x.csv", "не в UTF-8"),
        (b"\xd0\xcf\x11\xe0", "old.xls", "старый формат Excel"),
        (b"PK\x03\x04broken", "x.xlsx", "не читается как таблица Excel"),
        (b"%PDF-1.4", "list.pdf", r"«list\.pdf» — не таблица CSV или Excel \(\.xlsx\)"),
    ],
)
def test_a_file_is_refused_whole_only_in_words(data: bytes, name: str, said: str) -> None:
    with pytest.raises(BulkFileError, match=said):
        read_table(data, name)


def test_every_missing_column_is_named_at_once_with_what_the_first_row_says() -> None:
    with pytest.raises(BulkFileError) as refused:
        read_table("имя;сумма\nАнна;1\n".encode(), "x.csv")

    assert str(refused.value) == (
        "в первой строке нет колонок «домен» и «цена» — назовите колонки, как в шаблоне: "
        "домен, цена, валюта, откуда цена. Сейчас в первой строке: имя, сумма"
    )


def test_a_long_first_row_is_cut_in_the_words() -> None:
    with pytest.raises(BulkFileError) as refused:
        read_table(("имя," + "я" * 500 + "\nАнна,1\n").encode(), "x.csv")

    assert str(refused.value).endswith("…")
    assert len(str(refused.value)) < 400


def test_more_rows_than_the_ceiling_is_refused_naming_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bulk_file, "MAX_ROWS", 1_000)
    data = "домен,цена\n" + "".join(f"example.com,{i}\n" for i in range(1, 1_002))

    with pytest.raises(
        BulkFileError, match="строк с донорами больше 1\u00a0000 — столько берёт одна загрузка"
    ):
        read_table(data.encode(), "x.csv")


def test_xlsx_stops_reading_past_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bulk_file, "MAX_ROWS", 2)
    data = _xlsx([["домен", "цена"], *[["example.com", i] for i in range(1, 50)]])

    with pytest.raises(BulkFileError, match="больше 2 — столько берёт одна загрузка"):
        read_table(data, "x.xlsx")


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda body: body[: len(body) // 2], id="обрезанный XML листа"),
        pytest.param(lambda body: body.replace(b"<v>150</v>", b"<v>abc</v>"), id="текст в числе"),
    ],
)
def test_a_broken_sheet_is_refused_in_words_not_with_a_crash(
    change: Callable[[bytes], bytes],
) -> None:
    data = _rewritten(
        _xlsx([["домен", "цена"], ["example.com", 150]]), "xl/worksheets/sheet1.xml", change
    )

    with pytest.raises(BulkFileError, match=r"лист «Sheet» в файле «x\.xlsx» не читается"):
        read_table(data, "x.xlsx")


def test_an_empty_first_sheet_is_named() -> None:
    with pytest.raises(BulkFileError, match=r"лист «Sheet» в файле «x\.xlsx» пуст"):
        read_table(_xlsx([]), "x.xlsx")


def test_a_book_without_a_visible_sheet_says_so() -> None:
    data = _rewritten(
        _xlsx([["домен", "цена"], ["example.com", 1]]),
        "xl/workbook.xml",
        lambda body: body.replace(b'state="visible"', b'state="hidden"'),
    )

    with pytest.raises(BulkFileError, match="нет видимого листа"):
        read_table(data, "x.xlsx")


def test_a_file_larger_than_the_limit_is_refused_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bulk_file, "MAX_BYTES", 10)

    with pytest.raises(BulkFileError, match="больше 0 МБ — разделите список"):
        read_table(b"domain,price\nexample.com,1\n", "x.csv")


def test_the_file_name_in_words_is_its_last_part_only() -> None:
    with pytest.raises(BulkFileError, match=r"«list\.csv»"):
        read_table(b"", "../../secret/list.csv")


def test_the_ceiling_is_ten_thousand_rows() -> None:
    assert MAX_ROWS == 10_000


def test_the_screen_names_and_checks_the_same_limits() -> None:
    """Экран проверяет файл до отправки и называет пределы в подсказке — теми же числами."""
    text = SCREEN.read_text(encoding="utf-8")
    megabytes = re.search(r"export const FILE_LIMIT_MB = (\d+);", text)
    rows = re.search(r"export const ROW_LIMIT = ([\d_]+);", text)

    assert megabytes, "у экрана нет FILE_LIMIT_MB"
    assert rows, "у экрана нет ROW_LIMIT"
    assert int(megabytes.group(1)) * 2**20 == bulk_file.MAX_BYTES
    assert int(rows.group(1).replace("_", "")) == MAX_ROWS


@pytest.mark.parametrize("conf", PROXIES, ids=lambda conf: conf.name)
def test_the_proxy_takes_the_largest_file_with_the_form_around_it(conf: Path) -> None:
    """Больше предела тела прокси отбил бы файл своей страницей 413, мимо слов сервиса;
    мегабайт запаса — обёртке формы."""
    limits = [int(size) * 2**20 for size in BODY_LIMIT.findall(conf.read_text(encoding="utf-8"))]

    assert limits, f"в {conf.name} нет client_max_body_size"
    assert min(limits) >= bulk_file.MAX_BYTES + 2**20
