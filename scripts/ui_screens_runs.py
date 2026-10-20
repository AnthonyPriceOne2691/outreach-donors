"""Экран прогона, вход «Конкуренты», для замера контраста — на широком окне и на телефоне.

Часть каталога `ui_screens.py`, вынесенная своим модулем, как экраны отчётов: основной
каталог стоит у предела длины файла. Элементы этого входа существуют только в нём и только
после сметы — подготовка переключает вход, вписывает двух конкурентов и считает смету: без
неё «Запустить» выключена, а у выключенной меряется серое на сером.

**Ответ сметы — подменой** (`page.route`): замер не спрашивает Ahrefs об остатке ключа и не
зависит от того, сколько его на стенде, — плашка «помещается» и плашка потолка модели
выходят всегда одни и те же. Остальные ответы — стенда.

Нормы передаются параметрами, а не импортируются из `ui_screens`: тот подключает этот
модуль, и обратный импорт замкнул бы круг.
"""

from typing import Any

from playwright.sync_api import Page, Route, expect

#: Конкуренты для сметы — RFC 2606: смета ничего не тратит, но домены в замере — не чужие.
RIVALS = ("example.com", "example.net")
#: Смета, которая помещается: поля — как у `CompetitorForecast` сервера, числа выдуманы.
ESTIMATE: dict[str, Any] = {
    "lists": [{"target": rival, "rows": 100, "units": 1400} for rival in RIVALS],
    "depth": 100,
    "row_units": 14,
    "units_lists": 2800,
    "expected_domains": 200,
    "units_screen": 400,
    "units_metrics": 5600,
    "units_by_country": 11000,
    "units_total": 19800,
    "tokens": 360000,
    "judge_mode": "shadow",
    "topic_tokens": 400000,
    "topic_tokens_per_site": 2000,
    "topic_measured": False,
    "llm_refusal": None,
    "units_left": 100000,
    "units_cap": 100000,
    "units_spent_in_cycle": 6416,
    "cap_left": 93584,
    "cycle_start": "2026-10-01T00:00:00+00:00",
    "cycle_by_key": True,
    "cap_unknown": None,
    "next_reset": "2026-11-01T00:00:00+00:00",
    "run_ceiling": None,
    "budget": 93584,
    "affordable": True,
    "shortfall": 0,
    "binding": "cap",
    "short_of": [],
}
#: Суд сайтов не помещается в потолок модели на прогон — словами сервера.
MODEL_CAPPED: dict[str, Any] = {
    **ESTIMATE,
    "llm_refusal": (
        "Сайты списков обойдутся модели до 760 000 токенов, а потолок модели на прогон — "
        "100 000: прогон встал бы посреди суда, когда списки уже оплачены. Или уберите "
        "конкурента, или уменьшите глубину списка — или попросите администратора поднять потолок"
    ),
}


def _estimate(page: Page, forecast: dict[str, Any]) -> None:
    """Вход «Конкуренты», два домена и смета с подменённым ответом.

    Кликается подпись сегмента, а не роль: у Mantine радио-вход спрятан, и playwright по
    роли ждёт видимости до таймаута. Поле доменов — видимое из двух с одной подписью.
    """

    def answer(route: Route) -> None:
        route.fulfill(json=forecast)

    page.route("**/api/runs/competitors/estimate", answer)
    page.locator(".mantine-SegmentedControl-label", has_text="Конкуренты").click()
    field = page.locator('input[data-type="visible"]').first
    for rival in RIVALS:
        field.fill(rival)
        page.keyboard.press("Enter")
    page.get_by_role("button", name="Посчитать смету").click()
    expect(page.get_by_text("Списки конкурентов")).to_be_visible()
    page.wait_for_timeout(900)


def rivals_estimate(page: Page) -> None:
    """Смета помещается — до ожившей кнопки «Запустить»."""
    _estimate(page, ESTIMATE)


def rivals_model_capped(page: Page) -> None:
    """Суд не помещается в потолок модели — красная плашка, кнопка выключена."""
    _estimate(page, MODEL_CAPPED)


def _probes(norm: float, big: float) -> list[tuple[str, str, float]]:
    return [
        ("выбранный вход", ".mantine-SegmentedControl-innerLabel:has-text('Конкуренты')", norm),
        ("невыбранный вход", ".mantine-SegmentedControl-innerLabel:has-text('Собрать')", norm),
        ("подпись поля конкурентов", "label:has-text('Сайты конкурентов')", norm),
        ("пояснение поля", "p:has-text('Enter или запятая')", norm),
        ("домен в поле", ".mantine-TagsInput-pill", norm),
        ("строка о списках", "p:has-text('Возьмём сайты')", norm),
        ("пояснение глубины", "p:has-text('Сильнейшие по DR')", norm),
        ("подпись плитки сметы", ".metricTitle:has-text('Списки конкурентов')", norm),
        ("число плитки", ".metricTile:has-text('Списки конкурентов') .metricTitle + p", big),
        ("пояснение плитки", ".metricHint:has-text('юн. за строку')", norm),
        ("строка модели", "p:has-text('Судья площадок')", norm),
        ("плашка сметы", ".mantine-Alert-body", norm),
        ("кнопка «Запустить»", "button:has-text('Запустить')", big),
    ]


def _capped_probes(norm: float) -> list[tuple[str, str, float]]:
    capped = ".mantine-Alert-root:has-text('потолок модели')"
    return [
        ("заголовок плашки модели", f"{capped} .mantine-Alert-title", norm),
        ("слова плашки модели", f"{capped} .mantine-Alert-message", norm),
    ]


def run_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Экраны и точки. `norm` — норма обычного текста, `big` — крупного."""
    screen = {"path": "/run", "ready": ("button", "Посчитать смету"), "probes": _probes(norm, big)}
    phone = {"viewport": {"width": 390, "height": 844}}
    capped = {**screen, "probes": _capped_probes(norm)}
    return {
        "run-competitors": screen,
        # На телефоне три входа стоят столбиком (`RunPage`, `segments`), поля — по одному
        # в строке, плитки сметы — по две: текст ложится на другие места полотна.
        "run-competitors-phone": {**screen, **phone},
        "run-competitors-model": capped,
        "run-competitors-model-phone": {**capped, **phone},
    }


def run_prepare() -> dict[str, Any]:
    return {
        "run-competitors": rivals_estimate,
        "run-competitors-phone": rivals_estimate,
        "run-competitors-model": rivals_model_capped,
        "run-competitors-model-phone": rivals_model_capped,
    }
