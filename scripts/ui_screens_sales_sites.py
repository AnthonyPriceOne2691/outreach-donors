"""Сайты прогона и пересуд — окно «Сайты прогона» отчёта прогона (вкладка «Сбор базы») для замера
контраста.

Своим модулем, как вкладка сбора (`ui_screens_sales_segment.py`), который их подключает: каталог
продаж у предела длины. Окно открыто адресом (`?run=N&sites_run=N`) — номер прогона называет
стенд: его база, в ней прогон со всеми полосами и пересуд, сменивший полосы (`STAND_RUN`). С
15.10.2026 список — в окне поверх отчёта, на телефоне — во весь экран, а решения «на проверку» —
на её вкладке (`sales-collect-sites-review`): причина целиком, цитата, «Принять» и «Отказ».
Точки — то, по чему решают: полоса, кто решил и когда, причина и цитата, итог, «было → стало».
"""

from typing import Any

from ui_screens_sales_hints import hint_probe

#: Прогон стенда, у которого есть сайты всех полос и прошедший пересуд.
STAND_RUN = 1
#: Ячейки таблицы сайтов — по подписям колонок (`data-label`), а не по номеру колонки.
CELL = "table.filteredTable td[data-label='{}']"
#: Телефон: окно во весь экран, строка таблицы — карточкой.
PHONE = {"width": 390, "height": 844}


def sites_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Окно сайтов: вкладки «Все», «На проверку» и «Сменили при пересуде» — широкое окно и
    телефон, обе темы."""
    ready = ("heading", "Сайты прогона")
    base = f"/sales?tab=collect&run={STAND_RUN}&sites_run={STAND_RUN}"
    rows = [
        ("заголовок окна", "[role='dialog'] .mantine-Modal-title", norm),
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
    review = [
        ("вкладка «На проверку»", ".mantine-SegmentedControl-label:has-text('На проверку')", norm),
        ("причина целиком", f"{CELL.format('Почему')} .segmentReason", norm),
        ("цитата сайта", f"{CELL.format('Почему')} .judgeQuote", norm),
        ("вердикт судьи", f"{CELL.format('Судья')} p", norm),
        ("кнопка «Принять»", "td.cellDecide button:has-text('Принять')", big),
        ("кнопка «Отказ»", "td.cellDecide button:has-text('Отказ')", big),
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
        # Телефон: окно во весь экран, строка — карточкой, подписи колонок — над значениями.
        "sales-collect-sites-phone": {
            "path": base,
            "ready": ready,
            "viewport": PHONE,
            # Строки подписей колонок на телефоне нет — подписи у значений.
            "probes": [probe for probe in rows[:16] if probe[0] != "заголовок колонки"],
        },
        "sales-collect-sites-review": {
            "path": f"{base}&sites=review",
            "ready": ready,
            "probes": review,
        },
        # На телефоне вкладки — столбиком во всю ширину окна, и вырезка выбранной ловит края
        # индикатора и полотно, а не буквы: 1,36 : 1 при тёмных чернилах на светлой бирюзе
        # (сверено по вычисленным цветам, стенд 15.10.2026). Меряется соседняя вкладка.
        "sales-collect-sites-review-phone": {
            "path": f"{base}&sites=review",
            "ready": ready,
            "viewport": PHONE,
            "probes": [
                ("вкладка-полоса", ".mantine-SegmentedControl-label:has-text('Отсечены')", norm),
                *review[1:],
            ],
        },
        "sales-collect-sites-changed": {
            "path": f"{base}&sites=changed",
            "ready": ready,
            "probes": changed,
        },
    }
