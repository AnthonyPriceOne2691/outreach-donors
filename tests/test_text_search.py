"""Поиск по набранному (`shared/database/text_search.py`) — на настоящей базе.

Два свойства, на которых стоят все поля поиска: набранное — текст, а не шаблон, и регистр
сворачивается в любой письменности. Второе до 11.11.2026 держалось на локали кластера: с
`LC_CTYPE=C` «анна» не находила «Анна», и тесты экранов падали на таком кластере, а на обычном
были зелёными. Теперь правила регистра называет сам запрос — тест сверяет и слова запроса, и
ответ базы: первый краснеет без сортировки на любом кластере, второй — на кластере `C`.
"""

from __future__ import annotations

import pytest
from backend.features.core.models.domain import DomainModel
from backend.shared.database.text_search import ANY_SCRIPT, contains
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
