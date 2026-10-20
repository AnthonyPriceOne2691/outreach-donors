"""Замер контраста текста: перемер внутри элемента — на снимке, нарисованном в тесте.

С 20.10.2026 у поля и обычной кнопки на светлой панели кромка из чернил, и она
попадает в вырез 6 px вокруг элемента: порог Оцу кладёт её к буквам, и значение
фильтра с теми же буквами намерилось 3,58 вместо 16,58. Здесь снимок рисуется
руками — панель, поле с рамкой в 1 px и буквы-штрихи, у рамки и букв сглаживание,
как на снимке экрана, — и меряется тем же кодом, что у `scripts/ui_contrast.py`:
браузер и Pillow для этого не нужны.
"""

from __future__ import annotations

import pytest
from scripts.ui_contrast_ratio import MARGIN, PAD, SCALE, inside, measure, ratio_of, rect_of

NORM = 4.5
Colour = tuple[int, int, int]
PANEL: Colour = (250, 250, 250)
FILL: Colour = (240, 242, 243)
#: Кромка из чернил — 42 % чернил на белом, как у поля на светлой панели, — и строка
#: сглаживания с внутренней стороны: рамка стоит на дробной координате.
INK_EDGE: tuple[Colour, Colour] = ((150, 160, 165), (196, 202, 205))
#: Прежняя кромка — белая: на светлой панели её почти не видно.
WHITE_EDGE: tuple[Colour, Colour] = ((252, 252, 252), (246, 247, 248))
#: Буквы и их сглаженный край.
INK: tuple[Colour, Colour] = ((25, 30, 35), (120, 125, 130))
#: Бледные буквы: на заливке поля — около 2 : 1, ниже нормы и без всякой рамки.
PALE: tuple[Colour, Colour] = ((165, 165, 165), (200, 200, 200))

#: Поле в пикселях браузера: широкое, значение короткое — как фильтр «все».
FIELD = {"x": 20.0, "y": 20.0, "width": 300.0, "height": 36.0}
#: Рамка 1 px со всех сторон и поля 12 px слева и справа — как у поля Mantine.
INSETS = (1.0, 13.0, 1.0, 13.0)

Snapshot = list[list[Colour]]


def snapshot(edge: tuple[Colour, Colour], letters: tuple[Colour, Colour]) -> Snapshot:
    """Снимок в пикселях снимка: панель, на ней поле с рамкой в 1 px и восемь букв-штрихов
    1 × 7 px через 4 px после левого поля."""
    width = int((2 * FIELD["x"] + FIELD["width"]) * SCALE)
    height = int((2 * FIELD["y"] + FIELD["height"]) * SCALE)
    grid = [[PANEL] * width for _ in range(height)]
    x0, y0, x1, y1 = rect_of(FIELD, 0)
    ring, ring_soft = edge
    for y in range(y0, y1):
        for x in range(x0, x1):
            depth = min(x - x0, x1 - 1 - x, y - y0, y1 - 1 - y)
            grid[y][x] = ring if depth < SCALE else ring_soft if depth == SCALE else FILL
    stroke, stroke_soft = letters
    top = (y0 + y1) // 2 - 7 * SCALE // 2
    for n in range(8):
        left = x0 + (14 + 4 * n) * SCALE
        for y in range(top, top + 7 * SCALE):
            grid[y][left - 1] = grid[y][left + SCALE] = stroke_soft
            for x in range(left, left + SCALE):
                grid[y][x] = stroke
    return grid


def reader(grid: Snapshot, core: bool, calls: list[float] | None = None):
    """`read` замера по нарисованному снимку: вырез — тем же `rect_of`, что у снимка экрана."""

    def read(box: dict[str, float], pad: float) -> float | None:
        if calls is not None:
            calls.append(pad)
        x0, y0, x1, y1 = rect_of(box, pad)
        return ratio_of((px for row in grid[y0:y1] for px in row[x0:x1]), core)

    return read


@pytest.mark.parametrize("core", [False, True], ids=["средним", "по ядру"])
def test_ink_edge_in_cut_is_not_letters(core: bool) -> None:
    """(а) Кромка из чернил в вырезе роняет замер ниже нормы, а буквы прежние: внутри
    поля — норма, печатается число внутри и рядом — по вырезу."""
    read = reader(snapshot(INK_EDGE, INK), core)
    cut = read(FIELD, PAD)
    assert cut is not None
    assert cut < NORM

    value, where = measure(read, FIELD, lambda: INSETS, NORM)

    assert value is not None
    # Средним сглаженный край буквы — в чернилах: порог Оцу — корзиной (20.10.2026), а штрих
    # этого снимка наполовину из края, и среднее чернил ниже ядра. Спасает оба замера.
    assert value >= (3 * NORM if core else NORM)
    assert value > cut
    assert where == f"  внутри; по вырезу {cut:.2f}"


@pytest.mark.parametrize("edge", [INK_EDGE, WHITE_EDGE], ids=["кромка чернилами", "белая кромка"])
def test_pale_letters_stay_low(edge: tuple[Colour, Colour]) -> None:
    """(б) Бледные буквы ниже нормы и внутри поля: остаётся число по вырезу, без
    пометки — «МАЛО» перемер не прячет."""
    read = reader(snapshot(edge, PALE), core=True)
    cut = read(FIELD, PAD)
    assert cut is not None
    assert cut < NORM

    value, where = measure(read, FIELD, lambda: INSETS, NORM)

    assert (value, where) == (cut, "")


def test_point_in_norm_is_not_remeasured() -> None:
    """(в) Точка в норме по вырезу — один замер; рамку и поля у браузера не спрашивают."""
    calls: list[float] = []
    read = reader(snapshot(WHITE_EDGE, INK), core=True, calls=calls)

    def insets() -> tuple[float, ...]:
        raise AssertionError("точку в норме не перемеряют")

    value, where = measure(read, FIELD, insets, NORM)

    assert value is not None
    assert value >= NORM
    assert (where, calls) == ("", [PAD])


def test_inside_drops_border_padding_and_margin() -> None:
    """Внутри — без рамки, полей и запаса со всех сторон; от узкого элемента — ничего."""
    assert inside(FIELD, INSETS) == {
        "x": FIELD["x"] + 13 + MARGIN,
        "y": FIELD["y"] + 1 + MARGIN,
        "width": FIELD["width"] - 2 * (13 + MARGIN),
        "height": FIELD["height"] - 2 * (1 + MARGIN),
    }
    assert inside({"x": 0, "y": 0, "width": 30, "height": 8}, INSETS) is None


def test_nothing_left_inside_keeps_cut() -> None:
    """Элемент уже рамки с полями — перемерить нечего: остаётся число по вырезу."""
    read = reader(snapshot(INK_EDGE, INK), core=True)
    cut = read(FIELD, PAD)

    value, where = measure(read, FIELD, lambda: (20.0, 160.0, 20.0, 160.0), NORM)

    assert (value, where) == (cut, "")


def test_one_colour_cut_is_not_measured() -> None:
    """Однородный кусок — не измерено (`None`), а не 21 : 1."""
    assert ratio_of([FILL] * 400, core=True) is None
