"""Экраны доноров для замера контраста: список, пустой список, карточка.

Часть каталога `ui_screens.py`, вынесенная своим модулем, как экраны писем
(`ui_screens_mail.py`): основной каталог упёрся в предел длины файла
(540 строк после экрана отбора и новых точек доноров 26.09.2026), а правило
«новый элемент меряется в обеих темах» от этого не отменяется. Экраны одного
потока — один модуль.

Нормы передаются параметрами, а не импортируются из `ui_screens`: тот
подключает этот модуль, и обратный импорт замкнул бы круг.
"""

from collections.abc import Callable
from typing import Any

from playwright.sync_api import expect


def donor_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Экраны и точки. `norm` — норма обычного текста, `big` — крупного."""
    return {
        # Доноры — одна панель: шапка, таблица с фильтрами под заголовками
        # колонок, страницы (замечание 25.09.2026). Мерится то, по чему выбирают
        # донора, — домен, вердикт, адрес — и сами фильтры: их значение в поле
        # и подсказка пустого (подсказку сверяют со значением рядом, а не с
        # нормой, — см. шапку `ui_contrast.py`). «Ждут адреса» отрисовывается,
        # только когда ждущие есть: на базе без принятых доноров точка честно
        # «не измерена». С 26.09.2026 первой колонкой стоит отметка (номера
        # колонок сдвинулись на одну), под каждой колонкой — фильтр, а в шапке —
        # «отмечено: N» (подготовка отмечает первую строку).
        "donors": {
            "path": "/donors",
            "ready": ("heading", "Доноры"),
            "probes": [
                ("заголовок экрана", "h3:text-is('Доноры')", big),
                ("сколько найдено", ".donorsFound", norm),
                ("кнопка выгрузки", "button:has-text('Выгрузить')", big),
                ("«отмечено»", ".pickedLine p", norm),
                ("«снять отметку»", "button:has-text('снять отметку')", big),
                ("«ждут адреса»", ".pendingContacts p", norm),
                ("подпись колонки", ".donorsTable thead th:text-is('Трафик')", norm),
                ("значение фильтра вердикта", "input[aria-label='Вердикт']", norm),
                ("значение фильтра гео", "input[aria-label='Гео']", norm),
                ("значение фильтра адресов", "input[aria-label='Адреса']", norm),
                ("значение фильтра данных", "input[aria-label='Данные']", norm),
                (
                    "подсказка поиска",
                    "input[aria-label='Поиск по домену или причине отсева']",
                    norm,
                ),
                ("подсказка «не ниже» у DR", "input[aria-label='DR не ниже']", norm),
                ("подсказка «не ниже» у трафика", "input[aria-label='Трафик не ниже']", norm),
                ("флажок строки", ".donorsTable tbody .mantine-Checkbox-input", big),
                ("домен в строке", ".donorsTable tbody a.donorHost", norm),
                ("причина отсева", ".donorsTable tbody td:nth-child(2) p", norm),
                (
                    "значок вердикта",
                    ".donorsTable tbody td:nth-child(3) .mantine-Badge-label",
                    norm,
                ),
                ("число в «Адресах»", ".donorsTable .addressCount", norm),
                ("значок исхода поиска", ".donorsTable .addressCell .mantine-Badge-label", norm),
                (
                    "значок свежести",
                    ".donorsTable tbody td:nth-child(8) .mantine-Badge-label",
                    norm,
                ),
                (
                    "номер другой страницы",
                    "nav[aria-label='Страницы доноров'] button:not([aria-current]) "
                    "[data-page-number]:text-is('2')",
                    norm,
                ),
                (
                    "номер текущей страницы",
                    "nav[aria-label='Страницы доноров'] button[aria-current='page'] [data-page-number]",
                    norm,
                ),
                ("пункт меню", "nav a", norm),
            ],
        },
        # Доноров нет вовсе (решение 26.09.2026: донор — принятый человеком):
        # путь к рассмотрению прогона и сколько ждёт. Мерится на базе без
        # принятых доноров — на другой базе точки честно «не измерены».
        "donors-empty": {
            "path": "/donors",
            "ready": ("heading", "Доноры"),
            "probes": [
                ("«Доноров пока нет.»", "p:text-is('Доноров пока нет.')", norm),
                ("почему и куда", "p:has-text('когда его принимает человек')", norm),
                ("кнопка «Рассмотреть»", "a:has-text('Рассмотреть')", big),
            ],
        },
        # Карточка донора с найденным адресом: раздел «Адреса» — что есть,
        # откуда, когда искали. Номер донора зависит от базы, поэтому карточка
        # открывается из списка — первой строкой с найденным адресом.
        "donor": {
            "path": "/donors",
            "ready": ("heading", "Доноры"),
            "open_row_with": "адрес найден",
            "probes": [
                ("домен карточки", "h3", big),
                ("значок вердикта", ".glassPanel .mantine-Badge-label", norm),
                ("«К списку»", "a.backLink", norm),
                ("заголовок «Адреса»", "h5:text-is('Адреса')", norm),
                (
                    "исход поиска значком",
                    ".mantine-Card-root:has(h5:text-is('Адреса')) .mantine-Badge-label",
                    norm,
                ),
                ("когда искали", "p:has-text('Искали')", norm),
                # Адрес и ступень — по своим элементам, а не по ячейкам: ячейка во
                # всю ширину колонки над розовым пятном, и Оцу делил фон с его
                # переливом — 2,3 : 1 тёмному тексту на светлом (25.09.2026).
                ("адрес в строке", ".donorAddresses .donorEmail", norm),
                ("откуда адрес", ".donorAddresses .donorSource", norm),
                # С 26.09.2026: кто это — донор или кандидат; почему письмо не
                # соберётся; удалить адрес; вписать адрес руками (подготовка
                # вписывает адрес в поле, не нажимая: у пустого поля кнопка
                # выключена, а у выключенной меряется серое на сером).
                ("кандидат, а не донор", ".donorStanding", norm),
                ("отметка «письмо уйдёт сюда»", ".letterMark .mantine-Badge-label", norm),
                ("значок «удалить адрес»", "button[aria-label^='Удалить'] svg", big),
                ("адрес в поле", "input[aria-label='Новый адрес почты']", norm),
                ("кнопка «Добавить адрес»", "button:has-text('Добавить адрес')", big),
                ("пункт меню", "nav a", norm),
            ],
        },
        # Та же карточка без адреса: почему поиск не ставится — или кнопка поиска.
        # Кнопка есть только у принятого человеком донора без свежей попытки: на
        # базе без таких доноров точка честно «не измерена», а отказ — измерен.
        "donor-search": {
            "path": "/donors",
            "ready": ("heading", "Доноры"),
            "open_row_with": "не искали",
            "probes": [
                ("заголовок «Адреса»", "h5:text-is('Адреса')", norm),
                (
                    "значок «не искали»",
                    ".mantine-Card-root:has(h5:text-is('Адреса')) .mantine-Badge-label",
                    norm,
                ),
                ("«ещё не искали»", "p:has-text('ещё не искали')", norm),
                ("почему поиск не ставится", "p:has-text('Адрес ищут')", norm),
                ("кнопка «Найти адрес»", "button:has-text('Найти адрес')", big),
            ],
        },
    }


def pick_first_donor(page: Any) -> None:
    """Отметить первую строку — появляется «отмечено: 1 · снять отметку»."""
    page.locator(".donorsTable tbody .mantine-Checkbox-input").first.check()
    expect(page.locator(".pickedLine")).to_be_visible()
    page.wait_for_timeout(300)


def type_an_address(page: Any) -> None:
    """Вписать адрес в поле, не нажимая: кнопка оживает, а у пустого поля
    она выключена и не меряется."""
    page.get_by_label("Новый адрес почты").fill("editor@probe.example.test")
    expect(page.get_by_role("button", name="Добавить адрес")).to_be_enabled()
    page.wait_for_timeout(300)


def donor_prepare() -> dict[str, Callable[[Any], None]]:
    """Что сделать на экране до замера — там, где без этого мерить нечего."""
    return {
        "donors": pick_first_donor,
        "donor": type_an_address,
    }
