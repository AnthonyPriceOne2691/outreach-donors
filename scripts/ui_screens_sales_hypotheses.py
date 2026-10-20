"""Вкладка «Гипотезы» и окно гипотезы в режиме правки — для замера контраста.

Своим модулем: каталог продаж (`ui_screens_sales.py`) стоит у предела длины файла. Нормы
передаются параметрами — как у каталога продаж, обратный импорт замкнул бы круг.

Строка гипотезы — имя и «Править» под ним, описание в три строки и «показать целиком» (когда
длиннее), числа-ссылки и нули. Окно правки — поля с «i» у подписей и «Удалить гипотезу» под
ними: у пустой кнопка открыта, у держащей закрыта, и под ней — что держит (выключенная кнопка
не меряется, меряются слова). Подтверждение удаления — у самой кнопки. Номер гипотезы, которой
больше нет, в адресе — строка «больше нет» под вкладками.

Поле «страны компаний» (судья сегмента v4) — в окне правки: подпись переключателя «Компании из
других стран», выбранный и невыбранный его пункт, подпись «Не пишем компаниям из» и страна в поле
(у держащей — выбрана), и пояснение под подписью «Пусто — никому не отказываем по стране». Было
подсказкой в поле — бледными чернилами подсказки (`--ink-faint`) оно мерилось 3,43 на свету.

Стенд: гипотеза `EMPTY` пустая, `HELD` держат лиды и прогон, её описание длиннее трёх строк;
без них точки окон честно «не найдены».
"""

from collections.abc import Callable
from typing import Any

from playwright.sync_api import expect

#: Узкое окно — телефон: таблица — в прокрутке, имя и «Править» — в первой колонке.
PHONE = {"width": 390, "height": 844}
DIALOG = ".mantine-Modal-content"
TABLE = ".hypothesesTable"
#: Подтверждение у кнопки (`ConfirmPopover`): просто `.mantine-Popover-dropdown` — это ещё и
#: скрытый список «Раздел».
CONFIRM = ".mantine-Popover-dropdown.glassSolid"
#: Номер гипотезы, которой на стенде нет.
LOST = "/sales?tab=hypotheses&hypothesis=99999"
#: Гипотезы стенда: пустая — удаляется, держащая — нет.
EMPTY = "Clínicas dentales MX"
HELD = "Школы английского"


def _edit(name: str) -> Callable[[Any], None]:
    """Окно правки гипотезы `name` — кнопкой «Править» в её строке."""

    def open_edit(page: Any) -> None:
        page.get_by_role("button", name=f"Править гипотезу «{name}»").click()
        expect(page.locator(DIALOG)).to_be_visible()
        # Что держит гипотезу, окно читает при открытии — ждём слова или открытую кнопку.
        page.wait_for_timeout(700)

    return open_edit


def open_confirm(page: Any) -> None:
    """Подтверждение удаления пустой гипотезы — у самой кнопки."""
    _edit(EMPTY)(page)
    page.get_by_role("button", name="Удалить гипотезу").click()
    expect(page.locator(CONFIRM)).to_be_visible()
    page.wait_for_timeout(400)


def open_lost(page: Any) -> None:
    """Строка «больше нет» живёт, пока не сменили гипотезу: замер перезагружает страницу уже по
    исправленному адресу — заходим по старой ссылке заново."""
    page.goto(f"{page.url.split('/sales')[0]}{LOST}")
    expect(page.locator(".glassPanel p[role='status']")).to_be_visible()
    page.wait_for_timeout(900)


