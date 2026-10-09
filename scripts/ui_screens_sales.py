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

Очередь писем (срез 4.6b) — своим модулем `ui_screens_sales_queue.py`, воронка (срез 5.4) —
`ui_screens_sales_funnel.py`.

Цепочка писем (срез 4.6) — вкладка с карточками языков и окно шага с письмом глазами
адресата. Шаблоны и отправитель — в базе стенда: у английской цепочки заданы все шаги
(третий выключен), у русской — первое письмо, у отправителя нет адреса — так на экране
есть все значки шага и плашка «отправка не готова».
"""

from collections.abc import Callable
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from playwright.sync_api import expect
from ui_screens_sales_funnel import funnel_prepare, funnel_screens
from ui_screens_sales_hints import hint_probe, note_probes
from ui_screens_sales_queue import queue_screens

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
        # Пояснение раздела — в «i» у заголовка (аудит 09.10.2026), а не абзацем под ним;
        # слова очистки — за «!» лида, по нажатию.
        hint_probe("пояснение раздела", "Откуда лиды и как их чистят", norm),
        *note_probes(norm),
    ]
    return {
        # Лиды — плитки состояний, вкладки, таблица с фильтрами под колонками. Мерится
        # то, по чему решают: состояние, причина и слова очистки за «!». Значок причины и
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
                hint_probe("пояснение раздела", "Откуда лиды и как их чистят", norm),
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
        **chain_screens(norm, big),
        **queue_screens(norm, big),
        **funnel_screens(norm, big),
    }


#: Текст записи для замера — на несколько строк: значение поля замер видит только тогда,
#: когда буквы — большая часть вырезки (у короткой строки порог делит кромку поля с ними).
ENTRY_TEXT = " ".join(
    [
        "Выдуманная запись для замера контраста: строка за строкой, чтобы буквы заняли поле.",
        "Цвет текста от длины не зависит — меняется только доля букв в вырезке замера.",
        "Ещё строка выдуманного текста для того же замера, и ещё одна, последняя,",
        "чтобы поле выросло на все свои строки и кромка стала малой долей вырезки.",
    ]
)


def open_entry(page: Any) -> None:
    """Окно правки первой записи; «Сохранить» оживает от правки текста.

    Текст — длинный (`ENTRY_TEXT`): им меряется значение поля окна, а короткое значение
    замер не видит — вырезка берёт кромку поля (05.10.2026, разбор — у проб окна)."""
    page.locator(".kbTable tbody tr").first.get_by_role("button", name="Править").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    dialog.get_by_role("textbox", name="Текст").fill(ENTRY_TEXT)
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
        ("пояснение над таблицей", ".salesTabs + div p", norm),
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
        hint_probe("что агент видит", "Что агент видит из базы", norm),
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
                # Значения вида и заголовка не меряются: вырезка берёт кромку поля, а на
                # плотном стекле светлой темы она из чернил (`--field-edge`, 0,42), и
                # порог делит её с буквами короткой строки — 2,74 : 1 при 5,0–6,4 по
                # строке букв и 14,5 по цветам текста и подложки (05.10.2026). Тот же
                # класс, что у значения поля в `ui_screens.py`. Стили поля и текста те
                # же — у текста записи ниже: он меряется длинным текстом (`open_entry`).
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


#: Тело первого письма для замера — плотное, на все строки поля: значение поля замер видит
#: только тогда, когда буквы — большая часть вырезки (как `ENTRY_TEXT` базы знаний).
#: Годное для показа письма: зоны, подстановки из списка, коридор отличия достижим.
STEP_LINES = (
    "Made-up letter for the contrast check, {{name}}: line after line,",
    "so that the letters fill the field. Text colour does not depend on length;",
    "only the share of letters in the probe crop changes with it.",
    "One more made-up line for the same check, for {{company}} and {{site}} only,",
    "and the last made-up line, so that the field grows to all of its rows",
    "and the edge of the field becomes a small share of the crop.",
)
STEP_BODY = (
    "[greeting] rewrite\n"
    + "\n".join(STEP_LINES[:3])
    + "\n\n[offer] fixed\n"
    + "\n".join(STEP_LINES[3:])
)


def open_step(page: Any) -> None:
    """Окно первого письма английской цепочки и письмо глазами адресата под формой.

    Текст поля — плотный (`STEP_BODY`): им меряется значение поля, и правка оживляет
    «Сохранить» — у выключенной кнопки меряется серое на сером. Шаблон стенда с пустыми
    строками между зонами вырезка видит хуже: кромка поля — большая доля (разбор — у проб окна)."""
    english = page.get_by_role("region", name="Английский")
    english.locator(".chainStep").first.get_by_role("button", name="Править").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    dialog.get_by_role("textbox", name="Текст письма").fill(STEP_BODY)
    expect(dialog.get_by_role("button", name="Сохранить")).to_be_enabled()
    dialog.get_by_role("button", name="Показать письмо").click()
    expect(dialog.locator(".chainLetter")).to_be_visible()
    page.wait_for_timeout(400)


def chain_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка «Цепочка писем»: карточки языков на 1440 и 390, окно шага с письмом."""
    ready = ("heading", "Продажи")
    card = "section.glassQuiet"
    chain_list = [
        ("пояснение над цепочками", ".salesTabs + div p", norm),
        ("подпись поля набора", "label:text-is('Набор')", norm),
        ("язык цепочки", f"{card} h5", norm),
        ("значок цепочки", f"{card} h5 + .mantine-Badge-root .mantine-Badge-label", norm),
        ("состояние цепочки", f"{card} .chainState", norm),
        ("название шага", f"{card} .chainStepTitle", norm),
        ("значок «задан»", ".chainStep .mantine-Badge-label:text-is('задан')", norm),
        ("значок «выключен»", ".chainStep .mantine-Badge-label:text-is('выключен')", norm),
        ("значок «не задан»", ".chainStep .mantine-Badge-label:text-is('не задан')", norm),
        ("тема шага", ".chainSubject", norm),
        ("начало текста", ".chainExcerpt", norm),
        ("кто и когда правил", ".chainWho", norm),
        ("кнопка «Править»", ".chainStep button:has-text('Править')", norm),
        ("кнопка «Задать»", ".chainStep button:has-text('Задать')", norm),
        hint_probe("тексты и чья цепочка", "Где живут тексты и чья цепочка действует", norm),
    ]
    dialog = ".mantine-Modal-content"
    return {
        "sales-chain": {"path": "/sales?tab=chain", "ready": ready, "probes": chain_list},
        "sales-chain-phone": {
            "path": "/sales?tab=chain",
            "ready": ready,
            "viewport": PHONE,
            "probes": [chain_list[0], *chain_list[2:7], chain_list[10], chain_list[12]],
        },
        "sales-chain-step": {
            "path": "/sales?tab=chain",
            "ready": ready,
            "probes": [
                ("заголовок окна", f"{dialog} .mantine-Modal-title", norm),
                ("что за шаг", f"{dialog} .chainHint", norm),
                ("подпись поля", f"{dialog} .mantine-InputWrapper-label", norm),
                ("пояснение поля", f"{dialog} .mantine-InputWrapper-description", norm),
                # Текст поля — плотный (`open_step`): шаблон стенда с пустыми строками между
                # зонами давал на свету 4,39 — вырезка берёт кромку поля из чернил
                # (`--field-edge`, 0,42), а по строке букв 5,86, внутри поля 6,11, по ядру
                # 15,55 (05.10.2026); тот же класс, что у окна записи базы знаний.
                ("текст письма", f"{dialog} textarea", norm),
                ("подпись переключателя", f"{dialog} .mantine-Switch-label", norm),
                ("кнопка «Показать письмо»", f"{dialog} button:has-text('Показать письмо')", big),
                ("кнопка «Сохранить»", f"{dialog} button:has-text('Сохранить')", big),
                ("от кого", f"{dialog} .chainLetter > p:first-child", norm),
                ("имя зоны", f"{dialog} .chainZone .mantine-Group-root p", norm),
                ("что с зоной", f"{dialog} .chainZone .mantine-Badge-label", norm),
                ("текст зоны", f"{dialog} .chainZone > p", norm),
                ("подпись из настроек", f"{dialog} .chainSigned", norm),
                ("адреса нет", f"{dialog} .chainLetter > p:text-matches('не задан')", norm),
                ("заголовок «не готова»", f"{dialog} .mantine-Alert-title", norm),
                ("слова «не готова»", f"{dialog} .mantine-Alert-message", norm),
                ("что подставлено", f"{dialog} .chainValues", norm),
            ],
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
        "sales-chain-step": open_step,
        **funnel_prepare(),
    }
