"""Экраны рассылки, диалогов и списков для замера контраста.

Часть каталога `ui_screens.py`, вынесенная своим модулем: каталог упёрся
в предел длины файла, а правило «новый экран добавляется в каталог вместе
со своими точками» от этого не отменяется. Точки заведены 25.09.2026 для
экранов, которых в каталоге не было вовсе: список диалогов, формы, пороги,
расход, домены рассылки.

Нормы передаются параметрами, а не импортируются из `ui_screens`: тот
подключает этот модуль, и обратный импорт замкнул бы круг.
"""

from collections.abc import Callable
from typing import Any

from playwright.sync_api import expect


def mail_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Экраны и точки. `norm` — норма обычного текста, `big` — крупного."""
    return {
        # Список диалогов: фильтры под заголовками колонок, как у доноров.
        # Мерится то, по чему ищут диалог, — донор, состояние, цена, — и
        # значение фильтра в поле: его сверяют с текстом рядом.
        "threads": {
            "path": "/threads",
            "ready": ("heading", "Диалоги"),
            "probes": [
                ("заголовок раздела", "h3", big),
                ("сколько найдено", ".glassPanel h3 + p", norm),
                ("пояснение под ним", ".glassPanel p.mantine-Text-root >> nth=1", norm),
                ("значение фильтра состояния", "input[aria-label='Состояние']", norm),
                ("подсказка поиска", "input[aria-label='Поиск по донору или адресу']", norm),
                ("донор в строке", "table tbody td a", norm),
                ("адрес под донором", "table tbody td:first-child p", norm),
                ("значок состояния", "table tbody .mantine-Badge-label", norm),
                ("цена", "table tbody td:nth-child(4) p", norm),
                ("когда последнее событие", "table tbody td:nth-child(5) p", norm),
                ("пункт меню", "nav a", norm),
            ],
        },
        "forms": {
            "path": "/forms",
            "ready": ("heading", "Формы"),
            "probes": [
                ("заголовок раздела", "h3", big),
                ("пояснение под ним", "p.mantine-Text-root", norm),
                # Имя — чернилами: бирюзовая ссылка у бирюзового угла
                # полотна намерилась 4,30 : 1 (25.09.2026).
                ("домен в строке", "table tbody td a", norm),
                ("DR значком", "table tbody .mantine-Badge-label", norm),
                ("когда искали", "table tbody td:nth-child(4)", norm),
                ("кнопка «Вписать адрес»", "table tbody button:has-text('Вписать адрес')", big),
                ("кнопка «Не вышло»", "table tbody button:has-text('Не вышло')", big),
                ("пункт меню", "nav a", norm),
            ],
        },
        "thresholds": {
            "path": "/settings",
            "ready": ("heading", "Пороги отбора"),
            "probes": [
                ("заголовок раздела", "h3", big),
                ("подпись поля", "label:has-text('DR не ниже')", norm),
                ("пояснение поля", ".mantine-InputWrapper-description", norm),
                ("значение в поле", ".mantine-NumberInput-input", norm),
                ("заголовок «Что станет с базой»", "h5", norm),
                ("номер версии", "table tbody td p", norm),
                ("значок «действует»", "table tbody .mantine-Badge-label", norm),
                ("кто и когда", "table tbody td:nth-child(6) p", norm),
                ("пункт меню", "nav a", norm),
            ],
        },
        "usage": {
            "path": "/usage",
            "ready": ("heading", "Расход"),
            "probes": [
                ("заголовок раздела", "h3", big),
                ("сколько потратили", "p:has-text('Мы потратили')", norm),
                ("подпись плитки", ".glassQuiet p:nth-child(1)", norm),
                ("число в плитке", ".glassQuiet p:nth-child(2)", big),
                # Единица — словом под числом («токена»), а не хвостом за
                # ним: на телефоне хвост ломался на отдельную строку.
                ("единица под числом", ".glassQuiet p:nth-child(3)", norm),
                ("статья расхода", "table tbody td p", norm),
                ("провайдер значком", "table tbody .mantine-Badge-label", norm),
                ("пункт меню", "nav a", norm),
            ],
        },
        # Пробы — внутри карточки домена: шапка тоже `.glass`, и проба
        # `.glass p` первой брала почту в шапке (25.09.2026).
        "senders": {
            "path": "/senders",
            "ready": ("heading", "Домены рассылки"),
            "probes": [
                ("заголовок раздела", "h3", big),
                ("домен", ".mantine-Card-root.glass p", norm),
                ("значок состояния", ".mantine-Card-root.glass .mantine-Badge-label", norm),
                ("сколько ушло сегодня", ".mantine-Card-root.glass p[data-size='xs']", norm),
                (
                    "кнопка «Выключить»",
                    ".mantine-Card-root.glass button:has-text('Выключить')",
                    big,
                ),
                ("пункт меню", "nav a", norm),
            ],
        },
    }


def usage_loaded(page: Any) -> None:
    """Дождаться расхода: остаток экран спрашивает у провайдеров, и ответ
    идёт секунду-две. Девятисот миллисекунд после перезагрузки не хватало —
    замер 25.09.2026 не нашёл на экране ни одной точки, кроме меню."""
    expect(page.get_by_text("Мы потратили")).to_be_visible(timeout=15_000)
    page.wait_for_timeout(900)


def mail_prepare() -> dict[str, Callable[[Any], None]]:
    """Что сделать на экране до замера — там, где без этого мерить нечего."""
    return {"usage": usage_loaded}
