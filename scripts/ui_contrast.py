"""Контраст текста поверх стекла — замером по пикселям, а не на глаз.

Прозрачные панели тем и опасны, что «кажется читаемо» и 4.5 : 1 —
разные утверждения: под текстом едет полотно, и в одном месте экрана
он лежит на бирюзе, в другом на песке. Координаты берутся у самого
браузера, снимок режется по ним, и отношение считается по WCAG.

Запуск (сервер на 8100, фронт на 5173, учётка заведена командой):

    python scripts/ui_contrast.py ivan@site.com "три слова подряд"
    python scripts/ui_contrast.py ivan@site.com "пароль" --screen letters

**Экран выбирается, а не вшит.** Правило «каждый новый экран меряется
в обеих темах» невыполнимо, пока замер умеет только один экран: проверка,
которую нельзя провести, не проводится. Новый экран добавляется строкой
в `SCREENS` — вместе со своими точками, потому что мерить надо то,
на что смотрят, а не то, что нашлось первым.

Почему это не тест в сьюте: нужен настоящий браузер и настоящий сервер.
Зато найденное этим способом закрывается насовсем — правкой токенов
в `frontend/src/styles/glass.css`, а не подбором на глаз.

**Мелкий текст меряется по ядру буквы.** У тонкого штриха почти каждый
пиксель — край, сглаженный в сторону фона, и среднее «чернил» съезжает
к фону: текст, который в других местах даёт 8 : 1, в поле ввода намерился
на 3.9 : 1, подпись судьи в истории прогонов — 4,12 при ядре буквы 6,6
(аудит 25.09.2026). Цвет, которым текст написан, несут пиксели середины
штриха. Поэтому у текста мельче 14 px чернила — треть пикселей, дальше
всех от фона, а фон — среднее своей группы; такая точка печатается
с пометкой «по ядру». Крупный текст меряется средним, как раньше: у него
середина штриха и есть большинство пикселей.

Что он нашёл 19.09.2026, когда стекло стало прозрачнее:
  • тихий текст на свету — 3.8 : 1 при норме 4.5 (чернила посветлели
    на прозрачной панели), в темноте — 4.5 : 1 у пояснений;
  • «тихие» кнопки акцентом шестой ступени — 2.8 : 1;
  • подсвеченный пункт меню поверх цветного пятна — 3.9 : 1.
Все три закрыты чернилами и заливкой рамы, а не увеличением шрифта.
"""

import sys
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright

#: Снимки складываются во временную папку, а не рядом с кодом. Первая
#: версия писала их в текущий каталог, и четыре мегабайта картинок уехали
#: в репозиторий вместе с правкой — заметил это только `git ls-files`.
SHOTS = Path(tempfile.mkdtemp(prefix="ui-contrast-"))

SCALE = 2
# Каталог экранов лежит рядом: `python scripts/ui_contrast.py` кладёт
# папку скрипта в путь поиска, и соседний модуль находится без установки.
from ui_screens import NORM, PREPARE, SCREENS  # noqa: E402


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


