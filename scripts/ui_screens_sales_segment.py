"""Вкладка «Сбор базы» раздела продаж для замера контраста (срез 6.1).

Своим модулем, как очередь и воронка: каталог продаж (`ui_screens_sales.py`) у предела длины.
Нормы передаются параметрами — обратный импорт замкнул бы круг.

Экран мерится со сметой: подготовка вписывает ключи и считает смету (`collect_prepare`), иначе
строк сметы и открытой кнопки «Запустить прогон» на экране нет. Отчёт — последнего прогона
гипотезы стенда, компактный (15.10.2026): шапка с кнопками окон, плитки-ссылки, «На проверку N —
решить», причины отсева — раскрытием свёрнутой строки (`open_why`), ключи прогона. Расход рядом
со сметой — в окне «Расход и смета» (`sales-collect-spend`: приглушённая колонка сметы и пометка о
Hunter — мелким текстом); сайты прогона и решения «на проверку» — в окне сайтов
(`ui_screens_sales_sites.py`). Какой прогон на стенде, решает его база (в нём должны быть сайты
всех полос и итог этапов в отчёте).
"""

import re
from typing import Any

from playwright.sync_api import expect
from ui_screens_sales_hints import hint_probe
from ui_screens_sales_sites import sites_screens

#: Узкое окно — телефон: плитки в две колонки, окна отчёта — во весь экран.
PHONE = {"width": 390, "height": 844}

#: Ключи для сметы: один отсеивается гигиеной — видна и строка отсеянных.
KEYWORDS = "buy mountain bike online\nbike parts store\nsite:example.com bikes"


def collect_prepare(page: Any) -> None:
    """Вписать ключи и посчитать смету: без этого строк сметы на экране нет. Кнопка — с
    клавиатуры, а не мышью: после щелчка фокус на «i» уже не «видимый», и подсказка точки
    `hint_probe` не открылась бы (у Mantine подсказка по фокусу — только клавиатурному)."""
    page.get_by_role("textbox", name="Ключи").fill(KEYWORDS)
    page.get_by_role("button", name="Посчитать смету").focus()
    page.keyboard.press("Enter")
    expect(page.locator(".segmentEstimate p").first).to_be_visible()
    page.wait_for_timeout(400)


def open_why(page: Any) -> None:
    """Шаг перед точкой: свёрнутая строка «Почему отсечены и без адреса» раскрыта — с клавиатуры,
    как смета (`collect_prepare`): после щелчка мышью фокус на «i» следующей точки не «видимый».
    Замер перезагружает экран перед каждой темой — строка снова свёрнута."""
    page.get_by_role("button", name=re.compile(r"^Почему (отсечены|без адреса)")).focus()
    page.keyboard.press("Enter")
    page.locator("ul[aria-label='Отсечены — почему'] li").first.wait_for()
    page.wait_for_timeout(500)


def spend_prepare(page: Any) -> None:
    """Открыть окно «Расход и смета» кнопкой у заголовка прогона."""
    page.get_by_role("button", name="Расход и смета").click()
    expect(page.get_by_role("dialog", name=re.compile(r"^Расход и смета"))).to_be_visible()
    page.wait_for_timeout(600)


