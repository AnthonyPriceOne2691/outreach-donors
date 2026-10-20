"""Вкладка «emails» раздела продаж для замера контраста: колонка «Дата добавления» и период.

Своим модулем: каталог продаж (`ui_screens_sales.py`) стоит у предела длины файла. Норма
передаётся параметром — как у каталога продаж, обратный импорт замкнул бы круг; крупного
текста здесь нет.

Период живёт в адресе (`added_from`, `added_to` — дни), поэтому экраны — путями, без подготовки:
период с 1 по 15 октября 2026 (1440 и 390: на телефоне колонка за прокруткой таблицы, замер
сам прокручивает к точке), период наоборот — рамка полей, слова под ними и объяснение вместо
строк, и период, за который никого не добавили, — условие словами в пустой таблице и пустое
поле «с». Лиды — в базе стенда, добавлены в сентябре и октябре 2026.
"""

from typing import Any

#: Узкое окно — телефон: таблица в прокрутке, поля периода — в её шапке.
PHONE = {"width": 390, "height": 844}
#: Окно, где вкладки раздела стоят рядом: уже 100em (1 600 px) вместо них — список «Раздел»,
#: и точки вкладок экрана `sales` на 1440 с 12.10.2026 не находятся.
WIDE = {"width": 1680, "height": 900}

HEAD = ".leadsTable thead"
#: Объяснение пустой таблицы — под таблицей, не в ней (`FixedTable`, `filler`).
FILLER = ".mantine-TableScrollContainer-scrollContainer + div"


def added_screens(norm: float) -> dict[str, dict[str, Any]]:
    """Период задан, задан наоборот и пуст; колонка даты в строках — на 1440 и 390."""
    ready = ("heading", "Продажи")
    october = "/sales?added_from=2026-10-01&added_to=2026-10-15"
    period = [
        ("подпись колонки даты", f"{HEAD} th:text-is('Дата добавления')", norm),
        ("слово «с» у поля", f"{HEAD} label:text-is('с')", norm),
        ("слово «по» у поля", f"{HEAD} label:text-is('по')", norm),
        ("день в поле «с»", "input[aria-label='Добавлены с']", norm),
        ("день в поле «по»", "input[aria-label='Добавлены по']", norm),
        ("дата добавления в строке", ".leadsTable tbody .leadAdded", norm),
    ]
    return {
        "sales-added": {"path": october, "ready": ready, "probes": period},
        "sales-added-phone": {
            "path": october,
            "ready": ready,
            "viewport": PHONE,
            "probes": [period[0], period[2], period[4], period[5]],
        },
        "sales-added-wrong": {
            "path": "/sales?added_from=2026-10-28&added_to=2026-10-15",
            "ready": ready,
            "probes": [
                ("слова под полями", f"{HEAD} .addedProblem", norm),
                ("день в поле с отказом", "input[aria-label='Добавлены по']", norm),
                ("заголовок объяснения", f"{FILLER} p:text-is('Период задан наоборот.')", norm),
                ("слова объяснения", f"{FILLER} p:has-text('такой период пуст')", norm),
                ("кнопка «Сбросить фильтры»", f"{FILLER} button", norm),
            ],
        },
        "sales-added-none": {
            "path": "/sales?added_to=2026-09-01",
            "ready": ready,
            "probes": [
                ("пустое поле «с»", "input[aria-label='Добавлены с']", norm),
                ("условие пустой таблицы", f"{FILLER} p:has-text('добавлены по')", norm),
            ],
        },
        # Вкладки рядом: выбранная — «emails — N».
        "sales-tabs-wide": {
            "path": "/sales",
            "ready": ready,
            "viewport": WIDE,
            "probes": [
                (
                    "выбранная вкладка «emails»",
                    ".glassPanel .mantine-SegmentedControl-label[data-active] "
                    ".mantine-SegmentedControl-innerLabel",
                    norm,
                ),
                (
                    "невыбранная вкладка",
                    ".glassPanel .mantine-SegmentedControl-label:not([data-active]) "
                    ".mantine-SegmentedControl-innerLabel",
                    norm,
                ),
            ],
        },
    }
