"""«Прогон» → «Собрать моделью» → «По примерам сайтов» — для замера контраста.

Часть каталога `ui_screens.py`, своим модулем: правило «новый элемент экрана меряется
в обеих темах» (docs/UI_RULES.md), а основной каталог стоит у предела длины файла.

Мерится то, по чему решают: основа сборки, поле примеров с подписью и счётом, тема
примеров, тема ниши на языке рынка, каждый пример с исходом и токены модели.

**Ответ сборки подменяется** (`page.route`): настоящая сборка открыла бы чужие главные
и потратила бы модель ради замера цвета. Примеры — синтетические (`.example.test`);
мерится вид итога, а не сама сборка — её проверяют тесты (`tests/test_api_keywords_examples.py`).

Нормы передаются параметрами, а не импортируются из `ui_screens`: тот подключает этот
модуль, и обратный импорт замкнул бы круг.
"""

import json
from typing import Any

from playwright.sync_api import expect

#: Итог сборки по примерам — по своей подписи.
OUTCOME = "[aria-label='Итог сборки по примерам']"

#: Подмена ответа сборки: два примера прочитаны (один — не про общую тему), один — нет.
BUILT = {
    "keywords": ["home gardening blog", "balcony vegetable garden tips"],
    "languages": ["English"],
    "asked": 12,
    "received": 10,
    "rejected": 0,
    "near_duplicates": 1,
    "refusals": [],
    "tokens": 3412,
    "model": "gpt-5",
    "theme": "блоги о домашнем садоводстве",
    "topics": {"English": "home gardening"},
    "sites": [
        {"host": "garden.example.test", "read": True, "reason": "", "title": "Garden friends",
         "off_theme": False},
        {"host": "recipes.example.test", "read": True, "reason": "", "title": "Weeknight dinners",
         "off_theme": True},
        {"host": "gone.example.test", "read": False,
         "reason": "не открылся: имя не найдено или соединение отклонено", "title": "",
         "off_theme": False},
    ],
    "repeats": 0,
    "theme_tokens": 1210,
}  # fmt: skip


def build_from_examples(page: Any) -> None:
    """Открыть «По примерам сайтов», вписать примеры и собрать — с подменой ответа."""
    page.route(
        "**/api/keywords/examples",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(BUILT, ensure_ascii=False),
        ),
    )
    # Кликается подпись, а не роль: у Mantine сам радио-вход спрятан (`estimate`).
    page.locator(".mantine-SegmentedControl-label", has_text="Собрать моделью").click()
    page.locator(".mantine-SegmentedControl-label", has_text="По примерам сайтов").click()
    page.get_by_label("Примеры сайтов").fill(
        "garden.example.test\nrecipes.example.test\ngone.example.test"
    )
    page.get_by_role("button", name="Собрать по примерам").click()
    expect(page.locator(OUTCOME)).to_be_visible()
    page.wait_for_timeout(900)


def examples_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Экраны и точки. `norm` — норма обычного текста, `big` — крупного."""
    return {
        "run-examples": {
            "path": "/run",
            "ready": ("button", "Посчитать смету"),
            "probes": [
                # Внутренний span сегмента, а не рамка: рамка шире текста (22.09.2026).
                (
                    "выбранная основа сборки",
                    ".mantine-SegmentedControl-innerLabel:has-text('По примерам сайтов')",
                    norm,
                ),
                (
                    "невыбранная основа",
                    ".mantine-SegmentedControl-innerLabel:has-text('По теме')",
                    norm,
                ),
                (
                    "подпись поля примеров",
                    ".mantine-InputWrapper-label:text-is('Примеры сайтов')",
                    norm,
                ),
                (
                    "описание поля примеров",
                    ".mantine-InputWrapper-description:has-text('Адрес или домен')",
                    norm,
                ),
                ("счёт под полем", "p:has-text('сайта из 10')", norm),
                ("тема примеров", f"{OUTCOME} p:has-text('Тема примеров:')", norm),
                ("тема ниши на языке рынка", f"{OUTCOME} p:has-text('Ключи — про')", norm),
                ("прочитанный пример", f"{OUTCOME} li >> nth=0", norm),
                ("не прочитанный пример", f"{OUTCOME} li:has-text('не прочитан')", norm),
                ("токены модели", f"{OUTCOME} p:has-text('Модель')", norm),
                ("кнопка «Собрать по примерам»", "button:has-text('Собрать по примерам')", big),
            ],
        },
    }


def examples_prepare() -> dict[str, Any]:
    """Подготовка экрана перед замером: без неё блока итога на экране нет."""
    return {"run-examples": build_from_examples}
