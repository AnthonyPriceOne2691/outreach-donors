"""Вкладка «Рассылка» раздела продаж и строка срока на «Очереди писем» для замера контраста (круг 22).

Своим модулем: каталог продаж (`ui_screens_sales.py`) стоит у предела длины файла — экраны
подключает модуль очереди (`ui_screens_sales_queue.py`): «Рассылка» — соседняя вкладка, а строка
срока стоит на очереди. Нормы передаются параметрами — обратный импорт замкнул бы круг.

Пять экранов. Вкладка на 1440 — вступление и «обновлено», режим строками сервера, плитки срока,
плашка писем, которые не уйдут, слова срока, адреса по гипотезам, сегодня (у ящика стенда три
мягких отказа за сутки — плашка сниженных лимитов) и окно получателя, план и факт по дням, ящики;
на 390 — плитки в две колонки, таблицы карточками. Причины «не напишет» и «сняты» появляются только
по стрелке — свой экран. Строка «очередь уйдёт к …» и «собирать нечего» у гипотезы, которой сборка
не напишет никому, — экраны «Очереди писем». Очередь, письма, ящики и лиды — в базе стенда; как
считается прогноз и план — в «i» у заголовков: меряется в открытой подсказке.

Ждать есть чего: замер идёт через 900 мс после перезагрузки, а прогноз на стенде считается дольше.
Шаг ожидания — у первой точки экрана (шаги точки идут перед ней), без отдельной подготовки в
каталоге.
"""

from typing import Any

from ui_screens_sales_hints import SECTION_FIELD, hint_probe

#: Узкое окно — телефон: плитки в две колонки, таблицы карточками.
PHONE = {"width": 390, "height": 844}
#: Точки только широкого окна: шапки таблиц на телефоне спрятаны — подписи стоят у чисел карточки.
WIDE_ONLY = frozenset({"шапка адресов", "шапка плана"})


def settled(page: Any) -> None:
    """Обе ручки ответили: плитки срока и ящики — прогноза, таблица адресов — частей адресов."""
    for selector in (".mailingEtaTiles", ".mailingParts", ".mailingBoxes"):
        page.locator(selector).first.wait_for()
    page.wait_for_timeout(300)


def eta_line(page: Any) -> None:
    """Строка срока на «Очереди писем» встаёт, когда ответил прогноз, — после очереди."""
    page.locator(".queueEta").first.wait_for()
    page.wait_for_timeout(300)


def nothing_to_build(page: Any) -> None:
    """«Очередь писем» гипотезы, которой сборка не напишет никому, — перебором списка «Гипотеза»:
    номер гипотезы у стенда свой, а строка «собирать нечего» — признак, по которому её узнать."""
    field = page.get_by_role("textbox", name="Гипотеза")
    field.click()
    page.get_by_role("option").first.wait_for()
    count = page.get_by_role("option").count()
    page.keyboard.press("Escape")
    for index in range(count):
        field.click()
        page.get_by_role("option").nth(index).click()
        page.locator(".metricTile").first.wait_for()
        page.wait_for_timeout(600)
        if page.locator(".queueNothing").count():
            return
    raise AssertionError("на стенде нет гипотезы, которой сборка не напишет никому")


def open_reasons(page: Any) -> None:
    """Причины у итога раскрыты — «не напишет» и «сняты»: списки под строкой «Все гипотезы»."""
    settled(page)
    for name in ("Почему сборка не напишет: все гипотезы", "Почему сняты: все гипотезы"):
        page.get_by_role("button", name=name, exact=False).first.click()
    page.locator(".mailingWhy li").first.wait_for()
    page.wait_for_timeout(300)


