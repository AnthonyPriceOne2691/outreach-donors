"""Поиск по набранному (`shared/database/text_search.py`) — на настоящей базе.

Два свойства, на которых стоят все поля поиска: набранное — текст, а не шаблон, и регистр
сворачивается в любой письменности. Второе до 11.11.2026 держалось на локали кластера: с
`LC_CTYPE=C` «анна» не находила «Анна», и тесты экранов падали на таком кластере, а на обычном
были зелёными. Теперь правила регистра называет сам запрос — тест сверяет и слова запроса, и
ответ базы: первый краснеет без сортировки на любом кластере, второй — на кластере `C`.

Третье — у полей имени сайта и адреса (`contains_host`, 18.11.2026): база хранит домены IDN в ASCII
(`xn--e1afmkfd.xn--p1ai`), экраны показывают их письменностью сайта («пример.рф»), и набранное
так находит запись базы — целой меткой.
"""

from __future__ import annotations

import pytest
from backend.features.core.models.domain import DomainModel
from backend.shared.database.text_search import ANY_SCRIPT, contains, contains_host
from backend.shared.net.idn import ascii_host, shown_host, stored_name
from sqlalchemy import literal, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession


def test_the_comparison_names_its_own_case_rules() -> None:
    """Правила регистра — у запроса, а не у кластера."""
    sql = str(contains(DomainModel.host, "анна").compile(dialect=postgresql.dialect()))

    assert f'COLLATE "{ANY_SCRIPT}") ILIKE' in sql


@pytest.mark.parametrize(
    ("stored", "typed"),
    [
        ("Анна Смирнова", "анна"),
        ("анна смирнова", "АННА"),
        ("Κτήμα Άλφα", "κτήμα"),
        ("Route AI", "route ai"),
        ("山田酒造", "山田"),
    ],
)
async def test_typed_text_is_found_in_any_script_whatever_the_case(
    session: AsyncSession, stored: str, typed: str
) -> None:
    assert await session.scalar(select(contains(literal(stored), typed))) is True


@pytest.mark.parametrize(
    ("stored", "typed", "found"),
    [
        ("скидка 100%", "100%", True),
        ("скидка 100 руб", "100%", False),
        ("a_b", "_", True),
        ("ab", "_", False),
        ("путь C:\\work", "c:\\w", True),
        ("  Анна  ", " анна ", True),
    ],
)
async def test_typed_text_is_text_not_a_pattern(
    session: AsyncSession, stored: str, typed: str, found: bool
) -> None:
    """«%», «_» и «\\» значат сами себя; пробелы по краям набранного не ищутся."""
    assert await session.scalar(select(contains(literal(stored), typed))) is found


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("пример", "xn--e1afmkfd"),
        (" Пример.РФ ", "xn--e1afmkfd.xn--p1ai"),
        ("info@пример.рф", "info@xn--e1afmkfd.xn--p1ai"),
        # Кусок имени, пока набирают: точка без зоны, зона без имени.
        ("пример.", "xn--e1afmkfd."),
        (".рф", ".xn--p1ai"),
        ("shop-цветы.рф", "xn--shop--lwev8h7a5c.xn--p1ai"),
        ("сад.example.test", "xn--80ai1b.example.test"),
        ("例え.テスト", "xn--r8jz45g.xn--zckzah"),
        ("пример。рф", "xn--e1afmkfd.xn--p1ai"),
    ],
)
def test_a_name_typed_in_its_script_gets_the_form_the_base_keeps(typed: str, stored: str) -> None:
    assert stored_name(typed) == stored


@pytest.mark.parametrize(
    "typed",
    [
        "example.com",  # уже ASCII — другой формы нет
        "",
        "пример рф",  # пробел — не имя
        "пример,рф",
        "имя@пример.рф",  # имя ящика не в ASCII: в ASCII его не записать
        "а" * 64,  # метка длиннее 63 знаков
    ],
)
def test_text_without_an_ascii_form_has_none(typed: str) -> None:
    assert stored_name(typed) is None


@pytest.mark.parametrize(
    "host", ["пример.рф", "сад.example.test", "例え.テスト", "κτήμα.example.test"]
)
def test_the_stored_form_reads_back_as_the_name_on_screen(host: str) -> None:
    """`stored_name` обратно к `shown_host`: что показали, то и найдётся."""
    stored = stored_name(host)

    assert stored is not None
    assert stored.isascii()
    assert shown_host(stored) == host


@pytest.mark.parametrize(
    ("host", "stored"),
    [
        ("пример.рф", "xn--e1afmkfd.xn--p1ai"),
        ("Пример.РФ", "xn--e1afmkfd.xn--p1ai"),
        ("shop.пример.рф", "shop.xn--e1afmkfd.xn--p1ai"),
        # Метка в ASCII — как есть: подчёркивание в поддомене IDNA бы не пропустила.
        ("my_shop.пример.рф", "my_shop.xn--e1afmkfd.xn--p1ai"),
        # UTS 46 без переходной схемы: «ß» остаётся «ß», а не «ss» (IDNA 2003).
        ("straße.example.test", "xn--strae-oqa.example.test"),
        ("例え.テスト", "xn--r8jz45g.xn--zckzah"),
    ],
)
def test_a_host_gets_the_ascii_key(host: str, stored: str) -> None:
    assert ascii_host(host) == stored


@pytest.mark.parametrize(
    "host",
    [
        "Example.COM",  # в ASCII — байт в байт, регистр тоже
        "sub_domain.example.com",
        "xn--e1afmkfd.xn--p1ai",
        "",
        "пример рф",  # не кодируется — как есть, без исключения
        "пример..рф",
        "пример.рф.",
        "а" * 64 + ".рф",
    ],
)
def test_a_host_without_another_form_stays_as_given(host: str) -> None:
    assert ascii_host(host) == host


def test_typed_ascii_is_searched_once() -> None:
    """Набранное в ASCII — то же условие, что у `contains`: второго сравнения нет."""
    dialect = postgresql.dialect()

    assert str(contains_host(DomainModel.host, "example").compile(dialect=dialect)) == str(
        contains(DomainModel.host, "example").compile(dialect=dialect)
    )


@pytest.mark.parametrize(
    ("stored", "typed", "found"),
    [
        ("xn--e1afmkfd.xn--p1ai", "пример", True),
        ("xn--e1afmkfd.xn--p1ai", "Пример.РФ", True),
        ("xn--e1afmkfd.xn--p1ai", "рф", True),
        ("shop.xn--e1afmkfd.xn--p1ai", "пример.рф", True),
        ("info@xn--e1afmkfd.xn--p1ai", "info@пример.рф", True),
        ("info@xn--e1afmkfd.xn--p1ai", "пример.рф", True),
        ("xn--e1afmkfd.xn--p1ai", "XN--E1AFMKFD", True),
        ("example.com", "пример", False),
        ("xn--80ai1b.example.test", "пример", False),
        # Обрывок метки ASCII-формы не находит: punycode кодирует метку целиком.
        ("xn--e1afmkfd.xn--p1ai", "прим", False),
        # Имя, записанное письменностью, находит обрывок — первая половина условия.
        ("пример.рф", "прим", True),
        # Знаки шаблона — по-прежнему текст.
        ("xn--e1afmkfd.xn--p1ai", "_", False),
    ],
)
async def test_a_name_typed_in_its_script_finds_the_ascii_form(
    session: AsyncSession, stored: str, typed: str, found: bool
) -> None:
    assert await session.scalar(select(contains_host(literal(stored), typed))) is found