def segment_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка сбора: широкое окно и телефон, обе темы — у замера."""
    ready = ("heading", "Продажи")
    path = "/sales?tab=collect"
    report = [
        ("заголовок блока", "h5:text-is('Сегмент гипотезы')", norm),
        ("описание сегмента", ".segmentText", norm),
        ("подпись поля", ".mantine-InputWrapper-label:text-is('Ключи')", norm),
        ("пояснение поля", ".mantine-InputWrapper-description", norm),
        ("строка сметы", ".segmentEstimate p", norm),
        ("кнопка «Посчитать смету»", "button:has-text('Посчитать смету')", big),
        ("кнопка «Запустить прогон»", "button:has-text('Запустить прогон')", big),
        ("этап прогона", ".mantine-Badge-label:text-is('готово')", norm),
        ("ссылка «Сайты прогона · N»", "a:has-text('Сайты прогона ·')", norm),
        ("кнопка «Расход и смета»", "button:has-text('Расход и смета')", norm),
        ("режим судьи", "p:has-text('судья в наблюдении')", norm),
        ("подпись плитки", ".metricTile p:nth-child(1)", norm),
        ("число в плитке", ".metricTile p:nth-child(2)", big),
        ("подпись плитки-ссылки", "a.metricTile p:nth-child(1)", norm),
        ("«На проверку N — решить»", "a:has-text('— решить')", norm),
        ("свёрнутое «почему»", "button:has-text('Почему')", norm),
        ("причина отсева", "ul[aria-label='Отсечены — почему'] li", norm, open_why),
        ("ключ прогона", "section[aria-label='Прогоны гипотезы'] ul[aria-label^='Взято'] li", norm),
        *_form_probes(norm),
    ]
    probes = [
        ("вступление вкладки", ".salesTabs + div p", norm),
        *report,
        hint_probe("как идёт прогон", "Как идёт прогон", norm),
    ]
    going = [
        ("значок «в очереди задач»", ".mantine-Badge-label:text-is('в очереди задач')", norm),
        ("шапка прогона", "p:has-text('Язык писем')", norm),
        ("ход прогона", "[aria-label='Ход прогона']", norm),
        ("пульс прогона", "[aria-label='Ход прогона'] span", norm),
        ("плашка «некому взять»", ".mantine-Alert-message:has-text('служба долгих')", norm),
        ("подпись «Ещё не судили»", ".metricTile p:text-is('Ещё не судили')", norm),
    ]
    spend = _spend_probes(norm)
    return {
        "sales-collect": {"path": path, "ready": ready, "probes": probes},
        "sales-collect-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": report,
        },
        # Ход прогона (волна 14): последний прогон стенда стоит в очереди, воркеров нет —
        # «в очереди задач · поставлен …» и плашка «Задачу некому взять».
        "sales-collect-going": {"path": path, "ready": ready, "probes": going},
        "sales-collect-going-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": going,
        },
        # Окно «Расход и смета» (15.10.2026): на телефоне — во весь экран, строка — карточкой.
        "sales-collect-spend": {"path": path, "ready": ready, "probes": spend},
        "sales-collect-spend-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": spend,
        },
        **sites_screens(norm, big),
    }


def _form_probes(norm: float) -> list[tuple[str, str, float]]:
    """Режим выдачи и ключи сметы в форме нового прогона (проверка прода 14.10)."""
    form = "section[aria-label='Новый прогон']"
    return [
        ("подпись поля «Выдача»", ".mantine-InputWrapper-label:text-is('Выдача')", norm),
        ("взятый ключ сметы", f"{form} ul[aria-label^='Взято ключей'] li", norm),
        ("отсеянный ключ сметы", f"{form} ul[aria-label^='Отсеяно ключей'] li", norm),
    ]


def _spend_probes(norm: float) -> list[tuple[str, str, float]]:
    """Окно «Расход и смета»: расход рядом со сметой — смета в таблице и пометка о Hunter
    приглушённым текстом, и он тоже меряется; смета на запуске строками сервера."""
    window = "[role='dialog']"
    return [
        ("заголовок окна", f"{window} .mantine-Modal-title", norm),
        ("подпись расхода", f"{window} section[aria-label='Расход прогона'] > p:first-child", norm),
        ("статья расхода", f"{window} .segmentSpend tbody td:nth-child(1) p", norm),
        ("факт расхода", f"{window} .segmentSpend tbody td:nth-child(2) p", norm),
        ("смета в расходе", f"{window} .segmentSpend tbody td:nth-child(3) p", norm),
        ("пометка о Hunter", f"{window} section[aria-label='Расход прогона'] > p:last-child", norm),
        ("строка сметы на запуске", f"{window} section[aria-label='Смета на запуске'] p + p", norm),
    ]


def segment_prepare() -> dict[str, Any]:
    """Подготовка экранов сбора до замера: смета посчитана; окно расхода открыто."""
    return {
        "sales-collect": collect_prepare,
        "sales-collect-phone": collect_prepare,
        "sales-collect-spend": spend_prepare,
        "sales-collect-spend-phone": spend_prepare,
    }