def hypothesis_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Вкладка на 1440 и 390, окно пустой и держащей, подтверждение, строка «больше нет»."""
    ready = ("heading", "Продажи")
    path = "/sales?tab=hypotheses"
    rows = [
        ("подпись колонки", f"{TABLE} thead th:text-is('Готовы')", norm),
        ("имя гипотезы", f"{TABLE} tbody td:first-child .mantine-Group-root > p", norm),
        ("кнопка «Править»", f"{TABLE} tbody button:has-text('Править')", norm),
        ("описание гипотезы", f"{TABLE} .hypothesisDescription", norm),
        ("«показать целиком»", f"{TABLE} button:has-text('показать целиком')", norm),
        ("число-ссылка", f"{TABLE} tbody a", norm),
        ("ноль без ссылки", f"{TABLE} tbody td p:text-is('0')", norm),
        ("дата", f"{TABLE} tbody td:last-child p", norm),
    ]
    countries = [
        (
            "подпись переключателя стран",
            f"{DIALOG} div.mantine-Text-root:has-text('других стран')",
            norm,
        ),
        ("выбранный пункт", f"{DIALOG} .mantine-SegmentedControl-label[data-active]", norm),
        ("невыбранный пункт", f"{DIALOG} .mantine-SegmentedControl-label:not([data-active])", norm),
        ("подпись «Не пишем…»", f"{DIALOG} .mantine-InputWrapper-label:has-text('Не пишем')", norm),
        ("страна в поле", f"{DIALOG} .mantine-Pill-label", norm),
        (
            "пояснение «Не пишем…»",
            f"{DIALOG} .mantine-InputWrapper-description:has-text('никому не отказываем')",
            norm,
        ),
    ]
    window = [
        ("заголовок окна", f"{DIALOG} .mantine-Modal-title", norm),
        ("подпись поля", f"{DIALOG} .mantine-InputWrapper-label", norm),
        ("пояснение поля", f"{DIALOG} .mantine-InputWrapper-description", norm),
        ("кнопка «Отмена»", f"{DIALOG} button:has-text('Отмена')", big),
        ("кнопка «Сохранить»", f"{DIALOG} button:has-text('Сохранить')", big),
    ]
    return {
        "sales-hypotheses": {"path": path, "ready": ready, "probes": rows},
        "sales-hypotheses-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": rows[1:5],
        },
        "sales-hypothesis-edit": {
            "path": path,
            "ready": ready,
            "probes": [
                *window,
                ("кнопка «Удалить гипотезу»", f"{DIALOG} .hypothesisRemove button", big),
            ],
        },
        "sales-hypothesis-held": {
            "path": path,
            "ready": ready,
            "probes": [
                *window[:3],
                *countries,
                ("что держит", f"{DIALOG} .hypothesisRemove p", norm),
            ],
        },
        "sales-hypothesis-held-phone": {
            "path": path,
            "ready": ready,
            "viewport": PHONE,
            "probes": [*countries, ("что держит", f"{DIALOG} .hypothesisRemove p", norm)],
        },
        "sales-hypothesis-confirm": {
            "path": path,
            "ready": ready,
            "probes": [
                ("вопрос с именем", f"{CONFIRM} p", norm),
                # «Отмена» не меряется: у кнопки вида `default` обводка — тёмная бирюза 42 %, и
                # по коробке она уходит в «чернила» Оцу — на свету 3,77 при тексте oklch(0,23) на
                # белой заливке: 16,6 : 1 по ядру буквы против заливки (вырезка снимка стенда
                # 15.10.2026), в темноте 14,0 тем же замером. Тот же класс, что у просторного поля.
                ("кнопка «Удалить»", f"{CONFIRM} button:has-text('Удалить')", norm),
            ],
        },
        "sales-hypothesis-lost": {
            "path": LOST,
            "ready": ready,
            "probes": [("гипотезы больше нет", ".glassPanel p[role='status']", norm)],
        },
    }


def hypothesis_prepare() -> dict[str, Callable[[Any], None]]:
    """Что открыть до замера: окна открываются нажатием."""
    return {
        "sales-hypothesis-edit": _edit(EMPTY),
        "sales-hypothesis-held": _edit(HELD),
        "sales-hypothesis-held-phone": _edit(HELD),
        "sales-hypothesis-confirm": open_confirm,
        "sales-hypothesis-lost": open_lost,
    }
