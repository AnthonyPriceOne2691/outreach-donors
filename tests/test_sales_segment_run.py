"""Прогон гипотезы целиком на настоящей базе (срез 6.1): выдача → судья сегмента → адрес
лестницей → лиды гипотезы → очистка.

Сеть — подделки: выдача — объект с тем же протоколом (`SerpProvider`), модель — подставной
транспорт, сайты (главные для судьи и страницы для лестницы адресов) — `httpx.MockTransport`
с главной по хосту, DNS — таблицей, проверка адресов — выдуманная (`FixtureVerifier`).
Лестница — настоящая, через её публичный вход `search_contacts(queue=…)`. Домены выдуманы.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.contacts import mx
from backend.features.core.domain import (
    ContactStatus,
    MessageStatus,
    Stage,
    ThreadStatus,
    UsageProvider,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.sales import cleaning, segment_report, segment_serp
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
)
from backend.features.sales.segment_judge import SegmentClient
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    SalesSegmentVerdictModel,
)
from backend.features.sales.segment_models import Stage as RunStage
from backend.features.sales.segment_run import RunDeps, execute
from backend.features.sales.verifier import FixtureVerifier
from backend.features.serp.dataforseo import SerpError
from backend.features.serp.protocol import SerpResult, SerpWatch
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_judge import MODEL_KEY

SEGMENT = "Интернет-магазины велосипедов: продают велосипеды и запчасти у себя на сайте."
KEYWORD = "купить горный велосипед"
#: Сайт сегмента с адресом на главной.
SHOP = "velo-shop-sample.com"
#: Сайт сегмента без адреса нигде.
PARTS = "velo-parts-sample.net"
#: Новостной сайт про велосипеды — не сегмент.
NEWS = "velo-news-sample.org"
#: Не понять по тексту.
MAYBE = "velo-maybe-sample.com"
PLATFORM = "youtube.com"

TITLES = {
    SHOP: "Горные велосипеды — интернет-магазин Велошоп",
    PARTS: "Запчасти для велосипедов — магазин Велодеталь",
    NEWS: "Новости велоспорта и обзоры велосипедов",
    MAYBE: "Велосипеды",
    PLATFORM: "Обзор горного велосипеда — видео",
}
ANSWERS: dict[str, Any] = {
    SHOP: {"fit": "yes", "quote": "интернет-магазин Велошоп", "why": "магазин велосипедов"},
    PARTS: {"fit": "yes", "quote": "Запчасти для велосипедов", "why": "магазин запчастей"},
    NEWS: {"fit": "no", "quote": "Новости велоспорта", "why": "пишет о чужих, сам не продаёт"},
    MAYBE: {"fit": "unsure", "quote": "Велосипеды на главной", "why": "мало текста"},
}
HOME = "<html><head><title>{title}</title></head><body>{body}</body></html>"


def answer(body: Any, *, tokens: int) -> dict[str, Any]:
    content = json.dumps(body, ensure_ascii=False)
    return {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": tokens}}


@dataclass
class Serp:
    """Выдача-подделка: позиции по ключу, цена задачи — как назвал бы провайдер."""

    results: dict[str, list[SerpResult]]
    price: float = 0.0037
    name: str = "fake"
    spent: float = 0.0
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def search(
        self,
        keywords: Sequence[str],
        country: str,
        *,
        depth_pages: int = 1,
        watch: SerpWatch | None = None,
    ) -> dict[str, list[SerpResult]]:
        self.calls.append(tuple(keywords))
        self.spent += self.price * len(keywords) * depth_pages
        return {key: self.results.get(key, []) for key in keywords}


def serp(*hosts: str) -> Serp:
    rows = [
        SerpResult(i + 1, f"https://{host}/catalog", TITLES[host], f"О сайте {host}")
        for i, host in enumerate(hosts)
    ]
    return Serp({KEYWORD: rows})


@dataclass
class Model:
    """Модель-подделка: ответ по домену из сообщения; число — код отказа провайдера."""

    answers: dict[str, Any]
    tokens: int = 613
    asked: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        user = json.loads(request.content)["messages"][1]["content"]
        host = user.split("\n", 1)[0].removeprefix("Domain: ")
        self.asked.append(host)
        reply = self.answers[host]
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": {"message": "сбой провайдера"}})
        return httpx.Response(200, json=answer(reply, tokens=self.tokens), request=request)


@dataclass
class Sites:
    """Сайты-подделка: главная по хосту (`www.` — тот же сайт), остальное — 404."""

    homes: dict[str, str]
    hits: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host.removeprefix("www.")
        self.hits.append(f"{host}{request.url.path}")
        if request.url.path in ("", "/") and host in self.homes:
            return httpx.Response(200, html=self.homes[host], request=request)
        return httpx.Response(404, text="нет", request=request)


def sites(emails: dict[str, str] | None = None) -> Sites:
    """Главные сайтов; у кого назван адрес — он ссылкой на главной."""
    homes = {}
    for host, title in TITLES.items():
        mail = (emails or {}).get(host)
        body = f'<a href="mailto:{mail}">{mail}</a>' if mail else "<p>Каталог и доставка</p>"
        homes[host] = HOME.format(title=title, body=body)
    return Sites(homes)


@pytest.fixture(autouse=True)
def _network(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS — у всех доменов почта есть; платной ступени поиска адреса нет."""

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    async def no_paid(_http: httpx.AsyncClient, _report: object) -> None:
        return None

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    monkeypatch.setattr(cleaning, "mail_route", route)
    monkeypatch.setattr("backend.features.contacts.search._paid_step", no_paid)


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> Sites:
    """Сайты для лестницы адресов: у магазина — адрес на главной."""
    found = sites({SHOP: f"hello@{SHOP}"})
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(found)),
    )
    return found


