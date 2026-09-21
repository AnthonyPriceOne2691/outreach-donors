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

#: Заголовок и как достать значение. Порядок — как на экране.
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


def to_csv(rows: Sequence[Any]) -> bytes:
    """Строки таблицы доноров одним файлом."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([title for title, _ in COLUMNS])
    for row in rows:
        writer.writerow([_cell(getattr(row, field, None)) for _, field in COLUMNS])
    # Метка порядка байтов — ради Excel, см. модуль.
    return buffer.getvalue().encode("utf-8-sig")


def _cell(value: Any) -> str:
    """Пусто — пустая ячейка, а не «None»: файл читает человек."""
    if value is None:
        return ""
    if hasattr(value, "value"):  # перечисления отдаём их значением
        return str(value.value)
    if hasattr(value, "date"):  # даты — без часов, они здесь не нужны
        return str(value.date())
    return str(value)
