"""Вкладка «Сбор базы» раздела продаж для замера контраста (срез 6.1).

Своим модулем, как очередь и воронка: каталог продаж (`ui_screens_sales.py`) у предела длины.
Нормы передаются параметрами — обратный импорт замкнул бы круг.

Экран мерится со сметой: подготовка вписывает ключи и считает смету (`collect_prepare`), иначе
строк сметы и открытой кнопки «Запустить прогон» на экране нет. Отчёт — последнего прогона
гипотезы стенда: плитки, причины словами, строки «на проверку» с кнопками решения. Какой
прогон на стенде, решает его база (в нём должны быть сайты всех полос).
"""

from typing import Any

from playwright.sync_api import expect
from ui_screens_sales_hints import hint_probe

#: Узкое окно — телефон: плитки в две колонки, таблица «на проверку» — прокруткой.
PHONE = {"width": 390, "height": 844}

#: Ключи для сметы: один отсеивается гигиеной — видна и строка отсеянных.
KEYWORDS = "buy mountain bike online\nbike parts store\nsite:example.com bikes"


def collect_prepare(page: Any) -> None:
    """Вписать ключи и посчитать смету: без этого строк сметы на экране нет. Кнопка — с
    клавиатуры, а не мышью: после щелчка фокус на «i» уже не «видимый», и подсказка точки
    `hint_probe` не открылась бы (у Mantine подсказка по фокусу — только клавиатурному)."""
    page.get_by_role("textbox", name="Ключи").fill(KEYWORDS)
    page.get_by_role("button", name="Посчитать смету").focus()
    page.keyboard.press("Enter")
    expect(page.locator(".segmentEstimate p").first).to_be_visible()
    page.wait_for_timeout(400)


def segment_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка сбора: широкое окно и телефон, обе темы — у замера."""
    ready = ("heading", "Продажи")
    path = "/sales?tab=collect"
    probes = [
        ("вступление вкладки", ".salesTabs + div p", norm),
        ("заголовок блока", "h5:text-is('Сегмент гипотезы')", norm),
        ("описание сегмента", ".segmentText", norm),
        ("подпись поля", ".mantine-InputWrapper-label:text-is('Ключи')", norm),
        ("пояснение поля", ".mantine-InputWrapper-description", norm),
        ("строка сметы", ".segmentEstimate p", norm),
        ("кнопка «Посчитать смету»", "button:has-text('Посчитать смету')", big),
        ("кнопка «Запустить прогон»", "button:has-text('Запустить прогон')", big),
        ("этап прогона", ".mantine-Badge-label:text-is('готово')", norm),
        ("режим судьи", "p:has-text('судья в наблюдении')", norm),
        ("подпись плитки", ".metricTile p:nth-child(1)", norm),
        ("число в плитке", ".metricTile p:nth-child(2)", big),
        ("причина отсева", "ul[aria-label='Отсечены — почему'] li", norm),
        ("причина «на проверку»", ".segmentReview .segmentReason", norm),
        ("цитата сайта", ".segmentReview tbody p:has-text('«')", norm),
        ("кнопка «Принять»", ".segmentReview button:has-text('Принять')", big),
        ("кнопка «Отказ»", ".segmentReview button:has-text('Отказ')", big),
        hint_probe("как идёт прогон", "Как идёт прогон", norm),
    ]
    return {
        "sales-collect": {"path": path, "ready": ready, "probes": probes},
        "sales-collect-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": probes[1:13],
        },
    }


def segment_prepare() -> dict[str, Any]:
    """Подготовка экранов сбора до замера: смета посчитана."""
    return {"sales-collect": collect_prepare, "sales-collect-phone": collect_prepare}