async def hypothesis(
    session: AsyncSession, description: str | None = SEGMENT
) -> SalesHypothesisModel:
    made = SalesHypothesisModel(name="сайты EN", description=description)
    session.add(made)
    await session.flush()
    return made


async def make_run(
    session: AsyncSession,
    owner: SalesHypothesisModel,
    *,
    mode: JudgeMode = JudgeMode.ENFORCE,
    keywords: Sequence[str] = (KEYWORD,),
) -> SalesSegmentRunModel:
    run = SalesSegmentRunModel(
        hypothesis_id=owner.id,
        keywords=list(keywords),
        country="us",
        language="en",
        depth_pages=1,
        mode=mode.value,
        estimate={"lines": ["смета"]},
        report={},
        created_by="seller@ours.example.test",
    )
    session.add(run)
    await session.commit()
    return run


async def go(
    session: AsyncSession,
    run: SalesSegmentRunModel,
    found: Serp,
    model: Model,
    *,
    homes: Sites | None = None,
) -> dict[str, Any]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(model))
    home = httpx.AsyncClient(transport=httpx.MockTransport(homes)) if homes is not None else None
    deps = RunDeps(
        serp=found,
        judge=SegmentClient(http, model="gpt-5-mini", api_key=MODEL_KEY),
        home=home,
        verifier=FixtureVerifier(),
    )
    return await execute(session, run.id, deps)


async def lanes(session: AsyncSession, run: SalesSegmentRunModel) -> dict[str, str]:
    rows = await session.execute(
        select(DomainModel.host, SalesSegmentCandidateModel.lane)
        .join(DomainModel, DomainModel.id == SalesSegmentCandidateModel.domain_id)
        .where(SalesSegmentCandidateModel.run_id == run.id)
    )
    return dict(rows.tuples().all())


async def leads(session: AsyncSession) -> list[SalesLeadModel]:
    return list((await session.scalars(select(SalesLeadModel).order_by(SalesLeadModel.id))).all())


# --- путь целиком -------------------------------------------------------------------------


