"""Гос. и учебные зоны в «кому не пишем» Этапа 2 — правилом Этапа 1 (сверка ТЗ 11.10.2026).

Список Этапа 2 знал восемь суффиксов (`.gov`, `.gov.uk`, `.ac.uk`…), и сайт
австралийского министерства или японского университета выходил кандидатом —
при том что Этап 1 давно отсекал их общим правилом `is_public_zone`.
"""

from __future__ import annotations

import pytest
from backend.features.core.domain import Verdict
from backend.features.crawl.denylist import BLOCKED_SUFFIXES, DenyReason, denial_for
from backend.features.crawl.links import OutLink
from backend.features.crawl.scoring import score_candidates
from backend.features.donors.publisher_judge import is_public_zone


@pytest.mark.parametrize(
    ("root", "zone"),
    [
        ("example.gov.au", ".gov.au"),
        ("example-dept.gov.in", ".gov.in"),
        ("example.gc.ca", ".gc.ca"),
        ("example-uni.ac.jp", ".ac.jp"),
        ("example.gob.es", ".gob.es"),
        ("example.gouv.fr", ".gouv.fr"),
        ("example.gv.at", ".gv.at"),
        ("example.go.jp", ".go.jp"),
        ("example.mil", ".mil"),
        ("example-college.edu", ".edu"),
    ],
)
def test_any_country_public_zone_is_denied(root: str, zone: str) -> None:
    denial = denial_for(root)

    assert denial is not None
    assert (denial.reason, denial.matched) == (DenyReason.ZONE, zone)


@pytest.mark.parametrize("root", ["gov.uk", "www.gov.uk", "example.gov.uk", "example.ac.za"])
def test_the_old_list_still_holds(root: str) -> None:
    """`gov.uk` без имени перед собой правило Этапа 1 не видит: это корень-зона,
    и его держит прежний список."""
    denial = denial_for(root)

    assert denial is not None
    assert denial.reason is DenyReason.ZONE


@pytest.mark.parametrize(
    "root", ["go.com", "education.com", "mygov.com", "gov.tech", "edu.example", "example.test"]
)
def test_lookalikes_are_candidates(root: str) -> None:
    """`go` — зона только перед страной: `go.jp` да, `go.com` нет."""
    assert denial_for(root) is None


def test_one_rule_for_both_stages() -> None:
    """Всё, что Этап 1 называет гос. или учебной зоной, Этап 2 не пишет."""
    roots = ["example.gov.au", "example.ac.jp", "example.gc.ca", "example.gouv.fr"]

    assert all(is_public_zone(root) for root in roots)
    assert all(denial_for(root) is not None for root in roots)
    assert ".gov.au" not in BLOCKED_SUFFIXES, "правило — общее, а не дописанный суффикс"


def test_scoring_blocks_a_sponsored_link_to_a_ministry() -> None:
    link = OutLink(
        page_url="https://donor.example.test/post",
        url="https://www.example.gov.au/grants",
        target_host="www.example.gov.au",
        target_root="example.gov.au",
        anchor="Apply for a grant",
        anchor_key="apply for a grant",
        nofollow=False,
        sponsored=True,
        ugc=False,
    )

    (candidate,) = score_candidates([link], donor_root="donor.example.test")

    assert candidate.verdict is Verdict.BLOCKED
    assert "кому не пишем: zone (.gov.au)" in candidate.reasons
