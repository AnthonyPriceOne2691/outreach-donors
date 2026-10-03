"""Страна и часовой пояс лида — срез 1.4 (решение владельца 03.10).

Страна — кодом по таблице названий RU/EN (`sales/geo.py`), пояс — из колонки
файла, иначе по стране. Таблица — данные, и тест держит её форму: коды уникальны,
каждый пояс есть в базе поясов машины, у страны с несколькими поясами — пояс
столицы и замечание (решение владельца 04.10). Путь строки файла — через
`intake.preview`; запись — на настоящей базе дерева.

Утверждения точные: испорченная строка таблицы или правило колонки краснеет.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import available_timezones

import pytest
from backend.features.sales import geo, intake
from backend.features.sales.columns import LeadField, guess
from backend.features.sales.intake import Problem
from backend.features.sales.models import SalesHypothesisModel, SalesLeadModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

#: У этих стран поясов несколько — ставится столичный, с замечанием в отчёте.
SEVERAL_ZONES = (
    ("us", "America/New_York"),
    ("ru", "Europe/Moscow"),
    ("ca", "America/Toronto"),
    ("au", "Australia/Sydney"),
    ("br", "America/Sao_Paulo"),
    ("mx", "America/Mexico_City"),
    ("id", "Asia/Jakarta"),
    ("cl", "America/Santiago"),
    ("es", "Europe/Madrid"),
    ("pt", "Europe/Lisbon"),
)
#: Ни единого пояса, ни столицы: Антарктида и необитаемые острова.
NO_ZONE_AT_ALL = frozenset({"aq", "bv", "hm", "um"})
#: Пояс по стране: одна зона на страну — и две поправки (Китай по закону, Украина без Крыма).
ONE_ZONE = (
    ("de", "Europe/Berlin"),
    ("gb", "Europe/London"),
    ("fr", "Europe/Paris"),
    ("jp", "Asia/Tokyo"),
    ("cn", "Asia/Shanghai"),
    ("ua", "Europe/Kyiv"),
    ("ae", "Asia/Dubai"),
)
FILE = (
    "Email;Country;Time zone\n"
    "a@acme.example.test;Germany;\n"
    "b@acme.example.test;USA;America/New_York\n"
    "c@acme.example.test;USA;\n"
    "d@acme.example.test;Россия;europe/moscow\n"
    "e@acme.example.test;Нарния;\n"
    "f@acme.example.test;de;Berlin\n"
    "g@acme.example.test;;Asia/Tokyo\n"
)
FILE_LEADS = [
    ("de", "Europe/Berlin"),
    ("us", "America/New_York"),
    ("us", "America/New_York"),
    ("ru", "Europe/Moscow"),
    (None, None),
    ("de", "Europe/Berlin"),
    (None, "Asia/Tokyo"),
]
BY_CAPITAL = (
    "часовой пояс по столице — America/New_York: у страны us их несколько, в файле не задан"
)
FILE_NOTES = [
    Problem(4, BY_CAPITAL, "USA", loaded=True),
    Problem(6, intake.NO_COUNTRY, "Нарния", loaded=True),
    Problem(7, intake.NO_ZONE, "Berlin", loaded=True),
]


def _preview(tmp_path: Path, text: str) -> intake.Preview:
    path = tmp_path / "база.csv"
    path.write_text(text, encoding="utf-8")
    return intake.preview(intake.read_file(path))


def test_table_codes_are_unique_lowercase_and_every_zone_is_real() -> None:
    codes = [code for code, *_ in geo.COUNTRIES]
    zones = available_timezones()

    assert len(set(codes)) == len(codes) == 249
    assert all(len(code) == 2 and code.isascii() and code.islower() for code in codes)
    assert all(en and ru for _, en, ru, _ in geo.COUNTRIES)
    assert {zone for *_, zone in geo.COUNTRIES if zone} <= zones
    assert all(code in dict.fromkeys(codes) for code in geo.ALIASES.values())
    # Столичный пояс — только у страны, у которой единого пояса нет, и наоборот.
    without_zone = {code for code, *_, zone in geo.COUNTRIES if not zone}
    assert set(geo.CAPITAL_ZONES) == without_zone - NO_ZONE_AT_ALL
    assert set(geo.CAPITAL_ZONES.values()) <= zones


def test_single_zone_countries_get_it_and_several_zone_ones_the_capital_with_a_mark() -> None:
    assert [(code, geo.timezone_for(code)) for code, _ in SEVERAL_ZONES] == list(SEVERAL_ZONES)
    assert [(code, geo.timezone_for(code)) for code, _ in ONE_ZONE] == list(ONE_ZONE)
    assert [geo.by_capital(code) for code, _ in SEVERAL_ZONES] == [True] * len(SEVERAL_ZONES)
    assert [geo.by_capital(code) for code, _ in ONE_ZONE] == [False] * len(ONE_ZONE)
    assert [geo.timezone_for(code) for code in ("xx", *sorted(NO_ZONE_AT_ALL))] == [None] * 5


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("DE", "de"),
        ("de", "de"),
        ("Germany", "de"),
        ("Германия", "de"),
        ("  germany ", "de"),
        ("U.S.A.", "us"),
        ("United States of America", "us"),
        ("Соединённые Штаты", "us"),
        ("США", "us"),
        ("UK", "gb"),
        ("Великобритания", "gb"),
        ("Côte d'Ivoire", "ci"),
        ("Чехия", "cz"),
        ("Czech Republic", "cz"),
        ("Нарния", None),
        ("", None),
        ("12", None),
    ],
)
def test_country_code_reads_codes_names_and_aliases_in_both_languages(
    text: str, code: str | None
) -> None:
    assert geo.country_code(text) == code


@pytest.mark.parametrize(
    ("text", "zone"),
    [
        ("Europe/Berlin", "Europe/Berlin"),
        ("europe/berlin", "Europe/Berlin"),
        ("  america/new_york ", "America/New_York"),
        ("UTC", "UTC"),
        ("Berlin", None),
        ("+1", None),
        ("", None),
    ],
)
def test_zone_name_is_canonical_or_none(text: str, zone: str | None) -> None:
    assert geo.zone_name(text) == zone


def test_timezone_column_is_recognised_by_its_titles() -> None:
    titles = ["Email", "Часовой пояс", "Страна"]
    assert guess(titles) == {
        LeadField.EMAIL: 0,
        LeadField.TIMEZONE: 1,
        LeadField.COUNTRY: 2,
    }
    assert guess(["email", "time zone"])[LeadField.TIMEZONE] == 1


def test_country_is_a_code_and_the_zone_comes_from_the_column_else_the_country(
    tmp_path: Path,
) -> None:
    found = _preview(tmp_path, FILE)

    assert found.mapping[LeadField.TIMEZONE] == 2
    assert [(lead.country, lead.timezone) for lead in found.leads] == FILE_LEADS
    assert found.problems == FILE_NOTES
    assert found.rejected == 0


async def test_country_and_zone_are_written_to_the_lead(
    session: AsyncSession, tmp_path: Path
) -> None:
    hypothesis = SalesHypothesisModel(name="сайты EN")
    session.add(hypothesis)
    await session.flush()

    loaded = await intake.load(session, _preview(tmp_path, FILE), hypothesis.id, author_id=None)

    rows = await session.execute(
        select(SalesLeadModel.country, SalesLeadModel.timezone).order_by(SalesLeadModel.id)
    )
    assert loaded == 7
    assert [(country, zone) for country, zone in rows] == FILE_LEADS