async def test_the_whole_way_from_keywords_to_a_clean_lead(
    session: AsyncSession, web: Sites
) -> None:  # A1 A5
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    model = Model(ANSWERS)

    report = await go(session, run, serp(SHOP, NEWS, PARTS, MAYBE, PLATFORM), model)

    assert await lanes(session, run) == {
        SHOP: Lane.ACCEPT,
        PARTS: Lane.ACCEPT,
        NEWS: Lane.REJECT,
        MAYBE: Lane.REVIEW,
        PLATFORM: Lane.REJECT,
    }
    # Платформу правило отсекло до модели: её не спрашивали.
    assert sorted(model.asked) == sorted([SHOP, NEWS, PARTS, MAYBE])
    [lead] = await leads(session)
    assert (lead.email, lead.source, lead.status) == (
        f"hello@{SHOP}",
        LeadSource.COLLECTED,
        LeadStatus.READY,
    )
    assert (lead.hypothesis_id, lead.country, lead.language, lead.timezone) == (
        owner.id,
        "us",
        "en",
        "America/New_York",
    )
    assert lead.contact_id is not None  # адрес сайта — общей строкой `contacts` (решение 01.10)
    candidate = await session.scalar(
        select(SalesSegmentCandidateModel).where(SalesSegmentCandidateModel.lead_id == lead.id)
    )
    assert candidate is not None
    assert candidate.quote == "интернет-магазин Велошоп"
    # Магазин запчастей принят, но адреса на сайте нет: лида нет, в отчёте — «без адреса».
    view = await segment_report.run_view(session, run.id)
    assert view is not None
    assert view.no_address == [("адреса на сайте нет", 1)]
    assert segment_report.summary(view) == {
        "hosts": 5,
        "accepted": 2,
        "review": 1,
        "rejected": 2,
        "pending": 0,
        "no_address": 1,
        "leads": 1,
    }
    assert sorted(view.rejected) == [
        ("пишет о чужих, сам не продаёт", 1),
        ("платформам и соцсетям не пишем", 1),
    ]
    assert report["leads"]["created"] == 1
    assert run.stage == RunStage.DONE
    assert run.finished_at is not None


async def test_spending_is_written_serp_in_dollars_and_the_judge_in_tokens(
    session: AsyncSession, web: Sites
) -> None:
    run = await make_run(session, await hypothesis(session))

    await go(session, run, serp(SHOP, NEWS), Model(ANSWERS, tokens=613))

    rows = await session.execute(
        select(
            UsageRecordModel.operation,
            UsageRecordModel.provider,
            UsageRecordModel.units,
            UsageRecordModel.amount_usd,
        )
    )
    found = {
        operation: (provider, units, amount) for operation, provider, units, amount in rows.tuples()
    }
    assert found["sales_serp"][0] is UsageProvider.SERP
    assert found["sales_serp"][2] == Decimal("0.0037")
    assert found["sales_segment_judge"][:2] == (UsageProvider.LLM, 2 * 613)


async def test_the_serp_is_bought_once_and_a_second_pass_changes_nothing(
    session: AsyncSession, web: Sites
) -> None:
    run = await make_run(session, await hypothesis(session))
    found, model = serp(SHOP, NEWS), Model(ANSWERS)

    await go(session, run, found, model)
    await go(session, run, found, model)

    assert len(found.calls) == 1
    assert len(model.asked) == 2  # судья второй раз не звался
    assert len(await leads(session)) == 1
    spent = await session.scalar(
        select(func.count()).where(UsageRecordModel.operation == "sales_serp")
    )
    assert spent == 1


async def test_a_host_the_hypothesis_already_collected_is_not_judged_again(
    session: AsyncSession, web: Sites
) -> None:
    owner = await hypothesis(session)
    first = await make_run(session, owner)
    await go(session, first, serp(SHOP), Model(ANSWERS))
    second = await make_run(session, owner)
    model = Model(ANSWERS)

    report = await go(session, second, serp(SHOP, NEWS), model)

    assert model.asked == [NEWS]
    assert (report["serp"]["new"], report["serp"]["known"]) == (1, 1)
    assert await lanes(session, second) == {NEWS: Lane.REJECT}


