"""Коды цепочки писем — одни у сервера и у экрана (срез 4.6, часть 1).

Экран называет шаг, язык, вид зоны и подстановку словами по коду. Новый язык или
подстановка на сервере без слова на экране показались бы голым кодом, а шаг с другим
названием — вторым смыслом одного слова. Сверка читает файлы экрана как текст: тест
живёт в коммите экрана, а не сервера.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.features.letters.template import ZoneKind
from backend.features.sales import chain_text

ROOT = Path(__file__).resolve().parent.parent
TYPES = (ROOT / "frontend/src/api/salesTypes.ts").read_text(encoding="utf-8")
LABELS = (ROOT / "frontend/src/api/salesLabels.ts").read_text(encoding="utf-8")


def _union(name: str) -> set[str]:
    """Коды строкового объединения `export type <name> = | 'a' | 'b';`."""
    found = re.search(rf"export type {name} =([^;]+);", TYPES)
    assert found is not None, f"в salesTypes.ts нет типа {name}"
    return set(re.findall(r"'([a-z_]+)'", found.group(1)))


def _table(name: str) -> str:
    found = re.search(rf"export const {name}\b.*?= \{{\n(.*?)\n\}};", LABELS, re.DOTALL)
    assert found is not None, f"в salesLabels.ts нет таблицы {name}"
    return found.group(1)


def _keys(name: str) -> set[str]:
    """Ключи таблицы подписей верхнего уровня."""
    return set(re.findall(r"^  ([a-z_]+):", _table(name), re.MULTILINE))


def test_languages_of_the_chain_are_the_languages_of_the_screen() -> None:
    languages = set(chain_text.LANGUAGES)

    assert _union("ChainLanguage") == languages
    assert _keys("CHAIN_LANGUAGES") == languages


def test_placeholders_of_the_server_each_have_words_on_the_screen() -> None:
    placeholders = set(chain_text.PLACEHOLDERS)

    assert _union("ChainPlaceholder") == placeholders
    assert _keys("CHAIN_PLACEHOLDERS") == placeholders


def test_zone_kinds_of_the_template_each_have_words_on_the_screen() -> None:
    kinds = {kind.value for kind in ZoneKind}

    assert _union("ZoneKind") == kinds
    assert _keys("ZONE_KINDS") == kinds


def test_steps_are_named_the_same_on_the_server_and_on_the_screen() -> None:
    titles = dict(re.findall(r"^  (\d+): \{ title: '([^']+)'", _table("CHAIN_STEPS"), re.MULTILINE))

    assert {int(step): title.lower() for step, title in titles.items()} == chain_text.STEP_TITLES
