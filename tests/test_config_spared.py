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
from backend.config._base import DomainSettings, Fallback, spared
from backend.features.core import usage
from pydantic import Field, ValidationError
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
print(outreach.direction_limit("sales"), outreach.direction_limit("donors"),
      outreach.direction_unread("sales"), outreach.direction_unread("donors"),
      sorted(outreach.UNREAD_LIMITS))
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
    # Ноль продажам — и имя непрочитанной настройки: по нему отказ отправки и экран «Домены
    # рассылки» говорят «не прочитана», а не «0 из 0» (ревью продаж 13.10.2026).
    assert limits == "0 7 OUTREACH_SALES_DAILY_LIMIT None ['OUTREACH_SALES_DAILY_LIMIT']"
    assert model == "sales model: refused"
    for name in ("SALES_DAILY_TOKEN_CAP", "OUTREACH_SALES_DAILY_LIMIT"):
        assert f"НАСТРОЙКА НЕ ПРОЧИТАНА — {name}: в ней «{typo}»" in done.stderr
    assert "взято: ноль токенов продажам" in done.stderr
    assert "взято: 0 — первых писем продаж в сутки ноль" in done.stderr


@pytest.mark.parametrize(
    ("cap", "limit", "caps", "limits"),
    [
        ("1200", "40", "1200 [] 900", "40 7 None None []"),
        ("0", "0", "0 [] 900", "0 7 None None []"),
        ("", "", "None [] 900", "None 7 None None []"),
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


class _Probe(DomainSettings):
    """Модуль настроек в том, что видит `spared`: одна настройка щадится, другая — нет."""

    SPARED_PROBE_CAP: int = Field(default=7, ge=0)
    SPARED_PROBE_SHARED: int = 1


#: Страховка только для потолка — как у продаж в `config/llm.py`.
PROBE_FALLBACKS = {
    "SPARED_PROBE_CAP": Fallback(0, "целое число не меньше нуля", "ноль — потолок закрыт")
}


def test_a_readable_setting_is_taken_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    """В этом процессе, без подпроцесса: тот же разбор, что при импорте конфига."""
    monkeypatch.setenv("SPARED_PROBE_CAP", "3")

    found, unread = spared(_Probe, PROBE_FALLBACKS)

    assert (found.SPARED_PROBE_CAP, unread) == (3, {})


@pytest.mark.parametrize("raw", ["-5", "10k"])
def test_an_unreadable_spared_setting_takes_its_safe_value_and_says_so(
    raw: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SPARED_PROBE_CAP", raw)

    with caplog.at_level("ERROR", logger="backend.config._base"):
        found, unread = spared(_Probe, PROBE_FALLBACKS)

    assert (found.SPARED_PROBE_CAP, found.SPARED_PROBE_SHARED) == (0, 1)
    assert unread == {"SPARED_PROBE_CAP": raw}
    [said] = caplog.records
    assert said.levelname == "ERROR"
    assert all(
        part in said.getMessage()
        for part in ("SPARED_PROBE_CAP", f"«{raw}»", "не меньше нуля", "потолок закрыт")
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"SPARED_PROBE_SHARED": "abc"},
        {"SPARED_PROBE_SHARED": "abc", "SPARED_PROBE_CAP": "-5"},
    ],
    ids=["общая", "общая-и-щадимая"],
)
def test_an_unreadable_unspared_setting_is_refused(
    settings: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, raw in settings.items():
        monkeypatch.setenv(name, raw)

    with pytest.raises(ValidationError):
        spared(_Probe, PROBE_FALLBACKS)
