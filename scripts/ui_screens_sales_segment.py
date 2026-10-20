"""Вкладка «Сбор базы» раздела продаж для замера контраста (срез 6.1).

Своим модулем, как очередь и воронка: каталог продаж (`ui_screens_sales.py`) у предела длины.
Нормы передаются параметрами — обратный импорт замкнул бы круг.

Экран мерится со сметой: подготовка раскрывает форму «Новый прогон» (у гипотезы с прогонами она
свёрнута в кнопку), вписывает ключи, считает смету и снимает галочку у одного ключа
(`collect_prepare`), иначе строк сметы, снятого ключа и открытой кнопки «Запустить прогон» на
экране нет. Отчёт — последнего прогона гипотезы стенда, компактный (15.10.2026): шапка с кнопками
окон, плитки-ссылки, «На проверку N — решить», причины отсева — раскрытием свёрнутой строки
(`open_why`), ключи прогона таблицей. Расход рядом со сметой — в окне «Расход и смета»
(`sales-collect-spend`: приглушённая колонка сметы и пометка о Hunter — мелким текстом); сайты
прогона и решения «на проверку» — в окне сайтов (`ui_screens_sales_sites.py`). Какой прогон на
стенде, решает его база (в нём должны быть сайты всех полос, итог этапов в отчёте и ключ, чья
выдача не пришла; описание сегмента — длиннее трёх строк). Что ждёт повтора (20.10.2026,
`sales-collect-waits`): у последнего прогона стенда — сайт, не ответивший поиску адреса, и лиды,
ждущие очистки («Hunter ещё проверяет», первый `unknown`, сбой сети), — строки, «Повторить сейчас» и
что она возьмёт.
"""

import re
from typing import Any

from playwright.sync_api import expect
from ui_screens_sales_hints import hint_probe
from ui_screens_sales_sites import sites_screens

#: Узкое окно — телефон: плитки в две колонки, окна отчёта — во весь экран.
PHONE = {"width": 390, "height": 844}

#: Ключи для сметы: один отсеивается гигиеной — виден и отсеянный чип; у второго подготовка
#: снимает галочку (`DROPPED`) — виден и снятый.
KEYWORDS = "buy mountain bike online\nbike parts store\nsite:example.com bikes"
DROPPED = "bike parts store"
#: Блоки вкладки: форма нового прогона и прогоны гипотезы.
FORM = "section[aria-label='Новый прогон']"
RUNS = "section[aria-label='Прогоны гипотезы']"
#: Помощник описания (16.10.2026): гипотеза стенда с расплывчатым описанием — по имени; её описание
#: помощник называет расплывчатым, и смета показывает предупреждение с предложением. Ответ модели —
#: живой, ждать его дольше, чем экран.
HELPER_HYPOTHESIS = "ветклиники IE"
HELPER_DRAFT = "Websites related to veterinary clinics in Ireland"
HELPER_KEYWORDS = "vet clinic dublin\nveterinary practice cork"
HELPER_WAIT = 90_000
PROPOSAL = "[aria-label='Предложение помощника']"


def collect_prepare(page: Any) -> None:
    """Раскрыть форму, вписать ключи, посчитать смету и снять галочку у одного ключа: без этого
    строк сметы и снятого ключа на экране нет. Кнопки — с клавиатуры, а не мышью: после щелчка
    фокус на «i» уже не «видимый», и подсказка точки `hint_probe` не открылась бы (у Mantine
    подсказка по фокусу — только клавиатурному)."""
    fold = page.locator(f"{FORM} button[aria-expanded='false']")
    if fold.count() > 0:
        fold.first.focus()
        page.keyboard.press("Enter")
    page.get_by_role("textbox", name="Ключи").fill(KEYWORDS)
    page.get_by_role("button", name="Посчитать смету").focus()
    page.keyboard.press("Enter")
    expect(page.locator(".segmentEstimate p").first).to_be_visible()
    page.get_by_role("checkbox", name=DROPPED).focus()
    page.keyboard.press("Space")
    # Смета пересчитывается сама, без кнопки: запуск открыт снова — значит, пересчитана.
    expect(page.get_by_role("button", name="Запустить прогон")).to_be_enabled(timeout=10_000)
    page.wait_for_timeout(400)


