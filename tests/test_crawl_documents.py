"""«Кому не пишем: исследования и PDF» — по каждой ссылке, а не по первой (сверка ТЗ 11.10.2026).

Документ сверялся по первой ссылке группы — ссылок одного домена-получателя. Отсюда
две ошибки, обе зависели от порядка ссылок на странице: PDF первым снимал домен
целиком — вместе с его настоящей рекламой, а PDF вторым считался размещением,
набирал баллы и мог стать ссылкой, под которую пишется письмо.
"""

from __future__ import annotations

from backend.features.core.domain import Verdict
from backend.features.crawl.denylist import DenyReason, denial_for, is_document
from backend.features.crawl.links import OutLink
from backend.features.crawl.scoring import Candidate, score_candidates

DONOR = "donor.example.test"
ROOT = "brand.example.test"


def _link(path: str, *, page: str = "post-1", sponsored: bool = False) -> OutLink:
    anchor = "Brand annual report" if path.endswith(".pdf") else "Brand"
    return OutLink(
        page_url=f"https://{DONOR}/{page}/",
        url=f"https://{ROOT}{path}",
        target_host=ROOT,
        target_root=ROOT,
        anchor=anchor,
        anchor_key=anchor.lower(),
        nofollow=False,
        sponsored=sponsored,
        ugc=False,
    )


def _one(links: list[OutLink]) -> Candidate:
    (candidate,) = score_candidates(links, donor_root=DONOR)
    return candidate


def test_a_document_first_no_longer_hides_the_advertiser() -> None:
    candidate = _one([_link("/files/report.pdf"), _link("/offer", sponsored=True)])

    assert candidate.verdict is Verdict.BOUGHT
    assert candidate.best_link is not None
    assert candidate.best_link.url == f"https://{ROOT}/offer"
    assert "ссылки на документы (PDF и т. п.) не в счёт: 1" in candidate.reasons


def test_a_sponsored_document_second_is_not_a_placement() -> None:
    """PDF с `rel=sponsored` — документ, а не страница рекламодателя: баллов
    он не даёт и ссылкой письма не становится."""
    candidate = _one([_link("/"), _link("/whitepaper.PDF?utm=x", sponsored=True)])

    assert candidate.verdict is Verdict.SKIPPED
    assert candidate.best_link is not None
    assert candidate.best_link.url == f"https://{ROOT}/"
    assert (candidate.links, candidate.pages) == (1, 1)


def test_only_documents_is_a_deny_list_answer() -> None:
    candidate = _one([_link("/a.pdf", sponsored=True), _link("/b.xlsx", page="post-2")])

    assert candidate.verdict is Verdict.BLOCKED
    assert candidate.denial is not None
    assert candidate.denial.reason is DenyReason.DOCUMENT
    assert f"кому не пишем: document (https://{ROOT}/a.pdf)" in candidate.reasons


def test_pages_with_documents_do_not_make_a_scheme() -> None:
    """Повторяемость — по страницам со ссылкой на сайт: три статьи со ссылкой
    на отчёт компании схемой размещений не становятся."""
    documents = [_link("/report.pdf", page=f"post-{n}", sponsored=True) for n in range(1, 4)]

    candidate = _one([*documents, _link("/offer", page="post-9", sponsored=True)])

    assert candidate.pages == 1
    assert not any("страниц донора" in reason for reason in candidate.reasons)


def test_the_single_link_check_keeps_its_shape() -> None:
    """Публичный вход денилиста прежний: домен и одна ссылка."""
    denial = denial_for(ROOT, f"https://{ROOT}/report.pdf")

    assert denial is not None
    assert denial.reason is DenyReason.DOCUMENT
    assert denial_for(ROOT, f"https://{ROOT}/pricing") is None
    assert is_document("https://x.example.test/deck.PPTX?download=1")
    assert is_document("https://x.example.test/report.pdf#page=2")
    assert is_document("https://x.example.test/report.pdf?v=3#page=2")
    assert not is_document("https://x.example.test/pdf-tools/")
