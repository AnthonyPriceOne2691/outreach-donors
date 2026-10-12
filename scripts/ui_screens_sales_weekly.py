"""Вкладка «Отчёт» раздела продаж для замера контраста (срез 6.3).

Своим модулем: каталог продаж (`ui_screens_sales.py`) стоит у предела длины файла. Нормы
передаются параметрами — как у каталога продаж, обратный импорт замкнул бы круг.

Два окна. Прошлая неделя на 1440 — неделя и кнопка выгрузки, строка отправки в группу (на
стенде — не доставлен: плашка янтарём), плитки, разделы словами с подробностями списком и
таблица по гипотезам; на 390 — плитки в две колонки и разделы во всю ширину, таблица уезжает
в прокрутку. Отчёт, лиды и строка журнала отправки — в базе стенда. Как считается отчёт —
в «i» у вступления: меряется в открытой подсказке.
"""

from typing import Any

from ui_screens_sales_hints import hint_probe

#: Узкое окно — телефон: плитки в две колонки, разделы во всю ширину.
PHONE = {"width": 390, "height": 844}


def weekly_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка недельного отчёта: прошлая неделя (1440 и 390)."""
    ready = ("heading", "Продажи")
    path = "/sales?tab=report"
    tiles = ".weeklyTiles .metricTile"
    probes = [
        ("пояснение над отчётом", ".weeklyIntro", norm),
        ("подпись поля недели", "label:text-is('Неделя')", norm),
        ("кнопка «Выгрузить CSV»", "button:has-text('Выгрузить CSV')", big),
        ("строка отправки в группу", "p.weeklyDelivery, .weeklyDelivery p", norm),
        ("подпись плитки", f"{tiles} p:nth-child(1)", norm),
        ("число в плитке", f"{tiles} p:nth-child(2)", big),
        ("пояснение плитки", f"{tiles} p:nth-child(3)", norm),
        ("заголовок раздела", ".weeklySection > p:nth-child(1)", norm),
        ("итог раздела", ".weeklySection > p:nth-child(2)", norm),
        ("строка списка раздела", ".weeklySection li", norm),
        ("пояснение таблицы", ".weeklyTableNote", norm),
        ("шапка таблицы", ".weeklyTable thead th:text-is('Отправлено')", norm),
        ("имя гипотезы", ".weeklyTable tbody td.cellName p", norm),
        ("число в таблице", ".weeklyTable tbody td:nth-child(3)", norm),
    ]
    hints = [hint_probe("как считается отчёт", "Как считается отчёт", norm)]
    return {
        "sales-weekly": {"path": path, "ready": ready, "probes": [*probes, *hints]},
        "sales-weekly-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": [*probes[:10], *hints],
        },
    }
