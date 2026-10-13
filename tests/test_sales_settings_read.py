"""Настройки продаж читаются, не падая: опечатка выключает продажи, а не общий старт.

`config/sales.py` читают при импорте API, общий воркер, worker-sales, reaper и консоль. Опечатка
в `SALES_*` — число вне границ, не «true/false», чужой режим судьи, окно отправки или время
отчёта не по образцу — роняла их все, а с ними приём ответов и события доноров. Теперь негодное
значение берёт умолчание поля, модуль продаж выключен целиком (`ENABLED`, агент продаж,
автоотправка, автосборка), а причина сказана словами: строкой журнала при старте процесса — имя,
значение и что годится, — и на экране продаж, без имён настроек.

Тесты — на поведение модуля, каким его видят процессы: `BROKEN`, тумблеры и значения после
импорта при заданном окружении, а не на то, как модуль читает (общим разбором настроек без
падения, `config/_base.spared`). Импорт с окружением — в подпроцессе: в этом
процессе модуль уже загружен целым, а перезагрузка здесь пересоздала бы перечисления режимов
судьи под ногами у соседних тестов.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.core.stages import SalesNotConnectedError
from backend.features.sales import connection
from backend.features.sales.sender import Sender
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parents[1]

#: Все переменные настроек продаж — так их называет `.env`.
ALIASES = [str(field.validation_alias) for field in sales_cfg._Sales.model_fields.values()]

#: Каждая настройка, которую разбирают при импорте: опечатка, слова о том, что годится, и что
#: модуль берёт вместо — значение своей постоянной (имя, умолчание).
BAD: dict[str, tuple[str, str, str, object]] = {
    "SALES_ENABLED": ("ture", "нужно true или false", "ENABLED", False),
    "SALES_AGENT_ENABLED": ("да", "нужно true или false", "AGENT_ENABLED", False),
    "SALES_AUTOSEND": ("вкл", "нужно true или false", "AUTOSEND", False),
    "SALES_AUTOBUILD": ("2", "нужно true или false", "AUTOBUILD", False),
    "SALES_TELEGRAM_GROUP_COPY": ("yes!", "нужно true или false", "TELEGRAM_GROUP_COPY", True),
    "SALES_SEGMENT_PAID_CONTACTS": ("нет", "нужно true или false", "SEGMENT_PAID_CONTACTS", False),
    "SALES_SEND_SPREAD_MIN": ("-5", "нужно целое от 0 до 1440", "SEND_SPREAD_MIN", 30),
    "SALES_OOO_DELAY_DAYS": ("0", "нужно целое от 1 до 60", "OOO_DELAY_DAYS", 7),
    "SALES_REWRITE_ATTEMPTS": ("11", "нужно целое от 1 до 10", "REWRITE_ATTEMPTS", 3),
    "SALES_AUTOSEND_PASS_SEC": ("5", "нужно целое от 10 до 3600", "AUTOSEND_PASS_SEC", 60),
    "SALES_AUTOSEND_GAP_MIN": ("0", "нужно целое от 1 до 240", "AUTOSEND_GAP_MIN", 15),
    "SALES_AUTOREPLY_MIN_N": ("30 решений", "нужно целое от 5 до 1000", "AUTOREPLY_MIN_N", 30),
    "SALES_COMPLAINT_PAUSE": ("0.3", "нужно число больше 0 до 0.02", "COMPLAINT_PAUSE", 0.001),
    "SALES_REPLY_CONFIDENCE": ("0,8", "нужно число от 0 до 1", "REPLY_CONFIDENCE", 0.8),
    "SALES_JUDGE_MODE": ("strict", "нужно одно из: shadow, enforce", "JUDGE_MODE", "enforce"),
    "SALES_SEGMENT_JUDGE_MODE": (
        "on",
        "нужно одно из: off, shadow, enforce",
        "SEGMENT_JUDGE_MODE",
        "shadow",
    ),
    "SALES_SEND_DAYS": ("пн-пт", "дни — числа 1…7", "SEND_DAYS", [0, 1, 2, 3, 4]),
    "SALES_SEND_HOURS": (
        "17:00-09:00",
        "окно закрывается раньше, чем открывается",
        "SEND_HOURS",
        ["09:00:00", "17:00:00"],
    ),
    "SALES_WEEKLY_REPORT_AT": ("MON 9", "например «MON 09:00»", "WEEKLY_REPORT_AT", None),
}

#: Тумблеры продаж включены — их выключает только негодная настройка.
SWITCHED_ON = {
    "SALES_ENABLED": "true",
    "SALES_AGENT_ENABLED": "true",
    "SALES_AUTOSEND": "true",
    "SALES_AUTOBUILD": "true",
}
TOGGLES = ("ENABLED", "AGENT_ENABLED", "AUTOSEND", "AUTOBUILD")

#: Границы двух настроек (ревью стыков 13.10): значение и годится ли оно. Разброс «на весь день»
#: (480) обрезает само окно; доля до 0.02 — та же одна жалоба на окно в 50 писем.
BOUNDS = [
    ("SALES_SEND_SPREAD_MIN", "0", True),
    ("SALES_SEND_SPREAD_MIN", "480", True),
    ("SALES_SEND_SPREAD_MIN", "1440", True),
    ("SALES_SEND_SPREAD_MIN", "1441", False),
    ("SALES_SEND_SPREAD_MIN", "-1", False),
    ("SALES_COMPLAINT_PAUSE", "0.02", True),
    ("SALES_COMPLAINT_PAUSE", "0.01", True),
    ("SALES_COMPLAINT_PAUSE", "0.0001", True),
    ("SALES_COMPLAINT_PAUSE", "0.021", False),
    ("SALES_COMPLAINT_PAUSE", "0.1", False),
    ("SALES_COMPLAINT_PAUSE", "0", False),
]

#: Время отчёта не по образцу — отказ: час без минут или минуты одной цифрой.
REFUSED_TIMES = ["MON 9", "MON 9:5", "MON 9:00:00", "MON 19:5"]


def _sample() -> dict[str, str]:
    """Настройки продаж из образца `.env.example` — из него собирают `.env` развёртывания."""
    found = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name in ALIASES:
            found[name] = value
    return found


def _cases() -> dict[str, dict[str, str]]:
    """Окружения, при которых процесс импортирует настройки продаж, — по имени случая."""
    return {
        "контроль": SWITCHED_ON,
        "образец": _sample(),
        "остальное как задано": {
            "SALES_AUTOSEND_GAP_MIN": "0",
            "SALES_AUTOSEND_PASS_SEC": "120",
            "SALES_SEND_HOURS": "17:00-09:00",
            "SALES_SEND_DAYS": "1-3",
        },
        "час одной цифрой": {
            "SALES_WEEKLY_REPORT_AT": "MON 9:00",
            "SALES_SEND_HOURS": "9:00-17:00",
        },
        **{name: SWITCHED_ON | {name: case[0]} for name, case in BAD.items()},
        **{f"{name}={value}": {name: value} for name, value, _ in BOUNDS},
        **{f"время {raw}": {"SALES_WEEKLY_REPORT_AT": raw} for raw in REFUSED_TIMES},
    }


RELOAD = """
import importlib, json, os, sys
from datetime import time

