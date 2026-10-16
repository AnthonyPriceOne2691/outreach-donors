"""Ключ — название фирмы (`sales/legal_forms.py`, `sales/segment_keywords.py`): смета предупреждает
словами, если в ключе организационно-правовая форма любой страны отдельным словом, — выдача по
имени фирмы найдёт её реестры и карточки, а не сегмент. Ключ не отсеивается: решает человек.

Формы — разных стран и письменностей, с точками и без, в начале, середине и конце ключа; внутри
слова форма — не форма; обычные ключи разных ниш и языков — без пометки. Фирмы выдуманы.
"""

from __future__ import annotations

import unicodedata

import pytest
from backend.features.sales import legal_forms
from backend.features.sales.legal_forms import firm_form
from backend.features.sales.segment_keywords import FIRM, clean_keywords


@pytest.mark.parametrize(
    ("key", "form"),
    [
        ("ТОО Логистик Сервис", "ТОО"),
        ("отзывы ооо ромашка москва", "ооо"),
        ("АО «Образец» Алматы", "АО"),
        ("ип иванов грузоперевозки", "ип"),
        ("trans wisla sp. z o.o.", "sp. z o.o."),
        ("trans wisla sp z oo", "sp z oo"),
        ("Müller Bau GmbH Köln", "GmbH"),
        ("acme l.l.c.", "l.l.c."),
        ("Acme LLC Texas", "LLC"),
        ("Acme Pty Ltd", "Pty Ltd"),
        ("Rossi Costruzioni S.p.A.", "S.p.A."),
        ("Rossi Costruzioni SpA", "SpA"),
        ("Industrias Lopez S.L.", "S.L."),
        ("Comercial Norte S.A. de C.V.", "S.A. de C.V."),
        ("Hansen Transport A/S", "A/S"),
        ("Vzorek Stavby a.s.", "a.s."),
        ("Sample Holding N.V. Rotterdam", "N.V."),
        ("Kaupmees OÜ Tallinn", "OÜ"),
        ("Pavyzdys UAB", "UAB"),
        ("Minta Kft. Budapest", "Kft."),
        ("Örnek Lojistik A.Ş.", "A.Ş."),
        ("Δείγμα Α.Ε. Αθήνα", "Α.Ε."),
        ("შპს ნიმუში თბილისი", "შპს"),
        ("טבע דוגמה בע״מ", "בע״מ"),
        ("شركة النموذج ذ.م.م دبي", "ذ.م.م"),
        ("東京サンプル株式会社", "株式会社"),
        ("サンプル㈱ 大阪", "(株)"),
        ("深圳样本贸易有限公司", "有限公司"),
        ("บจก. ตัวอย่าง", "บจก."),
    ],
    ids=[
        "kz-start",
        "ru-middle-lowercase",
        "ru-quoted-name",
        "ru-sole-trader",
        "pl-end-dotted",
        "pl-end-bare",
        "de-middle",
        "us-dotted",
        "us-bare-middle",
        "au-two-words",
        "it-dotted",
        "it-registry-case",
        "es-dotted",
        "mx-longest",
        "dk-slash",
        "cz-dotted-code",
        "nl-dotted-code-middle",
        "ee-diacritics",
        "lt",
        "hu",
        "tr",
        "gr",
        "ge-georgian",
        "il-rtl",
        "ae-rtl-dotted",
        "jp-attached",
        "jp-compatibility-sign",
        "cn-attached",
        "th",
    ],
)
def test_a_legal_form_of_any_country_marks_the_key_as_a_firm_name(key: str, form: str) -> None:
    assert firm_form(key) == form


