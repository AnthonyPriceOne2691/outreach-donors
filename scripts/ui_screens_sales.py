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
    }


def sales_prepare() -> dict[str, Callable[[Any], None]]:
    """Что сделать на экране до замера: мастер показывает шаг только после действий."""
    return {
        "sales-import": give_file,
        "sales-import-columns": read_file,
        "sales-import-report": open_report,
    }
