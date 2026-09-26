"""Выгрузка доноров в CSV.

**Колонки те же, что на экране, и в том же порядке.** Файл — это
не другой отчёт, а тот же, вынесенный из браузера: разойдись они,
и первый же вопрос будет «почему в файле другое».

**Разделитель — точка с запятой, кодировка — UTF-8 с меткой.** Обе
уступки Excel: с запятой он раскладывает русские строки в одну
колонку, без метки — показывает кракозябры. Файл открывают в Excel,
а не в редакторе, и спорить с этим дороже, чем уступить.

**Слова — те же, что на экране.** До 25.09.2026 вердикт, исход поиска
и регион уходили в файл кодами (`unsuitable`, `found`, `us`), а доля
региона — сырым числом `0.19888…`. Экран называет их «не подходит»,
«адрес найден», «США» и «20%», и файл рядом с ним читался бы как
выгрузка из другой системы. Слова берутся там же, где их берёт причина
отсева (`donors/wording.py`).

**Потолок строк — один, и у отмеченных тоже** (26.09.2026). Файл —
не больше `EXPORT_LIMIT` строк: выгрузка всей базы одним ответом однажды
положит сервер ровно в тот момент, когда его попросят об отчёте. Номеров
отмеченных — не больше столько же, и отказ говорит это словами, а не
файлом, в котором молча не хватает строк.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from typing import Any

from backend.features.donors.browse import DonorRow
from backend.features.donors.wording import (
    CONTACT_STATUS_TITLES,
    DONOR_STATUS_TITLES,
    NOT_SEARCHED,
    country_title,
    reject_reason_text,
    share_text,
)

#: Потолок строк файла и числа отмеченных номеров. Экран получает его
#: с сервера (`DonorsPage.export_limit`) и не обещает больше.
EXPORT_LIMIT = 10_000


class PickRefusedError(ValueError):
    """Отмеченных не выгрузить: ни одного или больше потолка. Текст говорит почему."""


def _spaced(number: int) -> str:
    """Число с разрядами, как на экране: «10 000»."""
    return f"{number:,}".replace(",", "\u00a0")


def checked_picks(ids: Sequence[int]) -> list[int]:
    """Номера отмеченных — или отказ словами."""
    unique = list(dict.fromkeys(ids))
    if not unique:
        raise PickRefusedError("Не отмечено ни одного донора — выгружать нечего.")
    if len(unique) > EXPORT_LIMIT:
        raise PickRefusedError(
            f"Отмечено {_spaced(len(unique))} — в файл за раз идёт не больше "
            f"{_spaced(EXPORT_LIMIT)}. Снимите часть отметок или выгрузите найденных фильтром."
        )
    return unique


#: Заголовок и поле строки таблицы. Порядок — как на экране.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("домен", "host"),
    ("вердикт", "status"),
    ("причина отсева", "reject_reason"),
    ("DR", "dr"),
    ("трафик", "org_traffic"),
    ("регион", "geo"),
    ("доля региона", "geo_top_share"),
    ("адресов", "contacts"),
    ("исход поиска", "contact_status"),
    ("цена", "last_price"),
    ("валюта", "last_price_currency"),
    ("метрики от", "metrics_refreshed_at"),
)


#: Поля, которые экран показывает словами, а не как хранятся. Остальные
#: уходят как есть: числа, цена, даты.
WORDS: dict[str, Callable[[Any], str | None]] = {
    "status": lambda status: DONOR_STATUS_TITLES[status],
    "reject_reason": reject_reason_text,
    "geo": country_title,
    "geo_top_share": share_text,
    # Пусто — не «пусто», а «не искали»: «не нашли» и «не искали» решаются
    # по-разному, и на экране у них разные слова.
    "contact_status": lambda status: (
        NOT_SEARCHED if status is None else CONTACT_STATUS_TITLES[status]
    ),
}


def to_csv(rows: Sequence[DonorRow]) -> bytes:
    """Строки таблицы доноров одним файлом."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([title for title, _ in COLUMNS])
    for row in rows:
        writer.writerow([_cell(_said(field, _value(row, field))) for _, field in COLUMNS])
    # Метка порядка байтов — ради Excel, см. модуль.
    return buffer.getvalue().encode("utf-8-sig")


def _said(field: str, value: Any) -> Any:
    """Значение поля словами экрана, если экран говорит о нём словами."""
    words = WORDS.get(field)
    return value if words is None else words(value)


def _value(row: DonorRow, field: str) -> Any:
    """Поле строки: домен и число адресов — у самой строки, остальное — у донора.

    До 25.09.2026 значение бралось только у строки — с умолчанием «пусто»,
    а у неё из двенадцати колонок есть две. Файл выходил с доменами и
    пустыми вердиктами, причинами, DR и трафиком; тесты смотрели только на
    заголовок и на домены, а с экрана выгрузка не скачивалась вовсе, и
    содержимое файла никто не видел. Умолчания здесь больше нет: поле,
    которого нет ни у строки, ни у донора, — ошибка, а не пустая ячейка.
    """
    if hasattr(row, field):
        return getattr(row, field)
    return getattr(row.donor, field)


def _cell(value: Any) -> str:
    """Пусто — пустая ячейка, а не «None»: файл читает человек."""
    if value is None:
        return ""
    if hasattr(value, "value"):  # перечисления отдаём их значением
        return str(value.value)
    if hasattr(value, "date"):  # даты — без часов, они здесь не нужны
        return str(value.date())
    return str(value)
