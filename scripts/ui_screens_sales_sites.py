"""Сайты прогона и пересуд в отчёте прогона (вкладка «Сбор базы») для замера контраста.

Своим модулем, как вкладка сбора (`ui_screens_sales_segment.py`), который их подключает: каталог
продаж у предела длины. Список открыт адресом (`?sites_run=N`) — номер прогона называет стенд:
его база, в ней прогон со всеми полосами и пересуд, сменивший полосы (`STAND_RUN`).
Точки — то, по чему решают: полоса, кто решил и когда, причина и цитата, итог, «было → стало».
"""

from typing import Any

from ui_screens_sales_hints import hint_probe

#: Прогон стенда, у которого есть сайты всех полос и прошедший пересуд.
STAND_RUN = 1
#: Ячейки таблицы сайтов — по подписям колонок (`data-label`), а не по номеру колонки.
CELL = "table.filteredTable td[data-label='{}']"


def sites_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Список сайтов (вкладка «Все») и сменившие полосу при пересуде — широкое окно, обе темы."""
    ready = ("heading", "Сайты прогона")
    base = f"/sales?tab=collect&sites_run={STAND_RUN}"
    rows = [
        ("заголовок списка", "h5:text-is('Сайты прогона')", norm),
        ("вкладка-полоса", ".mantine-SegmentedControl-label:has-text('Приняты')", norm),
        ("заголовок колонки", "table.filteredTable th:text-is('Почему')", norm),
        ("домен сайта", "table.filteredTable td.cellName a", norm),
        ("ключ сайта", "table.filteredTable td.cellName p:has-text('по ключу')", norm),
        (
            "полоса «принят»",
            f"{CELL.format('Полоса')} .mantine-Badge-label:text-is('принят')",
            norm,
        ),
        (
            "полоса «на проверке»",
            f"{CELL.format('Полоса')} .mantine-Badge-label:text-is('на проверке')",
            norm,
        ),
        (
            "полоса «отсечён»",
            f"{CELL.format('Полоса')} .mantine-Badge-label:text-is('отсечён')",
            norm,
        ),
        ("вердикт судьи", f"{CELL.format('Судья')} p", norm),
        ("кто решил", f"{CELL.format('Кто решил')} p", norm),
        ("когда решено", f"{CELL.format('Кто решил')} .mantine-Stack-root p:nth-child(2)", norm),
        ("причина", f"{CELL.format('Почему')} p:first-child", norm),
        ("цитата сайта", f"{CELL.format('Почему')} .judgeQuote", norm),
        ("итог словами", f"{CELL.format('Итог')} p", norm),
        ("лид ссылкой", f"{CELL.format('Итог')} a", norm),
        ("сколько показано", "p:has-text('Показаны')", norm),
        ("кнопка «Пересудить прогон»", "button:has-text('Пересудить прогон')", big),
        ("итог пересуда", "[role='status'] p:has-text('сменили полосу')", norm),
        ("было → стало", "ul[aria-label='Было → стало'] li", norm),
        hint_probe("что в списке сайтов", "Что в списке сайтов", norm),
    ]
    changed = [
        (
            "вкладка «Сменили при пересуде»",
            ".mantine-SegmentedControl-label:has-text('Сменили')",
            norm,
        ),
        ("«было» / «стало бы»", f"{CELL.format('Полоса')} .mantine-Stack-root p", norm),
        ("причина «стало бы»", f"{CELL.format('Почему')} p:has-text('стало бы')", norm),
    ]
    return {
        "sales-collect-sites": {"path": base, "ready": ready, "probes": rows},
        # Телефон: строка — карточкой, подписи колонок — над значениями.
        "sales-collect-sites-phone": {
            "path": base,
            "ready": ready,
            "viewport": {"width": 390, "height": 844},
            # Строки подписей колонок на телефоне нет — подписи у значений.
            "probes": [probe for probe in rows[:16] if probe[0] != "заголовок колонки"],
        },
        "sales-collect-sites-changed": {
            "path": f"{base}&sites=changed",
            "ready": ready,
            "probes": changed,
        },
    }