# --- очистка — та же, что у загрузки ---------------------------------------------------------


async def test_a_host_whose_address_already_has_a_lead_is_rejected_as_a_duplicate(
    session: AsyncSession, web: Sites
) -> None:
    owner = await hypothesis(session)
    domain = DomainModel(host=SHOP)
    session.add(domain)
    await session.flush()
    earlier = SalesLeadModel(
        hypothesis_id=owner.id,
        domain_id=domain.id,
        email=f"hello@{SHOP}",
        source=LeadSource.IMPORT,
        status=LeadStatus.READY,
    )
    session.add(earlier)
    run = await make_run(session, owner)

    await go(session, run, serp(SHOP), Model(ANSWERS))

    _, collected = await leads(session)
    assert (collected.status, collected.rejection_reason) == (
        LeadStatus.REJECTED,
        RejectionReason.DUPLICATE,
    )
    assert collected.cleaning_note == f"дубль: адрес уже у лида №{earlier.id}"


async def test_a_domain_in_work_of_another_direction_is_rejected_by_the_cleaning(
    session: AsyncSession, web: Sites
) -> None:
    owner = await hypothesis(session)
    domain = DomainModel(host=SHOP)
    campaign = CampaignModel(stage=Stage.DONORS, name="доноры")
    session.add_all([domain, campaign])
    await session.flush()
    talk = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, status=ThreadStatus.OPEN)
    session.add(talk)
    await session.flush()
    # В работе — живое письмо доноров, правилом отправки (одна роль на сайт, `elsewhere.py`).
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            thread_id=talk.id,
            domain_id=domain.id,
            step=0,
            status=MessageStatus.SENT,
            idempotency_key=f"donors:{SHOP}:0",
        )
    )
    run = await make_run(session, owner)

    await go(session, run, serp(SHOP), Model(ANSWERS))

    [collected] = await leads(session)
    assert (collected.status, collected.rejection_reason) == (
        LeadStatus.REJECTED,
        RejectionReason.OTHER_DIRECTION,
    )


async def test_an_address_already_on_the_domain_is_used_without_a_search(
    session: AsyncSession, web: Sites
) -> None:
    """Адрес принадлежит домену, а не роли: найденный донорам — тот же адрес лида."""
    domain = DomainModel(host=PARTS)
    session.add(domain)
    await session.flush()
    session.add(ContactModel(domain_id=domain.id, email=f"sales@{PARTS}", source="page"))
    run = await make_run(session, await hypothesis(session))

    await go(session, run, serp(PARTS), Model(ANSWERS))

    [lead] = await leads(session)
    assert lead.email == f"sales@{PARTS}"
    assert not [hit for hit in web.hits if hit.startswith(PARTS)]  # сайт не обходили
    candidate = await session.scalar(select(SalesSegmentCandidateModel))
    assert candidate is not None
    assert candidate.contact_status is None


# --- судья: режим, отказ модели, потолок -------------------------------------------------


async def test_shadow_writes_the_verdict_but_does_not_cut(
    session: AsyncSession, web: Sites
) -> None:
    run = await make_run(session, await hypothesis(session), mode=JudgeMode.SHADOW)

    await go(session, run, serp(SHOP, NEWS), Model(ANSWERS))

    assert await lanes(session, run) == {SHOP: Lane.ACCEPT, NEWS: Lane.REVIEW}
    news = await session.scalar(
        select(SalesSegmentCandidateModel)
        .join(DomainModel, DomainModel.id == SalesSegmentCandidateModel.domain_id)
        .where(DomainModel.host == NEWS)
    )
    assert news is not None
    assert (news.verdict, news.reason) == (
        Lane.REJECT,
        "судья в наблюдении отрезал бы: пишет о чужих, сам не продаёт",
    )
    written = await session.scalar(
        select(SalesSegmentVerdictModel.verdict).where(
            SalesSegmentVerdictModel.domain_id == news.domain_id
        )
    )
    assert written == Lane.REJECT


