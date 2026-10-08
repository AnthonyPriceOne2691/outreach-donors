"""Вкладка «Воронка» раздела продаж для замера контраста (срез 5.4).

Своим модулем: каталог продаж (`ui_screens_sales.py`) стоит у предела длины файла. Нормы
передаются параметрами — как у каталога продаж, обратный импорт замкнул бы круг.

Три экрана. Все гипотезы за всё время — плитки шагов с долями, слова правил, таблица по
гипотезам с долями под числами (1440 и 390: на телефоне плитки в две колонки, таблица
уезжает в прокрутку). Свои даты в обратном порядке — поля дат и отказ под полем розой:
подготовка выбирает «Свои даты» и вписывает дни. Мерится то, по чему решают: числа, доли
и их основа словами. Лиды, письма и ответы — в базе стенда.
"""

from typing import Any

#: Узкое окно — телефон: плитки в две колонки, таблица в прокрутке.
PHONE = {"width": 390, "height": 844}


def reversed_dates(page: Any) -> None:
    """Свои даты: первый день позже последнего — под полем отказ словами."""
    # Радиокнопка сегмента спрятана под подписью — нажимается подпись, как человеком.
    page.locator(".funnelPeriod label", has_text="Свои даты").click()
    page.get_by_label("Первый день").fill("2026-10-09")
    page.get_by_label("Последний день").fill("2026-10-01")
    page.get_by_text("Первый день позже последнего").wait_for()


def funnel_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка воронки: всё время (1440 и 390) и свои даты с отказом (1440)."""
    ready = ("heading", "Продажи")
    path = "/sales?tab=funnel"
    tiles = ".funnelTiles .metricTile"
    probes = [
        ("пояснение над воронкой", ".funnelIntro", norm),
        ("подпись поля гипотезы", "label:text-is('Гипотеза')", norm),
        ("подпись периода", ".funnelPeriod > span", norm),
        (
            "выбранный период",
            ".funnelPeriod .mantine-SegmentedControl-label[data-active] "
            ".mantine-SegmentedControl-innerLabel",
            norm,
        ),
        (
            "невыбранный период",
            ".funnelPeriod .mantine-SegmentedControl-label:not([data-active]) "
            ".mantine-SegmentedControl-innerLabel",
            norm,
        ),
        ("подпись плитки", f"{tiles} p:nth-child(1)", norm),
        ("число в плитке", f"{tiles} p:nth-child(2)", big),
        ("доля в плитке", f"{tiles} p:nth-child(3)", norm),
        ("правила шагов", ".funnelRules", norm),
        ("пояснение таблицы", ".funnelTableNote", norm),
        ("шапка таблицы", ".funnelTable thead th:text-is('Отправлено')", norm),
        ("имя гипотезы", ".funnelTable tbody td.cellName p", norm),
        ("число в таблице", ".funnelTable tbody td:nth-child(3) p", norm),
        ("доля в таблице", ".funnelTable tbody .funnelShare", norm),
    ]
    return {
        "sales-funnel": {"path": path, "ready": ready, "probes": probes},
        "sales-funnel-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            # Период на телефоне — столбиком: меряется и он, а не только плитки.
            "probes": [probes[0], *probes[3:9]],
        },
        "sales-funnel-dates": {
            "path": path,
            "ready": ready,
            "probes": [
                ("подпись поля даты", "label:text-is('Первый день')", norm),
                ("значение поля даты", "input[type='date']", norm),
                ("пояснение поля", ".mantine-InputWrapper-description", norm),
                ("отказ под полем", ".mantine-InputWrapper-error", norm),
            ],
        },
    }


def funnel_prepare() -> dict[str, Any]:
    """Что сделать до замера: свои даты появляются только после выбора."""
    return {"sales-funnel-dates": reversed_dates}
