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

**Ниже нормы — перемер внутри элемента.** Вырез берёт 6 px вокруг
элемента, чтобы у букв был фон. С 20.10.2026 у поля и обычной кнопки
на панели светлой темы кромка из чернил, как у окон, и в вырез попадает
тёмная линия: своя рамка поля или край переключателя под подписью. Порог
Оцу кладёт её к буквам, и чернила светлеют: значение фильтра с теми же
буквами намерилось 3,58 вместо 16,58, подпись над переключателем — 2,32
вместо 6,67. Поэтому точка ниже нормы перемеряется внутри элемента —
без рамки, полей и выреза вокруг, с запасом 2 px: там только буквы и то,
на чём они лежат. В норме там — печатается это число с пометкой «внутри»
и рядом число по вырезу; ниже и там — «МАЛО». Точку, которая в норме по
вырезу, перемер не трогает. Расчёт по пикселям и это решение — в
`ui_contrast_ratio.py`, под тестом.

Что он нашёл 19.09.2026, когда стекло стало прозрачнее:
  • тихий текст на свету — 3.8 : 1 при норме 4.5 (чернила посветлели
    на прозрачной панели), в темноте — 4.5 : 1 у пояснений;
  • «тихие» кнопки акцентом шестой ступени — 2.8 : 1;
  • подсвеченный пункт меню поверх цветного пятна — 3.9 : 1.
Все три закрыты чернилами и заливкой рамы, а не увеличением шрифта.
"""

import re
import sys
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright

#: Снимки складываются во временную папку, а не рядом с кодом. Первая
#: версия писала их в текущий каталог, и четыре мегабайта картинок уехали
#: в репозиторий вместе с правкой — заметил это только `git ls-files`.
SHOTS = Path(tempfile.mkdtemp(prefix="ui-contrast-"))

# Каталог экранов и расчёт по пикселям лежат рядом: `python scripts/ui_contrast.py`
# кладёт папку скрипта в путь поиска, и соседние модули находятся без установки.
from ui_contrast_ratio import PAD, SCALE, measure, ratio_of, rect_of  # noqa: E402
from ui_screens import NORM, PREPARE, SCREENS  # noqa: E402


def contrast(path, box, pad=PAD, core=False):
    """Отношение контраста между буквами и фоном под ними — в вырезе снимка
    (`ui_contrast_ratio.ratio_of`). `None` — измерить не удалось: в куске одна краска."""
    crop = Image.open(path).convert("RGB").crop(rect_of(box, pad))
    # `getdata` уходит из Pillow 14; новый вызов есть с 12.1 (не с 12.0).
    return ratio_of(crop.get_flattened_data(), core)


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

#: Рамка и поля элемента с каждой стороны: сверху, справа, снизу, слева.
INSETS = """node => { const s = getComputedStyle(node);
  return ['Top', 'Right', 'Bottom', 'Left'].map(
    (k) => parseFloat(s['padding' + k]) + parseFloat(s['border' + k + 'Width'])); }"""


def point(el, frame, small, norm):
    """Отношение у точки и пометка к нему (`ui_contrast_ratio.measure`): ниже
    нормы по вырезу — перемер внутри элемента."""
    return measure(
        lambda box, pad: contrast(frame, box, pad, core=small),
        el.bounding_box(),
        lambda: el.evaluate(INSETS),
        norm,
    )


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
        value, where = point(el, frame, small, norm)
        if value is None:
            print(f"  {name:28} НЕ ИЗМЕРЕНО: в куске одна краска")
            worst_ok = False
            continue
        mark = "ок" if value >= norm else "МАЛО"
        worst_ok &= value >= norm
        how = "  по ядру" if small else ""
        print(f"  {name:28} {value:5.2f} : 1  при норме {norm}  {mark}{how}{where}")
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
        # Окно — экрана: телефонная раскладка ставит текст над другими местами
        # полотна, и замер на широком окне о ней ничего не говорит.
        viewport = target.get("viewport", {"width": 1440, "height": 900})
        page = b.new_page(viewport=viewport, device_scale_factor=SCALE)
        page.goto(f"{base}/login")
        page.get_by_label("Почта").fill(email)
        page.get_by_label("Пароль").fill(password)
        page.get_by_role("button", name="Войти").click()
        # Вход — по кнопке почты в шапке: её название «Вошли как …» есть на любой ширине,
        # а надпись — только на широком окне; на 390 px остаётся значок (`AccountMenu`).
        expect(page.get_by_role("button", name=re.compile(r"^Вошли как"))).to_be_visible()
        page.goto(f"{base}{target['path']}")
        role, name = target["ready"]
        expect(page.get_by_role(role, name=name).first).to_be_visible()

        prepare = PREPARE.get(screen)
        wanted = target.get("open_row_with")
        if wanted:
            # Экран-карточка открывается из списка: адрес у неё с номером,
            # а номер зависит от базы.
            # Строка таблицы — или строка списка (`[data-thread]`, «Диалоги» с 09.10.2026).
            page.locator("table tbody tr, [data-thread]", has_text=wanted).first.click()
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