async def test_model_unavailable_leaves_review_without_a_verdict_and_the_next_pass_judges_again(
    session: AsyncSession, web: Sites
) -> None:  # A3
    run = await make_run(session, await hypothesis(session))
    found = serp(SHOP)

    await go(session, run, found, Model({SHOP: 503}))

    assert await lanes(session, run) == {SHOP: Lane.REVIEW}
    candidate = await session.scalar(select(SalesSegmentCandidateModel))
    assert candidate is not None
    assert candidate.unanswered
    assert candidate.reason is not None
    assert candidate.reason.startswith("модель не ответила")
    assert await session.scalar(select(func.count()).select_from(SalesSegmentVerdictModel)) == 0
    assert await leads(session) == []

    model = Model(ANSWERS)
    await go(session, run, found, model)

    assert model.asked == [SHOP]
    assert await lanes(session, run) == {SHOP: Lane.ACCEPT}
    assert len(await leads(session)) == 1


async def test_a_spent_model_cap_stops_the_run_in_words_before_any_call(
    session: AsyncSession, web: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("backend.config.llm.SALES_DAILY_TOKEN_CAP", 977)
    session.add(
        UsageRecordModel(
            system="outreach-donors",
            provider=UsageProvider.LLM,
            operation="sales_reply_kind",
            units=977,
        )
    )
    run = await make_run(session, await hypothesis(session))
    model = Model(ANSWERS)

    await go(session, run, serp(SHOP), model)

    assert model.asked == []
    assert run.stage == RunStage.STOPPED
    assert run.error is not None
    assert run.error.startswith(
        "судья остановлен: дневной потолок вызовов модели продаж выбран: 977 из 977 токенов"
    )
    assert await lanes(session, run) == {SHOP: Lane.PENDING}
    assert await leads(session) == []


async def test_a_segment_erased_after_the_start_stops_the_run_before_the_model(
    session: AsyncSession, web: Sites
) -> None:
    """Смета не пустит прогон без описания сегмента, но описание могли стереть после запуска:
    угадывать сегмент модель не должна — прогон встаёт словами до первого вызова."""
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    owner.description = "  "
    await session.commit()
    model = Model(ANSWERS)

    await go(session, run, serp(SHOP), model)

    assert model.asked == []
    assert run.stage == RunStage.STOPPED
    assert run.error is not None
    assert run.error.startswith("прогон остановлен: у гипотезы нет описания сегмента")
    assert await lanes(session, run) == {SHOP: Lane.PENDING}


async def test_the_judge_reads_the_home_page_of_the_site(session: AsyncSession, web: Sites) -> None:
    run = await make_run(session, await hypothesis(session))
    homes = sites()

    await go(session, run, serp(SHOP), Model(ANSWERS), homes=homes)

    candidate = await session.scalar(select(SalesSegmentCandidateModel))
    assert candidate is not None
    assert candidate.site_text is not None
    assert "Заголовок главной: Горные велосипеды — интернет-магазин Велошоп" in candidate.site_text
    assert f"{SHOP}/" in homes.hits


async def test_a_platform_is_cut_by_its_name_and_its_site_is_not_opened(
    session: AsyncSession, web: Sites
) -> None:
    """Платформу и госзону правило режет по имени домена: их главную не открываем вовсе —
    чужой сайт, который мы всё равно отсечём, не должен видеть наш трафик."""
    run = await make_run(session, await hypothesis(session))
    homes = sites()

    await go(session, run, serp(PLATFORM, SHOP), Model(ANSWERS), homes=homes)

    assert await lanes(session, run) == {PLATFORM: Lane.REJECT, SHOP: Lane.ACCEPT}
    assert not [hit for hit in homes.hits if hit.startswith(PLATFORM)]
    assert f"{SHOP}/" in homes.hits


# --- решение человека ---------------------------------------------------------------------------


async def test_a_human_accepts_a_review_site_and_the_resumed_run_makes_its_lead(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    found_sites = sites({MAYBE: f"team@{MAYBE}"})
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(found_sites)),
    )
    run = await make_run(session, await hypothesis(session))
    found = serp(MAYBE)
    await go(session, run, found, Model(ANSWERS))
    candidate = await session.scalar(select(SalesSegmentCandidateModel))
    assert candidate is not None
    assert candidate.lane == Lane.REVIEW

    await segment_report.decide(
        session, candidate.id, accept=True, author="seller@ours.example.test"
    )
    await session.commit()
    model = Model(ANSWERS)
    await go(session, run, found, model)

    assert model.asked == []  # решённое человеком не пересуживается
    [lead] = await leads(session)
    assert lead.email == f"team@{MAYBE}"
    assert (candidate.decided_by, candidate.decided_by_user, candidate.verdict) == (
        "human",
        "seller@ours.example.test",
        Lane.REVIEW,
    )


