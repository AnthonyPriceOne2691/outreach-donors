"""Порог Оцу в замере контраста — корзиной, а не яркостью; фон — группа на кромке куска.

Находка ревью 20.10.2026 (`scripts/ui_contrast_ratio.py`): порог возвращался `корзина / 255`,
а пиксели делились по `яркость <= порог` — пиксели пороговой корзины, тёмной для Оцу, уходили к
фону. Буквы одного цвета без сглаживания уходили целиком, и замер говорил «не измерено». И второе:
у мелкого текста чернилами считалась меньшая группа — в тесной коробке крупных букв ею оказывался
фон. Куски здесь собираются из пикселей руками — без браузера и Pillow, тем же кодом, что у
`scripts/ui_contrast.py`.
"""

from __future__ import annotations

import pytest
from scripts.ui_contrast_ratio import _ground_is_dark, _otsu, lum, ratio_of

Colour = tuple[int, int, int]


def wcag(a: Colour, b: Colour) -> float:
    """Отношение WCAG двух цветов — то, что замер обязан вернуть у куска из двух красок."""
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def cut(*parts: tuple[Colour, int]) -> list[Colour]:
    """Кусок снимка без геометрии: каждая краска — столько раз, сколько сказано."""
    return [colour for colour, count in parts for _ in range(count)]


@pytest.mark.parametrize("core", [False, True], ids=["средним", "по ядру"])
@pytest.mark.parametrize(
    ("letters", "ground"),
    [((25, 30, 35), (250, 250, 250)), ((230, 230, 230), (20, 24, 28))],
    ids=["светлая тема", "ровный фон тёмной темы"],
)
def test_one_colour_letters_are_measured(letters: Colour, ground: Colour, core: bool) -> None:
    """Буквы одной краской на ровном фоне: две корзины — и ровно отношение этих красок, а не
    «не измерено». До правки пороговая корзина уходила к фону, и тёмная группа пустела."""
    value = ratio_of(cut((ground, 900), (letters, 100)), core)

    assert value == pytest.approx(wcag(letters, ground))


def test_threshold_bin_is_ink() -> None:
    """Пиксели пороговой корзины — в тёмной группе, как их посчитал Оцу: чернила в трёх корзинах
    подряд, фон — в двух. Отношение — средних групп целиком."""
    ink = cut(((10, 10, 10), 30), ((14, 14, 14), 30), ((18, 18, 18), 30))
    ground = cut(((240, 240, 240), 400), ((244, 244, 244), 400))
    dark, light = [lum(p) for p in ink], [lum(p) for p in ground]
    expected = (sum(light) / len(light) + 0.05) / (sum(dark) / len(dark) + 0.05)

    assert ratio_of(ink + ground) == pytest.approx(expected)


def test_otsu_returns_last_dark_bin() -> None:
    """Порог — номер последней тёмной корзины: на плато через пустые корзины встаёт первым."""
    hist = [0] * 256
    hist[20], hist[200] = 10, 90

    assert _otsu(hist, 100) == 20


#: Тесная коробка крупных букв: кромка — фон, внутри буквы — 2/3 пикселей. Буквы — тёмным
#: ядром и сглаженным краем, фон ровный.
WIDTH, HEIGHT = 20, 12
GROUND: Colour = (250, 250, 250)
CORE: Colour = (20, 20, 20)
SOFT: Colour = (110, 110, 110)


def tight_box() -> list[Colour]:
    """Пиксели построчно: кромка в 1 px — фон; внутри — буквы ядром и краем, просветы фона."""
    rows: list[Colour] = []
    for y in range(HEIGHT):
        for x in range(WIDTH):
            rim = y in (0, HEIGHT - 1) or x in (0, WIDTH - 1)
            gap = (x + y) % 5 == 0
            rows.append(GROUND if rim or gap else CORE if x % 3 else SOFT)
    return rows


def test_tight_box_letters_outnumber_ground() -> None:
    """Сама постановка: букв больше половины — «меньшая группа» здесь фон."""
    pixels = tight_box()
    letters = sum(1 for p in pixels if p != GROUND)

    assert letters * 2 > len(pixels)


def test_ground_is_taken_from_the_rim() -> None:
    """С шириной куска чернила — группа внутри, а не меньшая: ядро — у букв, отношение — ядра
    букв к фону. Без ширины — прежнее правило, ядро бралось у фона и давало другое число."""
    pixels = tight_box()
    letters = [lum(p) for p in pixels if p != GROUND]
    ordered = sorted(letters)
    part = ordered[: len(ordered) // 3]
    by_letters = (lum(GROUND) + 0.05) / (sum(part) / len(part) + 0.05)

    assert ratio_of(pixels, core=True, width=WIDTH) == pytest.approx(by_letters)
    assert ratio_of(pixels, core=True) != pytest.approx(by_letters)


@pytest.mark.parametrize(
    ("rim_dark", "expected"),
    [(True, True), (False, False)],
    ids=["тёмная кромка — фон тёмный", "светлая кромка — фон светлый"],
)
def test_rim_decides_ground(rim_dark: bool, expected: bool) -> None:
    """Кромка куска — фон, какой бы группы он ни был: тёмная тема и светлая."""
    marks = [
        rim_dark if (y in (0, 3) or x in (0, 5)) else not rim_dark
        for y in range(4)
        for x in range(6)
    ]

    assert _ground_is_dark(marks, 6) is expected


def test_no_width_or_split_rim_keeps_old_rule() -> None:
    """Ширины нет или кромка поделена поровну — `None`: чернила — меньшая группа, как раньше."""
    assert _ground_is_dark([True, False] * 8, None) is None
    split = [y < 2 for y in range(4) for _ in range(4)]

    assert _ground_is_dark(split, 4) is None


@pytest.mark.parametrize("colour", [(250, 250, 250), (20, 24, 28)], ids=["светлый", "тёмный"])
def test_one_colour_cut_is_still_not_measured(colour: Colour) -> None:
    """Кусок одной краски — «не измерено», как и до правки: порогом быть нечему."""
    assert ratio_of([colour] * 400, core=True, width=20) is None
