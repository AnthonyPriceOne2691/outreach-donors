"""Пересуд прогона гипотезы после правки описания сегмента — на настоящей базе.

Проверяется то, ради чего он: те же сайты и тот же текст, новый вопрос, «было → стало» по
сайтам; решения человека не пересуживаются, полоса сайта с лидом только показывается («стало
бы»), «ещё не судили» и сайты прошлых прогонов не трогаются, мимо кэша вердиктов, расход —
строкой судьи; повтор задачи продолжает с места; смета до запуска.

Модель и главные сайтов — подставные, домены и описания выдуманы.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config import storage
from backend.config.judge import JudgeMode
from backend.features.core import usage
from backend.features.core.domain import UsageProvider
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales import segment_rejudge as sr
from backend.features.sales import segment_rejudge_estimate as se
from backend.features.sales import segment_window
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesStoplistModel,
)
from backend.features.sales.segment_judge import PROMPT_VERSION, SegmentClient, segment_digest
from backend.features.sales.segment_judging import STOPLIST_OWN
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    SalesSegmentVerdictModel,
)
from backend.features.sales.segment_rejudge_report import (
    DONE,
    GOING,
    HUMAN_KEPT,
    KEY,
    QUEUED,
    STOPPED,
    Change,
    changes,
    summary,
    write_block,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN
from tests.segment_facts_fakes import facts
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_run import SEGMENT, Model, hypothesis, make_run

Candidate = SalesSegmentCandidateModel

SHOP = "bikes-shop.example.test"
NEWS = "bikes-news.example.test"
PARTS = "bikes-parts.example.test"
MAYBE = "bikes-maybe.example.test"
#: Описание после правки: те же сайты, другой вопрос.
NARROWER = (
    "Интернет-магазины горных велосипедов: продают велосипеды у себя на сайте, без запчастей."
)
#: Текст, который видел прошлый суд: выдача и главная, строками с подписями.
TEXTS = {
    SHOP: "Заголовок в выдаче: Горные велосипеды — интернет-магазин Велодом\n"
    "Меню главной: Каталог · Доставка · Оплата",
    NEWS: "Заголовок в выдаче: Новости велоспорта и обзоры велосипедов",
    PARTS: "Заголовок в выдаче: Запчасти для велосипедов — магазин Велодеталь",
    MAYBE: "Заголовок в выдаче: Велосипеды",
}
YES = facts("yes", "интернет-магазин Велодом", "магазин велосипедов")
#: Слова судьи v4 у магазина запчастей: правило таблицы и пояснение модели.
NOT_PARTS_WORDS = "другая деятельность — не то, что ищет сегмент. Запчасти, не велосипеды"
NO_PARTS = facts("no", "Запчасти для велосипедов", "запчасти, не велосипеды")
NO_NEWS = facts("no", "Новости велоспорта и обзоры", "пишет о чужих")


async def site(
    session: AsyncSession,
    run: SalesSegmentRunModel,
    host: str,
    *,
    lane: Lane,
    **fields: Any,
) -> Candidate:
    """Кандидат прогона в нужной полосе — как его оставил прошлый суд."""
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    if domain is None:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
    judged = lane is not Lane.PENDING
    columns: dict[str, Any] = {
        "keyword": "купить горный велосипед",
        "page_url": f"https://{host}/catalog",
        "page_title": TEXTS.get(host, host).split("\n", 1)[0].removeprefix("Заголовок в выдаче: "),
        "site_text": TEXTS.get(host) if judged else None,
        "verdict": lane if judged else None,
        "reason": "по тексту сайта" if judged else None,
        "decided_by": Decider.MODEL if judged else None,
    } | fields
    made = Candidate(
        run_id=run.id, hypothesis_id=run.hypothesis_id, domain_id=domain.id, lane=lane, **columns
    )
    session.add(made)
    await session.commit()
    return made


async def lead_of(session: AsyncSession, candidate: Candidate) -> SalesLeadModel:
    lead = SalesLeadModel(
        hypothesis_id=candidate.hypothesis_id,
        domain_id=candidate.domain_id,
        email="hello@bikes-shop.example.test",
        source=LeadSource.COLLECTED,
        status=LeadStatus.READY,
    )
    session.add(lead)
    await session.flush()
    candidate.lead_id = lead.id
    await session.commit()
    return lead


def deps(model: Model, home: httpx.AsyncClient | None = None) -> sr.RejudgeDeps:
    http = httpx.AsyncClient(transport=httpx.MockTransport(model))
    return sr.RejudgeDeps(SegmentClient(http, model="gpt-5-mini", api_key=MODEL_KEY), home)


async def asked(
    session: AsyncSession, run: SalesSegmentRunModel, by: str = "seller@ours.example.test"
) -> None:
    """Как маршрут запуска: «поставлен» — в отчёт прогона до очереди."""
    await write_block(
        session, run.id, {"state": QUEUED, "by": by, "asked_at": "2026-10-14T09:00:00+00:00"}
    )
    await session.commit()


async def report(session: AsyncSession, run: SalesSegmentRunModel) -> dict[str, Any]:
    found = await session.scalar(
        select(SalesSegmentRunModel.report).where(SalesSegmentRunModel.id == run.id)
    )
    return dict(found or {})


async def another(session: AsyncSession, name: str) -> SalesHypothesisModel:
    made = SalesHypothesisModel(name=name, description=SEGMENT)
    session.add(made)
    await session.flush()
    return made


async def describe(session: AsyncSession, owner: SalesHypothesisModel, text: str | None) -> None:
    owner.description = text
    await session.commit()


@pytest.fixture
async def owner(session: AsyncSession) -> SalesHypothesisModel:
    return await hypothesis(session)


@pytest.fixture
async def run(session: AsyncSession, owner: SalesHypothesisModel) -> SalesSegmentRunModel:
    return await make_run(session, owner, mode=JudgeMode.ENFORCE)


# --- было → стало ----------------------------------------------------------------------------------


async def test_a_rejudge_asks_the_new_question_and_names_what_changed(
    session: AsyncSession, owner: SalesHypothesisModel, run: SalesSegmentRunModel
) -> None:
    shop = await site(session, run, SHOP, lane=Lane.ACCEPT)
    parts = await site(session, run, PARTS, lane=Lane.ACCEPT)
    await describe(session, owner, NARROWER)
    await asked(session, run)
    model = Model({SHOP: YES, PARTS: NO_PARTS}, tokens=577)

    outcome = await sr.rejudge(session, run.id, deps(model))

    assert sorted(model.asked) == [PARTS, SHOP]
    assert (shop.lane, parts.lane) == (Lane.ACCEPT, Lane.REJECT)
    # Судья v4: слова правила таблицы и пояснение модели.
    assert (parts.reason, parts.quote) == (NOT_PARTS_WORDS, "Запчасти для велосипедов")
    assert parts.decided_at is not None
    found = changes(await report(session, run))
    assert found == {parts.id: sr_change("accept", "reject")}
    assert outcome["state"] == DONE
    assert (outcome["total"], outcome["done"]) == (2, 2)
    assert outcome["moved"] == [{"was": "accept", "now": "reject", "would": False, "count": 1}]
    assert outcome["tokens"] == 2 * 577


def sr_change(was: str, now: str, **detail: Any) -> Change:
    return Change(was, now, **detail)


async def test_the_judge_reads_the_text_of_the_last_judgment_not_the_home_page_again(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    """Без новой выдачи и без новой главной: разница «было → стало» — от описания, а не от
    сайта, который успел поменяться. Сайт без сохранённого текста судится как обычно."""
    await site(session, run, SHOP, lane=Lane.REVIEW)
    bare = await site(session, run, MAYBE, lane=Lane.REVIEW)
    bare.site_text = None
    await session.commit()
    await asked(session, run)
    hits: list[str] = []

    def homes(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        html = "<html><head><title>Велосипеды</title></head><body>Каталог</body></html>"
        return httpx.Response(200, html=html, request=request)

    home = httpx.AsyncClient(transport=httpx.MockTransport(homes))
    model = Model({SHOP: YES, MAYBE: facts("unsure", "x y z", "мало")})

    await sr.rejudge(session, run.id, deps(model, home))

    assert set(hits) <= {MAYBE, f"www.{MAYBE}"}
    assert hits, "сайт без сохранённого текста — главная, как у обычного суда"


async def test_a_cached_verdict_of_the_same_question_does_not_spare_the_model(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    """Мимо кэша: пересуд — вопрос заново, даже если описание то же."""
    shop = await site(session, run, SHOP, lane=Lane.ACCEPT)
    session.add(
        SalesSegmentVerdictModel(
            domain_id=shop.domain_id,
            hypothesis_id=run.hypothesis_id,
            prompt_version=PROMPT_VERSION,
            segment_digest=segment_digest(SEGMENT),
            verdict=Lane.ACCEPT,
            quote="интернет-магазин Велодом",
            reason="магазин",
            model="gpt-5-mini",
            decided_by=Decider.MODEL,
            tokens=611,
        )
    )
    await session.commit()
    await asked(session, run)
    model = Model({SHOP: YES})

    await sr.rejudge(session, run.id, deps(model))

    assert model.asked == [SHOP]


# --- кого не трогает ----------------------------------------------------------------------------------


async def test_a_human_decision_is_not_judged_over(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    taken = await site(
        session,
        run,
        NEWS,
        lane=Lane.ACCEPT,
        verdict=Lane.REJECT,
        reason="принял человек",
        decided_by=Decider.HUMAN,
        decided_by_user="seller@ours.example.test",
    )
    await asked(session, run)
    model = Model({NEWS: NO_NEWS})

    outcome = await sr.rejudge(session, run.id, deps(model))

    assert model.asked == []
    assert (taken.lane, taken.decided_by, taken.reason) == (
        Lane.ACCEPT,
        Decider.HUMAN,
        "принял человек",
    )
    assert (outcome["kept_human"], outcome["total"]) == (1, 0)


async def test_a_human_decision_in_the_middle_of_the_rejudge_wins(
    session: AsyncSession, run: SalesSegmentRunModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = await site(session, run, MAYBE, lane=Lane.REVIEW)
    await asked(session, run)
    real = segment_window._one

    async def meanwhile(*args: Any) -> Any:
        review.lane, review.decided_by, review.reason = Lane.ACCEPT, Decider.HUMAN, "принял человек"
        await session.flush()
        return await real(*args)

    monkeypatch.setattr(segment_window, "_one", meanwhile)
    model = Model({MAYBE: facts("no", "x y z", "не тот")})

    outcome = await sr.rejudge(session, run.id, deps(model))

    lane = await session.scalar(select(Candidate.lane).where(Candidate.id == review.id))
    assert lane == Lane.ACCEPT
    assert outcome["human"] == 1
    assert changes(await report(session, run)) == {}


async def test_a_site_with_a_lead_only_shows_what_it_would_become(
    session: AsyncSession, owner: SalesHypothesisModel, run: SalesSegmentRunModel
) -> None:
    """Лид уже заведён (и, может быть, ему ушло письмо): полоса и лид не трогаются, новая полоса
    — «стало бы» в отчёте, со словами и цитатой."""
    parts = await site(session, run, PARTS, lane=Lane.ACCEPT)
    lead = await lead_of(session, parts)
    await describe(session, owner, NARROWER)
    await asked(session, run)

    outcome = await sr.rejudge(session, run.id, deps(Model({PARTS: NO_PARTS})))

    assert (parts.lane, parts.lead_id, parts.decided_by) == (Lane.ACCEPT, lead.id, Decider.MODEL)
    assert (await session.get(SalesLeadModel, lead.id)) is not None
    assert lead.status == LeadStatus.READY
    assert changes(await report(session, run)) == {
        parts.id: sr_change(
            "accept",
            "reject",
            would=True,
            reason=NOT_PARTS_WORDS,
            quote="Запчасти для велосипедов",
        )
    }
    assert outcome["moved"] == [{"was": "accept", "now": "reject", "would": True, "count": 1}]


async def test_not_yet_judged_sites_and_sites_of_other_runs_are_left_alone(
    session: AsyncSession, owner: SalesHypothesisModel, run: SalesSegmentRunModel
) -> None:
    """«Ещё не судили» судит продолжение прогона; сайт, заведённый прошлым прогоном гипотезы, —
    не этого прогона, и его пересуд этого прогона не трогает."""
    earlier = await make_run(session, owner)
    other = await site(session, earlier, NEWS, lane=Lane.ACCEPT)
    waiting = await site(session, run, MAYBE, lane=Lane.PENDING)
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    await asked(session, run)
    model = Model({SHOP: YES})

    outcome = await sr.rejudge(session, run.id, deps(model))

    assert model.asked == [SHOP]
    assert (other.lane, waiting.lane) == (Lane.ACCEPT, Lane.PENDING)
    assert (outcome["pending"], outcome["total"]) == (1, 1)


async def test_the_stoplist_still_cuts_before_the_model(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    shop = await site(session, run, SHOP, lane=Lane.ACCEPT)
    session.add(SalesStoplistModel(host=SHOP))
    await session.commit()
    await asked(session, run)
    model = Model({SHOP: YES})

    await sr.rejudge(session, run.id, deps(model))

    assert model.asked == []
    assert (shop.lane, shop.reason, shop.decided_by) == (Lane.REJECT, STOPLIST_OWN, Decider.RULE)


# --- расход, потолок, повтор ----------------------------------------------------------------------


async def test_the_spending_is_a_line_of_the_segment_judge(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    await asked(session, run)

    await sr.rejudge(session, run.id, deps(Model({SHOP: YES}, tokens=733)))

    lines = (
        await session.scalars(
            select(UsageRecordModel.units).where(
                UsageRecordModel.operation == "sales_segment_judge",
                UsageRecordModel.provider == UsageProvider.LLM,
            )
        )
    ).all()
    assert list(lines) == [733]


async def test_the_model_cap_stops_the_rejudge_in_words_and_keeps_what_is_done(
    session: AsyncSession, run: SalesSegmentRunModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    shop = await site(session, run, SHOP, lane=Lane.ACCEPT)
    await asked(session, run)

    async def capped(*_args: object, **_kwargs: object) -> None:
        raise usage.LlmCapExceededError("дневной потолок вызовов модели продаж выбран")

    monkeypatch.setattr(usage, "ensure_llm_within_cap", capped)
    model = Model({SHOP: YES})

    outcome = await sr.rejudge(session, run.id, deps(model))

    assert model.asked == []
    assert outcome["state"] == STOPPED
    assert outcome["error"] == "пересуд остановлен: дневной потолок вызовов модели продаж выбран"
    assert (outcome["total"], outcome["done"], shop.lane) == (1, 0, Lane.ACCEPT)


async def test_a_retried_job_continues_the_same_rejudge(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    """Задача упала посреди пересуда (база, воркер) — очередь повторяет её: пересуженный сайт
    второй раз к модели не идёт, «было» — из снимка до пересуда."""
    shop = await site(session, run, SHOP, lane=Lane.REJECT)
    parts = await site(session, run, PARTS, lane=Lane.ACCEPT)
    shop.lane = Lane.ACCEPT  # пересуд первой попытки уже сменил полосу
    await session.commit()
    sites = {str(shop.id): ["reject", "accept"], str(parts.id): ["accept", None]}
    await write_block(session, run.id, {"state": GOING, "sites": sites, "tokens": 401})
    await session.commit()
    model = Model({PARTS: NO_PARTS}, tokens=399)

    outcome = await sr.rejudge(session, run.id, deps(model))

    assert model.asked == [PARTS]
    assert {(c.was, c.now) for c in changes(await report(session, run)).values()} == {
        ("reject", "accept"),
        ("accept", "reject"),
    }
    assert outcome["tokens"] == 401 + 399


async def test_a_repeated_rejudge_after_another_edit_asks_again(
    session: AsyncSession, owner: SalesHypothesisModel, run: SalesSegmentRunModel
) -> None:
    parts = await site(session, run, PARTS, lane=Lane.ACCEPT)
    await describe(session, owner, NARROWER)
    await asked(session, run)
    await sr.rejudge(session, run.id, deps(Model({PARTS: NO_PARTS})))
    await describe(session, owner, SEGMENT)
    await asked(session, run)
    model = Model({PARTS: facts("yes", "Запчасти для велосипедов", "магазин")})

    await sr.rejudge(session, run.id, deps(model))

    assert model.asked == [PARTS]
    assert parts.lane == Lane.ACCEPT
    assert changes(await report(session, run)) == {parts.id: sr_change("reject", "accept")}


async def test_a_finished_rejudge_is_not_run_twice_by_a_stray_job(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    await write_block(session, run.id, {"state": DONE, "sites": {}})
    await session.commit()
    model = Model({SHOP: YES})

    await sr.rejudge(session, run.id, deps(model))

    assert model.asked == []


async def test_a_run_judged_without_the_model_is_not_rejudged(
    session: AsyncSession, owner: SalesHypothesisModel
) -> None:
    off = await make_run(session, owner, mode=JudgeMode.OFF)
    await site(session, off, SHOP, lane=Lane.REVIEW)
    await asked(session, off)
    model = Model({SHOP: YES})

    outcome = await sr.rejudge(session, off.id, deps(model))

    assert model.asked == []
    assert (outcome["state"], outcome["error"]) == (
        STOPPED,
        f"пересуд не выполнен: {sr.JUDGE_WAS_OFF}",
    )


# --- смета ---------------------------------------------------------------------------------------------


async def test_the_estimate_names_sites_model_calls_tokens_and_whom_it_leaves_alone(
    session: AsyncSession, run: SalesSegmentRunModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(llm_cfg, "API_KEY", MODEL_KEY)
    monkeypatch.setattr(se, "tokens_left", _left(50_000))
    shop = await site(session, run, SHOP, lane=Lane.ACCEPT)
    await lead_of(session, shop)
    await site(session, run, PARTS, lane=Lane.ACCEPT)
    await site(session, run, "youtube.com", lane=Lane.REJECT)
    await site(session, run, MAYBE, lane=Lane.PENDING)
    await site(session, run, NEWS, lane=Lane.ACCEPT, decided_by=Decider.HUMAN)
    session.add(
        SalesSegmentVerdictModel(
            domain_id=shop.domain_id,
            hypothesis_id=run.hypothesis_id,
            prompt_version=PROMPT_VERSION,
            segment_digest="0123456789ab",
            verdict=Lane.ACCEPT,
            quote="интернет-магазин Велодом",
            reason="магазин",
            decided_by=Decider.MODEL,
            tokens=1077,
        )
    )
    await session.commit()

    found = await se.estimate(session, run)

    assert (found.sites, found.model, found.with_lead) == (3, 2, 1)
    assert (found.kept_human, found.pending) == (1, 1)
    # Разбора описания у прогона ещё нет: один вызов разбора — в смете отдельной строкой.
    assert (found.per_site, found.tokens, found.tokens_left) == (
        1077,
        2 * 1077 + se.SCOPE_TOKENS,
        50_000,
    )
    assert found.refusal is None
    assert found.digest == segment_digest(SEGMENT)
    assert found.lines[0].startswith("Пересудить сайтов: 3 — по тексту, который видел прошлый суд")
    assert "токенов ≈ 2\u00a0854" in found.lines[1]
    assert "Разбор описания сегмента — один вызов" in " ".join(found.lines)


def _left(tokens: int | None) -> Any:
    async def left(_session: AsyncSession, _now: datetime | None = None) -> int | None:
        return tokens

    return left


@pytest.mark.parametrize(
    ("setup", "words"),
    [
        ({"enabled": False}, "модуль продаж выключен"),
        ({"description": None}, "у гипотезы нет описания сегмента"),
        ({"key": ""}, "ключ модели не задан"),
        ({"left": 100}, "смета пересуда выше остатка дневного потолка модели"),
        ({"lane": Lane.PENDING}, "пересуживать нечего"),
        ({"mode": JudgeMode.OFF}, "в этом прогоне судья был выключен"),
    ],
    ids=["sales-off", "no-segment", "no-model-key", "over-cap", "nothing", "judge-was-off"],
)
async def test_the_estimate_refuses_in_words(
    session: AsyncSession,
    owner: SalesHypothesisModel,
    monkeypatch: pytest.MonkeyPatch,
    setup: dict[str, Any],
    words: str,
) -> None:
    monkeypatch.setattr(sales_cfg, "ENABLED", setup.get("enabled", True))
    monkeypatch.setattr(llm_cfg, "API_KEY", setup.get("key", MODEL_KEY))
    monkeypatch.setattr(se, "tokens_left", _left(setup.get("left")))
    if "description" in setup:
        await describe(session, owner, setup["description"])
    run = await make_run(session, owner, mode=setup.get("mode", JudgeMode.ENFORCE))
    await site(session, run, SHOP, lane=setup.get("lane", Lane.ACCEPT))

    found = await se.estimate(session, run)

    assert found.refusal is not None
    assert words in found.refusal


async def test_tokens_per_site_come_from_the_history_of_the_judge(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    assert await se.tokens_per_site(session, run.hypothesis_id) == se.TOKENS_PER_SITE
    other = await another(session, "другая гипотеза")
    shop = await site(session, run, SHOP, lane=Lane.ACCEPT)
    for hypothesis_id, tokens in ((other.id, 901), (run.hypothesis_id, 1203)):
        session.add(
            SalesSegmentVerdictModel(
                domain_id=shop.domain_id,
                hypothesis_id=hypothesis_id,
                prompt_version=PROMPT_VERSION,
                segment_digest="0123456789ab",
                verdict=Lane.ACCEPT,
                quote="интернет-магазин Велодом",
                reason="магазин",
                decided_by=Decider.MODEL,
                tokens=tokens,
            )
        )
    await session.commit()

    assert await se.tokens_per_site(session, run.hypothesis_id) == 1203
    third = await another(session, "третья гипотеза")
    assert await se.tokens_per_site(session, third.id) == round((901 + 1203) / 2)


# --- задача очереди --------------------------------------------------------------------------------


@pytest.fixture
def quiet_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sr, "setup_logging", lambda: None)
    monkeypatch.setattr(sr, "check_storage", lambda: None)
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(storage, "DSN", TEST_DSN)


def test_a_permanent_refusal_is_the_outcome_in_words_not_three_retries(
    quiet_job: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "ENABLED", False)

    outcome = sr.rejudge_segment(987654)

    assert outcome["permanent"] is True
    assert "модуль продаж выключен" in outcome["error"]
    assert "SalesOffError" not in outcome["error"]


def test_a_passing_failure_goes_to_the_queue_retry(
    quiet_job: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(_run_id: int) -> dict[str, Any]:
        raise ConnectionError("база не ответила")

    monkeypatch.setattr(sr, "run_once", broken)

    with pytest.raises(ConnectionError, match="база не ответила"):
        sr.rejudge_segment(5)


async def test_the_job_runs_with_its_own_session_and_clients(
    quiet_job: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[sr.RejudgeDeps] = []

    async def rejudge(_session: AsyncSession, run_id: int, found: sr.RejudgeDeps) -> dict[str, Any]:
        seen.append(found)
        return {"run": run_id}

    monkeypatch.setattr(sr, "rejudge", rejudge)
    monkeypatch.setattr(llm_cfg, "TIMEOUT_S", 181.0)

    assert await sr.run_once(7) == {"run": 7}
    [found] = seen
    assert found.judge._http.timeout.read == 181.0


async def test_an_unknown_run_is_a_permanent_refusal(quiet_job: None) -> None:
    with pytest.raises(sr.UnknownRunError):
        await sr.run_once(987654)


def test_the_summary_of_a_report_without_a_rejudge_is_empty() -> None:
    assert summary({}) is None
    assert summary({KEY: {"state": DONE, "sites": {"5": ["accept", HUMAN_KEPT]}}}) == {
        "state": DONE,
        "by": None,
        "asked_at": None,
        "started_at": None,
        "finished_at": None,
        "total": 1,
        "done": 1,
        "human": 1,
        "kept_human": 0,
        "pending": 0,
        "moved": [],
        "tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "unsplit_tokens": 0,
        "reasoning_tokens": 0,
        "unanswered": 0,
        "failed": 0,
        # Пересуд до судьи v5 полей второго шанса не нёс — нули: экран строки не рисует.
        "quote_retried": 0,
        "quote_retry_held": 0,
        "error": None,
    }


async def test_the_report_block_is_written_without_touching_the_other_keys(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    run.report = {"serp": {"hosts": 7}, "judge": {"tokens": 1234}}
    await session.commit()

    await write_block(session, run.id, {"state": QUEUED})
    await session.commit()

    assert await report(session, run) == {
        "serp": {"hosts": 7},
        "judge": {"tokens": 1234},
        KEY: {"state": QUEUED},
    }


async def test_a_refusal_of_a_stray_job_does_not_rewrite_a_finished_rejudge(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    finished = {"state": DONE, "sites": {"5": ["accept", "reject"]}, "tokens": 377}
    await write_block(session, run.id, finished)
    await session.commit()

    await sr.stop(session, run.id, "пересуд не выполнен: модуль продаж выключен", datetime.now(UTC))

    assert (await report(session, run))[KEY] == finished


async def test_a_permanent_refusal_of_the_job_is_written_into_the_queued_rejudge(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    await asked(session, run)

    outcome = await sr.stop(
        session, run.id, "пересуд не выполнен: ключ модели не задан", datetime.now(UTC)
    )

    assert (outcome["state"], outcome["error"]) == (
        STOPPED,
        "пересуд не выполнен: ключ модели не задан",
    )