def _tables(norm: float) -> list[tuple[str, str, float]]:
    """Адреса по гипотезам, сегодня, план, факт и ящики."""
    return [
        ("из чего складывается «всего»", ".mailingPartsSum", norm),
        ("шапка адресов", ".mailingParts thead th:text-is('В очереди')", norm),
        ("имя гипотезы", ".mailingParts tbody td.cellName p", norm),
        ("число части", ".mailingParts tbody tr:first-child td:nth-child(3)", norm),
        ("сегодня строкой", "p.mailingToday", norm),
        ("лимиты снижены — плашкой", ".mailingCut p", norm),
        ("окно получателя", ".mailingWindow", norm),
        ("шапка плана", ".mailingPlan thead th:text-is('Мощность ящиков')", norm),
        ("день плана", ".mailingPlan tbody td.cellName p:first-child", norm),
        ("пометка «сегодня»", ".mailingPlan tbody td.cellName p:nth-child(2)", norm),
        ("число плана", ".mailingPlan tbody tr:first-child td:nth-child(3)", norm),
        ("почему меньше мощности", ".mailingWhyNot p", norm),
        ("день факта", ".mailingPast tbody td.cellName p", norm),
        ("число факта", ".mailingPast tbody tr:last-child td:nth-child(2)", norm),
        ("адрес ящика", ".mailingBoxes tbody td.cellName p", norm),
        ("ступень ящика", ".mailingBoxes tbody tr:first-child td:nth-child(2)", norm),
    ]


def mailing_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка «Рассылка» (1440 и 390), её причины раскрытыми (1440) и строка срока очереди."""
    ready = ("heading", "Когда уйдёт очередь")
    path = "/sales?tab=mailing"
    tiles = ".mailingEtaTiles .metricTile"
    top = [
        ("вступление вкладки", ".mailingIntro", norm, settled),
        ("когда обновлено", ".mailingUpdated", norm),
        ("значок режима", ".mailingMode .queueAutoBadge .mantine-Badge-label", norm),
        ("строка режима", ".mailingMode .queueAuto", norm),
        ("заголовок блока", "section.mailingEta h5", norm),
        ("подпись плитки срока", f"{tiles} p:nth-child(1)", norm),
        ("число плитки срока", f"{tiles} p:nth-child(2)", big),
        ("пояснение плитки", f"{tiles} p:nth-child(3)", norm),
        ("плашка «не уйдут»", ".mailingStuck .mantine-Alert-message > p", norm),
        ("причина «не уйдут»", ".mailingStuck li", norm),
        ("слова срока сервера", ".mailingEtaWords", norm),
    ]
    tables = _tables(norm)
    hints = [
        hint_probe("как считается прогноз", "Как считается прогноз рассылки", norm),
        hint_probe("как считается план", "Как считается «План на 7 дней»", norm),
    ]
    return {
        "sales-mailing": {"path": path, "ready": ready, "probes": [*top, *tables, *hints]},
        "sales-mailing-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            # На телефоне — без шапок таблиц (строка — карточкой) и без заголовка блока: тот же, что
            # на 1440; список «Раздел» вместо вкладок.
            "probes": [
                (*SECTION_FIELD, norm, settled),
                *top[:4],
                *top[5:],
                *[probe for probe in tables if probe[0] not in WIDE_ONLY],
                hints[1],
            ],
        },
        "sales-mailing-reasons": {
            "path": path,
            "ready": ready,
            "probes": [
                ("заголовок причин", ".mailingWhy > p", norm, open_reasons),
                ("причина словами сервера", ".mailingWhy li", norm),
            ],
        },
        "sales-queue-eta": {
            "path": "/sales?tab=queue",
            "ready": ("heading", "Продажи"),
            "probes": [
                ("срок очереди строкой", ".queueEta", norm, eta_line),
                ("ссылка на «Рассылку»", ".queueEta a", norm),
            ],
        },
        # Сборка не напишет никому (цепочки языка лидов нет), а без письма есть: кнопка закрыта,
        # строка «собирать нечего» — с числом без письма и входом в «Рассылку».
        "sales-queue-nothing": {
            "path": "/sales?tab=queue",
            "ready": ("heading", "Продажи"),
            "probes": [
                ("собирать нечего", ".queueNothing", norm, nothing_to_build),
                ("ссылка «почему»", ".queueNothing a", norm),
                ("сколько напишет сборка", ".metricTile p:nth-child(3)", norm),
            ],
        },
    }
