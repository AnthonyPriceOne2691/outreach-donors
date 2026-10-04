"""Экраны продаж для замера контраста: лиды, гипотезы, мастер загрузки.

Своим модулем, как экраны доноров (`ui_screens_donors.py`) и писем: основной
каталог стоит у предела длины файла, а правило «новый экран меряется в обеих
темах» от этого не отменяется. Экраны одного раздела — один модуль.

Мастер загрузки — три экрана по шагам: содержимое шага появляется только после
действий человека, и подготовка каждого экрана проходит путь заново (после
смены темы страница перезагружается). Файл для подготовки пишется во временную
папку при импорте: десять выдуманных строк, одна без адреса и одна с замечанием
о стране — чтобы в отчёте были оба значка.

Нормы передаются параметрами, а не импортируются из `ui_screens`: тот
подключает этот модуль, и обратный импорт замкнул бы круг.

База знаний и отправитель (срез 3.1) — вкладками того же раздела: таблица записей,
окно правки, окно «что увидит агент» и форма отправителя. Кнопки «Сохранить»
выключены, пока ничего не правлено, — подготовка дописывает знак в поле: у
выключенной кнопки меряется серое на сером. Записи — в базе стенда.
"""

from collections.abc import Callable
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from playwright.sync_api import expect

#: Узкое окно — телефон: вкладки встают столбиком, таблицы уезжают в прокрутку.
PHONE = {"width": 390, "height": 844}

ROWS = (
    "Почта;Имя;Компания;Сайт;Страна\n"
    "editor@acme.example.test;Иван Петров;Acme;acme.example.test;Германия\n"
    "lena@beta.example.test;Lena Weber;Beta;beta.example.test;de\n"
    ";Без Адреса;Gamma;gamma.example.test;Spain\n"
    "tom@delta.example.test;Tom Brown;Delta;delta.example.test;??\n"
    "mia@omega.example.test;Mia Rossi;Omega;omega.example.test;USA\n"
    "noah@sigma.example.test;Noah Novak;Sigma;;Austria\n"
    "emma@kappa.example.test;Emma Lee;Kappa;kappa.example.test;Poland\n"
    "luis@theta.example.test;Luis Fischer;Theta;theta.example.test;Чехия\n"
    "sofia@zeta.example.test;Sofia Schmidt;Zeta;zeta.example.test;Россия\n"
    "max@vega.example.test;Max Kozlov;Vega;vega.example.test;Испания\n"
)

CSV = Path(mkdtemp(prefix="ui-sales-")) / "leads.csv"
CSV.write_text(ROWS, encoding="utf-8")


def pick_hypothesis(page: Any) -> None:
    """Выбрать первую гипотезу в списке — без неё кнопка «Прочитать» выключена."""
    page.get_by_role("textbox", name="Гипотеза").click()
    page.locator("[data-combobox-option]").first.click()
    page.wait_for_timeout(200)


def give_file(page: Any) -> None:
    """Гипотеза и файл: кнопка «Прочитать» оживает — у выключенной меряется серое."""
    pick_hypothesis(page)
    page.locator("input[type='file']").set_input_files(str(CSV))
    expect(page.get_by_role("button", name="Прочитать")).to_be_enabled()
    page.wait_for_timeout(300)


def read_file(page: Any) -> None:
    """Дойти до шага колонок: таблица «колонка → поле» с угаданными полями."""
    give_file(page)
    page.get_by_role("button", name="Прочитать").click()
    expect(page.get_by_text("Колонки файла и поля лида")).to_be_visible()
    page.wait_for_timeout(400)


def open_report(page: Any) -> None:
    """Дойти до отчёта: плитки, первые лиды, строки с обоими значками судьбы."""
    read_file(page)
    page.get_by_role("button", name="К отчёту").click()
    expect(page.get_by_text("Станут лидами")).to_be_visible()
    page.wait_for_timeout(400)


