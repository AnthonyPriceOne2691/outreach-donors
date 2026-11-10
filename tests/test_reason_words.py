"""Причины скоринга на экране: в строке — коротко, пояснение — рядом (разведка экранов 10.11.2026).

Проверяется то, чем строка может соврать: причина зоны, которую пишет скоринг, уходит короткой
с пояснением, а строки, записанные до модуля, — так же; прочие причины и их порядок — как записаны,
только число держится за свой знак неразрывным пробелом (QA прода 10.11.2026).
"""

from __future__ import annotations

from backend.features.core.domain import Verdict
from backend.features.crawl.links import OutLink
from backend.features.crawl.reason_words import NONPROFIT_NOTE, shown_reasons
from backend.features.crawl.scoring import score_candidates

#: Причина зоны, как её хранят строки кандидатов, записанные до 10.11.2026.
STORED = "зона .org — чаще фонд или сообщество, чем рекламодатель: решает человек"


def _sponsored(root: str) -> OutLink:
    return OutLink(
        page_url="https://donor.example.test/news/1",
        url=f"https://{root}/",
        target_host=root,
        target_root=root,
        anchor="подробнее",
        anchor_key="подробнее",
        nofollow=False,
        sponsored=True,
        ugc=False,
        in_body=True,
    )


def test_the_zone_written_by_scoring_is_short_with_a_note() -> None:
    candidate = score_candidates([_sponsored("fund.example.org")])[0]
    assert candidate.verdict is Verdict.PENDING

    shown = shown_reasons(candidate.reasons)

    assert "зона .org" in shown.words
    assert shown.notes == {"зона .org": NONPROFIT_NOTE}
    assert not any("фонд" in words for words in shown.words), "пояснение — не в строке"


def test_rows_stored_before_are_shown_the_same_and_the_rest_as_written() -> None:
    shown = shown_reasons(["rel=sponsored +5", STORED, "ссылки с 3 страниц донора +3"])

    assert shown.words == ["rel=sponsored\u00a0+5", "зона .org", "ссылки с 3 страниц донора\u00a0+3"]
    assert shown.notes == {"зона .org": NONPROFIT_NOTE}
    assert NONPROFIT_NOTE.startswith("Чаще фонд или сообщество")


def test_no_reasons_is_nothing_and_a_lookalike_stays_whole() -> None:
    """Пояснение — только у причины зоны целиком: похожая строка — как записана."""
    assert shown_reasons(None).words == []
    lookalike = "зона .org — решает человек"

    shown = shown_reasons([lookalike])

    assert (shown.words, shown.notes) == ([lookalike], {})


def test_a_number_holds_on_to_its_sign() -> None:
    """«DR 82 > 80» в узкой ячейке рвалась: «80» уходило строкой ниже (QA прода 10.11.2026)."""
    shown = shown_reasons(["кому не пишем: DR 82 > 80", "коммерческая ссылка под nofollow +2", "DR ≥ 50"])

    assert shown.words == [
        "кому не пишем: DR\u00a082\u00a0>\u00a080",
        "коммерческая ссылка под nofollow\u00a0+2",
        "DR\u00a0≥\u00a050",
    ]
