"""Книга «План работ» — в черновик плана (`sales/plan_file.py`, `sales/plan_card.py`).

Проверяется то, ради чего модуль: лист — гипотеза, инструкция и скрытые листы пропускаются словами;
карточка читается по подписи, а не по номеру строки; значения — в поля гипотезы и прогона, ошибка
поля — словами с подписью; ключи на любом языке и письменности — те, что купит выдача, с подсчётом
убранного; плохой лист не роняет остальные; книга отвергается целиком только словами — не .xlsx,
больше предела, бомба, битый ZIP; прокси пропускает самую большую книгу. Книги собираются в памяти.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

import openpyxl
import pytest
from backend.features.sales import plan_file
from backend.features.sales.hypothesis_countries import OtherCountries
from backend.features.sales.plan_card import DEFAULT_DEPTH, Card, Field
from backend.features.sales.plan_file import (
    ALL_REJECTED,
    GUIDE,
    HIDDEN,
    MAX_SHEET_KEYS,
    NO_KEYS,
    NO_KEYS_ROW,
    TOO_MANY_KEYS,
    UNREADABLE_SHEET,
    PlanDraft,
    PlanFileError,
    PlanHypothesis,
    read_plan,
)

ROOT = Path(__file__).resolve().parent.parent
PROXIES = (ROOT / "deploy" / "nginx.conf", ROOT / "deploy" / "proxy" / "outreach.conf")
BODY_LIMIT = re.compile(r"^\s*client_max_body_size\s+(\d+)m\s*;", re.M)

#: Карточка листа — как в книге-примере: подпись, значение (данные выдуманные).
CARD: dict[str, object] = {
    "Название": "Логистика — пример",
    "Кому пишем": "Транспортные компании со своим сайтом; не каталоги и не СМИ.",
    "Где ищем": "Казахстан",
    "Язык писем": "русский",
    "Компании из других стран": "подходят",
    "Не пишем компаниям из": "Украина",
    "Найти сайтов": 5000,
    "Глубина поиска до": "топ-50",
}
KEYS = ("грузоперевозки алматы", "доставка грузов из китая", "складские услуги астана")

Sheet = tuple[str, Sequence[Sequence[object]]]


def rows(
    card: dict[str, object] | None = None, keys: Sequence[object] = KEYS
) -> list[list[object]]:
    """Лист-гипотеза: карточка в строках 1–8 с подсказкой в C, пустая 9, «Ключи» в 10, ключи ниже."""
    found: list[list[object]] = [
        [label, value, "подсказка"] for label, value in (CARD if card is None else card).items()
    ]
    found += [[], ["Ключи", None, "По одному в строке"]]
    return found + [[key] for key in keys]


def book(*sheets: Sheet, guide: bool = True, hidden: Sheet | None = None) -> bytes:
    """Книга в памяти: лист «Как заполнять» и листы-гипотезы по порядку."""
    made = openpyxl.Workbook()
    first = made.active
    assert first is not None
    made.remove(first)
    if guide:
        made.create_sheet("Как заполнять").append(["План работ — пример файла"])
    for title, lines in sheets:
        sheet = made.create_sheet(title)
        for line in lines:
            sheet.append(list(line))
    if hidden is not None:
        sheet = made.create_sheet(hidden[0])
        for line in hidden[1]:
            sheet.append(list(line))
        sheet.sheet_state = "hidden"
    out = io.BytesIO()
    made.save(out)
    return out.getvalue()


def one(card: dict[str, object] | None = None, keys: Sequence[object] = KEYS) -> PlanHypothesis:
    """Единственная гипотеза книги из одного листа."""
    [found] = read_plan(book(("Пилот", rows(card, keys))), "plan.xlsx").hypotheses
    return found


def changed(**values: object) -> dict[str, object]:
    """Карточка примера с другими значениями; `None` — строки поля нет вовсе."""
    card = dict(CARD)
    for label, value in values.items():
        name = label.replace("_", " ")
        if value is None:
            card.pop(name)
        else:
            card[name] = value
    return card


def test_two_hypothesis_sheets_and_the_guide_sheet() -> None:
    second = changed(
        Название="Курьеры — пример",
        Где_ищем="Узбекистан",
        **{"Компании из других стран": "на проверку", "Не пишем компаниям из": ""},
        Найти_сайтов="",
        Глубина_поиска_до="топ-30",
    )

    draft = read_plan(
        book(("Пилот 1", rows()), ("Пилот 2", rows(second, ["курьерская служба ташкент"]))),
        "План работ.xlsx",
    )

    assert draft.skipped == {"Как заполнять": GUIDE}
    first, other = draft.hypotheses
    assert (first.sheet, first.ready, first.issues, first.warnings) == ("Пилот 1", True, (), ())
    assert first.card == Card(
        name="Логистика — пример",
        segment="Транспортные компании со своим сайтом; не каталоги и не СМИ.",
        markets=("kz",),
        language="ru",
        other_countries=OtherCountries.ACCEPT,
        excluded_countries=("UA",),
        target=5000,
        depth_pages=5,
    )
    assert first.keys.taken == KEYS
    assert (other.card.markets, other.card.other_countries) == (("uz",), OtherCountries.REVIEW)
    assert (other.card.excluded_countries, other.card.target, other.card.depth_pages) == (
        (),
        None,
        3,
    )
    assert draft.ready == (first, other)


def test_the_card_is_read_by_label_not_by_row_number() -> None:
    """Строки переставлены, между ними пустые; регистр, пробелы, «ё» и двоеточие не важны."""
    lines: list[list[object]] = [
        ["  ГДЕ   ищем: ", "Казахстан"],
        [],
        ["глубина поиска до", "топ-50"],
        ["Язык  писем", "русский"],
        ["название", "Логистика — пример"],
        ["Не пишем компаниям из:", "Украина"],
        ["КОМУ ПИШЕМ", CARD["Кому пишем"]],
        ["Найти сайтов", 5000],
        ["Компании из других стран", "подходят"],
        [],
        [],
        ["ключи:"],
        *[[key] for key in KEYS],
    ]

    [found] = read_plan(book(("Пилот", lines)), "plan.xlsx").hypotheses

    assert found.ready
    assert found.card == one().card


def test_keys_in_any_language_and_script_are_taken_as_the_serp_buys_them() -> None:
    keys = [
        "Грузоперевозки  Алматы",  # кириллица, двойной пробел, заглавные
        "Freight forwarding Almaty",  # латиница
        "Lieferung Straße München",  # латиница с диакритикой
        "жүк тасымалдау Алматы",  # казахский
        "Oʻzbekiston boʻylab yetkazib berish",  # узбекский латиницей
        "юк ташиш Тошкент",  # узбекский кириллицей
        "شحن البضائع دبي",  # арабская письменность
    ]

    found = one(keys=keys)

    assert found.ready
    assert found.keys.taken == (
        "грузоперевозки алматы",
        "freight forwarding almaty",
        "lieferung straße münchen",
        "жүк тасымалдау алматы",
        "oʻzbekiston boʻylab yetkazib berish",
        "юк ташиш тошкент",
        "شحن البضائع دبي",
    )
    assert (found.keys.blank, found.keys.repeats, found.keys.rejected) == (0, 0, {})


def test_spaces_blank_rows_and_repeats_are_removed_and_counted() -> None:
    keys: list[object] = [
        "  доставка грузов  ",
        None,
        "",
        "ДОСТАВКА   грузов",  # тот же запрос выдачи
        "доставка грузов алматы",
        "   ",
        "Доставка грузов",  # и ещё раз
        "склад астана",
        None,
        None,  # пустые в конце — не «между ключами»
    ]

    found = one(keys=keys)

    assert found.keys.taken == ("доставка грузов", "доставка грузов алматы", "склад астана")
    assert (found.keys.blank, found.keys.repeats) == (3, 2)


def test_garbage_is_rejected_like_in_a_collection_run_with_the_reason() -> None:
    found = one(keys=[*KEYS, '"доставка грузов"', "site:example.test доставка"])

    assert found.ready
    assert found.keys.taken == KEYS
    assert found.keys.rejected == {
        '"доставка грузов"': "остаток разметки в тексте",
        "site:example.test доставка": "поисковый оператор: так пользователи не ищут",
    }
    assert found.warnings == ("отсеяно ключей: 2 — выдача их не купит; причина — у каждого ключа",)


@pytest.mark.parametrize(
    ("values", "field", "said"),
    [
        ({"Название": ""}, Field.NAME, "«Название» не заполнено"),
        ({"Название": None}, Field.NAME, "«Название» не заполнено"),
        ({"Название": "я" * 129}, Field.NAME, "«Название»: имя длиннее 128 знаков (129)"),
        ({"Кому пишем": "   "}, Field.SEGMENT, "«Кому пишем» не заполнено"),
        ({"Где ищем": ""}, Field.MARKETS, "«Где ищем» не заполнено"),
        ({"Где ищем": "Атлантида"}, Field.MARKETS, "«Где ищем»: не страны: «Атлантида»"),
        (
            {"Где ищем": "Казахстан, Монголия"},
            Field.MARKETS,
            "«Где ищем»: нет среди рынков выдачи: «Монголия»",
        ),
        (
            {"Язык писем": "немецкий"},
            Field.LANGUAGE,
            "«Язык писем»: «немецкий» — письма пишутся на языках «английский», «русский»",
        ),
        (
            {"Компании из других стран": "иногда"},
            Field.OTHER,
            "«Компании из других стран»: «иногда» — нужно одно из: «подходят», «на проверку»",
        ),
        (
            {"Не пишем компаниям из": "Украина, Нарния"},
            Field.EXCLUDED,
            "«Не пишем компаниям из»: не страны: «Нарния»",
        ),
        ({"Найти сайтов": "пять тысяч"}, Field.TARGET, "«Найти сайтов»: «пять тысяч» — не число"),
        ({"Найти сайтов": 12.5}, Field.TARGET, "«Найти сайтов»: «12.5» — не число"),
        ({"Найти сайтов": 0}, Field.TARGET, "«Найти сайтов»: «0» — нужно число от 1 до 1 000"),
        (
            {"Глубина поиска до": "топ-100"},
            Field.DEPTH,
            "«Глубина поиска до»: «топ-100» — нужно одно из: «топ-10», «топ-20», «топ-30», «топ-50»",
        ),
    ],
)
def test_a_bad_field_is_an_error_of_its_sheet_and_the_other_sheet_is_read(
    values: dict[str, object], field: Field, said: str
) -> None:
    card = dict(CARD)
    for label, value in values.items():
        if value is None:
            card.pop(label)
        else:
            card[label] = value

    other = rows(changed(Название="Хороший пилот"))

    bad, good = read_plan(book(("Плохой", rows(card)), ("Хороший", other)), "plan.xlsx").hypotheses

    assert not bad.ready
    [issue] = bad.issues
    assert issue.field == field
    assert issue.words.startswith(said)
    assert good.ready


def test_empty_optional_fields_take_the_defaults_named_in_the_book() -> None:
    blank = changed(**{"Компании из других стран": "", "Не пишем компаниям из": ""})
    gone = changed(**{"Найти сайтов": None, "Глубина поиска до": None})

    assert one(blank).ready
    assert one(gone).ready
    assert (one(blank).card.other_countries, one(blank).card.excluded_countries) == (
        OtherCountries.REVIEW,
        (),
    )
    assert (one(gone).card.target, one(gone).card.depth_pages) == (None, DEFAULT_DEPTH)


def test_values_do_not_depend_on_case_spaces_dashes_and_separators() -> None:
    card = changed(
        Где_ищем="казахстан; Узбекистан, KZ",
        Язык_писем="English",
        **{"Компании из других стран": "  ПОДХОДЯТ ", "Не пишем компаниям из": "ua, Россия"},
        Найти_сайтов="5 000",
        Глубина_поиска_до="Топ – 20",
    )

    found = one(card)

    assert found.ready, found.issues
    assert (found.card.markets, found.card.language) == (("kz", "uz"), "en")
    assert (found.card.other_countries, found.card.excluded_countries) == (
        OtherCountries.ACCEPT,
        ("RU", "UA"),
    )
    assert (found.card.target, found.card.depth_pages) == (5000, 2)


def test_unknown_label_twice_the_same_label_and_a_value_without_label() -> None:
    lines = rows()
    lines[1:1] = [["Бюджет", "100"], [None, "одинокое значение"], ["Название", "Другое имя"]]

    found = one_of(lines)

    assert [(issue.field, issue.words) for issue in found.issues] == [
        (
            None,
            "строка 2: подписи «Бюджет» в карточке нет — поля карточки: «Название», «Кому "
            "пишем», «Где ищем», «Язык писем», «Компании из других стран», «Не пишем компаниям "
            "из», «Найти сайтов», «Глубина поиска до»",
        ),
        (None, "строка 3: значение «одинокое значение» без подписи в колонке A"),
        (Field.NAME, "«Название» дважды — в строках 1 и 4: оставьте одну"),
    ]


def one_of(lines: Sequence[Sequence[object]]) -> PlanHypothesis:
    [found] = read_plan(book(("Пилот", lines)), "plan.xlsx").hypotheses
    return found


def test_without_the_keys_row_each_key_is_not_an_error_of_its_own() -> None:
    lines = [
        line for line in rows(keys=[f"ключ {n}" for n in range(1, 50)]) if line[:1] != ["Ключи"]
    ]

    found = one_of(lines)

    words = [issue.words for issue in found.issues]
    assert len(words) == plan_file.MAX_STRAYS + 2
    assert words[-2:] == ["и ещё строк без подписи карточки: 46", NO_KEYS_ROW]


@pytest.mark.parametrize(
    ("keys", "said"),
    [([], NO_KEYS), ([None, "  "], NO_KEYS), (['"ключ"', "site:example.test"], ALL_REJECTED)],
)
def test_a_sheet_without_keys_to_buy_is_an_error(keys: list[object], said: str) -> None:
    found = one(keys=keys)

    assert [issue.words for issue in found.issues] == [said]


def test_a_sheet_takes_up_to_its_limit_of_keys_and_more_is_its_error() -> None:
    full = [f"ключ {n:05d}" for n in range(MAX_SHEET_KEYS)]  # не «ключ 2019»: прошедший год
    second = changed(Название="Второй")

    exact, over = read_plan(
        book(("Ровно", rows(keys=full)), ("Больше", rows(second, [*full, "ещё один"]))),
        "plan.xlsx",
    ).hypotheses

    assert exact.ready
    assert len(exact.keys.taken) == MAX_SHEET_KEYS
    assert [issue.words for issue in over.issues] == [TOO_MANY_KEYS]


def test_keys_shared_between_sheets_are_a_warning_of_the_later_sheet() -> None:
    abroad = changed(Название="Узбекистан", Где_ищем="Узбекистан")
    same = changed(Название="Тот же рынок")

    draft = read_plan(
        book(
            ("Первый", rows()),
            ("Другой рынок", rows(abroad, ["Доставка грузов из Китая", "новый ключ"])),
            ("Тот же рынок", rows(same, KEYS)),
        ),
        "plan.xlsx",
    )

    first, abroad_sheet, same_sheet = draft.hypotheses
    assert first.warnings == ()
    assert abroad_sheet.warnings == (
        "общих ключей с листом «Первый» — 1 (например, «доставка грузов из китая»): рынки у "
        "листов разные — выдача у ключей своя",
    )
    assert same_sheet.warnings == (
        "общих ключей с листом «Первый» — 3 (например, «грузоперевозки алматы», «доставка грузов "
        "из китая», «складские услуги астана»): на общем рынке выдачу по ним купят дважды",
        "общих ключей с листом «Другой рынок» — 1 (например, «доставка грузов из китая»): рынки "
        "у листов разные — выдача у ключей своя",
    )
    assert all(found.ready for found in draft.hypotheses)


def test_the_same_name_on_two_sheets_is_an_error_of_the_later_one() -> None:
    first, second = read_plan(
        book(("Первый", rows()), ("Второй", rows(changed(Название="  Логистика —  пример ")))),
        "plan.xlsx",
    ).hypotheses

    assert first.ready
    assert [(issue.field, issue.words) for issue in second.issues] == [
        (
            Field.NAME,
            "«Название»: «Логистика — пример» уже у листа «Первый» — по имени гипотезу выбирают, "
            "у листов плана имена разные",
        )
    ]


def test_hidden_and_empty_sheets_are_skipped_in_words() -> None:
    draft = read_plan(
        book(("Пилот", rows()), ("Лист2", []), hidden=("Старый", rows())), "plan.xlsx"
    )

    assert [found.sheet for found in draft.hypotheses] == ["Пилот"]
    assert draft.skipped == {"Как заполнять": GUIDE, "Старый": HIDDEN, "Лист2": plan_file.EMPTY}


def _rewritten(data: bytes, part: str, change: Callable[[bytes], bytes]) -> bytes:
    """Та же книга, у которой часть `part` переписана — так ломается лист."""
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            body = source.read(item.filename)
            target.writestr(item, change(body) if item.filename == part else body)
    return out.getvalue()


def test_a_broken_sheet_is_an_error_of_that_sheet_not_a_crash() -> None:
    data = book(("Хороший", rows()), ("Битый", rows(changed(Найти_сайтов=777))), guide=False)
    broken = _rewritten(
        data, "xl/worksheets/sheet2.xml", lambda body: body.replace(b"<v>777</v>", b"<v>abc</v>")
    )

    good, bad = read_plan(broken, "plan.xlsx").hypotheses

    assert good.ready
    assert (bad.sheet, [issue.words for issue in bad.issues]) == ("Битый", [UNREADABLE_SHEET])


@pytest.mark.parametrize(
    ("data", "name", "said"),
    [
        (b"", "plan.xlsx", "файл «plan.xlsx» пуст"),
        (b"\xd0\xcf\x11\xe0", "old.xls", r"старый формат Excel \(\.xls\)"),
        (b"%PDF-1.4", "plan.pdf", r"«plan\.pdf» — не книга Excel \(\.xlsx\)"),
        ("ключ;рынок\n".encode(), "plan.xlsx", r"не книга Excel \(\.xlsx\)"),
        (b"PK\x03\x04broken", "plan.xlsx", r"не читается как книга Excel \(\.xlsx\)"),
        (b"PK\x03\x04broken", "", "файл без имени не читается"),
    ],
)
def test_a_file_is_refused_whole_only_in_words(data: bytes, name: str, said: str) -> None:
    with pytest.raises(PlanFileError, match=said):
        read_plan(data, name)


def test_a_book_without_a_hypothesis_sheet_says_what_a_sheet_is() -> None:
    with pytest.raises(PlanFileError, match="нет ни одного листа-гипотезы: лист — карточка"):
        read_plan(book(("Лист2", [])), "plan.xlsx")


def test_more_sheets_than_the_plan_takes_is_refused_before_reading_them() -> None:
    sheets = [(f"Пилот {n}", rows(changed(Название=f"Пилот {n}"))) for n in range(11)]

    with pytest.raises(PlanFileError, match="листов-гипотез 11 — план берёт до 10"):
        read_plan(book(*sheets), "plan.xlsx")


def test_a_file_larger_than_the_limit_is_refused_before_reading() -> None:
    with pytest.raises(PlanFileError, match="больше 7 МБ — разделите план на две книги"):
        read_plan(b"PK\x03\x04" + bytes(plan_file.MAX_BYTES), "plan.xlsx")


def test_a_zip_bomb_is_refused_by_its_table_of_contents_before_unpacking() -> None:
    """Часть из нулей больше предела распаковки ужимается в сотни раз — файл маленький."""
    out = io.BytesIO(book(("Пилот", rows())))
    with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/media/zeros.bin", bytes(plan_file.MAX_UNPACKED + 1))
    data = out.getvalue()

    assert len(data) < plan_file.MAX_BYTES // 10
    with pytest.raises(PlanFileError, match=r"распаковывается в 128 МБ из \d+ частей"):
        read_plan(data, "plan.xlsx")


def test_the_limits_hold_the_largest_plan_asked_for() -> None:
    """«До 10 пилотов», «10, 20, 30 тысяч ключей»; распаковка — выше замера большой книги (40 МБ)."""
    assert (plan_file.MAX_SHEETS, MAX_SHEET_KEYS, plan_file.MAX_KEYS) == (10, 30_000, 300_000)
    assert plan_file.MAX_UNPACKED > 2 * 40 * 2**20


@pytest.mark.parametrize("conf", PROXIES, ids=lambda conf: conf.name)
def test_the_proxy_takes_the_largest_book_with_the_form_around_it(conf: Path) -> None:
    """Больше предела тела прокси отбил бы книгу своей страницей 413, мимо слов сервиса."""
    limits = [int(size) * 2**20 for size in BODY_LIMIT.findall(conf.read_text(encoding="utf-8"))]

    assert limits, f"в {conf.name} нет client_max_body_size"
    assert min(limits) >= plan_file.MAX_BYTES + 2**20


def test_the_draft_lists_ready_hypotheses_only() -> None:
    draft = PlanDraft(
        (one(), one(changed(Название=""))),
    )

    assert [found.card.name for found in draft.ready] == ["Логистика — пример"]
