"""Приём базы лидов: файл → записи с номерами строк файла.

**Читает общий `contacts.sweep_input.read_records`** — второго читателя CSV нет:
кодировка (cp1251 из Excel — отказ словами), BOM, разделитель и номера строк файла
проверяются в одном месте для всех направлений.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from backend.features.contacts.sweep_input import read_records


class IntakeError(ValueError):
    """Источник не читается целиком или сопоставление не годится. Текст — что делать."""


@dataclass(frozen=True, slots=True)
class Table:
    """Непустые записи источника с номерами строк файла."""

    source: str
    records: list[tuple[int, list[str]]]


def read_file(path: Path, *, delimiter: str | None = None) -> Table:
    """Таблица с диска. Отказ целиком — `IntakeError` со словами общего читателя."""
    try:
        records = read_records(path, delimiter=delimiter)
    except OSError as exc:
        raise IntakeError(f"файл {path} не открылся: {exc.strerror or exc}") from exc
    except csv.Error as exc:
        raise IntakeError(f"файл {path.name} не читается как CSV: {exc}") from exc
    except ValueError as exc:  # не UTF-8, невозможный разделитель — уже словами
        raise IntakeError(str(exc)) from exc
    if not records:
        raise IntakeError(f"в файле {path.name} нет ни одной строки")
    return Table(path.name, records)
