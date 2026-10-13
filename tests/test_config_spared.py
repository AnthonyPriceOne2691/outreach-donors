"""Опечатка в настройке продаж не роняет общие процессы (ревью стыков 12.10.2026).

`SALES_DAILY_TOKEN_CAP` (`config/llm.py`) и `OUTREACH_SALES_DAILY_LIMIT` (`config/outreach.py`)
разбираются при импорте, а эти модули грузят все процессы. Негодное значение — `-5`, `10k`,
`abc` — было отказом pydantic, и по кругу падали API с вебхуками доноров, воркер, разбор и
проход добивок. Теперь такая настройка берётся безопасной (`config/_base.spared`): продажам —
ноль токенов (вызовы модели продаж отказывают) и ноль первых писем, а при старте — громкая
строка журнала: имя переменной, что в ней и что взято. Общие настройки — как были, и их
опечатка по-прежнему останавливает старт.

Импорт — в подпроцессе: в этом процессе конфиг уже загружен с настройками теста.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from backend.config import llm as llm_cfg
from backend.features.core import usage
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parents[1]

#: Процессы, которые грузят оба конфига: вебхуки и маршруты API, воркер, разбор со сторожем,
#: проход добивок, обходчик. Значения — общие и продаж; отказ модели продаж — по его потолку.
PROBE = """
import asyncio, importlib

for name in ("backend.api.app", "backend.workers.main", "backend.workers.reaper",
             "backend.workers.followups", "backend.workers.crawler"):
    importlib.import_module(name)

from backend.config import llm, outreach
from backend.features.core import usage
from backend.features.sales.usage_cap import sales_cap

# Журнал расхода, где за день не потрачено ничего: годный потолок вызов пропускает.
class NothingSpent:
    async def scalar(self, _statement):
        return 0


print(llm.SALES_DAILY_TOKEN_CAP, sorted(llm.UNREAD_CAPS), llm.DAILY_TOKEN_CAP)
print(outreach.direction_limit("sales"), outreach.direction_limit("donors"))
try:
    asyncio.run(usage.ensure_llm_within_cap(NothingSpent(), own=sales_cap()))
    print("sales model: allowed")
except usage.LlmCapExceededError:
    print("sales model: refused")
"""

#: Общие настройки рядом: их значения опечатка продаж не трогает.
SHARED = {"LLM_DAILY_TOKEN_CAP": "900", "OUTREACH_DONORS_DAILY_LIMIT": "7"}


def _started(settings: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **SHARED, **settings, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


@pytest.mark.parametrize("typo", ["-5", "10k", "abc"])
def test_a_typo_in_a_sales_setting_starts_the_processes_with_zero_for_sales(typo: str) -> None:
    done = _started({"SALES_DAILY_TOKEN_CAP": typo, "OUTREACH_SALES_DAILY_LIMIT": typo})

    assert done.returncode == 0, done.stderr
    caps, limits, model = done.stdout.strip().splitlines()[-3:]
    assert caps == "None ['SALES_DAILY_TOKEN_CAP'] 900"
    assert limits == "0 7"
    assert model == "sales model: refused"
    for name in ("SALES_DAILY_TOKEN_CAP", "OUTREACH_SALES_DAILY_LIMIT"):
        assert f"НАСТРОЙКА НЕ ПРОЧИТАНА — {name}: в ней «{typo}»" in done.stderr
    assert "взято: ноль токенов продажам" in done.stderr
    assert "взято: 0 — первых писем продаж в сутки ноль" in done.stderr


@pytest.mark.parametrize(
    ("cap", "limit", "caps", "limits"),
    [
        ("1200", "40", "1200 [] 900", "40 7"),
        ("0", "0", "0 [] 900", "0 7"),
        ("", "", "None [] 900", "None 7"),
    ],
    ids=["числа", "нули", "пусто"],
)
def test_good_values_are_read_as_before(cap: str, limit: str, caps: str, limits: str) -> None:
    done = _started({"SALES_DAILY_TOKEN_CAP": cap, "OUTREACH_SALES_DAILY_LIMIT": limit})

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-3:] == [caps, limits, "sales model: allowed"]
    assert "НАСТРОЙКА НЕ ПРОЧИТАНА" not in done.stderr


@pytest.mark.parametrize(
    "settings",
    [
        {"LLM_DAILY_TOKEN_CAP": "abc"},
        {"OUTREACH_DONORS_DAILY_LIMIT": "10k"},
        {"OUTREACH_DONORS_DAILY_LIMIT": "10k", "OUTREACH_SALES_DAILY_LIMIT": "abc"},
    ],
    ids=["общий-потолок", "лимит-доноров", "доноры-и-продажи"],
)
def test_a_typo_in_a_shared_setting_still_stops_the_start(settings: dict[str, str]) -> None:
    """Щадится только настройка продаж: опечатка в общей — отказ на старте, как всегда."""
    done = _started(settings)

    assert done.returncode != 0
    assert "ValidationError" in done.stderr


async def test_only_the_part_with_an_unread_setting_is_refused(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Потолок продаж не прочитан — отказ его части сразу; часть с прочитанной настройкой
    (черновики агента) идёт как шла."""
    monkeypatch.setattr(llm_cfg, "UNREAD_CAPS", frozenset({"SALES_DAILY_TOKEN_CAP"}))
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
    sales = usage.OwnCap(
        "вызовов модели продаж", frozenset({"made_up"}), 0, "SALES_DAILY_TOKEN_CAP"
    )
    drafts = usage.OwnCap("черновиков агента", frozenset({"made_up"}), 0, "AGENT_DAILY_TOKEN_CAP")

    with pytest.raises(usage.LlmCapExceededError, match="не прочитан из настроек"):
        await usage.ensure_llm_within_cap(session, own=sales)
    await usage.ensure_llm_within_cap(session, own=drafts)
