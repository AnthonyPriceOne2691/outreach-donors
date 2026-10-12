"""Виды Этапа 2 из сверки ТЗ 11.10.2026 — для замера контраста.

Часть каталога `ui_screens.py`, своим модулем: правило «новый экран добавляется
в каталог вместе со своими точками» (docs/UI_RULES.md), а основной каталог стоит
у предела длины файла. Три вида: все рекламодатели списком с выгрузкой, приёмка
по донорам и вкладка рекламодателей на «Формах».

Мерится то, по чему решают: домен и слова строки — адрес и письмо значками,
число найденных у заголовка, значение фильтра в поле; у приёмки — вердикт порога
и чего не хватает до него; у форм — вкладка с числом и кнопки строки.

Нормы передаются параметрами, а не импортируются из `ui_screens`: тот подключает
этот модуль, и обратный импорт замкнул бы круг.
"""

from typing import Any

#: Переключатель вкладок — по своей подписи.
SEGMENT = ".mantine-SegmentedControl-label"


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
                ("донор в строке", "table tbody td:first-child p", norm),
                ("вердикт порога", "table tbody td:last-child .mantine-Badge-label", norm),
                ("чего не хватает", "table tbody td:last-child p", norm),
                ("числа приёмки", "table tbody td:nth-child(4)", norm),
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
