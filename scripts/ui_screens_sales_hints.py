"""Подсказки раздела продаж для замера контраста: «i» у заголовков и у подписей плиток.

С аудита экранов 09.10.2026 пояснения вкладок — одной строкой, остальное в «i»: текст
живёт в подсказке (плотное стекло поверх содержимого), и мерить его можно, только
открыв её. Подсказка открывается фокусом — так же, как с клавиатуры; точка подсказки
в каталоге стоит последней: открытая, она легла бы поверх следующих точек экрана.

Своим модулем: каталог продаж (`ui_screens_sales.py`) стоит у предела длины файла.
"""

from collections.abc import Callable
from typing import Any

#: Подсказка «i» — плотное стекло в слое поверх страницы.
TOOLTIP = ".mantine-Tooltip-tooltip"


def open_hint(name: str) -> Callable[[Any], None]:
    """Шаг перед точкой: фокус на «i» с этим именем — подсказка открыта."""

    def show(page: Any) -> None:
        page.get_by_role("button", name=name).first.focus()
        page.locator(TOOLTIP).first.wait_for()
        page.wait_for_timeout(300)

    return show


def hint_probe(what: str, name: str, norm: float) -> tuple[str, str, float, Callable[[Any], None]]:
    """Точка замера текста подсказки «i»: имя точки, вырезка, норма и шаг, который её открывает."""
    return (f"подсказка «i»: {what}", TOOLTIP, norm, open_hint(name))
