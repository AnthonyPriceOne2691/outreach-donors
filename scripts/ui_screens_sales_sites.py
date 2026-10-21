"""Сайты прогона и пересуд — окно «Сайты прогона» отчёта прогона (вкладка «Сбор базы») для замера
контраста.

Своим модулем, как вкладка сбора (`ui_screens_sales_segment.py`), который их подключает: каталог
продаж у предела длины. Окно открыто адресом (`?run=N&sites_run=N`) — номер прогона называет
стенд: его база, в ней прогон со всеми полосами и пересуд, сменивший полосы (`STAND_RUN`). С
15.10.2026 список — в окне поверх отчёта, на телефоне — во весь экран, а решения «на проверку» —
на её вкладке (`sales-collect-sites-review`): причина целиком, цитата, «Принять» и «Отказ».
Точки — то, по чему решают: полоса, кто решил и когда, причина и цитата, итог, «было → стало».
С 20.10.2026 — и отмена решения человека: «Отменить решение» под «кто решил» и в колонке «Решение»
после решения (шаг `decide_first` решает строку стенда — каждая тема следующую), слова судьи у
решения человека («Судья: …») и «решает человек» под полосой сайта, которому судья отказал. С
варианта (b) судьи (20.10.2026) — вкладка «Судья отказал бы» (`sales-collect-sites-would-reject`):
строка «решать не обязательно» над таблицей, бейдж полосы, «Принять» и «Отказ»; на «Все» — бейдж
«судья отказал бы» и «язык» у «кто решил». С 21.10.2026 у сайта, чьему лиду письмо не ушло, под
«Отменить решение» — что станет с лидом («лид снимется — письмо ему не уйдёт»).
"""

from typing import Any

from ui_screens_sales_hints import hint_probe

#: Прогон стенда, у которого есть сайты всех полос и прошедший пересуд.
STAND_RUN = 1
#: Ячейки таблицы сайтов — по подписям колонок (`data-label`), а не по номеру колонки.
CELL = "table.filteredTable td[data-label='{}']"
#: Телефон: окно во весь экран, строка таблицы — карточкой.
PHONE = {"width": 390, "height": 844}
#: Решение в колонке «Решение» вкладки «На проверку» — и его отмена под ним.
DECIDE = "td.cellDecide button:has-text('{}')"


def decide_first(page: Any) -> None:
    """Шаг перед точкой: «Принять» по первой строке «на проверку» — под «решено» встаёт «Отменить
    решение». Решение пишется в базу стенда: после перезагрузки (смена темы) решается следующая."""
    page.locator(DECIDE.format("Принять")).first.click()
    page.locator(DECIDE.format("Отменить решение")).first.wait_for()
    page.wait_for_timeout(700)


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
        (
            "«решает человек» у полосы",
            f"{CELL.format('Полоса')} p:has-text('решает человек')",
            norm,
        ),
        ("слова судьи у решения", f"{CELL.format('Почему')} p:has-text('Судья:')", norm),
        (
            "«Отменить решение»",
            f"{CELL.format('Кто решил')} button:has-text('Отменить решение')",
            norm,
        ),
        # С 21.10.2026 — под кнопкой у сайта с лидом: что станет с лидом.
        ("что станет с лидом", f"{CELL.format('Кто решил')} p:has-text('лид снимется')", norm),
        ("сколько показано", "p:has-text('Показаны')", norm),
        ("кнопка «Пересудить прогон»", "button:has-text('Пересудить прогон')", big),
        ("итог пересуда", "[role='status'] p:has-text('сменили полосу')", norm),
        ("было → стало", "ul[aria-label='Было → стало'] li", norm),
        hint_probe("что в списке сайтов", "Что в списке сайтов", norm),
        (
            "полоса «судья отказал бы»",
            f"{CELL.format('Полоса')} .mantine-Badge-label:text-is('судья отказал бы')",
            norm,
        ),
        ("кто решил: язык", f"{CELL.format('Кто решил')} p:text-is('язык')", norm),
    ]
    refused = [
        (
            "вкладка «Судья отказал бы»",
            ".mantine-SegmentedControl-label:has-text('Судья отказал бы')",
            norm,
        ),
        ("строка «решать не обязательно»", "[role='dialog'] p:has-text('Решать по ним')", norm),
        (
            "полоса «судья отказал бы»",
            f"{CELL.format('Полоса')} .mantine-Badge-label:text-is('судья отказал бы')",
            norm,
        ),
        ("причина целиком", f"{CELL.format('Почему')} .segmentReason", norm),
        ("вердикт судьи", f"{CELL.format('Судья')} p", norm),
        ("кнопка «Принять»", DECIDE.format("Принять"), big),
        ("кнопка «Отказ»", DECIDE.format("Отказ"), big),
        # Последними: шаг решает строку стенда.
        ("«Отменить решение» после решения", DECIDE.format("Отменить решение"), norm, decide_first),
        ("«решено: …»", "td.cellDecide p:has-text('решено:')", norm),
    ]
    review = [
        ("вкладка «На проверку»", ".mantine-SegmentedControl-label:has-text('На проверку')", norm),
        ("причина целиком", f"{CELL.format('Почему')} .segmentReason", norm),
        ("цитата сайта", f"{CELL.format('Почему')} .judgeQuote", norm),
        ("вердикт судьи", f"{CELL.format('Судья')} p", norm),
        ("кнопка «Принять»", DECIDE.format("Принять"), big),
        ("кнопка «Отказ»", DECIDE.format("Отказ"), big),
        (
            "«решает человек» у полосы",
            f"{CELL.format('Полоса')} p:has-text('решает человек')",
            norm,
        ),
        # Последними: шаг решает строку стенда.
        ("«Отменить решение» после решения", DECIDE.format("Отменить решение"), norm, decide_first),
        ("«решено: …»", "td.cellDecide p:has-text('решено:')", norm),
        ("слова судьи после решения", f"{CELL.format('Почему')} p:has-text('Судья:')", norm),
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
            "probes": [probe for probe in rows[:20] if probe[0] != "заголовок колонки"],
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
        "sales-collect-sites-would-reject": {
            "path": f"{base}&sites=would_reject",
            "ready": ready,
            "probes": refused,
        },
        # На телефоне вкладки — столбиком, вырезка выбранной ловит индикатор: меряется соседняя.
        "sales-collect-sites-would-reject-phone": {
            "path": f"{base}&sites=would_reject",
            "ready": ready,
            "viewport": PHONE,
            "probes": [
                ("вкладка-полоса", ".mantine-SegmentedControl-label:has-text('Отсечены')", norm),
                *refused[1:],
            ],
        },
    }