async def test_only_a_review_site_takes_a_human_decision(session: AsyncSession, web: Sites) -> None:
    run = await make_run(session, await hypothesis(session))
    await go(session, run, serp(SHOP), Model(ANSWERS))
    candidate = await session.scalar(select(SalesSegmentCandidateModel))
    assert candidate is not None

    with pytest.raises(segment_report.DecisionRefusedError, match="он уже «принят»"):
        await segment_report.decide(session, candidate.id, accept=False, author="x")
    with pytest.raises(segment_report.UnknownCandidateError):
        await segment_report.decide(session, 987654, accept=True, author="x")


async def test_no_address_words_name_why(session: AsyncSession, web: Sites) -> None:  # A5
    """Сайт не ответил — не «адреса нет»: повтор по сроку, и отчёт так и говорит."""
    run = await make_run(session, await hypothesis(session))
    await go(session, run, serp(PARTS), Model(ANSWERS))
    candidate = await session.scalar(select(SalesSegmentCandidateModel))
    assert candidate is not None
    assert candidate.contact_status is ContactStatus.NOT_FOUND
    candidate.contact_status = ContactStatus.NO_ANSWER
    await session.flush()

    view = await segment_report.run_view(session, run.id)

    assert view is not None
    assert view.no_address == [("сайт не ответил — повтор по сроку", 1)]


# --- лестница — только публичным входом -----------------------------------------------------------


