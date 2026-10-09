"""Слои раздела продаж для замера контраста: «i», «!» лида и список «Раздел» на телефоне.

С аудита экранов 09.10.2026 пояснения — в «i», слова очистки лида — за «!», вкладки на
узком окне — списком «Раздел»: текст живёт в слое поверх содержимого, и мерить его можно,
только открыв. Подсказка открывается фокусом, как с клавиатуры, «!» и список — нажатием.
Такие точки в каталоге стоят последними: открытый слой лёг бы поверх следующих точек
экрана; «!» и список — после подсказки, их слой держит фокус.

Своим модулем: каталог продаж (`ui_screens_sales.py`) стоит у предела длины файла.
"""

from collections.abc import Callable
from typing import Any

#: Подсказка «i» — плотное стекло в слое поверх страницы.
TOOLTIP = ".mantine-Tooltip-tooltip"


def open_hint(name: str) -> Callable[[Any], None]:
    """Шаг перед точкой: фокус на «i» с этим именем — подсказка открыта."""

    def show(page: Any) -> None:
        page.get_by_role("button", name=name).first.focus()
        page.locator(TOOLTIP).first.wait_for()
        page.wait_for_timeout(300)

    return show


def hint_probe(what: str, name: str, norm: float) -> tuple[str, str, float, Callable[[Any], None]]:
    """Точка замера текста подсказки «i»: имя точки, вырезка, норма и шаг, который её открывает."""
    return (f"подсказка «i»: {what}", TOOLTIP, norm, open_hint(name))


def open_note(page: Any) -> None:
    """Шаг перед точкой: «!» первого лида со словами очистки нажат — слова в поповере."""
    page.get_by_role("button", name="Что сказала очистка").first.click()
    page.locator(".mantine-Popover-dropdown .leadNote").first.wait_for()
    page.wait_for_timeout(300)


def note_probes(norm: float) -> list[tuple[Any, ...]]:
    """Заголовок и слова очистки в поповере «!» — на экране с отклонёнными лидами."""
    dropdown = ".mantine-Popover-dropdown"
    return [
        ("заголовок слов очистки", f"{dropdown} p:first-child", norm, open_note),
        ("слова очистки", f"{dropdown} .leadNote", norm),
    ]


#: Список «Раздел» вместо вкладок на телефоне (аудит экранов 09.10.2026): значение в поле.
SECTION_FIELD = ("раздел в поле", "input[aria-label='Раздел']")


def open_section(page: Any) -> None:
    """Шаг перед точкой: список «Раздел» открыт — пункты и черта «Настройки» на виду."""
    page.get_by_role("textbox", name="Раздел").click()
    page.locator(".mantine-Select-groupLabel").first.wait_for()
    page.wait_for_timeout(300)


def section_probes(norm: float) -> list[tuple[Any, ...]]:
    """Пункт открытого списка «Раздел» и подпись черты настроек — последними на экране."""
    return [
        ("пункт списка разделов", ".mantine-Select-option:not([data-combobox-selected])", norm,
         open_section),
        ("черта «Настройки»", ".mantine-Select-groupLabel", norm),
    ]  # fmt: skip