def sales_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Экраны и точки. `norm` — норма обычного текста, `big` — крупного."""
    leads_probes = [
        ("заголовок экрана", "h3:text-is('Продажи')", big),
        ("пояснение под ним", ".glassPanel p.mantine-Text-root", norm),
        ("кнопка «Загрузить базу»", "a:has-text('Загрузить базу')", big),
        ("подпись плитки", ".metricTile p:nth-child(1)", norm),
        ("число в плитке", ".metricTile p:nth-child(2)", big),
        # Внутри панели: первый переключатель на странице — выбор темы в колонке
        # меню, и без сужения мерился бы его значок (на телефоне — за краем окна).
        ("выбранная вкладка", ".glassPanel .mantine-SegmentedControl-innerLabel", norm),
        (
            "невыбранная вкладка",
            ".glassPanel .mantine-SegmentedControl-label:not([data-active]) "
            ".mantine-SegmentedControl-innerLabel",
            norm,
        ),
        ("подпись колонки", ".leadsTable thead th:text-is('Причина')", norm),
        (
            "подсказка поиска",
            "input[aria-label='Поиск по адресу, имени или компании']",
            norm,
        ),
        ("значение фильтра гипотезы", "input[aria-label='Гипотеза']", norm),
        ("значение фильтра состояния", "input[aria-label='Состояние']", norm),
        ("значение фильтра причины", "input[aria-label='Причина отказа']", norm),
        ("адрес лида", ".leadsTable tbody .leadEmail", norm),
        ("имя и должность", ".leadsTable tbody .leadWho", norm),
        ("домен компании", ".leadsTable tbody a.leadHost", norm),
        ("гипотеза в строке", ".leadsTable tbody td:nth-child(3) p", norm),
        ("страна в строке", ".leadsTable tbody td:nth-child(4) p", norm),
        ("значок состояния", ".leadsTable tbody td:nth-child(5) .mantine-Badge-label", norm),
        ("значок причины", ".leadsTable tbody td:nth-child(6) .mantine-Badge-label", norm),
        ("слова очистки", ".leadsTable tbody .leadNote", norm),
        (
            "номер другой страницы",
            "nav[aria-label='Страницы лидов'] button:not([aria-current]) "
            "[data-page-number]:text-is('2')",
            norm,
        ),
        (
            "номер текущей страницы",
            "nav[aria-label='Страницы лидов'] button[aria-current='page'] [data-page-number]",
            norm,
        ),
        ("пункт меню", "nav a", norm),
    ]
    return {
        # Лиды — сводка плитками, вкладки, таблица с фильтрами под колонками. Мерится
        # то, по чему решают: состояние, причина и слова очистки. Значок причины и
        # слова есть только у отклонённых: на базе без очистки точки честно
        # «не найдены», как и страницы — без двадцати одного лида.
        "sales": {"path": "/sales", "ready": ("heading", "Продажи"), "probes": leads_probes},
        # Тот же экран на телефоне: вкладки столбиком, таблица в прокрутке.
        "sales-phone": {
            "path": "/sales",
            "ready": ("heading", "Продажи"),
            "viewport": PHONE,
            "probes": [
                ("заголовок экрана", "h3:text-is('Продажи')", big),
                ("подпись плитки", ".metricTile p:nth-child(1)", norm),
                ("число в плитке", ".metricTile p:nth-child(2)", big),
                ("выбранная вкладка", ".glassPanel .mantine-SegmentedControl-innerLabel", norm),
                ("адрес лида", ".leadsTable tbody .leadEmail", norm),
                (
                    "значок состояния",
                    ".leadsTable tbody td:nth-child(5) .mantine-Badge-label",
                    norm,
                ),
            ],
        },
        # Гипотезы: имя, описание, счётчики-ссылки и нули без ссылки.
        "sales-hypotheses": {
            "path": "/sales?tab=hypotheses",
            "ready": ("heading", "Продажи"),
            "probes": [
                ("подпись колонки", ".hypothesesTable thead th:text-is('Готовы')", norm),
                ("имя гипотезы", ".hypothesesTable tbody td:first-child p:nth-child(1)", norm),
                ("описание гипотезы", ".hypothesesTable tbody td:first-child p:nth-child(2)", norm),
                ("число-ссылка", ".hypothesesTable tbody a", norm),
                ("ноль без ссылки", ".hypothesesTable tbody td p:text-is('0')", norm),
                ("дата", ".hypothesesTable tbody td:last-child p", norm),
            ],
        },
        # Мастер, шаг источника: шаги, гипотеза, откуда база, кнопка — оживлённая.
        "sales-import": {
            "path": "/sales/import",
            "ready": ("heading", "Загрузка базы"),
            "probes": [
                ("заголовок экрана", "h3:text-is('Загрузка базы')", big),
                ("пояснение под ним", ".glassPanel p.mantine-Text-root", norm),
                ("«К продажам»", "a.backLink", norm),
                ("название шага", ".mantine-Stepper-stepLabel", norm),
                ("пояснение шага", ".mantine-Stepper-stepDescription", norm),
                ("подпись поля гипотезы", "label:has-text('Гипотеза')", norm),
                ("пояснение под полем", ".mantine-InputWrapper-description", norm),
                # Значение поля гипотезы не меряется: поле просторное, текст в нём —
                # малая доля вырезки, и порог делит заливку поля и полотно (1,32 : 1
                # при читаемом тексте, 04.10.2026). Те же стили поля — у имени файла ниже.
                ("подпись переключателя источника", ".mantine-Radio-label", norm),
                ("имя файла в поле", ".mantine-FileInput-input", norm),
                ("кнопка «Прочитать»", "button:has-text('Прочитать')", big),
                ("пункт меню", "nav a", norm),
            ],
        },
        # Шаг колонок: имя колонки, первые значения мелким приглушённым, поле лида.
        "sales-import-columns": {
            "path": "/sales/import",
            "ready": ("heading", "Загрузка базы"),
            "probes": [
                ("заголовок шага", "h5:text-is('Колонки файла и поля лида')", norm),
                ("пояснение шага", "h5 + p", norm),
                ("подпись переключателя заголовка", ".mantine-Switch-label", norm),
                ("подпись колонки таблицы", ".columnsTable thead th:text-is('Поле лида')", norm),
                ("имя колонки файла", ".columnsTable tbody td:first-child p", norm),
                ("первые значения", ".columnsTable tbody td:nth-child(2) p", norm),
                ("значение поля лида", "input[aria-label^='Поле для колонки']", norm),
                ("кнопка «К источнику»", "button:has-text('К источнику')", big),
                ("кнопка «К отчёту»", "button:has-text('К отчёту')", big),
            ],
        },
        # Шаг отчёта: плитки, первые лиды, строки с судьбой — оба значка.
        "sales-import-report": {
            "path": "/sales/import",
            "ready": ("heading", "Загрузка базы"),
            "probes": [
                ("заголовок шага", "h5:text-is('Что получится')", norm),
                ("пояснение шага", "h5 + p", norm),
                ("подпись плитки", ".metricTile p:nth-child(1)", norm),
                ("число в плитке", ".metricTile p:nth-child(2)", big),
                ("подпись таблицы лидов", "p:has-text('Первые лиды')", norm),
                ("подпись колонки отчёта", ".reportTable thead th:text-is('Почему')", norm),
                ("номер строки", ".reportTable tbody td:first-child p", norm),
                (
                    "значок «строка отклонена»",
                    ".reportTable tbody .mantine-Badge-root:has-text('строка отклонена') "
                    ".mantine-Badge-label",
                    norm,
                ),
                (
                    "значок «загружен с замечанием»",
                    ".reportTable tbody .mantine-Badge-root:has-text('загружен с замечанием') "
                    ".mantine-Badge-label",
                    norm,
                ),
                ("причина словами", ".reportTable tbody td:nth-child(3) p", norm),
                ("ячейка как есть", ".reportTable tbody .reportCell", norm),
                ("кнопка «К колонкам»", "button:has-text('К колонкам')", big),
                ("кнопка «Загрузить»", "button:has-text('Загрузить')", big),
            ],
        },
        **kb_screens(norm, big),
    }


def open_entry(page: Any) -> None:
    """Окно правки первой записи; «Сохранить» оживает от знака в тексте."""
    page.locator(".kbTable tbody tr").first.get_by_role("button", name="Править").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    text = dialog.get_by_role("textbox", name="Текст")
    text.fill(text.input_value() + " ")
    expect(dialog.get_by_role("button", name="Сохранить")).to_be_enabled()
    page.wait_for_timeout(400)


def open_agent(page: Any) -> None:
    """Окно «что увидит агент»: группы видов с фактами."""
    page.get_by_role("button", name="Что увидит агент").click()
    expect(page.get_by_role("dialog").locator(".agentGroup").first).to_be_visible()
    page.wait_for_timeout(400)


def touch_sender(page: Any) -> None:
    """Форма отправителя: «Сохранить» оживает от правки, а значение поля меряется
    по длинной ссылке — короткое слово в широком поле замер не видит (1.5, 04.10)."""
    site = page.get_by_role("textbox", name="Сайт")
    site.fill("https://studio-of-made-up-examples.example.test/contacts/sales-team")
    expect(page.get_by_role("button", name="Сохранить")).to_be_enabled()
    page.wait_for_timeout(300)


#: Поле по его подписи: у Mantine подпись — `label` рядом с полем, а не над ним в DOM.
def _field(label: str, tag: str = "input") -> str:
    return f".mantine-InputWrapper-root:has(> label:text-is('{label}')) {tag}"


def kb_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладки «База знаний» и «Отправитель»: список, окна, форма — на 1440 и 390."""
    ready = ("heading", "Продажи")
    kb_list = [
        ("пояснение над таблицей", ".glassPanel .mantine-SegmentedControl-root + div p", norm),
        ("версия базы", ".kbVersion", norm),
        ("кнопка «Что увидит агент»", "button:has-text('Что увидит агент')", big),
        ("кнопка «Добавить запись»", "button:has-text('Добавить запись')", big),
        ("выбранная вкладка", ".glassPanel .mantine-SegmentedControl-innerLabel", norm),
        ("подпись колонки", ".kbTable thead th:text-is('Агент видит')", norm),
        ("заголовок записи", ".kbTable tbody .kbTitle", norm),
        ("текст записи", ".kbTable tbody .kbText", norm),
        ("вид", ".kbTable tbody td:nth-child(2) p", norm),
        ("язык", ".kbTable tbody td:nth-child(3) p", norm),
        ("теги", ".kbTable tbody .kbTags", norm),
        ("кто правил", ".kbTable tbody td:nth-child(5) p:nth-child(1)", norm),
        ("когда правил", ".kbTable tbody td:nth-child(5) p:nth-child(2)", norm),
        ("кнопка «Править»", ".kbTable tbody button:has-text('Править')", norm),
    ]
    dialog = ".mantine-Modal-content"
    sender = [
        ("заголовок готовности", ".mantine-Alert-title", norm),
        ("слова готовности", ".mantine-Alert-message", norm),
        ("подпись поля", "form .mantine-InputWrapper-label", norm),
        ("пояснение поля", "form .mantine-InputWrapper-description", norm),
        ("значение поля (ссылка)", _field("Сайт"), norm),
        ("значение в несколько строк", "form textarea", norm),
        ("кто и когда правил", "form p:text-matches('Правил|Ещё не')", norm),
        ("кнопка «Сохранить»", "form button:has-text('Сохранить')", big),
    ]
    return {
        "sales-kb": {"path": "/sales?tab=kb", "ready": ready, "probes": kb_list},
        "sales-kb-phone": {
            "path": "/sales?tab=kb",
            "ready": ready,
            "viewport": PHONE,
            "probes": [kb_list[1], kb_list[3], kb_list[4], kb_list[6], kb_list[7]],
        },
        "sales-kb-entry": {
            "path": "/sales?tab=kb",
            "ready": ready,
            "probes": [
                ("заголовок окна", f"{dialog} .mantine-Modal-title", norm),
                ("подпись поля", f"{dialog} .mantine-InputWrapper-label", norm),
                ("пояснение поля", f"{dialog} .mantine-InputWrapper-description", norm),
                ("значение вида", f"{dialog} .mantine-Select-input", norm),
                ("значение заголовка", f"{dialog} {_field('Заголовок')}", norm),
                ("текст записи", f"{dialog} textarea", norm),
                ("подпись переключателя", f"{dialog} .mantine-Switch-label", norm),
                ("кнопка «Отмена»", f"{dialog} button:has-text('Отмена')", big),
                ("кнопка «Сохранить»", f"{dialog} button:has-text('Сохранить')", big),
            ],
        },
        "sales-kb-agent": {
            "path": "/sales?tab=kb",
            "ready": ready,
            "probes": [
                ("заголовок окна", f"{dialog} .mantine-Modal-title", norm),
                ("версия и число", f"{dialog} .mantine-Modal-body > div > p", norm),
                ("вид и язык группы", f"{dialog} .agentGroup h5", big),
                ("заголовок факта", f"{dialog} .agentFact p:nth-child(1)", norm),
                ("текст факта", f"{dialog} .agentFact p:nth-child(2)", norm),
                ("теги факта", f"{dialog} .agentFact p:nth-child(3)", norm),
            ],
        },
        "sales-sender": {"path": "/sales?tab=sender", "ready": ready, "probes": sender},
        "sales-sender-phone": {
            "path": "/sales?tab=sender",
            "ready": ready,
            "viewport": PHONE,
            "probes": [sender[0], sender[1], sender[2], sender[4], sender[7]],
        },
    }


def sales_prepare() -> dict[str, Callable[[Any], None]]:
    """Что сделать на экране до замера: мастер показывает шаг только после действий."""
    return {
        "sales-import": give_file,
        "sales-import-columns": read_file,
        "sales-import-report": open_report,
        "sales-kb-entry": open_entry,
        "sales-kb-agent": open_agent,
        "sales-sender": touch_sender,
        "sales-sender-phone": touch_sender,
    }
