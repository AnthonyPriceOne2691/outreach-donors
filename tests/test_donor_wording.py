"""Слова о доноре, которые собирает сервер, — те же, что у экрана.

Выгрузку доноров файлом и причину отсева собирает сервер, а подписи к кодам
держит фронт (`frontend/src/api/labels.ts`). Две копии слов расходятся молча:
файл рядом с экраном читается как выгрузка из другой системы. Поэтому здесь
не только правила перевода, но и сверка копий построчно.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from backend.features.core.domain import ContactStatus, DonorStatus
from backend.features.donors.geo import CountryShare, check_geo
from backend.features.donors.wording import (
    CONTACT_STATUS_TITLES,
    COUNTRY_TITLES,
    DONOR_STATUS_TITLES,
    NOT_SEARCHED,
    country_title,
    reject_reason_text,
    share_text,
)
from backend.features.serp.dataforseo import COUNTRY_CODES

LABELS_TS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api" / "labels.ts"


def _block(name: str) -> str:
    """Тело объявления `const NAME … = { … };` из `labels.ts`."""
    source = LABELS_TS.read_text(encoding="utf-8")
    found = re.search(rf"const {name}\b[^=]*=\s*\{{(.*?)\n\}};", source, re.S)
    assert found is not None, f"в {LABELS_TS.name} нет {name}"
    return found.group(1)


def _pairs(name: str) -> dict[str, str]:
    """`код: 'слово'` построчно — для таблицы стран."""
    return dict(re.findall(r"^\s*([a-z_]+): '([^']+)',", _block(name), re.M))


def _titles(name: str) -> dict[str, str]:
    """`код: { title: 'слово', … }` построчно — для таблиц с цветом."""
    return dict(re.findall(r"^\s*([a-z_]+): \{ title: '([^']+)'", _block(name), re.M))


class TestSameWordsAsTheScreen:
    def test_countries(self) -> None:
        assert _pairs("COUNTRY_TITLES") == COUNTRY_TITLES

    def test_every_market_has_a_name(self) -> None:
        """Страна без названия ушла бы в файл кодом — ровно то, что чинили."""
        assert set(COUNTRY_CODES) <= set(COUNTRY_TITLES)

    def test_donor_verdicts(self) -> None:
        assert _titles("DONOR_STATUSES") == {
            status.value: title for status, title in DONOR_STATUS_TITLES.items()
        }
        assert set(DONOR_STATUS_TITLES) == set(DonorStatus)

    def test_search_outcomes(self) -> None:
        assert _titles("CONTACT_STATUSES") == {
            status.value: title for status, title in CONTACT_STATUS_TITLES.items()
        }
        assert set(CONTACT_STATUS_TITLES) == set(ContactStatus)

    def test_never_searched(self) -> None:
        source = LABELS_TS.read_text(encoding="utf-8")
        assert f"NOT_SEARCHED = {{ title: '{NOT_SEARCHED}'" in source


class TestRejectReason:
    @pytest.mark.parametrize(
        ("stored", "shown"),
        [
            # Так писало правило региона до 25.09.2026 — в базе такие лежат.
            (
                "ng не входит в топ-5 и даёт меньше 20%",
                "Нигерия не входит в топ-5 и даёт меньше 20%",
            ),
            # Так оно пишет теперь.
            ("ZA не входит в топ-5 и даёт меньше 20%", "ЮАР не входит в топ-5 и даёт меньше 20%"),
        ],
    )
    def test_country_code_becomes_its_name(self, stored: str, shown: str) -> None:
        assert reject_reason_text(stored) == shown

    @pytest.mark.parametrize(
        "reason",
        [
            # Две латинские буквы в начале — ещё не страна.
            "DR 12 ниже 20",
            "органический трафик 100 ниже 500",
            # Незнакомый код остаётся как есть, а не пропадает.
            "xx не входит в топ-5 и даёт меньше 20%",
        ],
    )
    def test_other_reasons_are_left_alone(self, reason: str) -> None:
        assert reject_reason_text(reason) == reason

    def test_no_reason_stays_none(self) -> None:
        assert reject_reason_text(None) is None

    def test_region_rule_speaks_in_the_expected_form(self) -> None:
        """Образец держится за формулировку правила региона: уехала она —
        код страны снова пошёл бы на экран, и тест об этом скажет."""
        verdict = check_geo(
            "br",
            [CountryShare(country, 100, 0.19) for country in ("us", "gb", "de", "fr", "in")],
        )

        assert not verdict.passed
        assert reject_reason_text(verdict.reason).startswith("Бразилия не входит в топ-")


class TestNumbersAndNames:
    @pytest.mark.parametrize(
        ("share", "shown"), [(0.19888, "20%"), (0.4, "40%"), (0.125, "13%"), (None, "")]
    )
    def test_share_is_a_percent_rounded_like_the_screen(
        self, share: float | None, shown: str
    ) -> None:
        """Половина — вверх, как `Math.round` экрана, а не к чётному."""
        assert share_text(share) == shown

    def test_unknown_country_is_its_code_in_capitals(self) -> None:
        assert country_title("xx") == "XX"
        assert country_title("US") == "США"
        assert country_title(None) == ""
