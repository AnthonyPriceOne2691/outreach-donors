"""Отношение контраста по пикселям — без браузера и без Pillow.

Это та часть замера `ui_contrast.py`, которую проверяет тест в CI
(`tests/test_ui_contrast_ratio.py`): вырез вокруг элемента, деление его
пикселей на буквы и фон, ядро буквы у мелкого текста и решение, когда
точку ниже нормы перемерить внутри элемента. Снимок экрана и координаты
элемента остаются в `ui_contrast.py`: им нужны браузер и Pillow, а в CI
ставятся только зависимости `dev`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

#: Снимок — с двойной плотностью: пиксель браузера — два пикселя снимка.
SCALE = 2

#: Вырез — столько пикселей браузера вокруг элемента: у букв должен быть фон.
PAD = 6

#: Запас внутрь сверх рамки и полей при перемере внутри элемента. Без него
#: у поля в вырез «внутри» всё равно попадали строка-две рамки: координаты
#: дробные, рамка сглажена, а у поля-пилюли скруглённый угол заходит за
#: поля. Одна строка рамки во всю ширину поля — сотни серых пикселей, и имя
#: файла в поле намерилось 3,34 при 13,5 в тёмной теме (20.10.2026).
MARGIN = 2

Box = dict[str, float]
Pixel = tuple[int, ...]


def lum(px: Pixel) -> float:
    def ch(value: int) -> float:
        c = value / 255
        return float(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)

    return 0.2126 * ch(px[0]) + 0.7152 * ch(px[1]) + 0.0722 * ch(px[2])


def _otsu(hist: list[int], total: int) -> int:
    """Порог, делящий пиксели на две группы — чернила и фон: номер корзины,
    последней в тёмной группе.

    Вынесено из замера отдельной функцией не для красоты: вместе они
    давали функцию сложности 11 при потолке 10, и ратчет сложности
    отказывался её принимать.

    **Порог — корзиной, а не яркостью** (20.10.2026, находка ревью). Раньше
    здесь возвращалось `корзина / 255`, а пиксели делились по `яркость <=
    порог`: пиксели самой пороговой корзины, которую Оцу считал тёмной,
    уходили к фону. Максимум межклассовой дисперсии — плато через пустые
    корзины между группами, и первым он встаёт ровно на последней тёмной
    корзине — к фону уходили самые светлые чернила. Буквы одного цвета без
    сглаживания уходили к фону целиком, и замер говорил «не измерено».
    """
    best, threshold = -1.0, 128
    sum_all = sum(i * hist[i] for i in range(256))
    sum_b = weight_b = 0
    for i in range(256):
        weight_b += hist[i]
        if weight_b in (0, total):
            continue
        sum_b += i * hist[i]
        mean_b = sum_b / weight_b
        mean_f = (sum_all - sum_b) / (total - weight_b)
        between = weight_b * (total - weight_b) * (mean_b - mean_f) ** 2
        if between > best:
            best, threshold = between, i
    return threshold


def rect_of(box: Box, pad: float) -> tuple[int, int, int, int]:
    """Вырез в пикселях снимка: коробка элемента и `pad` пикселей браузера вокруг."""
    return (
        int((box["x"] - pad) * SCALE),
        int((box["y"] - pad) * SCALE),
        int((box["x"] + box["width"] + pad) * SCALE),
        int((box["y"] + box["height"] + pad) * SCALE),
    )


def ratio_of(pixels: Iterable[Pixel], core: bool = False, width: int | None = None) -> float | None:
    """Отношение контраста между буквами и фоном под ними — по пикселям выреза.
    `None` — измерить не удалось: в куске одна краска. `width` — ширина куска
    в пикселях (пиксели — построчно): по ней видно, какая группа держит его
    кромку, то есть где фон (`_ground_is_dark`).

    Пиксели куска делятся на две группы порогом Оцу и сравниваются их
    средние. Простая медиана здесь врёт: на тесном куске, где буквы
    занимают половину площади, она попадает в сами буквы, и текст 96 %
    яркости над тёмным стеклом показывал 4.4 : 1 вместо двенадцати.
    На просторном куске врёт min/max: сглаживание даёт одиночные пиксели
    темнее текста.
    """
    lums = [lum(p) for p in pixels]
    bins = [min(255, int(value * 255)) for value in lums]
    hist = [0] * 256
    for one in bins:
        hist[one] += 1

    edge = _otsu(hist, len(lums))
    is_dark = [one <= edge for one in bins]
    dark = [v for v, inked in zip(lums, is_dark, strict=True) if inked]
    light = [v for v, inked in zip(lums, is_dark, strict=True) if not inked]
    if not dark or not light:
        # Мерить нечего: кусок однородный. Так выглядит элемент, уехавший
        # за край снимка, и выключенный — серое на сером. Раньше здесь
        # стояло 21.0, и оба случая печатались как безупречный результат.
        return None
    return _ratio(dark, light, core, _ground_is_dark(is_dark, width))


def _ground_is_dark(is_dark: list[bool], width: int | None) -> bool | None:
    """Фон — тёмная группа? Решает кромка куска: буквы окружены фоном, и его пиксели
    держат первую и последнюю строку и столбец. `None` — ширины нет или кромка
    поделена поровну: тогда чернила — меньшая группа, как раньше.

    Зачем (20.10.2026, ревью): в тесной коробке крупного жирного текста — так бывает
    при перемере внутри элемента (`measure`) — буквы могут занять больше половины
    пикселей, и «меньшая группа» оказывалась фоном: ядро бралось у фона, и точка
    ниже нормы могла «спастись» чужим числом. По числу пикселей «65 % букв на 35 %
    фона» не отличить от «35 % букв на 65 % фона» — различает только геометрия.
    """
    if not width or len(is_dark) < 2 * width:
        return None
    rows = len(is_dark) // width
    rim = [
        inked
        for index, inked in enumerate(is_dark[: rows * width])
        if index < width or index >= (rows - 1) * width or index % width in (0, width - 1)
    ]
    darks = sum(rim)
    if darks * 2 == len(rim):
        return None
    return darks * 2 > len(rim)


def _ratio(
    dark: list[float], light: list[float], core: bool, ground_is_dark: bool | None = None
) -> float:
    """Отношение групп: средних — или ядра чернил к среднему фона.

    Чернила — группа внутри, фон — на кромке куска (`_ground_is_dark`); кромки не
    знаем — чернила меньшая группа: в куске с полями вокруг текста фона всегда
    больше, чем букв. Ядро — треть пикселей чернил, дальше всех от фона.
    """
    if core:
        if ground_is_dark is None:
            ink, ground = (dark, light) if len(dark) <= len(light) else (light, dark)
        else:
            ink, ground = (light, dark) if ground_is_dark else (dark, light)
        ordered = sorted(ink, reverse=ink is light)
        part = ordered[: max(1, len(ordered) // 3)]
        a, b = sum(part) / len(part), sum(ground) / len(ground)
    else:
        a, b = sum(dark) / len(dark), sum(light) / len(light)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def inside(box: Box, insets: Sequence[float]) -> Box | None:
    """Коробка, на которой лежат буквы: элемент без рамки, полей и запаса.
    `insets` — рамка с полями сверху, справа, снизу и слева. `None` — от
    элемента ничего не осталось."""
    top, right, bottom, left = (side + MARGIN for side in insets)
    width, height = box["width"] - left - right, box["height"] - top - bottom
    if width < 2 or height < 2:
        return None
    return {"x": box["x"] + left, "y": box["y"] + top, "width": width, "height": height}


def measure(
    read: Callable[[Box, float], float | None],
    box: Box,
    insets: Callable[[], Sequence[float]],
    norm: float,
) -> tuple[float | None, str]:
    """Отношение у точки и пометка к нему: ниже нормы по вырезу — перемер внутри.

    `read(коробка, вырез)` — отношение в куске снимка; `insets()` — рамка и поля
    элемента, их спрашивают у браузера только у точки ниже нормы. Внутри тоже
    ниже нормы — остаётся число по вырезу и «МАЛО»: перемер ничего не прячет.
    """
    value = read(box, PAD)
    if value is None or value >= norm:
        return value, ""
    inner = inside(box, insets())
    again = None if inner is None else read(inner, 0)
    if again is None or again < norm:
        return value, ""
    return again, f"  внутри; по вырезу {value:.2f}"