def open_why(page: Any) -> None:
    """Шаг перед точкой: свёрнутая строка «Почему отсечены и без адреса» раскрыта — с клавиатуры,
    как смета (`collect_prepare`): после щелчка мышью фокус на «i» следующей точки не «видимый».
    Замер перезагружает экран перед каждой темой — строка снова свёрнута."""
    page.get_by_role("button", name=re.compile(r"^Почему (отсечены|без адреса)")).focus()
    page.keyboard.press("Enter")
    page.locator("ul[aria-label='Отсечены — почему'] li").first.wait_for()
    page.wait_for_timeout(500)


def _helper_hypothesis(page: Any) -> None:
    """Выбрать гипотезу стенда с расплывчатым описанием; её нет — замер падает словами."""
    page.get_by_role("textbox", name="Гипотеза").click()
    page.get_by_role("option", name=HELPER_HYPOTHESIS).click()
    expect(page.get_by_text(HELPER_DRAFT)).to_be_visible()


def helper_prepare(page: Any) -> None:
    """Помощник под полем описания: правка открыта, помощник ответил — предложение и вопросы."""
    _helper_hypothesis(page)
    page.get_by_role("button", name="Изменить").click()
    page.get_by_role("button", name="Помочь с описанием").click()
    expect(page.locator(PROPOSAL)).to_be_visible(timeout=HELPER_WAIT)
    page.wait_for_timeout(600)


def check_prepare(page: Any) -> None:
    """Проверка описания при смете: смета посчитана, предупреждение помощника на экране."""
    _helper_hypothesis(page)
    page.get_by_role("textbox", name="Ключи").fill(HELPER_KEYWORDS)
    page.get_by_role("button", name="Посчитать смету").click()
    expect(page.get_by_text("Описание сегмента расплывчатое")).to_be_visible(timeout=HELPER_WAIT)
    page.wait_for_timeout(600)


def spend_prepare(page: Any) -> None:
    """Открыть окно «Расход и смета» кнопкой у заголовка прогона."""
    page.get_by_role("button", name="Расход и смета").click()
    expect(page.get_by_role("dialog", name=re.compile(r"^Расход и смета"))).to_be_visible()
    page.wait_for_timeout(600)


