"""Коды базы знаний и отправителя — одни у сервера и у экрана (срез 3.1).

Экран называет вид записи и поле отправителя словами по коду. Новый вид на сервере
без слова на экране показался бы «другой вид (code)», а поле отправителя, которого
экран не знает, не попало бы в форму вовсе — и сохранение стирало бы его в `null`.
Сверка читает файлы экрана как текст: тест живёт в коммите экрана, а не сервера.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.features.sales import sender
from backend.features.sales.models import KbKind

ROOT = Path(__file__).resolve().parent.parent
TYPES = (ROOT / "frontend/src/api/salesTypes.ts").read_text(encoding="utf-8")
LABELS = (ROOT / "frontend/src/api/salesLabels.ts").read_text(encoding="utf-8")


def _union(name: str) -> set[str]:
    """Коды строкового объединения `export type <name> = | 'a' | 'b';`."""
    found = re.search(rf"export type {name} =([^;]+);", TYPES)
    assert found is not None, f"в salesTypes.ts нет типа {name}"
    return set(re.findall(r"'([a-z_]+)'", found.group(1)))


def _keys(name: str) -> set[str]:
    """Ключи таблицы подписей `export const <name>… = { key: … };` верхнего уровня."""
    found = re.search(rf"export const {name}\b.*?= \{{\n(.*?)\n\}};", LABELS, re.DOTALL)
    assert found is not None, f"в salesLabels.ts нет таблицы {name}"
    return set(re.findall(r"^  ([a-z_]+):", found.group(1), re.MULTILINE))


def test_kinds_of_the_server_are_the_kinds_of_the_screen_and_each_has_words() -> None:
    kinds = {kind.value for kind in KbKind}

    assert _union("KbKind") == kinds
    assert _keys("KB_KINDS") == kinds


def test_sender_fields_of_the_server_are_the_fields_of_the_screen_form() -> None:
    fields = set(sender.FIELDS)

    assert _union("SenderField") == fields
    assert _keys("SENDER_FIELDS") == fields
