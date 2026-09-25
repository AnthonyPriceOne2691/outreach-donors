"""Выгрузка доноров в CSV.

**Колонки те же, что на экране, и в том же порядке.** Файл — это
не другой отчёт, а тот же, вынесенный из браузера: разойдись они,
и первый же вопрос будет «почему в файле другое».

**Разделитель — точка с запятой, кодировка — UTF-8 с меткой.** Обе
уступки Excel: с запятой он раскладывает русские строки в одну
колонку, без метки — показывает кракозябры. Файл открывают в Excel,
а не в редакторе, и спорить с этим дороже, чем уступить.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from typing import Any

from backend.features.donors.browse import DonorRow

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


def to_csv(rows: Sequence[DonorRow]) -> bytes:
    """Строки таблицы доноров одним файлом."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([title for title, _ in COLUMNS])
    for row in rows:
        writer.writerow([_cell(_value(row, field)) for _, field in COLUMNS])
    # Метка порядка байтов — ради Excel, см. модуль.
    return buffer.getvalue().encode("utf-8-sig")


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
