"""Вкладка «Стоп-лист» раздела продаж и окно правки лида — для замера контраста.

Своим модулем: каталог продаж (`ui_screens_sales.py`) стоит у предела длины файла. Нормы
передаются параметрами — как у каталога продаж, обратный импорт замкнул бы круг.

Стоп-лист — свой список (кому, причина, кто и когда, «Снять») и общий, который держит продажи
(значок причины, без кнопок); окно «Добавить в стоп-лист продаж» открывается подготовкой.
Окно правки лида открывается значком у имени первого лида. Записи и лиды — в базе стенда:
без записей точки списка честно «не найдены».
"""

from collections.abc import Callable
from typing import Any

from playwright.sync_api import expect

#: Узкое окно — телефон: вместо вкладок список «Раздел», таблицы — в прокрутке.
PHONE = {"width": 390, "height": 844}
DIALOG = ".mantine-Modal-content"


def open_add(page: Any) -> None:
    """Окно пополнения стоп-листа: поля, пояснения, кнопки."""
    page.get_by_role("button", name="Добавить…").click()
    expect(page.locator(DIALOG)).to_be_visible()
    page.wait_for_timeout(400)


def open_lead(page: Any) -> None:
    """Окно правки первого лида — значком у его имени."""
    page.locator("button[aria-label^='Править лида']").first.click()
    expect(page.locator(DIALOG)).to_be_visible()
    page.wait_for_timeout(400)


def stoplist_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Стоп-лист на 1440 и 390, окно пополнения, окно правки лида."""
    ready = ("heading", "Продажи")
    path = "/sales?tab=stoplist"
    own = ".stoplistTable"
    listed = [
        ("кому продажи не пишут", "p:has-text('Кому продажи не пишут')", norm),
        ("число записей", "p:has-text('Записей —')", norm),
        ("кнопка «Добавить…»", "button:has-text('Добавить…')", big),
        ("подпись колонки", f"{own} thead th:text-is('Кто внёс')", norm),
        ("кому не пишем", f"{own} tbody td:nth-child(1)", norm),
        ("причина", f"{own} tbody td:nth-child(2) p", norm),
        ("кто внёс", f"{own} tbody td:nth-child(3)", norm),
        ("когда", f"{own} tbody td:nth-child(4)", norm),
        ("кнопка «Снять»", f"{own} tbody button:has-text('Снять')", norm),
        ("общий список — подпись", "p:has-text('Общий стоп-лист тоже держит')", norm),
        ("общий список — значок", ".stoplistGeneral tbody .mantine-Badge-label", norm),
        ("к общему стоп-листу", "a:has-text('К общему стоп-листу')", norm),
    ]
    return {
        "sales-stoplist": {"path": path, "ready": ready, "probes": listed},
        "sales-stoplist-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": [listed[0], listed[2], listed[4], listed[9], listed[10]],
        },
        "sales-stoplist-add": {
            "path": path,
            "ready": ready,
            "probes": [
                ("заголовок окна", f"{DIALOG} .mantine-Modal-title", norm),
                ("подпись поля", f"{DIALOG} .mantine-InputWrapper-label", norm),
                ("пояснение поля", f"{DIALOG} .mantine-InputWrapper-description", norm),
                ("что станет с лидами", f"{DIALOG} p:has-text('Лиды, которых закроют')", norm),
                ("кнопка «Отмена»", f"{DIALOG} button:has-text('Отмена')", big),
            ],
        },
        "sales-lead-edit": {
            "path": "/sales",
            "ready": ready,
            "probes": [
                ("заголовок окна", f"{DIALOG} .mantine-Modal-title", norm),
                ("подпись поля", f"{DIALOG} .mantine-InputWrapper-label", norm),
                ("пояснение поля", f"{DIALOG} .mantine-InputWrapper-description", norm),
                ("кнопка «Отмена»", f"{DIALOG} button:has-text('Отмена')", big),
            ],
        },
    }


def stoplist_prepare() -> dict[str, Callable[[Any], None]]:
    """Что открыть до замера: окна открываются нажатием."""
    return {"sales-stoplist-add": open_add, "sales-lead-edit": open_lead}