import backend.config.sales as sales

def plain(value):
    if isinstance(value, frozenset):
        return sorted(value)
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, tuple):
        return [plain(item) for item in value]
    return value

names = [str(field.validation_alias) for field in sales._Sales.model_fields.values()]
found = {}
for case, env in json.loads(sys.argv[1]).items():
    for name in names:
        os.environ.pop(name, None)
    os.environ.update(env)
    module = importlib.reload(sales)
    values = {name: plain(getattr(module, name)) for name in json.loads(sys.argv[2])}
    values["SEND_HOURS"] = plain((module.SEND_OPENS, module.SEND_CLOSES))
    found[case] = {"broken": module.BROKEN, "values": values}
print(json.dumps(found))
"""


def _clean_env() -> dict[str, str]:
    """Окружение подпроцесса: без `SALES_*` этого процесса, пакет — из корня дерева."""
    env = {name: value for name, value in os.environ.items() if name not in ALIASES}
    return {**env, "PYTHONPATH": str(ROOT)}


@pytest.fixture(scope="module")
def seen(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """Модуль настроек, импортированный при каждом окружении `_cases` — один подпроцесс на все:
    `BROKEN` и значения постоянных. Каталог без `.env` — читается только окружение случая."""
    consts = [*TOGGLES, *(case[2] for case in BAD.values() if case[2] != "SEND_HOURS")]
    done = subprocess.run(
        [sys.executable, "-c", RELOAD, json.dumps(_cases()), json.dumps(sorted(set(consts)))],
        cwd=tmp_path_factory.mktemp("no-env"),
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    found: dict[str, dict[str, Any]] = json.loads(done.stdout)
    return found


@pytest.mark.parametrize("name", list(BAD))
def test_a_typo_switches_sales_off_and_takes_the_default_in_words(
    seen: dict[str, dict[str, Any]], name: str
) -> None:
    bad, words, const, default = BAD[name]

    found = seen[name]

    assert list(found["broken"]) == [name]
    said = found["broken"][name]
    assert name in said, said
    assert f"«{bad}»" in said, said
    assert words in said, said
    assert [found["values"][toggle] for toggle in TOGGLES] == [False] * 4
    assert found["values"][const] == default


def test_without_a_typo_the_toggles_are_as_set(seen: dict[str, dict[str, Any]]) -> None:
    found = seen["контроль"]

    assert found["broken"] == {}
    assert [found["values"][toggle] for toggle in TOGGLES] == [True] * 4
    assert (found["values"]["SEND_DAYS"], found["values"]["SEND_HOURS"]) == (
        [0, 1, 2, 3, 4],
        ["09:00:00", "17:00:00"],
    )
    assert found["values"]["WEEKLY_REPORT_AT"] is None


def test_the_sample_environment_is_read_whole(seen: dict[str, dict[str, Any]]) -> None:
    """Образец `.env.example` годен целиком: из него собирают `.env` развёртывания."""
    assert seen["образец"]["broken"] == {}


def test_a_typo_takes_its_default_and_the_other_values_stay(
    seen: dict[str, dict[str, Any]],
) -> None:
    found = seen["остальное как задано"]

    assert sorted(found["broken"]) == ["SALES_AUTOSEND_GAP_MIN", "SALES_SEND_HOURS"]
    values = found["values"]
    assert (values["AUTOSEND_GAP_MIN"], values["AUTOSEND_PASS_SEC"]) == (15, 120)
    assert (values["SEND_HOURS"], values["SEND_DAYS"]) == (["09:00:00", "17:00:00"], [0, 1, 2])


@pytest.mark.parametrize(("name", "value", "fine"), BOUNDS)
def test_spread_and_complaint_share_have_bounds(
    seen: dict[str, dict[str, Any]], name: str, value: str, fine: bool
) -> None:
    """Разброс — от 0 до суток (шире окна его обрезает окно); доля жалоб — больше нуля и не
    больше 0.02: «0.1» или «0.3», задуманные как проценты, ослабили бы паузу молча, а так
    выключают продажи громко (ревью стыков 13.10)."""
    assert (name not in seen[f"{name}={value}"]["broken"]) is fine


def test_one_digit_hour_is_the_same_hour(seen: dict[str, dict[str, Any]]) -> None:
    """«MON 9:00» и «9:00-17:00» прочесть иначе нельзя — тот же час, а не отказ."""
    found = seen["час одной цифрой"]

    assert found["broken"] == {}
    assert found["values"]["WEEKLY_REPORT_AT"] == [0, "09:00:00"]
    assert found["values"]["SEND_HOURS"] == ["09:00:00", "17:00:00"]


@pytest.mark.parametrize("raw", REFUSED_TIMES)
def test_a_bare_hour_or_one_digit_minutes_are_still_refused(
    seen: dict[str, dict[str, Any]], raw: str
) -> None:
    assert "например «MON 09:00»" in seen[f"время {raw}"]["broken"]["SALES_WEEKLY_REPORT_AT"]


# --- процессы поднимаются: подпроцесс точек входа -----------------------------------------------

ENTRY = """
import importlib, json

