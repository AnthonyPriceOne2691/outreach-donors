"""Вкладка «Сбор базы» раздела продаж для замера контраста (срез 6.1).

Своим модулем, как очередь и воронка: каталог продаж (`ui_screens_sales.py`) у предела длины.
Нормы передаются параметрами — обратный импорт замкнул бы круг.

Экран мерится со сметой: подготовка вписывает ключи и считает смету (`collect_prepare`), иначе
строк сметы и открытой кнопки «Запустить прогон» на экране нет. Отчёт — последнего прогона
гипотезы стенда: плитки, причины словами, строки «на проверку» с кнопками решения, ключи прогона
и расход рядом со сметой (приглушённая колонка сметы и пометка о Hunter — мелким текстом). Какой
прогон на стенде, решает его база (в нём должны быть сайты всех полос и итог этапов в отчёте).
"""

from typing import Any

from playwright.sync_api import expect
from ui_screens_sales_hints import hint_probe

#: Узкое окно — телефон: плитки в две колонки, таблица «на проверку» — прокруткой.
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


def segment_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка сбора: широкое окно и телефон, обе темы — у замера."""
    ready = ("heading", "Продажи")
    path = "/sales?tab=collect"
    probes = [
        ("вступление вкладки", ".salesTabs + div p", norm),
        ("заголовок блока", "h5:text-is('Сегмент гипотезы')", norm),
        ("описание сегмента", ".segmentText", norm),
        ("подпись поля", ".mantine-InputWrapper-label:text-is('Ключи')", norm),
        ("пояснение поля", ".mantine-InputWrapper-description", norm),
        ("строка сметы", ".segmentEstimate p", norm),
        ("кнопка «Посчитать смету»", "button:has-text('Посчитать смету')", big),
        ("кнопка «Запустить прогон»", "button:has-text('Запустить прогон')", big),
        ("этап прогона", ".mantine-Badge-label:text-is('готово')", norm),
        ("режим судьи", "p:has-text('судья в наблюдении')", norm),
        ("подпись плитки", ".metricTile p:nth-child(1)", norm),
        ("число в плитке", ".metricTile p:nth-child(2)", big),
        ("причина отсева", "ul[aria-label='Отсечены — почему'] li", norm),
        ("причина «на проверку»", ".segmentReview .segmentReason", norm),
        ("цитата сайта", ".segmentReview tbody p:has-text('«')", norm),
        ("кнопка «Принять»", ".segmentReview button:has-text('Принять')", big),
        ("кнопка «Отказ»", ".segmentReview button:has-text('Отказ')", big),
        hint_probe("как идёт прогон", "Как идёт прогон", norm),
        *_spend_probes(norm),
    ]
    going = [
        ("значок «в очереди задач»", ".mantine-Badge-label:text-is('в очереди задач')", norm),
        ("шапка прогона", "p:has-text('Язык писем')", norm),
        ("ход прогона", "[aria-label='Ход прогона']", norm),
        ("пульс прогона", "[aria-label='Ход прогона'] span", norm),
        ("плашка «некому взять»", ".mantine-Alert-message:has-text('служба долгих')", norm),
        ("подпись «Ещё не судили»", ".metricTile p:text-is('Ещё не судили')", norm),
    ]
    return {
        "sales-collect": {"path": path, "ready": ready, "probes": probes},
        "sales-collect-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": [*probes[1:13], *_spend_probes(norm)],
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
    }


def _spend_probes(norm: float) -> list[tuple[str, str, float]]:
    """Режим выдачи, ключи сметы и прогона, расход рядом со сметой (проверка прода 14.10): смета в таблице
    расхода и пометка о Hunter — приглушённым текстом, и он тоже меряется."""
    form, runs = "section[aria-label='Новый прогон']", "section[aria-label='Прогоны гипотезы']"
    return [
        ("подпись поля «Выдача»", ".mantine-InputWrapper-label:text-is('Выдача')", norm),
        ("взятый ключ сметы", f"{form} ul[aria-label^='Взято ключей'] li", norm),
        ("отсеянный ключ сметы", f"{form} ul[aria-label^='Отсеяно ключей'] li", norm),
        ("ключ прогона", f"{runs} ul[aria-label^='Взято ключей'] li", norm),
        ("статья расхода", ".segmentSpend tbody td:nth-child(1) p", norm),
        ("факт расхода", ".segmentSpend tbody td:nth-child(2) p", norm),
        ("смета в расходе", ".segmentSpend tbody td:nth-child(3) p", norm),
        ("пометка о Hunter", "section[aria-label='Расход прогона'] > p:last-child", norm),
    ]


def segment_prepare() -> dict[str, Any]:
    """Подготовка экранов сбора до замера: смета посчитана."""
    return {"sales-collect": collect_prepare, "sales-collect-phone": collect_prepare}
