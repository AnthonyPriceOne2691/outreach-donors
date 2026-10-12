"""Вкладка «Агент: качество» раздела продаж для замера контраста (срез 6.4).

Своим модулем: каталог продаж (`ui_screens_sales.py`) стоит у предела длины файла. Нормы
передаются параметрами — как у каталога продаж, обратный импорт замкнул бы круг.

Два экрана: 1440 и 390 (на телефоне плитки в две колонки, таблицы — в прокрутке, правка —
черновик над ушедшим). Мерится то, по чему решают: доли и их основа, итог ворот автоответа
и его слова, убранное и дописанное в правке — розой и мятой поверх тихого стекла. Черновики
с решениями — в базе стенда: экран читает только журнал, модель не зовётся. Как считается
качество — в «i» у вступления: оно меряется в открытой подсказке.
"""

from typing import Any

from ui_screens_sales_hints import hint_probe

#: Узкое окно — телефон: плитки в две колонки, таблицы в прокрутке.
PHONE = {"width": 390, "height": 844}


def quality_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка качества агента: 1440 и 390."""
    ready = ("heading", "Продажи")
    path = "/sales?tab=agent"
    tiles = ".qualityTiles .metricTile"
    change = ".qualityChange"
    probes = [
        ("пояснение над числами", ".qualityIntro", norm),
        ("подпись поля версии", "label:text-is('Версия агента')", norm),
        ("версия в поле", ".qualityPick input", norm),
        ("версии промптов и базы", ".qualityVersion", norm),
        ("подпись плитки", f"{tiles} p:nth-child(1)", norm),
        ("доля в плитке", f"{tiles} p:nth-child(2)", big),
        ("основа доли", f"{tiles} p:nth-child(3)", norm),
        ("пояснение таблицы", ".qualityTableNote", norm),
        ("шапка таблицы", ".qualitySituations thead th:text-is('Как есть')", norm),
        ("ситуация", ".qualitySituations tbody td.cellName p", norm),
        ("доля в таблице", ".qualitySituations tbody .qualityShare", norm),
        ("итог ворот", ".qualitySituations .qualityGate", norm),
        ("слова ворот", ".qualitySituations .qualityGateWhy", norm),
        ("пометка версии", ".qualityVersions .qualityMarks", norm),
        ("убранное в правке", f"{change} .qualityRemoved", norm),
        ("дописанное в правке", f"{change} .qualityAdded", norm),
        ("почему отдан человеку", f"{change} .qualityHeld", norm),
        ("кнопка «Показать ещё»", ".qualityMore", norm),
    ]
    hints = [hint_probe("как считается качество", "Как считается качество агента", norm)]
    return {
        "sales-quality": {"path": path, "ready": ready, "probes": [*probes, *hints]},
        "sales-quality-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": [*probes[:7], *probes[14:16], *hints],
        },
    }
