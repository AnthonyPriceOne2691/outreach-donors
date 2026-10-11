"""Предел базы продаж — по прокси: файл с экрана идёт телом формы через оба nginx.

Ревью 11.10.2026: модуль обещал «до 10 МБ», а nginx пропускает 8 МБ на всё тело запроса — файл
в 8–10 МБ получал страницу прокси 413, и мастер показывал «Сервер ответил 413» вместо «разделите
базу». Здесь сверяется, что предел модуля вместе с обёрткой формы влезает в предел тела у обоих
nginx и что мастер называет и проверяет то же число.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from backend.features.sales import intake

ROOT = Path(__file__).resolve().parent.parent
PROXIES = (ROOT / "deploy" / "nginx.conf", ROOT / "deploy" / "proxy" / "outreach.conf")
WIZARD = ROOT / "frontend" / "src" / "sales" / "ImportWizard.tsx"
#: Обёртка формы: границы частей, их заголовки, сопоставление колонок — с запасом.
ENVELOPE = 2**20
UNITS = {"": 1, "k": 2**10, "m": 2**20, "g": 2**30}
BODY_LIMIT = re.compile(r"^\s*client_max_body_size\s+(\d+)([kmg]?)\s*;", re.M | re.I)


def _body_limit(conf: Path) -> int:
    """Самый тесный предел тела в конфиге — его встретит и форма загрузки базы."""
    found = BODY_LIMIT.findall(conf.read_text(encoding="utf-8"))
    assert found, f"в {conf.name} нет client_max_body_size — nginx пропустит лишь 1 МБ"
    return min(int(size) * UNITS[unit.lower()] for size, unit in found)


@pytest.mark.parametrize("conf", PROXIES, ids=lambda conf: conf.name)
def test_the_limit_with_the_form_envelope_fits_under_the_proxy(conf: Path) -> None:
    assert _body_limit(conf) >= intake.MAX_BYTES + ENVELOPE


def test_the_wizard_names_and_checks_the_same_limit() -> None:
    found = re.search(r"const FILE_LIMIT_MB = (\d+);", WIZARD.read_text(encoding="utf-8"))

    assert found, "в мастере нет FILE_LIMIT_MB"
    assert int(found.group(1)) * 2**20 == intake.MAX_BYTES
