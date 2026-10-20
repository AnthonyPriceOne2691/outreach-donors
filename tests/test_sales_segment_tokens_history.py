"""Токены судьи на сайт в смете прогона — из истории прогонов с судьёй, как доля сайтов.

Разбор прогона сбора: смета считала константой 1 100 на сайт (замер одного прогона), а живой
прогон вышел заметно дороже. Длина описания сегмента, текст главных и рассуждение модели у каждой
гипотезы свои — число берётся из своих прогонов (`segment_history.tokens_per_site`): суммой, на
сайт, который судья спросил заново; горсть — не история; нет истории — запасное число и слова.

Настройки сметы — те же, что у её тестов (`test_sales_segment_estimate._settings`); числа истории
выдуманы и не круглы, ключи — разных ниш и языков.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.config import llm as llm_cfg
from backend.config.judge import JudgeMode
from backend.features.sales import segment_estimate as se
from backend.features.sales import segment_history
from backend.features.sales.segment_estimate import estimate
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_spend import number
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_estimate import _settings, ask
from tests.test_sales_segment_run import hypothesis

__all__ = ["_settings"]  # настройки сметы — те же, что у её тестов

#: Ключи разных ниш и языков: стоматология (de), чай (ja), книги (tr).
KEYS = "zahnarzt berlin mitte\n日本茶 通販\nikinci el kitap satın al"


async def _past(session: AsyncSession, owner_id: int, judge: Any) -> None:
    """Прошлый прогон с итогом судьи в отчёте."""
    session.add(
        SalesSegmentRunModel(
            hypothesis_id=owner_id,
            keywords=["ключ"],
            country="de",
            language="en",
            depth_pages=1,
            mode=JudgeMode.SHADOW.value,
            estimate={},
            report={"judge": judge},
        )
    )
    await session.flush()


def _judge_line(lines: list[str]) -> str:
    return next(line for line in lines if line.startswith("Судья сегмента"))


async def test_without_history_the_spare_number_says_so(session: AsyncSession) -> None:
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, keywords=KEYS))

    assert (found.tokens_per_site, found.tokens_per_site_runs) == (se.TOKENS_PER_SITE, 0)
    assert found.tokens == found.sites * se.TOKENS_PER_SITE
    assert "(по 1 100 на сайт — запасное число: прогонов сбора с судьёй ещё нет)" in (
        _judge_line(found.lines)
    )
    assert (found.as_dict()["tokens_per_site"], found.as_dict()["tokens_per_site_runs"]) == (
        1100,
        0,
    )


async def test_tokens_per_site_come_from_the_runs_with_the_judge(session: AsyncSession) -> None:
    """Суммой по прогонам, на сайт, который судья спросил заново: судил (и правилом) или не
    дождался модели. Кэш — не в счёте."""
    owner = await hypothesis(session)
    await _past(
        session,
        owner.id,
        {"tokens": 13_361, "judged": 9, "unanswered": 1, "from_cache": 40, "by_rule": 2},
    )
    await _past(session, owner.id, {"tokens": 30_007, "judged": 22, "unanswered": 0})

    found = await estimate(session, ask(owner.id, keywords=KEYS))

    per_site = round((13_361 + 30_007) / (9 + 1 + 22))
    assert (found.tokens_per_site, found.tokens_per_site_runs) == (per_site, 2)
    assert found.tokens == found.sites * per_site
    line = _judge_line(found.lines)
    assert f"(по {number(per_site)} на сайт, как в прошлых прогонах сбора (с судьёй — 2))" in line


@pytest.mark.parametrize(
    "judge",
    [
        {"tokens": 0, "judged": 30},  # судья выключен или всё из кэша
        {"tokens": 41_000, "judged": 0, "unanswered": 0},
        {"tokens": "много", "judged": 30},  # поле правили руками
        "не отчёт",
        {"judged": 30},  # токенов нет — прогон до счёта
    ],
)
async def test_runs_without_judged_sites_or_tokens_are_not_history(
    session: AsyncSession, judge: Any
) -> None:
    owner = await hypothesis(session)
    await _past(session, owner.id, judge)

    assert await segment_history.tokens_per_site(session) == (0, 0)


async def test_a_handful_of_sites_is_not_history(session: AsyncSession) -> None:
    owner = await hypothesis(session)
    await _past(
        session,
        owner.id,
        {"tokens": 19_000, "judged": segment_history.MIN_JUDGED_SITES - 1, "unanswered": 0},
    )

    found = await estimate(session, ask(owner.id, keywords=KEYS))

    assert (found.tokens_per_site, found.tokens_per_site_runs) == (se.TOKENS_PER_SITE, 0)


async def test_only_the_last_runs_count(session: AsyncSession) -> None:
    owner = await hypothesis(session)
    await _past(session, owner.id, {"tokens": 900_000, "judged": 30})  # давний и дорогой
    for _ in range(segment_history.HISTORY_RUNS):
        await _past(session, owner.id, {"tokens": 37_230, "judged": 30, "unanswered": 0})

    assert await segment_history.tokens_per_site(session) == (1241, segment_history.HISTORY_RUNS)


async def test_the_judge_off_spends_no_tokens_whatever_the_history(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("backend.config.sales.SEGMENT_JUDGE_MODE", JudgeMode.OFF)
    owner = await hypothesis(session)
    await _past(session, owner.id, {"tokens": 40_000, "judged": 30})

    found = await estimate(session, ask(owner.id, keywords=KEYS))

    assert found.tokens == 0


async def test_the_model_cap_is_checked_against_the_tokens_of_history(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """По константе прогон влез бы в остаток потолка, по истории — нет: отказ словами."""
    owner = await hypothesis(session)
    await _past(session, owner.id, {"tokens": 60_000, "judged": 30})
    sites = (await estimate(session, ask(owner.id, keywords=KEYS))).sites
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", sites * se.TOKENS_PER_SITE + 10)

    found = await estimate(session, ask(owner.id, keywords=KEYS))

    assert found.tokens == sites * 2000
    assert found.refusal is not None
    assert found.refusal.startswith("смета модели выше остатка дневного потолка")
