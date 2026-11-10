"""Вкладка «Отчёт» раздела продаж для замера контраста (срез 6.3).

Своим модулем: каталог продаж (`ui_screens_sales.py`) стоит у предела длины файла. Нормы
передаются параметрами — как у каталога продаж, обратный импорт замкнул бы круг.

Два окна. Прошлая неделя на 1440 — неделя и кнопка выгрузки, строка отправки в группу (на
стенде — не доставлен: плашка янтарём), плитки, таблица по гипотезам, кнопка «Текст отчёта для
группы продаж» и разделы словами с подробностями списком; на 390 — плитки в две колонки и разделы
во всю ширину, таблица уезжает в прокрутку. Отчёт, лиды и строка журнала отправки — в базе стенда.
Как считается отчёт — в «i» у вступления: меряется в открытой подсказке.

С замечания владельца 10.11.2026 разделы свёрнуты под таблицей: их точки идут после шага, который
нажимает кнопку текста (`open_text`). Плашка «Ждут действия» и «показать N» у сложенной строки
проблем не меряются: есть ли они, решает база стенда на день замера (повод для беспокойства, две
снятые паузы ящиков). Плашка — тот же янтарный `Alert` со строкой `sm`, что строка отправки,
ссылка — чернила `inkLink`; обе меряются там.
"""

from typing import Any

from ui_screens_sales_hints import hint_probe

#: Узкое окно — телефон: плитки в две колонки, разделы во всю ширину.
PHONE = {"width": 390, "height": 844}
#: Кнопка, под которой с 10.11.2026 свёрнуты разделы словами.
TEXT = "Текст отчёта для группы продаж"


def open_text(page: Any) -> None:
    """Шаг перед точкой: текст отчёта раскрыт — разделы словами на виду. С клавиатуры, как причины
    отсева на «Сборе базы» (`ui_screens_sales_segment.open_why`): после щелчка мышью фокус на «i»
    следующей точки не «видимый». Замер перезагружает экран перед каждой темой — текст снова
    свёрнут, и шаг идёт заново."""
    page.get_by_role("button", name=TEXT).focus()
    page.keyboard.press("Enter")
    page.locator(".weeklySection").first.wait_for()
    page.wait_for_timeout(500)


def _sections(norm: float) -> list[tuple[Any, ...]]:
    """Кнопка текста и разделы — раскрытыми: первая точка раздела раскрывает их своим шагом."""
    return [
        ("кнопка текста отчёта", "button.weeklyTextToggle", norm),
        ("заголовок раздела", ".weeklySection > p:nth-child(1)", norm, open_text),
        ("итог раздела", ".weeklySection > p:nth-child(2)", norm),
        ("строка списка раздела", ".weeklySection li", norm),
    ]


def weekly_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка недельного отчёта: прошлая неделя (1440 и 390)."""
    ready = ("heading", "Продажи")
    path = "/sales?tab=report"
    tiles = ".weeklyTiles .metricTile"
    top = [
        ("пояснение над отчётом", ".weeklyIntro", norm),
        ("подпись поля недели", "label:text-is('Неделя')", norm),
        ("кнопка «Выгрузить CSV»", "button:has-text('Выгрузить CSV')", big),
        ("строка отправки в группу", "p.weeklyDelivery, .weeklyDelivery p", norm),
        ("подпись плитки", f"{tiles} p:nth-child(1)", norm),
        ("число в плитке", f"{tiles} p:nth-child(2)", big),
        ("пояснение плитки", f"{tiles} p:nth-child(3)", norm),
    ]
    table = [
        ("пояснение таблицы", ".weeklyTableNote", norm),
        ("шапка таблицы", ".weeklyTable thead th:text-is('Отправлено')", norm),
        ("имя гипотезы", ".weeklyTable tbody td.cellName p", norm),
        ("число в таблице", ".weeklyTable tbody td:nth-child(3)", norm),
    ]
    sections = _sections(norm)
    hints = [hint_probe("как считается отчёт", "Как считается отчёт", norm)]
    return {
        "sales-weekly": {
            "path": path,
            "ready": ready,
            "probes": [*top, *table, *sections, *hints],
        },
        # На телефоне — без таблицы: она уезжает в прокрутку, её точки — на 1440.
        "sales-weekly-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": [*top, *sections, *hints],
        },
    }