def segment_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка сбора: широкое окно и телефон, обе темы — у замера."""
    ready = ("heading", "Продажи")
    path = "/sales?tab=collect"
    report = [
        ("заголовок блока", "h5:text-is('Сегмент гипотезы')", norm),
        ("описание сегмента", ".segmentText", norm),
        ("подпись поля", ".mantine-InputWrapper-label:text-is('Ключи')", norm),
        ("пояснение поля", ".mantine-InputWrapper-description", norm),
        ("подпись поля «Выдача»", ".mantine-InputWrapper-label:text-is('Выдача')", norm),
        ("строка сметы", ".segmentEstimate p", norm),
        ("кнопка «Посчитать смету»", "button:has-text('Посчитать смету')", big),
        ("кнопка «Запустить прогон»", "button:has-text('Запустить прогон')", big),
        ("этап прогона", ".mantine-Badge-label:text-is('готово')", norm),
        ("ссылка «Сайты прогона · N»", "a:has-text('Сайты прогона ·')", norm),
        ("кнопка «Расход и смета»", "button:has-text('Расход и смета')", norm),
        ("режим судьи", "p:has-text('судья в наблюдении')", norm),
        ("подпись плитки", ".metricTile p:nth-child(1)", norm),
        ("число в плитке", ".metricTile p:nth-child(2)", big),
        ("подпись плитки-ссылки", "a.metricTile p:nth-child(1)", norm),
        ("«На проверку N — решить»", "a:has-text('— решить')", norm),
        ("свёрнутое «почему»", "button:has-text('Почему')", norm),
        ("причина отсева", "ul[aria-label='Отсечены — почему'] li", norm, open_why),
        *_keys_probes(norm),
    ]
    probes = [
        ("вступление вкладки", ".salesTabs + div p", norm),
        *report,
        hint_probe("как идёт прогон", "Как идёт прогон", norm),
    ]
    going = [
        ("значок «в очереди задач»", ".mantine-Badge-label:text-is('в очереди задач')", norm),
        ("шапка прогона", "p:has-text('Язык писем')", norm),
        ("ход прогона", "[aria-label='Ход прогона']", norm),
        ("пульс прогона", "[aria-label='Ход прогона'] span", norm),
        ("плашка «некому взять»", ".mantine-Alert-message:has-text('служба долгих')", norm),
        ("подпись «Ещё не судили»", ".metricTile p:text-is('Ещё не судили')", norm),
    ]
    spend = _spend_probes(norm)
    helper, check = _helper_probes(norm, big)
    waits = _waits_probes(norm, big)
    return {
        "sales-collect": {"path": path, "ready": ready, "probes": probes},
        "sales-collect-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": report,
        },
        # Ход прогона (волна 14): последний прогон стенда стоит в очереди, воркеров нет —
        # «в очереди задач · поставлен …» и плашка «Задачу некому взять».
        "sales-collect-going": {"path": path, "ready": ready, "probes": going},
        "sales-collect-going-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": going,
        },
        # Окно «Расход и смета» (15.10.2026): на телефоне — во весь экран, строка — карточкой.
        "sales-collect-spend": {"path": path, "ready": ready, "probes": spend},
        "sales-collect-spend-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": spend,
        },
        # Помощник описания (16.10.2026): ответ под полем описания и предупреждение при смете.
        "sales-collect-helper": {"path": path, "ready": ready, "probes": helper},
        "sales-collect-helper-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": helper,
        },
        # Что ждёт повтора (20.10.2026): строки под лидами и «Повторить сейчас».
        "sales-collect-waits": {"path": path, "ready": ready, "probes": waits},
        "sales-collect-waits-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": waits,
        },
        "sales-collect-check": {"path": path, "ready": ready, "probes": check},
        "sales-collect-check-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": check,
        },
        **sites_screens(norm, big),
    }


def _helper_probes(
    norm: float, big: float
) -> tuple[list[tuple[str, str, float]], list[tuple[str, str, float]]]:
    """Помощник описания: кнопка с пояснением, вывод, предложение, вопросы и кнопки — под полем
    описания; при смете — то же в жёлтой плашке. Приглушённое пояснение у кнопки меряется тоже."""
    alert = ".mantine-Alert-message"
    helper = [
        ("кнопка «Помочь с описанием»", "button:has-text('Помочь с описанием')", norm),
        ("пояснение у кнопки", "p:has-text('Помощник перепишет черновик')", norm),
        ("вывод помощника", "p:has-text('Черновик расплывчатый')", norm),
        ("«Что собьёт судью»", f"{PROPOSAL} p:text-is('Что собьёт судью')", norm),
        ("беда черновика", f"{PROPOSAL} ul >> nth=0 >> li", norm),
        ("«Помощник предлагает»", f"{PROPOSAL} p:text-is('Помощник предлагает')", norm),
        ("предложение помощника", f"{PROPOSAL} p:text-is('Помощник предлагает') + p", norm),
        ("«Что уточнить»", f"{PROPOSAL} p:text-is('Что уточнить в черновике')", norm),
        ("вопрос помощника", f"{PROPOSAL} ul >> nth=1 >> li", norm),
        ("кнопка «Взять этот текст»", "button:has-text('Взять этот текст')", big),
        ("кнопка «Скрыть»", f"{PROPOSAL} button:has-text('Скрыть')", big),
    ]
    check = [
        (
            "заголовок плашки",
            ".mantine-Alert-title:has-text('Описание сегмента расплывчатое')",
            norm,
        ),
        ("текст плашки", f"{alert} p:has-text('Судья читает описание буквально')", norm),
        ("беда черновика в плашке", f"{alert} {PROPOSAL} ul >> nth=0 >> li", norm),
        ("предложение в плашке", f"{alert} {PROPOSAL} p:text-is('Помощник предлагает') + p", norm),
        ("вопрос в плашке", f"{alert} {PROPOSAL} ul >> nth=1 >> li", norm),
        ("кнопка «Взять в описание»", "button:has-text('Взять в описание')", big),
        ("кнопка «Скрыть» в плашке", f"{alert} button:has-text('Скрыть')", big),
    ]
    return helper, check


def _waits_probes(norm: float, big: float) -> list[tuple[str, str, float]]:
    """Что ждёт повтора: заголовки групп с числом, строки причин со сроком, «Повторить сейчас»
    (приглушённой кнопкой на стекле), что она возьмёт (приглушённым текстом рядом — ревью 20.10)
    и строка лидов над ними; кнопка «Найти адреса принятым»."""
    return [
        ("строка лидов", "p:has-text('Лидов заведено')", norm),
        ("группа «Ждут адреса»", "p:has-text('Ждут адреса —')", norm),
        ("строка «ждут адреса»", "ul[aria-label='Ждут адреса'] li", norm),
        ("группа «Ждут очистки»", "p:has-text('Ждут очистки —')", norm),
        ("строка «ждут очистки»", "ul[aria-label='Ждут очистки'] li", norm),
        ("кнопка «Повторить сейчас»", "button:has-text('Повторить сейчас')", norm),
        ("что возьмёт кнопка", "button:has-text('Повторить сейчас') ~ p", norm),
        ("кнопка «Найти адреса принятым»", "button:has-text('Найти адреса принятым')", big),
    ]


def _spend_probes(norm: float) -> list[tuple[str, str, float]]:
    """Окно «Расход и смета»: расход рядом со сметой — смета в таблице и пометка о Hunter
    приглушённым текстом, и он тоже меряется; смета на запуске строками сервера."""
    window = "[role='dialog']"
    return [
        ("заголовок окна", f"{window} .mantine-Modal-title", norm),
        ("подпись расхода", f"{window} section[aria-label='Расход прогона'] > p:first-child", norm),
        ("статья расхода", f"{window} .segmentSpend tbody td:nth-child(1) p", norm),
        ("факт расхода", f"{window} .segmentSpend tbody td:nth-child(2) p", norm),
        ("смета в расходе", f"{window} .segmentSpend tbody td:nth-child(3) p", norm),
        ("пометка о Hunter", f"{window} section[aria-label='Расход прогона'] > p:last-child", norm),
        ("строка сметы на запуске", f"{window} section[aria-label='Смета на запуске'] p + p", norm),
    ]


def _keys_probes(norm: float) -> list[tuple[str, str, float]]:
    """Ключи сметы чипами (волна 15): взятый, снятый (пустая галочка, «снят вами»), отсеянный,
    слова рядом и строка чисел под чипами; ключи отчёта таблицей (16.10.2026): ключ, его числа,
    строка группы почти повторов, значок «выдача не пришла» и пометки под таблицей; кнопка
    свёрнутой формы и «показать целиком» у описания сегмента."""
    keys = "section[aria-label='Ключи прогона']"
    return [
        ("кнопка «Новый прогон»", f"{FORM} button:has-text('Новый прогон')", norm),
        ("описание: «показать целиком»", "button:has-text('показать целиком')", norm),
        ("взятый ключ сметы", f"{FORM} .keyChip[data-state='taken'] label", norm),
        ("снятый ключ сметы", f"{FORM} .keyChip[data-state='excluded'] label", norm),
        ("отсеянный ключ сметы", f"{FORM} .keyChip[data-state='rejected']", norm),
        ("слова у ключа сметы", f"{FORM} .keyWords", norm),
        ("числа ключей сметы", f"{FORM} .keyCounts", norm),
        ("ключ прогона", f"{keys} .runKeysTable td.cellName > span", norm),
        # Числа — колонкой, на телефоне — строкой под ключом (`runKeysCounts`).
        ("числа ключа прогона", f"{keys} tbody td:nth-child(2) p, {keys} .runKeysCounts", norm),
        ("группа почти повторов", f"{keys} .runKeysCaption p", norm),
        ("значок «выдача не пришла»", f"{keys} .mantine-Badge-label", norm),
        ("пометки под ключами", f"{keys} .runKeysNotes p", norm),
    ]


def segment_prepare() -> dict[str, Any]:
    """Подготовка экранов сбора до замера: смета посчитана, галочка снята; окно расхода открыто."""
    return {
        "sales-collect": collect_prepare,
        "sales-collect-phone": collect_prepare,
        "sales-collect-spend": spend_prepare,
        "sales-collect-spend-phone": spend_prepare,
        "sales-collect-helper": helper_prepare,
        "sales-collect-helper-phone": helper_prepare,
        "sales-collect-check": check_prepare,
        "sales-collect-check-phone": check_prepare,
    }
