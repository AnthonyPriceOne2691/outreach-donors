"""Что нашёл обход донора — в приёмке по донорам (`crawl/findings.py`).

Тест-группа 23.10.2026: «сколько с каждого сайта продажных ссылок, статей, спорных». Чем
числа могут соврать: «куплено» и «рекламодателей» расходятся без причины; витрина с тремя
рекламодателями считается трижды; ссылка через сеть партнёрок теряется; PDF, «не пишем» и
«мимо» дают страницы; поставщик попадает в «вместе».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import pytest
from backend.features.core.domain import CrawlOutcome, StopReason, UserRole, Verdict
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl.acceptance import acceptance
from backend.features.crawl.findings import Findings, by_donor
from backend.features.crawl.gate import judge_run
from backend.features.crawl.links import OutLink, harvest
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.repository import save_crawl
from backend.features.crawl.scoring import advertiser_pages, score_candidates
from backend.features.donors.host import normalize_host
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.stage2_rows import crawled, scored

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ALPHA = "alpha-donor.example.test"
BETA = "beta-donor.example.test"
SUPPLIER = "partner.example.test"
#: Ссылка через сеть партнёрок: рекламодатель (`example.de`) — в параметре адреса,
#: а домен самой ссылки — сети.
AWIN = (
    "https://www.awin1.com/cread.php?awinmid=7&awinaffid=9"
    "&ued=https%3A%2F%2Fstore.example.de%2Fsale"
)


async def linked(session: AsyncSession, run: CrawlRunModel, path: str, url: str) -> None:
    """Ссылка обхода `run` со страницы `path` — так, как её кладёт обход (`crawl/links.py`):
    домен ссылки — корень её хоста, у незнакомой зоны — хост целиком."""
    host = urlsplit(url).hostname or ""
    session.add(
        OutLinkModel(
            crawl_run_id=run.id,
            page_url=f"https://{run.host}/{path}",
            url=url,
            target_host=host,
            target_root=normalize_host(host) or host,
            anchor="Offer",
            anchor_key="offer",
            nofollow=True,
            sponsored=False,
            ugc=False,
            in_body=False,
        )
    )
    await session.flush()


@pytest.fixture
async def alpha(session: AsyncSession) -> CrawlRunModel:
    """Донор с обходом: кандидаты всех вердиктов и решения человека — «куплено» ×4 (одному
    «не пишем»), спорные ×3 («пишем», «не пишем», без решения), «мимо» ×2, «кому не
    пишем». Ссылки обхода: витрина с тремя рекламодателями и спорным без решения, обзор,
    две ссылки через сеть партнёрок, PDF рекламодателя, страницы «не пишем» и «мимо»."""
    run = await crawled(session, ALPHA)
    await scored(session, run, "brand-a.test")
    await scored(session, run, "brand-b.test", confirmed=True)
    await scored(session, run, "example.de")
    await scored(session, run, "false.test", confirmed=False)
    await scored(session, run, "maybe-yes.test", verdict=Verdict.PENDING, confirmed=True)
    await scored(session, run, "maybe-no.test", verdict=Verdict.PENDING, confirmed=False)
    await scored(session, run, "maybe.test", verdict=Verdict.PENDING)
    await scored(session, run, "noise.test", verdict=Verdict.SKIPPED)
    await scored(session, run, "noise-two.test", verdict=Verdict.SKIPPED)
    await scored(session, run, "facebook.com", verdict=Verdict.BLOCKED)
    showcase = ("brand-a.test/go", "brand-b.test/", "maybe-yes.test/", "maybe.test/")
    for target in showcase:
        await linked(session, run, "top-5", f"https://{target}")
    await linked(session, run, "review-a", "https://brand-a.test/review")
    await linked(session, run, "deal", AWIN)
    await linked(session, run, "deal-again", AWIN.replace("sale", "spring"))
    await linked(session, run, "report", "https://brand-a.test/files/report.pdf")
    await linked(session, run, "false-review", "https://false.test/")
    await linked(session, run, "maybe-no", "https://maybe-no.test/")
    await linked(session, run, "news", "https://noise.test/story")
    await linked(session, run, "news", "https://facebook.com/alpha")
    return run


async def test_a_donor_counts_verdicts_decisions_and_pages(
    session: AsyncSession, alpha: CrawlRunModel
) -> None:
    """«Куплено» — без «не пишем»; спорных — все, решённых — среди них; страницы — те, где
    есть ссылка на тех, кому пишем: витрина, обзор и две через сеть партнёрок. Ссылка через
    сеть лежит в обходе с доменом сети, а кандидат — у рекламодателя, которого сеть назвала:
    соединение по домену ссылки потеряло бы обе её страницы."""
    found = await by_donor(session)
    roots = await session.scalars(
        select(OutLinkModel.target_root).where(OutLinkModel.page_url.endswith("/deal"))
    )

    assert found[ALPHA] == Findings(bought=3, pending=3, decided=2, skipped=2, blocked=1, pages=4)
    assert list(roots) == ["awin1.com"]


async def test_advertisers_are_bought_plus_the_human_yes(
    session: AsyncSession, alpha: CrawlRunModel
) -> None:
    """Рекламодателей донора ровно «куплено» и ещё те, кому человек сказал «пишем» сверх
    него: разница двух чисел на экране — решения человека. «Пишем» консолью «мимо» —
    тоже рекламодатель (`writes_to`), и равенство его держит."""
    noise = await session.scalar(
        select(CandidateModel).where(CandidateModel.target_root == "noise-two.test")
    )
    assert noise is not None
    noise.confirmed = True
    await session.flush()

    report = await acceptance(session, limit=50)

    row = next(row for row in report.donors if row.host == ALPHA)
    beyond = await session.scalar(
        select(func.count()).where(
            CandidateModel.donor_host == ALPHA,
            CandidateModel.verdict != Verdict.BOUGHT,
            CandidateModel.confirmed.is_(True),
        )
    )
    assert (row.tally.found, row.findings.bought, beyond) == (5, 3, 2)
    assert row.tally.found == row.findings.bought + beyond
    # «Мимо» — вердиктом скоринга, как в шапке очереди проверки: решение его не двигает.
    assert row.findings.skipped == 2


DONOR = "gamma-donor.test"


def _pages() -> list[tuple[str, str]]:
    """Страницы донора: витрина двух рекламодателей, обзор одного с PDF рядом, ссылка через
    сеть партнёрок, новость со ссылками на «мимо» и на соцсеть."""
    body = {
        "top-5": '<a href="https://brand-a.test/go" rel="sponsored">Brand A</a> '
        '<a href="https://brand-b.test/" rel="sponsored">Brand B</a>',
        "review-a": '<a href="https://brand-a.test/review" rel="sponsored">Brand A</a>',
        "report": '<a href="https://brand-a.test/files/report.pdf">annual report</a>',
        "deal": f'<a href="{AWIN}" rel="nofollow">Buy now</a>',
        "news": '<a href="https://noise.test/story">a story</a> '
        '<a href="https://facebook.com/gamma">Follow us</a>',
    }
    text = "text " * 80
    return [
        (
            f"https://{DONOR}/{path}",
            f"<html><body><article><h1>{path}</h1><p>{text}</p>{links}</article></body></html>",
        )
        for path, links in body.items()
    ]


def _links() -> list[OutLink]:
    return [link for url, html in _pages() for link in harvest(html, url, DONOR)]


async def test_pages_of_a_crawled_donor_follow_the_pages_of_its_candidates(
    session: AsyncSession,
) -> None:
    """Донор с обходом от начала до конца: страницы, ссылки, скоринг. Страниц — без
    повторов: у кандидатов их 2 + 1 + 1, а разных — три (витрина у двоих). PDF страницей
    не считается — как в `pages` кандидата. «Не пишем» рекламодателю за сетью снимает
    страницу, которую держал только он."""
    run = await save_crawl(
        session,
        CrawlReport(
            host=DONOR,
            outcome=CrawlOutcome.OK,
            stop_reason=StopReason.EXHAUSTED,
            pages=[url for url, _ in _pages()],
            links=_links(),
            articles=len(_pages()),
        ),
    )
    await judge_run(session, run.id)
    rows = (await session.scalars(select(CandidateModel))).all()
    candidates = {row.target_root: row for row in rows}

    assert {root: (row.verdict, row.pages) for root, row in candidates.items()} == {
        "brand-a.test": (Verdict.BOUGHT, 2),
        "brand-b.test": (Verdict.BOUGHT, 1),
        "example.de": (Verdict.BOUGHT, 1),
        "noise.test": (Verdict.SKIPPED, 1),
        "facebook.com": (Verdict.BLOCKED, 1),
    }
    assert (await by_donor(session))[DONOR] == Findings(bought=3, skipped=1, blocked=1, pages=3)

    candidates["example.de"].confirmed = False
    await session.flush()

    assert (await by_donor(session))[DONOR] == Findings(bought=2, skipped=1, blocked=1, pages=2)


def test_advertiser_pages_are_the_pages_of_each_candidate() -> None:
    """Одно правило у кандидата и у приёмки: сколько страниц у получателя в
    `advertiser_pages`, столько и `pages` его кандидата — со ссылкой через сеть и с PDF."""
    links = _links()

    pages = advertiser_pages(links)

    assert {c.target_root: c.pages for c in score_candidates(links, DONOR)} == {
        root: len(urls) for root, urls in pages.items()
    }
    assert pages["example.de"] == {f"https://{DONOR}/deal"}
    assert f"https://{DONOR}/report" not in pages["brand-a.test"]


async def test_all_donors_together_sum_findings_without_suppliers(
    session: AsyncSession, alpha: CrawlRunModel
) -> None:
    """«Вместе» — суммой по донорам: рекламодатель двух доноров — два «куплено» и две
    страницы, а в «рекламодателях» — один домен. Поставщик в сумму не идёт, а его
    строка показывает, что нашёл его обход."""
    beta = await crawled(session, BETA)
    await scored(session, beta, "brand-a.test")
    await linked(session, beta, "post", "https://brand-a.test/")
    supplier = await crawled(session, SUPPLIER)
    await scored(session, supplier, "only-there.test")
    await linked(session, supplier, "post", "https://only-there.test/")
    session.add(SupplierDonorModel(host=SUPPLIER, note="партнёр агентства"))
    await session.flush()

    report = await acceptance(session, limit=50)

    rows = {row.host: row for row in report.donors}
    assert rows[SUPPLIER].findings == Findings(bought=1, pages=1)
    assert rows[BETA].findings == Findings(bought=1, pages=1)
    assert report.overall_findings == Findings(
        bought=4, pending=3, decided=2, skipped=2, blocked=1, pages=5
    )
    assert report.overall.found == 4


async def test_a_donor_crawled_without_candidates_found_nothing(session: AsyncSession) -> None:
    await crawled(session, "empty-donor.example.test")

    report = await acceptance(session, limit=50)

    assert [row.findings for row in report.donors] == [Findings()]
    assert report.overall_findings == Findings()


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("находки@site.com", role=UserRole.OPERATOR)
    return await sign_in("находки@site.com")


async def test_the_screen_gets_findings_of_each_donor_and_together(
    client: AsyncClient, token: str, session: AsyncSession, alpha: CrawlRunModel
) -> None:
    await session.commit()

    response = await client.get("/api/advertisers/acceptance", headers=bearer(token))

    body = response.json()
    assert response.status_code == 200, body
    expected = {
        "bought": 3,
        "pending": 3,
        "decided": 2,
        "skipped": 2,
        "blocked": 1,
        "pages": 4,
    }
    assert body["donors"][0]["host"] == ALPHA
    assert body["donors"][0]["findings"] == expected
    assert body["overall_findings"] == expected