@pytest.mark.parametrize(
    "key",
    [
        # Внутри слова форма — не форма.
        "Ltdbikes shop",
        "каолин купить оптом",
        "gmbhservice login",
        "ооокомпания",
        # Обычные ключи разных ниш и языков.
        "доставка грузов алматы",
        "dentist berlin",
        "abogado laboral madrid",
        "стоматологическая клиника",
        # Сокращение, которое без точек — обычное слово или единица: регистр не реестра.
        "software as a service",
        "ab workout at home",
        "50 kg rice bag",
        "spa salon riga",
        "rossi costruzioni spa",
        "vos sabés que",
        "ska band tickets",
        "lda topic model",
        "vitamin ivs near me",
        "aş evi istanbul",
        # Без точек — арабское слово, а не форма.
        "شركة ذمم مالية",
        # Двухбуквенная форма без точек совпадает с кодом региона или единицей — не форма.
        "dentist Calgary AB",
        "plumber Reno NV",
        "Adelaide SA dentist",
        "25 KG rice bag",
    ],
)
def test_an_ordinary_key_or_a_form_inside_a_word_is_not_a_firm(key: str) -> None:
    assert firm_form(key) is None


@pytest.mark.parametrize("form", legal_forms.CODES)
def test_a_two_letter_form_that_is_also_a_code_counts_only_with_dots(form: str) -> None:
    """«Calgary AB», «Reno NV», «25 KG» — регион и вес, а не фирма: двухбуквенная форма, которая
    совпадает с кодом страны, штата или единицей, считается только с точками. «Volvo AB» без
    точек не помечается — ложная пометка хуже пропущенной."""
    bare = form.replace(".", "")

    assert firm_form(f"Sample Group {bare}") is None
    assert firm_form(f"{bare} Sample Group") is None
    assert firm_form(f"Sample Group {form}") == form
    assert firm_form(f"Sample Group {form.lower()}") == form.lower()


def test_the_forms_are_data_of_many_countries_and_scripts() -> None:
    """Список — данными в одном модуле, с источником: формы многих стран и письменностей."""
    forms = (
        *legal_forms.FORMS,
        *legal_forms.CASED,
        *legal_forms.CODES,
        *legal_forms.DOTTED,
        *legal_forms.ATTACHED,
    )
    scripts = {_script(form) for form in forms}

    assert len(forms) >= 100
    assert len(set(forms)) == len(forms)
    assert {"LATIN", "CYRILLIC", "GREEK", "HEBREW", "ARABIC", "CJK", "THAI", "GEORGIAN"} <= scripts
    assert "ISO 20275" in (legal_forms.__doc__ or "")


def _script(form: str) -> str:
    letter = next(char for char in form if char.isalpha())
    return unicodedata.name(letter).split(" ")[0]


def test_the_note_names_the_form_as_written_and_the_key_is_taken() -> None:
    found = clean_keywords("ТОО Логистик Сервис\nгрузоперевозки алматы\nMüller Bau GmbH")

    assert found.taken == ["тоо логистик сервис", "грузоперевозки алматы", "müller bau gmbh"]
    assert found.rejected == {}
    assert found.notes == {
        "тоо логистик сервис": FIRM.format(form="ТОО"),
        "müller bau gmbh": FIRM.format(form="GmbH"),
    }
    assert FIRM.format(form="ТОО") == (
        "похоже на название фирмы («ТОО»): выдача найдёт её реестры и карточки, а не сегмент"
    )


def test_a_firm_note_joins_the_near_repeat_note_in_the_same_words() -> None:
    """Две пометки одного ключа — в одном поле слов, через «; »: «похож на» не затирается."""
    found = clean_keywords("ТОО Стоматология Сапа\nТОО Стоматология Сапа Алматы")

    note = found.notes["тоо стоматология сапа алматы"]
    near, firm = note.split("; ")
    assert near.startswith("похож на «тоо стоматология сапа» — общих слов 3 из 4")
    assert firm == FIRM.format(form="ТОО")
    assert found.notes["тоо стоматология сапа"] == FIRM.format(form="ТОО")


def test_the_case_of_the_form_is_read_as_the_human_wrote_it_not_normalized() -> None:
    """Взятый ключ — в нижнем регистре, а форма `CASED` считается по вписанному: «Rossi
    Costruzioni SpA» — фирма, «rossi costruzioni spa milano» — салон, не фирма."""
    found = clean_keywords("Rossi Costruzioni SpA\nrossi costruzioni spa milano")

    assert found.notes["rossi costruzioni spa"] == FIRM.format(form="SpA")
    assert "фирмы" not in found.notes["rossi costruzioni spa milano"]
