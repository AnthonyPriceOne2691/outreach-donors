"""Контраст текста поверх стекла — замером по пикселям, а не на глаз.

Прозрачные панели тем и опасны, что «кажется читаемо» и 4.5 : 1 —
разные утверждения: под текстом едет полотно, и в одном месте экрана
он лежит на бирюзе, в другом на песке. Координаты берутся у самого
браузера, снимок режется по ним, и отношение считается по WCAG.

Запуск (сервер на 8100, фронт на 5173, учётка заведена командой):

    python scripts/ui_contrast.py ivan@site.com "три слова подряд"

Почему это не тест в сьюте: нужен настоящий браузер и настоящий сервер.
Зато найденное этим способом закрывается насовсем — правкой токенов
в `frontend/src/styles/glass.css`, а не подбором на глаз.

Что он нашёл 19.09.2026, когда стекло стало прозрачнее:
  • тихий текст на свету — 3.8 : 1 при норме 4.5 (чернила посветлели
    на прозрачной панели), в темноте — 4.5 : 1 у пояснений;
  • «тихие» кнопки акцентом шестой ступени — 2.8 : 1;
  • подсвеченный пункт меню поверх цветного пятна — 3.9 : 1.
Все три закрыты чернилами и заливкой рамы, а не увеличением шрифта.
"""

import sys

from PIL import Image
from playwright.sync_api import expect, sync_playwright

SCALE = 2
NORM = 4.5  # норма для обычного текста
BIG = 3.0  # норма для крупного (18pt+ или 14pt жирного)


def lum(px):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(px[0]) + 0.7152 * ch(px[1]) + 0.0722 * ch(px[2])


def _otsu(hist, total):
    """Порог, делящий пиксели на две группы — чернила и фон.

    Вынесено из замера отдельной функцией не для красоты: вместе они
    давали функцию сложности 11 при потолке 10, и ратчет сложности
    отказывался её принимать.
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
    return threshold / 255


def contrast(path, box, pad=6):
    """Отношение контраста между буквами и фоном под ними.

    Пиксели куска делятся на две группы порогом Оцу и сравниваются их
    средние. Простая медиана здесь врёт: на тесном куске, где буквы
    занимают половину площади, она попадает в сами буквы, и текст 96 %
    яркости над тёмным стеклом показывал 4.4 : 1 вместо двенадцати.
    На просторном куске врёт min/max: сглаживание даёт одиночные пиксели
    темнее текста.
    """
    im = Image.open(path).convert("RGB")
    crop = im.crop(
        (
            int((box["x"] - pad) * SCALE),
            int((box["y"] - pad) * SCALE),
            int((box["x"] + box["width"] + pad) * SCALE),
            int((box["y"] + box["height"] + pad) * SCALE),
        )
    )
    lums = [lum(p) for p in crop.getdata()]
    hist = [0] * 256
    for value in lums:
        hist[min(255, int(value * 255))] += 1

    edge = _otsu(hist, len(lums))
    dark = [v for v in lums if v <= edge]
    light = [v for v in lums if v > edge]
    if not dark or not light:
        return 21.0
    a, b = sum(dark) / len(dark), sum(light) / len(light)
    return (b + 0.05) / (a + 0.05)


PROBES = [
    ("заголовок раздела", "h3", BIG),
    ("пояснение под ним", "p.mantine-Text-root", NORM),
    ("почта в строке", "table tbody tr td p", NORM),
    ("дата последнего входа", "table tbody tr td:nth-child(5) p", NORM),
    ("кнопка «Сбросить пароль»", "table tbody button", BIG),
    ("пункт меню", "nav a", NORM),
]


def probe_notification(page, scheme, email):
    """Отказ сервера всплывает уведомлением и гаснет через секунды,
    поэтому у него свой проход: вызвать, снять, померить.

    Меряется пояснение, а не заголовок: заголовок сообщает факт отказа,
    а что делать — написано именно в пояснении.
    """
    row = page.locator("tr", has_text=email)
    row.get_by_label(f"Учётка {email} включена").click(force=True)
    expect(page.get_by_text("самого себя")).to_be_visible()
    page.wait_for_timeout(400)
    shot = f"contrast-notice-{scheme}.png"
    page.screenshot(path=shot)
    body = page.get_by_text("Нельзя снять права с самого себя").first
    value = contrast(shot, body.bounding_box())
    mark = "ок" if value >= NORM else "МАЛО"
    print(f"  {'текст уведомления об отказе':28} {value:5.2f} : 1  при норме {NORM}  {mark}")
    return value >= NORM


def run(page, scheme, shot):
    page.evaluate("s => localStorage.setItem('mantine-color-scheme-value', s)", scheme)
    page.reload()
    page.wait_for_timeout(900)
    page.screenshot(path=shot)
    print(f"\n{scheme}:")
    worst_ok = True
    for name, selector, norm in PROBES:
        el = page.locator(selector).first
        box = el.bounding_box()
        if box is None:
            print(f"  {name:28} не найден ({selector})")
            continue
        value = contrast(shot, box)
        mark = "ок" if value >= norm else "МАЛО"
        worst_ok &= value >= norm
        print(f"  {name:28} {value:5.2f} : 1  при норме {norm}  {mark}")
    return worst_ok


USAGE = "Запуск: python scripts/ui_contrast.py <почта> <пароль> [адрес фронта]"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE)
        return 2
    email, password = argv[0], argv[1]
    base = argv[2] if len(argv) > 2 else "http://localhost:5173"

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=SCALE)
        page.goto(f"{base}/login")
        page.get_by_label("Почта").fill(email)
        page.get_by_label("Пароль").fill(password)
        page.get_by_role("button", name="Войти").click()
        expect(page.get_by_text("Вошли как")).to_be_visible()
        page.goto(f"{base}/users")
        expect(page.get_by_role("button", name="Завести учётку")).to_be_visible()

        ok = run(page, "light", "measure-light.png")
        ok &= probe_notification(page, "light", email)
        ok &= run(page, "dark", "measure-dark.png")
        ok &= probe_notification(page, "dark", email)
        b.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
