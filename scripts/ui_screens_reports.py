"""Экраны отчётов для замера контраста: отчёт прогона и журнал действий.

Часть каталога `ui_screens.py`, вынесенная своим модулем, как экраны доноров и писем:
основной каталог стоит у предела длины файла, а правило «новый экран меряется в обеих
темах» от этого не отменяется (`docs/UI_RULES.md`, «Что проверяется на каждом новом
экране»).

Нормы передаются параметрами, а не импортируются из `ui_screens`: тот подключает этот
модуль, и обратный импорт замкнул бы круг.
"""

from collections.abc import Callable
from typing import Any

#: Карточка лимита «Расхода» — по своему имени: рядом такие же (`settings/LimitCard.tsx`).
HUNTER_CARD = "[role='group'][aria-label='Hunter']"
#: Переключатель вкладок «Расхода»: мерится подпись, а не обёртка с подвижной подложкой.
USAGE_TAB = "[aria-label='Что смотреть на экране «Расход»'] .mantine-SegmentedControl-innerLabel"
#: «Подробнее» карточки Hunter: подробности свёрнуты и не отрисованы (10.11.2026).
HUNTER_MORE = f"{HUNTER_CARD} button[aria-expanded]"


#: «i» у кнопки «Рассмотреть» в отчёте прогона: почему очередь не равна «Подходит».
REVIEW_HINT = "Почему «Рассмотреть» не равно «Подходит»"


def _open_card(name: str) -> Callable[[Any], None]:
    """Шаг перед точкой: окошко «i» с этим именем открыто нажатием, прежнее закрыто Esc.

    Значка нет (числа очереди и «Подходит» сошлись, причин нет) — ничего не делать: точка
    честно печатает «не измерено», а не роняет весь замер ожиданием, как `open_reason`.
    """

    def show(page: Any) -> None:
        page.keyboard.press("Escape")
        hint = page.get_by_role("button", name=name)
        if hint.count() == 0:
            return
        hint.first.click()
        page.locator(".mantine-Popover-dropdown").first.wait_for()
        page.wait_for_timeout(300)

    return show


def _card_probe(what: str, name: str, text: str, norm: float) -> tuple[Any, ...]:
    """Точка текста в окошке «i»: имя точки, вырезка, норма и шаг, который его открывает."""
    return (what, f".mantine-Popover-dropdown p:has-text('{text}')", norm, _open_card(name))


def open_hunter_details(page: Any) -> None:
    """Раскрыть подробности карточки Hunter, если свёрнуты: тариф и потолок месяца — под
    «Подробнее». Раскрытие — два такта, мерить по улёгшемуся."""
    more = page.locator(HUNTER_MORE)
    if more.get_attribute("aria-expanded") == "false":
        more.click()
        page.wait_for_timeout(900)


def open_tasks(page: Any) -> None:
    """Раскрыть свёрнутые задачи панели лимитов: строки под ними не отрисованы (10.11.2026)."""
    closed = page.locator(".limitTable tr.tierRow button[aria-expanded='false']")
    while closed.count() > 0:
        closed.first.click()
    page.wait_for_timeout(500)


def _usage_limits(norm: float, big: float) -> list[tuple[Any, ...]]:
    """Точки вкладки «Лимиты»: по ним решают, сколько осталось и что менять."""
    return [
        ("заголовок экрана", "h3", big),
        ("вкладка", USAGE_TAB, norm),
        ("имя карточки", f"{HUNTER_CARD} p:text-is('Hunter')", norm),
        ("подпись над числом", f"{HUNTER_CARD} p:text-is('Осталось на учётке')", norm),
        ("крупное число", f"{HUNTER_CARD} .mantine-Group-root p:nth-child(1)", big),
        ("из скольких", f"{HUNTER_CARD} .mantine-Group-root p:nth-child(2)", norm),
        ("что значит число", f"{HUNTER_CARD} p:has-text('хватит на')", norm),
        ("сброс", f"{HUNTER_CARD} p:has-text('Сброс')", norm),
        ("«Подробнее»", f"{HUNTER_MORE} .mantine-Button-label", norm),
        ("подробности", f"{HUNTER_CARD} p:has-text('Тариф')", norm, open_hunter_details),
        ("подвал", f"{HUNTER_CARD} p:has-text('Потолок:')", norm),
        ("кнопка «Изменить»", "button[aria-label='Изменить потолок']", big),
        ("метка «без потолка»", ".mantine-Badge-label:has-text('без потолка')", norm),
    ]


def _limit_panel(norm: float, big: float) -> list[tuple[Any, ...]]:
    """Точки панели лимитов (02.11.2026): по ним решают, какой лимит менять и почём. Задачи
    свёрнуты (10.11.2026) — строки мерятся после раскрытия."""
    cell = ".limitTable tbody tr:not(.tierRow) td"
    return [
        ("заголовок экрана", "h3", big),
        ("вкладка", USAGE_TAB, norm),
        ("задача", ".limitTable .tierRow .mantine-Button-label", norm),
        ("сервис", f"{cell}:nth-child(1) p:nth-child(1)", norm, open_tasks),
        ("действие", f"{cell}:nth-child(1) p:nth-child(2)", norm),
        ("охват", f"{cell}:nth-child(2) p", norm),
        ("выбрано", f"{cell}:nth-child(6) p", norm),
        ("стоимость", ".limitCost", norm),
        ("текущее ограничение", f"{cell}:nth-child(8) p", norm),
        ("откуда значение", ".limitSource", norm),
        ("кнопка «Изменить»", ".limitTable .cellDecide button", big),
    ]


