"""Опечатка в настройках обхода роняет только обходчик.

Конфиг обхода (`config/crawl.py`) разбирается при импорте, и число вида «3,5» или
«50 usd» — отказ pydantic. Проверки старта зовут все процессы, а сторож живёт в одном
процессе с повтором передачи лидов продаж. Поэтому конфиг обхода грузят только
`startup_checks.check_crawl` (старт обходчика) и тревога потолка сторожа — внутри себя.
Проверка — в подпроцессе: в этом процессе конфиг уже загружен целым.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Опечатки из ревью: запятая в цене гигабайта (так она записана в смете) и единица в потолке.
BROKEN = {"CRAWL_PROXY_USD_PER_GB": "3,5", "CRAWL_MONTH_CAP_USD": "50 usd"}

PROBE = """
import asyncio, importlib

for name in ("backend.workers.send_jobs", "backend.workers.sales_jobs",
             "backend.workers.followups", "backend.workers.reaper",
             "backend.config.startup_checks"):
    importlib.import_module(name)
print("imported")

from backend.features.ops import silence

alarm = asyncio.run(silence._crawl_cap_reached(None))
print(alarm.code)
print(alarm.detail)

from backend.config import startup_checks

try:
    startup_checks.check_crawl()
except ValueError:
    print("crawler refused")
"""


def test_a_typo_in_crawl_settings_stops_only_the_crawler() -> None:
    env = {**os.environ, **BROKEN, "PYTHONPATH": str(ROOT)}
    done = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert done.returncode == 0, done.stderr
    imported, code, detail, crawler = done.stdout.strip().splitlines()[-4:]
    assert (imported, code, crawler) == ("imported", "crawl-config-broken", "crawler refused")
    # Имена настроек — да, значения — нет: в значении адреса прокси бывает пароль.
    assert "CRAWL_PROXY_USD_PER_GB" in detail
    assert "CRAWL_MONTH_CAP_USD" in detail
    assert "3,5" not in detail
    assert "50 usd" not in detail
