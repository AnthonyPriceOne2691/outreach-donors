"""«Письма» → «Текст первого письма» — для замера контраста.

Часть каталога `ui_screens.py`, своим модулем: основной каталог стоит у предела длины файла.

«Письма рекламодателям» (`letters-advertisers`, 23.10.2026) — переключатель двух аудиторий
Этапа 2: с отдельными пунктами меню он ушёл с «Писем донорам», и мерить его — здесь.

Текст первого письма правится в окне (09.10.2026). Мерятся два состояния, и в каждом —
то, на что в нём смотрят:

- **окно открыто** (`letter-draft`): заголовок, пояснение, подписи, поля и «Вернуть
  исходный текст» — на стекле окна. Кнопка меряется оживлённой: подготовка правит зону,
  выключенная дала бы серое на сером;
- **окно закрыто** (`letter-draft-badge`): значок «поправлен» рядом с кнопкой окна. При
  открытом окне он лежит под размытой подложкой, и замер мерил пятно: до порога Оцу
  корзиной — «не измерено: одна краска», после — 2,54 на свету и 1,60 в темноте (перемер
  продаж 20.10.2026). Правка закрытием окна не теряется (`LetterDraftEditor.tsx`).

Нормы передаются параметрами, а не импортируются из `ui_screens`: тот подключает этот
модуль, и обратный импорт замкнул бы круг.
"""

from typing import Any

from playwright.sync_api import expect

#: Окно правки — по его имени; оно же — подпись кнопки, которая его открывает.
DIALOG = "Текст первого письма"
#: Значок «поправлен / по умолчанию» рядом с кнопкой окна.
BADGE = "[data-letter-draft] .mantine-Badge-root"
#: Переключатель адресата и его подпись — без подвижной подложки.
SWITCH = "[aria-label='Кому письма']"
INNER = ".mantine-SegmentedControl-innerLabel"


def open_letter_draft(page: Any) -> None:
    """Открыть окно текста письма и поправить зону.

    Правка оживляет «Вернуть исходный текст» и переводит значок
    в «поправлен» — мерить надо то состояние, в котором на них смотрят.
    """
    page.get_by_role("button", name=DIALOG).click()
    expect(page.get_by_role("dialog", name=DIALOG)).to_be_visible()
    # Приветствие — «Hi,», три знака в широкой рамке: вырезка почти целиком
    # фон, и в тёмной теме замер давал 1,9 : 1 полю, у которого те же стили,
    # что у «Вопросов» с 5,5 : 1. Меряется поле с фразой, а не с тремя знаками.
    page.get_by_label("Приветствие").fill("Hello there, and thank you for reading this,")
    field = page.get_by_label("Условия")
    field.fill(field.input_value() + " Thank you.")
    expect(page.get_by_role("button", name="Вернуть исходный текст")).to_be_enabled()
    page.wait_for_timeout(300)


def close_letter_draft(page: Any) -> None:
    """Поправить зону и закрыть окно: значок «поправлен» — на странице, без подложки окна."""
    open_letter_draft(page)
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog", name=DIALOG)).to_be_hidden()
    expect(page.locator(BADGE)).to_have_text("поправлен")
    page.wait_for_timeout(300)


def letters_screens(norm: float, big: float) -> dict[str, dict[str, Any]]:
    """Экраны и точки. `norm` — норма обычного текста, `big` — крупного."""
    return {
        "letter-draft": {
            # Эталон «число в поле формы» здесь не меряется — форма сборки под окном
            # размыта подложкой; мелкий текст в поле сверяется с ним на «letters».
            "path": "/letters?stage=donors",
            "ready": ("button", DIALOG),
            "probes": [
                ("заголовок окна", "[role='dialog'] .mantine-Modal-title", big),
                ("пояснение окна", "[role='dialog'] .mantine-Stack-root > p", norm),
                ("название зоны", "[role='dialog'] .mantine-InputWrapper-label", norm),
                ("что сделает модель", "[role='dialog'] .mantine-InputWrapper-description", norm),
                ("текст зоны «Приветствие»", "[role='dialog'] textarea", norm),
                ("текст зоны «Вопросы»", "[role='dialog'] textarea >> nth=3", norm),
                (
                    "кнопка «Вернуть исходный»",
                    "[role='dialog'] button:has-text('Вернуть исходный текст')",
                    big,
                ),
            ],
        },
        "letter-draft-badge": {
            "path": "/letters?stage=donors",
            "ready": ("button", DIALOG),
            "probes": [("значок «поправлен»", BADGE, norm)],
        },
        # Переключатель аудитории: подпись невыбранной тише выбранной — меряются обе, и
        # внутренняя подпись, а не обёртка `-label`: вырезка обёртки захватывает подвижную
        # подложку и занижает число (24.09.2026 — 3,5 : 1 тексту, который читается чисто).
        "letters-advertisers": {
            "path": "/letters?stage=advertisers",
            "ready": ("heading", "Письма рекламодателям"),
            "probes": [
                ("заголовок раздела", "h3", big),
                ("аудитория, выбранная", f"{SWITCH} label[data-active] {INNER}", norm),
                ("аудитория, другая", f"{SWITCH} label:not([data-active]) {INNER}", norm),
                ("пояснение под ним", "p.mantine-Text-root", norm),
                ("пункт меню части", "nav a[data-active]", norm),
            ],
        },
    }


def letters_prepare() -> dict[str, Any]:
    """Подготовка экранов перед замером: без правки зоны оба состояния — «по умолчанию»."""
    return {"letter-draft": open_letter_draft, "letter-draft-badge": close_letter_draft}