def contrast(path, box, pad=6, core=False):
    """Отношение контраста между буквами и фоном под ними.
    `None` — измерить не удалось: в куске одна краска.

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
    # `getdata` уходит из Pillow 14; новый вызов есть с 12-й.
    lums = [lum(p) for p in crop.get_flattened_data()]
    hist = [0] * 256
    for value in lums:
        hist[min(255, int(value * 255))] += 1

    edge = _otsu(hist, len(lums))
    dark = [v for v in lums if v <= edge]
    light = [v for v in lums if v > edge]
    if not dark or not light:
        # Мерить нечего: кусок однородный. Так выглядит элемент, уехавший
        # за край снимка, и выключенный — серое на сером. Раньше здесь
        # стояло 21.0, и оба случая печатались как безупречный результат.
        return None
    return _ratio(dark, light, core)


def _ratio(dark, light, core):
    """Отношение групп: средних — или ядра чернил к среднему фона.

    Чернила — меньшая группа: в куске с полями вокруг текста фона всегда
    больше, чем букв. Ядро — треть пикселей чернил, дальше всех от фона.
    """
    if core:
        ink, ground = (dark, light) if len(dark) <= len(light) else (light, dark)
        ordered = sorted(ink, reverse=ink is light)
        part = ordered[: max(1, len(ordered) // 3)]
        a, b = sum(part) / len(part), sum(ground) / len(ground)
    else:
        a, b = sum(dark) / len(dark), sum(light) / len(light)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def probe_notification(page, scheme, email):
    """Отказ сервера всплывает уведомлением и гаснет через секунды,
    поэтому у него свой проход: вызвать, снять, померить.

    Меряется пояснение, а не заголовок: заголовок сообщает факт отказа,
    а что делать — написано именно в пояснении.
    """
    # Тема ставится здесь же. Раньше проба мерила уведомление в той теме,
    # в какой экран оставил предыдущий замер, то есть «светлое» уведомление
    # мерилось в тёмной теме; а уведомление первого вызова ещё висело, когда
    # всплывало второе, и проба по тексту находила два (25.09.2026).
    # Перезагрузка решает оба: тема своя, прежних уведомлений нет.
    page.evaluate("s => localStorage.setItem('mantine-color-scheme-value', s)", scheme)
    page.reload()
    page.wait_for_timeout(900)
    row = page.locator("tr", has_text=email)
    row.get_by_label(f"Учётка {email} включена").click(force=True)
    expect(page.get_by_text("самого себя")).to_be_visible()
    page.wait_for_timeout(400)
    shot = str(SHOTS / f"contrast-notice-{scheme}.png")
    page.screenshot(path=shot)
    body = page.get_by_text("Нельзя снять права с самого себя").first
    value = contrast(shot, body.bounding_box())
    if value is None:
        print(f"  {'уведомление об отказе, ' + scheme:28} НЕ ИЗМЕРЕНО: в куске одна краска")
        return False
    mark = "ок" if value >= NORM else "МАЛО"
    print(f"  {'уведомление об отказе, ' + scheme:28} {value:5.2f} : 1  при норме {NORM}  {mark}")
    return value >= NORM


#: Мелкий текст — мельче 14 px: у него почти весь штрих из краёв.
SMALL_TEXT = "node => parseFloat(getComputedStyle(node).fontSize) < 14"


def measurable(page, selector):
    """Элемент и причина, по которой мерить его нельзя.

    Выключенный не меряется вовсе: у него серое на сером, и замер
    показывает безупречные числа там, где ничего не проверено. Главная
    кнопка сервиса так и считалась проверенной три среза подряд.
    """
    el = page.locator(selector).first
    if el.count() == 0:
        return None, f"не найден ({selector})"
    if el.is_disabled():
        return None, "выключен — у выключенного меряется серое на сером"
    # В середину окна, а не «если нужно»: прокрутка к краю ставила элемент
    # под закреплённую шапку, и мерилось её стекло поверх него. 23.09 так
    # «провалились» подписи плиток сметы в светлой теме и подпись поля
    # цены (1,34 : 1) — на снимке обе читаются легко. С 25.09 шапка уезжает
    # со страницей, но середина окна осталась: у края стоит колонка меню.
    el.evaluate("node => node.scrollIntoView({block: 'center'})")
    page.wait_for_timeout(300)
    return el, None


def run(page, scheme, shot, probes, prepare=None):
    page.evaluate("s => localStorage.setItem('mantine-color-scheme-value', s)", scheme)
    page.reload()
    page.wait_for_timeout(900)
    # Подготовка повторяется после каждой перезагрузки: смена темы —
    # это reload, а всё, что живёт в состоянии страницы (посчитанная
    # смета), после него исчезает. Первая версия готовила экран один
    # раз, и во второй теме мерилась уже выключенная кнопка.
    if prepare is not None:
        prepare(page)
    print(f"\n{scheme}:")
    worst_ok = True
    for index, (name, selector, norm, *show) in enumerate(probes):
        # Шаг, который делает точку видимой (открыть поповер), — перед ней.
        for step in show:
            step(page)
        el, refusal = measurable(page, selector)
        if el is None:
            print(f"  {name:28} НЕ ИЗМЕРЕНО: {refusal}")
            worst_ok = False
            continue
        # Снимок на каждую точку, уже после прокрутки к ней: длинный
        # экран не помещается в окно, и кусок за его краем однороден —
        # раньше это печаталось как 21 : 1 и считалось отличным.
        frame = shot.replace(".png", f"-{index}.png")
        page.screenshot(path=frame)
        small = el.evaluate(SMALL_TEXT)
        value = contrast(frame, el.bounding_box(), core=small)
        if value is None:
            print(f"  {name:28} НЕ ИЗМЕРЕНО: в куске одна краска")
            worst_ok = False
            continue
        mark = "ок" if value >= norm else "МАЛО"
        worst_ok &= value >= norm
        how = "  по ядру" if small else ""
        print(f"  {name:28} {value:5.2f} : 1  при норме {norm}  {mark}{how}")
    return worst_ok


USAGE = (
    "Запуск: python scripts/ui_contrast.py <почта> <пароль> "
    f"[адрес фронта] [--screen {'|'.join(SCREENS)}]"
)


def main(argv: list[str]) -> int:
    rest = list(argv)
    screen = "users"
    if "--screen" in rest:
        at = rest.index("--screen")
        if at + 1 >= len(rest) or rest[at + 1] not in SCREENS:
            print(USAGE)
            return 2
        screen = rest[at + 1]
        del rest[at : at + 2]

    if len(rest) < 2:
        print(USAGE)
        return 2
    email, password = rest[0], rest[1]
    base = rest[2] if len(rest) > 2 else "http://localhost:5173"
    target = SCREENS[screen]

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=SCALE)
        page.goto(f"{base}/login")
        page.get_by_label("Почта").fill(email)
        page.get_by_label("Пароль").fill(password)
        page.get_by_role("button", name="Войти").click()
        expect(page.get_by_text("Вошли как")).to_be_visible()
        page.goto(f"{base}{target['path']}")
        role, name = target["ready"]
        expect(page.get_by_role(role, name=name).first).to_be_visible()

        prepare = PREPARE.get(screen)
        wanted = target.get("open_row_with")
        if wanted:
            # Экран-карточка открывается из списка: адрес у неё с номером,
            # а номер зависит от базы.
            page.locator("table tbody tr", has_text=wanted).first.click()
            page.wait_for_timeout(700)

        ok = run(page, "light", str(SHOTS / f"{screen}-light.png"), target["probes"], prepare)
        ok &= run(page, "dark", str(SHOTS / f"{screen}-dark.png"), target["probes"], prepare)
        # Уведомление об отказе живёт только на экране учёток: его
        # вызывает попытка снять права с самого себя.
        if screen == "users":
            ok &= probe_notification(page, "light", email)
            ok &= probe_notification(page, "dark", email)
        b.close()
    print(f"\nСнимки: {SHOTS}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
