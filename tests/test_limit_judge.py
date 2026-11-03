"""Строка панели лимитов о судье рекламодателей: свой потолок суток и выбранное по журналу расхода.

Строка только для чтения: потолок — настройка сервера. Выбрано — той же операцией журнала, что
считает потолок прохода: число панели не расходится с отказом судьи.
"""

from __future__ import annotations

import pytest
from backend.api.settings import limit_panel
from backend.config import llm as llm_cfg
from backend.features.core import llm_caps
from backend.features.core.domain import Stage
from backend.features.core.usage import record
from backend.features.crawl.judge_pass import JUDGE_OPERATION
from backend.features.ops import limit_judge
from sqlalchemy.ext.asyncio import AsyncSession


async def test_the_row_names_the_cap_and_counts_the_judge_only(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_DAILY_TOKEN_CAP", 2_000_000)
    record(session, operation=JUDGE_OPERATION, units=3_300)
    record(session, operation="site_topic", units=900)
    await session.flush()

    [row] = await limit_judge.rows(session)

    assert (row.key, row.task, row.unit) == (limit_judge.KEY, "Аутрич", "токенов")
    assert (row.per_day, row.used_day, row.used_month) == (2_000_000, 3_300, 3_300)
    assert row.source == ".env ADVERTISER_JUDGE_DAILY_TOKEN_CAP"
    assert row.editable == frozenset()
    assert row.now is not None
    assert "2 000 000" in row.now.replace("\xa0", " ")


async def test_no_own_cap_is_said_in_words(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_DAILY_TOKEN_CAP", 0)

    [row] = await limit_judge.rows(session)

    assert row.per_day is None
    assert row.now is not None
    assert "Своего потолка нет" in row.now


@pytest.mark.parametrize(
    ("saved", "words"),
    [
        (False, "проход отказывает, пока её не поправят"),
        (True, "своего потолка нет, проход держит лимит «Аутрич»"),
    ],
)
async def test_an_unread_cap_is_said_as_the_pass_behaves(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, saved: bool, words: str
) -> None:
    # Опечатка в настройке (кросс-ревью продаж 03.11): без сохранённого блока проход отказывает
    # (`usage._readable`), с блоком держит лимит части — строка панели говорит то же самое.
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "UNREAD_CAPS", frozenset({limit_judge.SETTING}))
    monkeypatch.setattr(llm_caps, "block", lambda: object() if saved else None)

    [row] = await limit_judge.rows(session)

    assert row.now is not None
    assert row.now.startswith("Настройка потолка не прочитана")
    assert words in row.now


def test_the_row_belongs_to_the_advertisers_stage() -> None:
    assert limit_panel.stage_of(limit_judge.KEY) is Stage.ADVERTISERS
    assert limit_judge in limit_panel.READ_ONLY