def report_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Экраны и точки. `norm` — норма обычного текста, `big` — крупного."""
    return {
        # «Расход» (02.11.2026): вкладка лимитов — карточки на вложенном стекле, на широком окне и
        # на телефоне: телефонная раскладка ставит их над другими местами полотна. Вкладка статей
        # — плитки и таблица под шапкой.
        "usage": {
            "path": "/usage",
            "ready": ("heading", "Расход"),
            "probes": _usage_limits(norm, big),
        },
        "usage-phone": {
            "path": "/usage",
            "ready": ("heading", "Расход"),
            "viewport": {"width": 390, "height": 844},
            "probes": _usage_limits(norm, big),
        },
        "usage-panel": {
            "path": "/usage?view=panel",
            "ready": ("table", "Панель лимитов"),
            "probes": _limit_panel(norm, big),
        },
        "usage-panel-phone": {
            "path": "/usage?view=panel",
            "ready": ("table", "Панель лимитов"),
            "viewport": {"width": 390, "height": 844},
            "probes": _limit_panel(norm, big),
        },
        "usage-articles": {
            "path": "/usage?view=articles",
            "ready": ("heading", "Расход"),
            "probes": [
                ("период над плитками", "p:has-text('календарный месяц')", norm),
                ("подпись плитки", ".metricTile p:nth-child(1)", norm),
                ("число в плитке", ".metricTile p:nth-child(2)", big),
                ("единица под числом", ".metricTile p:nth-child(3)", norm),
                ("заголовок «По этапам»", "p:text-is('По этапам')", norm),
                ("статья в таблице", "table tbody tr td p", norm),
            ],
        },
        # Отчёт прогона (сверка ТЗ 11.10.2026): по нему решают, какой порог режет и во
        # что обошёлся прогон. Мерится шапка с плитками «подходит / не подходит / не
        # проверен», таблица порогов и расход. Номер — прогон базы разработки с итогом;
        # у прогона без итога точки плиток и порогов честно «не найдены».
        "run-report": {
            "path": "/runs/18",
            "ready": ("heading", "Отчёт прогона №18"),
            "probes": [
                ("заголовок экрана", "h3", big),
                ("«К прогонам»", "a.backLink", norm),
                ("значок состояния", ".glassPanel .mantine-Badge-label", norm),
                ("факты прогона", ".glassPanel p:has-text('ключей')", norm),
                ("путь доменов", "p:has-text('Из выдачи')", norm),
                ("подпись плитки", ".glassQuiet p:nth-child(1)", norm),
                ("число в плитке", ".glassQuiet p:nth-child(2)", big),
                ("пояснение под числом", ".glassQuiet p:nth-child(3)", norm),
                ("порог в строке", "table tbody td:first-child", norm),
                # Каждая причина «не предложен» — словами (20.10.2026): крупный сайт — своей
                # строкой порогов, исключённые и «не проверен» — карточкой под порогами.
                ("крупный сайт в порогах", "table tbody td:has-text('слишком крупный')", norm),
                ("заголовок причин", "h5:has-text('Исключены и не проверены')", norm),
                ("группа причин", "p:has-text('Исключено до Ahrefs')", norm),
                ("причина в строке", "table tbody td:has-text('Кому не пишем')", norm),
                ("ссылка на резерв прогона", "a:has-text('в резерве прогона')", norm),
                ("статья расхода", "table tbody .cellName", norm),
                ("провайдер статьи", "table tbody .mantine-Badge-label", norm),
                ("пункт меню", "nav a", norm),
                # Пояснения — в «i» (слово владельца 10.11.2026): у кнопки «Рассмотреть» и у
                # заголовков карточек. Окошко ложится поверх экрана — эти точки последние.
                _card_probe("очередь и «Подходит»", REVIEW_HINT, "кто ждёт решения", norm),
                _card_probe("пояснение порогов", "Какие пороги в отчёте", "Пороги этого", norm),
                _card_probe("пояснение причин", "Кто исключён и кто не проверен", "гейт", norm),
            ],
        },
        # Журнал действий (11.10.2026): по нему разбирают доступ — кто, что и когда.
        # Мерятся фильтры, строка числа записей и строки таблицы: время, автор, действие
        # с уточнением под ним, цель-ссылка и подробности — мелкий текст, где ключ
        # приглушён, а значение — полными чернилами.
        "journal": {
            "path": "/journal",
            "ready": ("heading", "Журнал действий"),
            "probes": [
                ("заголовок экрана", "h3", big),
                ("подпись поля фильтра", ".mantine-InputWrapper-label", norm),
                ("пояснение под полем", ".mantine-InputWrapper-description", norm),
                ("сколько записей", "p:has-text('новые сверху')", norm),
                ("время записи", "table tbody td:first-child p", norm),
                ("автор", "table tbody td:nth-child(2) p", norm),
                ("действие", "table tbody td:nth-child(3) p", norm),
                ("что сделано под действием", "table tbody td:nth-child(3) p + p", norm),
                ("цель-ссылка", "table tbody td a", norm),
                ("подробность", "table tbody .journalDetail", norm),
                ("ключ подробности", "table tbody .journalDetail span", norm),
                ("пункт меню", "nav a", norm),
            ],
        },
    }
