"""Под курсором ничего не сдвигается — проверка наведением на каждый элемент.

Замечание 25.09.2026: «при наведении на любую кнопку она как будто
подпрыгивает — убрать; проверь абсолютно все кнопки». Подъём был на один
пиксель: глазом его видно только как дрожь, а на снимке не видно вовсе.
Поэтому проверка не смотрит, а меряет: на каждом экране курсор наводится
на каждую видимую кнопку, ссылку, пункт меню, вкладку, значок и плитку,
и рамка элемента и его `transform` сверяются до и после наведения.
Ничего не нажимается — экран только читается.

Запуск (сервер и фронт подняты, учётка заведена командой):

    python scripts/ui_hover.py ivan@site.com "три слова подряд"
    python scripts/ui_hover.py ivan@site.com "пароль" http://localhost:5174 --path /runs/18/review

Без `--path` обходятся все разделы меню. Код выхода 1, если хоть что-то
сдвинулось, — и строка на каждый такой элемент. Элемент, который исчез
из разметки, пока на него наводили (открылся поповер, строка обновилась),
печатается «НЕ ПРОВЕРЕН» — отдельным счётом, а не молча пропуском:
проверка, которая не сказала, чего не проверила, выглядит как «всё в порядке».

**Проверка проверена порчей.** С подложенным старым правилом
(`translateY(-1px)` на наведение кнопки) она находит «Выйти»,
«Отклонить», «Рассмотреть 394»; на исправленном коде — ноль из 2000.
"""

import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, expect, sync_playwright

#: Разделы меню и экран, куда попадают с главной. Новый раздел — новая строка.
PATHS = [
    "/",
    "/run",
    "/donors",
    "/selection",
    "/forms",
    "/advertisers",
    "/letters",
    "/threads",
    "/suppressions",
    "/settings",
    "/usage",
    "/senders",
    "/users",
]

#: Всё, что отвечает на курсор. Значки — потому что часть из них фильтры.
TARGETS = (
    "button, a, [role=button], [role=tab], [role=radio], .mantine-NavLink-root, "
    ".mantine-Pagination-control, .mantine-SegmentedControl-label, .mantine-Badge-root, "
    ".liftable, .metricLink"
)

GEOMETRY = """el => {
  const r = el.getBoundingClientRect();
  return {x: r.x, y: r.y + window.scrollY, w: r.width, h: r.height,
          t: getComputedStyle(el).transform};
}"""

#: `transform` без сдвига: браузер отдаёт его то словом, то единичной матрицей.
STILL = {"none", "matrix(1, 0, 0, 1, 0, 0)"}

#: Сдвиг меньше сотой пикселя — округление раскладки, а не движение.
EPS = 0.01

#: Пауза после наведения — дольше самого долгого перехода кнопки (160 мс).
SETTLE_MS = 260


def moved(before: dict, after: dict) -> bool:
    shifted = any(abs(before[key] - after[key]) > EPS for key in ("x", "y", "w", "h"))
    return shifted or after["t"] not in STILL


def label(el: Locator) -> str:
    text = el.evaluate("e => e.innerText || e.getAttribute('aria-label') || e.className")
    return " ".join(str(text).split())[:60]


def sweep(page: Page, path: str) -> tuple[int, list[str], list[str]]:
    """Навести курсор на каждый видимый элемент экрана.

    Возвращает счёт наведённых, сдвинутые и не проверенные.
    """
    found: list[str] = []
    missed: list[str] = []
    checked = 0
    targets = page.locator(TARGETS)
    for index in range(targets.count()):
        el = targets.nth(index)
        try:
            if not el.is_visible():
                continue
            el.scroll_into_view_if_needed(timeout=2000)
            # Курсор уводится в угол, чтобы «до» мерилось без наведения.
            page.mouse.move(1, 1)
            page.wait_for_timeout(60)
            before = el.evaluate(GEOMETRY)
            el.hover(force=True, timeout=2000)
            page.wait_for_timeout(SETTLE_MS)
            after = el.evaluate(GEOMETRY)
        except PlaywrightError as exc:
            # Сообщается сразу и ещё раз в итоге: элемент, ушедший из разметки,
            # не должен выглядеть проверенным.
            line = f"  {path}: элемент №{index} НЕ ПРОВЕРЕН — {str(exc).splitlines()[0]}"
            print(line, flush=True)
            missed.append(line)
            continue
        checked += 1
        if moved(before, after):
            dy = after["y"] - before["y"]
            found.append(f"  {path}: «{label(el)}» сдвиг {dy:+.2f} px, transform {after['t']}")
    return checked, found, missed


USAGE = "Запуск: python scripts/ui_hover.py <почта> <пароль> [адрес фронта] [--path /путь]"


def main(argv: list[str]) -> int:
    rest = list(argv)
    paths = PATHS
    if "--path" in rest:
        at = rest.index("--path")
        if at + 1 >= len(rest):
            print(USAGE)
            return 2
        paths = [rest[at + 1]]
        del rest[at : at + 2]
    if len(rest) < 2:
        print(USAGE)
        return 2
    email, password = rest[0], rest[1]
    base = rest[2] if len(rest) > 2 else "http://localhost:5173"

    total, moved_all, missed_all = 0, [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{base}/login")
        page.get_by_label("Почта").fill(email)
        page.get_by_label("Пароль").fill(password)
        page.get_by_role("button", name="Войти").click()
        expect(page.get_by_text("Вошли как")).to_be_visible()
        for path in paths:
            page.goto(f"{base}{path}")
            page.wait_for_load_state("networkidle")
            # Экран появляется подъёмом (`riseIn`): мерить до его конца —
            # значит принять появление за наведение.
            page.wait_for_timeout(900)
            checked, found, missed = sweep(page, path)
            total += checked
            moved_all += found
            missed_all += missed
            print(
                f"{path}: наведено {checked}, сдвинулось {len(found)}, не проверено {len(missed)}",
                flush=True,
            )
        browser.close()

    print(
        f"\nВсего наведено: {total}, сдвинулось: {len(moved_all)}, не проверено: {len(missed_all)}"
    )
    for line in moved_all + missed_all:
        print(line)
    return 1 if moved_all else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