for name in ("backend.api.app", "backend.workers.main", "backend.workers.jobs",
             "backend.workers.sales_jobs", "backend.workers.agent_jobs", "backend.workers.reaper",
             "backend.cli.main"):
    importlib.import_module(name)

from backend.api.app import create_app

create_app()

from backend.config import sales
from backend.features.agent.stages import AGENT_STAGES
from backend.features.core.domain import Stage

print("ИТОГ " + json.dumps([sorted(sales.BROKEN), sales.ENABLED, sales.AGENT_ENABLED,
                            sales.AUTOSEND, sales.AUTOBUILD, Stage.SALES in AGENT_STAGES]))
"""

#: Что нужно `create_app` сверх продаж: значения выдуманы, сервер не поднимается.
APP_ENV = {
    "STORAGE_DSN": "postgresql+asyncpg://u:p@localhost:1/none",
    "STORAGE_REDIS_URL": "redis://localhost:1/0",
    "ACCESS_JWT_SECRET": "s" * 64,
}


def _entry(tmp_path: Path, settings: dict[str, str]) -> tuple[list[object], str]:
    """Точки входа процессов и сборка приложения при этих настройках: итог и stderr."""
    done = subprocess.run(
        [sys.executable, "-c", ENTRY],
        cwd=tmp_path,
        env=_clean_env() | APP_ENV | settings,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert done.returncode == 0, done.stderr
    [line] = [line for line in done.stdout.splitlines() if line.startswith("ИТОГ ")]
    return json.loads(line.removeprefix("ИТОГ ")), done.stderr


def test_a_typo_does_not_stop_any_process_from_starting(tmp_path: Path) -> None:
    """Опечатки из ревью: API, общий воркер, worker-sales, задачи агента, reaper и консоль
    поднимаются; продажи выключены, строки продаж в реестре агента нет, строка журнала — одна
    на процесс, с именем, значением и тем, что годится."""
    typos = {"SALES_AUTOSEND_GAP_MIN": "0", "SALES_WEEKLY_REPORT_AT": "MON 9"}

    found, stderr = _entry(tmp_path, SWITCHED_ON | typos)

    assert found == [sorted(typos), False, False, False, False, False]
    assert stderr.count(sales_cfg.BROKEN_SAID) == 1, stderr
    assert "SALES_AUTOSEND_GAP_MIN=«0» — нужно целое от 1 до 240" in stderr
    # Каждая — и своей строкой общего разбора без падения (`config/_base.spared`): pydantic —
    # самим разбором, текстовая — своим.
    assert "НАСТРОЙКА НЕ ПРОЧИТАНА — SALES_AUTOSEND_GAP_MIN: в ней «0»" in stderr
    assert "НАСТРОЙКА НЕ ПРОЧИТАНА — SALES_WEEKLY_REPORT_AT «MON 9»" in stderr


def test_without_a_typo_the_processes_start_with_sales_as_set(tmp_path: Path) -> None:
    """Контроль: те же тумблеры и время отчёта часом одной цифрой — продажи включены."""
    found, stderr = _entry(tmp_path, SWITCHED_ON | {"SALES_WEEKLY_REPORT_AT": "MON 9:00"})

    assert found == [[], True, True, True, True, True]
    assert sales_cfg.BROKEN_SAID not in stderr


def test_garbage_in_every_sales_setting_does_not_stop_a_process(tmp_path: Path) -> None:
    """Мусор во всех `SALES_*` разом, и в текстовых, которые разбирает тот, кто читает: старт
    идёт, негодны ровно настройки из `BAD`. Новая настройка, которую разбирают при импорте, —
    строкой в `BAD` (иначе красный этот тест), а разобранная мимо правила — уронит его."""
    garbage = dict.fromkeys(ALIASES, "негодное значение")

    found, stderr = _entry(tmp_path, garbage)

    assert found == [sorted(BAD), False, False, False, False, False]
    assert stderr.count(sales_cfg.BROKEN_SAID) == 1, stderr


# --- экран и отказ: словами, без имён настроек --------------------------------------------------

GAP_SAID = "SALES_AUTOSEND_GAP_MIN=«0» — нужно целое от 1 до 240"


def test_screen_says_a_broken_setting_switched_sales_off_without_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sales_cfg, "ENABLED", False)
    monkeypatch.setattr(sales_cfg, "BROKEN", {"SALES_AUTOSEND_GAP_MIN": GAP_SAID})

    first = connection.gaps(Sender())[0]

    assert first == connection.Gap(connection.MODULE_BROKEN, "SALES_AUTOSEND_GAP_MIN")
    assert connection.reasons(Sender())[0] == connection.MODULE_BROKEN
    assert "SALES_" not in connection.MODULE_BROKEN
    assert connection.MODULE_BROKEN.startswith(connection.MODULE_OFF)


def test_switched_off_without_a_typo_names_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sales_cfg, "ENABLED", False)
    monkeypatch.setattr(sales_cfg, "BROKEN", {})

    assert connection.gaps(Sender())[0] == connection.Gap(
        "модуль продаж выключен — включает администратор", "SALES_ENABLED"
    )


async def test_refusal_names_the_broken_settings_in_the_journal(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Отказ сборки и отправки — словами человека; имена негодных настроек — строкой журнала."""
    monkeypatch.setattr(sales_cfg, "ENABLED", False)
    monkeypatch.setattr(
        sales_cfg, "BROKEN", {"SALES_AUTOSEND_GAP_MIN": GAP_SAID, "SALES_SEND_DAYS": "…"}
    )
    caplog.set_level(logging.WARNING, logger=connection.logger.name)

    with pytest.raises(SalesNotConnectedError) as refused:
        await connection.check(session, "сборка очереди продаж")

    assert connection.MODULE_BROKEN in str(refused.value)
    assert "SALES_" not in str(refused.value)
    [record] = [r for r in caplog.records if r.name == connection.logger.name]
    assert record.settings[0] == "SALES_AUTOSEND_GAP_MIN, SALES_SEND_DAYS"  # type: ignore[attr-defined]