async def test_the_address_is_searched_through_the_public_entry_with_the_candidates_queue(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Поиск адреса — общий вход лестницы `search_contacts(queue=…)`, а не своя копия: повтор
    «сайт не ответил», платная ступень и чекпоинты пачек — у соседей, одно на все очереди."""
    from backend.features.contacts.search import SearchReport  # noqa: PLC0415 — только здесь
    from backend.features.sales import segment_run  # noqa: PLC0415
    from backend.features.sales.segment_queue import SegmentContactQueue  # noqa: PLC0415

    seen: list[tuple[int, object]] = []

    async def spy(
        _session: AsyncSession, *, limit: int, queue: object, no_paid: bool
    ) -> SearchReport:
        seen.append((limit, queue))
        return SearchReport(pending=limit, walked=limit)

    monkeypatch.setattr(segment_run, "search_contacts", spy)
    run = await make_run(session, await hypothesis(session))

    await go(session, run, serp(SHOP, PARTS), Model(ANSWERS))

    [(limit, queue)] = seen
    assert limit == 2
    assert isinstance(queue, SegmentContactQueue)
    # Этап платной ступени поиск берёт у очереди: расход адресов сбора — продажам.
    assert queue.stage is Stage.SALES


@pytest.mark.parametrize(
    ("switched_on", "estimated", "no_paid"),
    [(False, 51, True), (True, 0, True), (True, 51, False)],
    ids=["switched-off", "not-in-the-estimate", "switched-on-and-estimated"],
)
async def test_the_paid_step_runs_only_when_switched_on_and_estimated(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    switched_on: bool,
    estimated: int,
    no_paid: bool,
) -> None:
    """Ключ Hunter общий с донорами: платная ступень сбора — только с флагом сбора и когда она
    была в смете, которую человек подтвердил запуском."""
    from backend.features.contacts.search import SearchReport  # noqa: PLC0415 — только здесь
    from backend.features.sales import segment_run  # noqa: PLC0415

    seen: list[bool] = []

    async def spy(
        _session: AsyncSession, *, limit: int, queue: object, no_paid: bool
    ) -> SearchReport:
        seen.append(no_paid)
        return SearchReport(pending=limit, walked=limit)

    monkeypatch.setattr(segment_run, "search_contacts", spy)
    monkeypatch.setattr("backend.config.sales.SEGMENT_PAID_CONTACTS", switched_on)
    run = await make_run(session, await hypothesis(session))
    run.estimate = {"lines": ["смета"], "hunter_searches": estimated}
    await session.commit()

    await go(session, run, serp(SHOP, PARTS), Model(ANSWERS))

    assert seen == [no_paid]


def test_the_collection_keeps_no_copy_of_the_ladder() -> None:
    """Ступени лестницы (страницы, браузер, RDAP, платный сервис) модуль сбора не знает: копия
    разошлась бы с общей на первой правке соседей."""
    from pathlib import Path  # noqa: PLC0415

    sales = Path(__file__).resolve().parent.parent / "backend/features/sales"
    for module in sorted(sales.glob("segment_*.py")):
        source = module.read_text(encoding="utf-8")
        copies = [
            name
            for name in ("ContactLadder", "PageFetcher", "HunterProvider", "rdap")
            if name in source
        ]
        assert copies == [], module.name


@dataclass
class PaidThenBroken(Serp):
    """Выдача, которая поставила задачи (и провайдер их списал), а забрать не смогла."""

    async def search(
        self,
        keywords: Sequence[str],
        country: str,
        *,
        depth_pages: int = 1,
        watch: SerpWatch | None = None,
    ) -> dict[str, list[SerpResult]]:
        self.calls.append(tuple(keywords))
        self.spent += self.price * len(keywords) * depth_pages
        raise SerpError("Провайдер выдачи ответил 503: перегружен")


async def test_serp_paid_and_then_failed_is_in_the_journal_before_the_job_retries(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Задачи выдачи поставлены — деньги списаны, — а забор упал: строка расхода `sales_serp`
    пишется и коммитится сразу, до повтора задачи очередью; иначе её унёс бы откат, а повтор
    купил бы выдачу снова — дважды оплаченное, ни разу не записанное. Выдача не куплена:
    повтор купит её заново и запишет свою строку — и прогон говорит это словами до повтора
    (номеров задач у этой выдачи нет)."""
    run = await make_run(session, await hypothesis(session))
    at_commit: list[list[Decimal | None]] = []
    commit = session.commit

    async def watched() -> None:
        rows = await session.scalars(
            select(UsageRecordModel.amount_usd).where(UsageRecordModel.operation == "sales_serp")
        )
        at_commit.append(list(rows))
        await commit()

    monkeypatch.setattr(session, "commit", watched)

    with pytest.raises(SerpError, match="ответил 503"):
        await go(session, run, PaidThenBroken({}, spent=0.5), Model(ANSWERS))  # 0,5 — прежние

    # Вход в этап выдачи коммитится до покупки — строки расхода тогда ещё нет.
    assert at_commit == [[], [Decimal("0.0037")]]
    await session.refresh(run)
    assert run.stage == RunStage.SERP
    assert not segment_serp.bought(run)
    # Расход в отчёте — как в журнале уже до повтора: панель расхода читает `cost_usd`.
    assert run.report["serp"] == {"paid_usd": 0.0037, "cost_usd": 0.0037}
    assert run.report["progress"]["note"] == (
        "выдача упала после оплаты ($0,0037): номера задач не сохранились — следующий проход "
        "купит её заново"
    )
    usage_row = await session.scalar(
        select(UsageRecordModel).where(UsageRecordModel.operation == "sales_serp")
    )
    assert usage_row is not None
    assert (usage_row.provider, usage_row.stage) == (UsageProvider.SERP, Stage.SALES)
