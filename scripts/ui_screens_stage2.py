"""Виды Этапа 2 из сверки ТЗ 11.10.2026 — для замера контраста.

Часть каталога `ui_screens.py`, своим модулем: правило «новый экран добавляется
в каталог вместе со своими точками» (docs/UI_RULES.md), а основной каталог стоит
у предела длины файла. Четыре вида: все рекламодатели списком с выгрузкой, приёмка
по донорам, обход доноров (29.10.2026) и вкладка рекламодателей на «Формах».

Мерится то, по чему решают: домен и слова строки — адрес и письмо значками,
число найденных у заголовка, значение фильтра в поле; у приёмки — вердикт порога
и чего не хватает до него, что нашёл обход и сколько спорных решено; у форм — вкладка
с числом и кнопки строки.

Нормы передаются параметрами, а не импортируются из `ui_screens`: тот подключает
этот модуль, и обратный импорт замкнул бы круг.
"""

from typing import Any

#: Переключатель вкладок — по своей подписи.
SEGMENT = ".mantine-SegmentedControl-label"
#: Строка донора в приёмке: первая строка таблицы — «Все доноры вместе».
DONOR_ROW = "table tbody tr:not(:first-child)"


def stage2_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Экраны и точки. `norm` — норма обычного текста, `big` — крупного."""
    return {
        "advertisers-list": {
            "path": "/advertisers?view=list",
            "ready": ("heading", "Все рекламодатели"),
            "probes": [
                ("заголовок вида", "h3", big),
                ("сколько всего или найдено", ".glassPanel h3 + p", norm),
                ("выбранный вид", f"{SEGMENT}[data-active]", norm),
                ("домен в строке", "table tbody td a", norm),
                # Причины балла — по ним сверяют рекламодателя, как в очереди проверки.
                ("причина балла", "table tbody td:nth-child(4) p", norm),
                ("адрес и письмо значками", "table tbody .mantine-Badge-label", norm),
                ("значение фильтра письма", "input[aria-label='Письмо']", norm),
                ("подсказка поиска", "input[aria-label='Поиск по домену рекламодателя']", norm),
                ("кнопка выгрузки", "button:has-text('Выгрузить')", big),
                ("пункт меню", "nav a", norm),
            ],
        },
        "advertisers-acceptance": {
            "path": "/advertisers?view=acceptance",
            "ready": ("heading", "Приёмка по донорам"),
            "probes": [
                ("заголовок вида", "h3", big),
                ("порог ТЗ", ".glassPanel p.mantine-Text-root", norm),
                # Первая строка — «Все доноры вместе», доноры — со второй (23.10.2026).
                ("все доноры вместе", "table tbody tr:first-child td:first-child p", norm),
                ("донор в строке", "table tbody tr:nth-child(2) td:first-child p", norm),
                ("что нашёл обход", f"{DONOR_ROW} td[data-label='Куплено']", norm),
                ("сколько спорных решено", f"{DONOR_ROW} td[data-label='Спорных'] p", norm),
                ("вердикт порога", f"{DONOR_ROW} td:last-child .mantine-Badge-label", norm),
                ("чего не хватает", f"{DONOR_ROW} td:last-child p", norm),
                ("числа приёмки", f"{DONOR_ROW} td[data-label='С адресом']", norm),
                ("пункт меню", "nav a", norm),
            ],
        },
        "advertisers-crawl": {
            # Вкладка «Обход доноров» (29.10.2026): счётчики-фильтры, строка в одну высоту,
            # значок обхода с «Переобойти» рядом, фильтры под колонками, «Обойти всех по фильтру».
            "path": "/advertisers?view=crawl",
            "ready": ("heading", "Обход доноров"),
            "probes": [
                ("заголовок вида", "h3", big),
                (
                    "что идёт у заголовка",
                    ".glassPanel h3 ~ div .mantine-Badge-label",
                    norm,
                ),
                (
                    "счётчик-фильтр нажатый",
                    "[aria-label='Статус последнего обхода'] button[aria-pressed='true']",
                    norm,
                ),
                (
                    "счётчик-фильтр",
                    "[aria-label='Статус последнего обхода'] button[aria-pressed='false']",
                    norm,
                ),
                ("подпись поля над таблицей", "span:has-text('Порядок')", norm),
                (
                    "домен в строке",
                    "table[aria-label='Доноры для обхода'] tbody td[data-label='Донор']",
                    norm,
                ),
                (
                    "значок обхода",
                    "table[aria-label='Доноры для обхода'] tbody .mantine-Badge-label",
                    norm,
                ),
                (
                    "пометка у цены",
                    "table[aria-label='Доноры для обхода'] tbody td[data-label='Цена'] p",
                    norm,
                ),
                (
                    "кнопка «Переобойти»",
                    "table[aria-label='Доноры для обхода'] button:has-text('Переобойти')",
                    big,
                ),
                ("значение фильтра", "input[aria-label='Срок цены']", norm),
                (
                    "кнопка «Обойти всех по фильтру»",
                    "button:has-text('Обойти всех по фильтру')",
                    big,
                ),
                ("пункт меню", "nav a", norm),
            ],
        },
        "forms-advertisers": {
            "path": "/forms?queue=advertisers",
            "ready": ("heading", "Формы"),
            "probes": [
                ("заголовок раздела", "h3", big),
                ("вкладка с числом", f"{SEGMENT}[data-active]", norm),
                ("ориентир темпа", ".glassPanel p.mantine-Text-root", norm),
                ("домен в строке", "table tbody td a", norm),
                ("откуда рекламодатель", "table tbody td:nth-child(2) p", norm),
                ("кнопка «Вписать адрес»", "table tbody button:has-text('Вписать адрес')", big),
                ("кнопка «Не вышло»", "table tbody button:has-text('Не вышло')", big),
                ("пункт меню", "nav a", norm),
            ],
        },
    }
