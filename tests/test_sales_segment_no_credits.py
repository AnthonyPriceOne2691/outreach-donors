"""Модель без кредитов у сбора базы продаж (слово владельца 20.10.2026): прогон не начинается и не
судит вхолостую.

- Смета, «Запустить», «Продолжить» и пересуд спрашивают модель до первой траты: любой отказ
  проверки — нет кредитов, ключ не принят, сеть, тайм-аут, 5xx, частота после повторов, ответ не
  разобран — отказ её словами, ни строки прогона, ни задачи; ответила — идут.
- Задача прогона спрашивает модель сама — до выдачи: воркер мог взять её позже нажатия.
- Судья v4: кредиты кончились на разборе описания — проход к сайтам не идёт и встаёт словами, а не
  «сбоем разбора» у каждого; на k-м сайте — проход встаёт, неосуждённые ждут, а не уходят «на
  проверку: сбой»; «Продолжить» после пополнения добирает их, выдачу второй раз не покупая. То же у
  пересуда; пересуд по фактам кэша модель не зовёт — и проверка ему не нужна.
- Помощник описания называет пустой счёт, а не «попробуйте позже».

Провайдер модели — подставной (`tests/test_model_no_credits.py`, факты и разбор описания —
`tests/segment_facts_fakes.py`), выдача и сайты — подделки тестов прогона; домены выдуманы.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import pytest
from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.sales import segment_helper as sh
from backend.features.sales import segment_rejudge as sr
from backend.features.sales import segment_scope, usage_cap
from backend.features.sales.segment_estimate import estimate
from backend.features.sales.segment_judge import segment_digest
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import Lane, SalesSegmentCandidateModel
from backend.features.sales.segment_models import SalesSegmentRunModel as Run
from backend.features.sales.segment_models import Stage as RunStage
from backend.features.sales.segment_rejudge_report import DONE, GOING, KEY, STOPPED, write_block
from backend.features.sales.segment_window import NO_CREDITS
from backend.shared.llm import NO_CREDITS_WORDS
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_segment_rejudge as rj
from tests import test_sales_segment_run as runs
from tests.segment_facts_fakes import is_scope
from tests.test_model_no_credits import REFUSALS, answered, answering, no_credits
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_segment_api import RUNS, headers, start
from tests.test_sales_segment_estimate import _settings, ask
from tests.test_sales_segment_field_rejudge import CHILE_SHOP, PORTUGAL
from tests.test_sales_segment_helper import client as helper_client
from tests.test_sales_segment_judging import candidates, judge
from tests.test_sales_segment_run import _network, web
from tests.test_sales_segment_sites import alive, outcomes, spendable

#: Фикстуры соседних тестов: вход продавца, настройки сметы, сеть прогона, очередь пересуда.
__all__ = ["_network", "_settings", "alive", "headers", "outcomes", "spendable", "web"]

KINDS = list(REFUSALS)
Candidate = SalesSegmentCandidateModel


@pytest.fixture(autouse=True)
def _one_site_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Окно судьи — по сайту за раз: на каком ответе кончились кредиты, известно заранее."""
    monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_CONCURRENCY", 1)
    monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_MODE", JudgeMode.SHADOW)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    """Очередь долгих задач продаж — одна у запуска, «Продолжить» и пересуда."""
    found = _Jobs()
    monkeypatch.setattr("backend.api.sales.segment.sales_bulk_queue", lambda: found)
    monkeypatch.setattr("backend.api.sales.segment_sites.sales_bulk_queue", lambda: found)
    return found


@dataclass
class Dry(runs.Model):
    """Модель судьи v4, у которой кончились кредиты: на разборе описания (`scope_broke`) или после
    `paid` ответов о сайтах — дальше 429 «нет кредитов»."""

    paid: int = 1
    scope_broke: bool = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if is_scope(request) and self.scope_broke:
            self.scoped += 1
            return no_credits()
        if is_scope(request) or len(self.asked) < self.paid:
            return super().__call__(request)
        user = json.loads(request.content)["messages"][1]["content"]
        self.asked.append(user.split("\n", 1)[0].removeprefix("Domain: "))
        return no_credits()


async def runs_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(Run)) or 0)


async def lane_of(session: AsyncSession, candidate: Candidate) -> str | None:
    found = await session.scalar(select(Candidate.lane).where(Candidate.id == candidate.id))
    return None if found is None else str(found)


# --- смета и «Запустить» ------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
async def test_the_estimate_refuses_in_the_words_of_the_check_and_buys_nothing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    reply, words = REFUSALS[kind]
    provider = answering(monkeypatch, reply)
    serp = runs.serp(runs.SHOP)
    monkeypatch.setattr(
        "backend.features.sales.segment_estimate.build_provider", lambda _client: serp
    )
    owner = await runs.hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.refusal == words
    assert provider.calls >= 1
    assert serp.calls == []


async def test_an_answering_model_lets_the_estimate_through(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = answering(monkeypatch, answered)
    owner = await runs.hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.refusal is None
    assert provider.calls == 1


async def test_with_the_judge_off_the_model_is_not_asked(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_MODE", JudgeMode.OFF)
    provider = answering(monkeypatch, no_credits)
    owner = await runs.hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.refusal is None
    assert provider.calls == 0


@pytest.mark.parametrize("kind", KINDS)
async def test_start_is_refused_in_words_with_no_run_and_no_job(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    reply, words = REFUSALS[kind]
    answering(monkeypatch, reply)
    owner = await runs.hypothesis(session)

    response = await start(client, headers, owner.id)

    assert response.status_code == 409
    assert response.json()["detail"] == f"Прогон не запущен: {words}"
    assert await runs_count(session) == 0
    assert jobs.enqueued == []


async def test_start_goes_when_the_model_answers(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answering(monkeypatch, answered)
    owner = await runs.hypothesis(session)

    response = await start(client, headers, owner.id)

    assert response.status_code == 202, response.text
    assert await runs_count(session) == 1
    assert len(jobs.enqueued) == 1


# --- «Продолжить» -------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
async def test_continue_is_refused_in_words_before_the_queue(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    reply, words = REFUSALS[kind]
    answering(monkeypatch, reply)
    run = await runs.make_run(session, await runs.hypothesis(session))

    response = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == words
    assert jobs.enqueued == []


@pytest.mark.parametrize("reply", [answered, no_credits], ids=["answers", "no-credits"])
async def test_continue_goes_when_the_model_answers_or_is_not_needed(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
    reply: object,
) -> None:
    """Выдача не куплена — модель спрошена и ответила; куплена, а судить некого — «Найти адреса
    принятым» модели не зовёт, и пустой счёт её не держит."""
    provider = answering(monkeypatch, reply)  # type: ignore[arg-type]
    run = await runs.make_run(session, await runs.hypothesis(session))
    if reply is no_credits:
        run.report = {"serp": {"hosts": 3}}
        await session.commit()

    response = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert response.status_code == 202, response.text
    assert len(jobs.enqueued) == 1
    assert provider.calls == (1 if reply is answered else 0)


# --- пересуд ------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [*KINDS, "ответила"])
async def test_the_rejudge_asks_the_model_before_the_queue(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    run = await runs.make_run(session, await runs.hypothesis(session))
    await rj.site(session, run, rj.SHOP, lane=Lane.ACCEPT)
    reply, words = REFUSALS.get(kind, (answered, ""))
    answering(monkeypatch, reply)

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", json={"digest": segment_digest(runs.SEGMENT)}, headers=headers
    )

    if not words:
        assert response.status_code == 202, response.text
        assert len(jobs.enqueued) == 1
        return
    assert response.status_code == 409
    assert response.json()["detail"] == words
    assert jobs.enqueued == []


# --- задача прогона и пересуда ------------------------------------------------------------------


async def test_the_task_asks_the_model_before_buying_the_serp(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Воркер взял прогон позже нажатия, а счёт уже пуст: ни выдачи, ни судьи, причина — словами."""
    answering(monkeypatch, no_credits)
    run = await runs.make_run(session, await runs.hypothesis(session))
    serp = runs.serp(runs.SHOP, runs.NEWS)
    model = runs.Model(runs.ANSWERS)

    await runs.go(session, run, serp, model)

    assert serp.calls == []
    assert (model.asked, model.scoped) == ([], 0)
    assert (run.stage, run.error) == (RunStage.STOPPED, NO_CREDITS_WORDS)


async def test_no_credits_on_the_scope_stops_the_pass_before_any_site_and_continue_goes_on(
    session: AsyncSession, web: runs.Sites
) -> None:
    """Разбор описания — первый вызов модели прохода: без кредитов к сайтам проход не идёт, и это не
    «описание не разобрано» у каждого сайта, а остановка словами."""
    run = await runs.make_run(session, await runs.hypothesis(session))
    serp = runs.serp(runs.SHOP, runs.NEWS, runs.PARTS, runs.MAYBE)
    dry = Dry(runs.ANSWERS, paid=99, scope_broke=True)

    await runs.go(session, run, serp, dry)

    assert (dry.scoped, dry.asked) == (1, [])
    assert set((await runs.lanes(session, run)).values()) == {Lane.PENDING}
    assert (run.stage, run.error) == (RunStage.STOPPED, f"судья остановлен: {NO_CREDITS}")
    assert segment_scope.KEY not in run.report  # не «сбой разбора»: его в отчёте нет
    assert "contacts" not in run.report

    paid = runs.Model(runs.ANSWERS)
    await runs.go(session, run, serp, paid)

    assert paid.scoped == 1
    assert sorted(paid.asked) == sorted([runs.SHOP, runs.NEWS, runs.PARTS, runs.MAYBE])
    assert len(serp.calls) == 1
    assert run.stage == RunStage.DONE


@pytest.mark.parametrize("judged", [0, 2], ids=["first-site", "third-site"])
async def test_credits_running_out_on_a_site_stop_the_pass_the_rest_wait_and_continue_takes_them(
    session: AsyncSession, web: runs.Sites, judged: int
) -> None:
    run = await runs.make_run(session, await runs.hypothesis(session))
    serp = runs.serp(runs.SHOP, runs.NEWS, runs.PARTS, runs.MAYBE)
    dry = Dry(runs.ANSWERS, paid=judged)

    await runs.go(session, run, serp, dry)

    *done, broke = (
        dry.asked
    )  # судили `judged`, на следующем кредиты кончились, дальше не спрашивали
    assert len(done) == judged
    lanes = await runs.lanes(session, run)
    waiting = {host for host, lane in lanes.items() if lane == Lane.PENDING}
    assert waiting == set(lanes) - set(done)
    assert broke in waiting
    assert (run.stage, run.error) == (RunStage.STOPPED, f"судья остановлен: {NO_CREDITS}")
    assert "contacts" not in run.report  # до поиска адреса не дошло: ни лестницы, ни Hunter

    # Счёт пополнили — «Продолжить» судит тех, кто ждёт, выдачу второй раз не покупает.
    paid = runs.Model(runs.ANSWERS)
    await runs.go(session, run, serp, paid)

    assert sorted(paid.asked) == sorted(waiting)
    assert paid.scoped == 0  # разбор того же описания — из отчёта прогона
    assert len(serp.calls) == 1
    assert Lane.PENDING not in (await runs.lanes(session, run)).values()
    assert run.stage == RunStage.DONE


async def test_no_credits_on_the_scope_stops_the_rejudge_in_words(session: AsyncSession) -> None:
    run = await runs.make_run(session, await runs.hypothesis(session))
    shop = await rj.site(session, run, rj.SHOP, lane=Lane.ACCEPT)
    await rj.asked(session, run)
    dry = Dry({rj.SHOP: rj.YES}, paid=99, scope_broke=True)

    outcome = await sr.rejudge(session, run.id, rj.deps(dry))

    assert (dry.scoped, dry.asked) == (1, [])
    assert (outcome["state"], outcome["error"]) == (STOPPED, f"пересуд остановлен: {NO_CREDITS}")
    assert await lane_of(session, shop) == Lane.ACCEPT


async def test_credits_running_out_mid_rejudge_stop_it_and_continue_takes_the_rest(
    session: AsyncSession,
) -> None:
    run = await runs.make_run(session, await runs.hypothesis(session))
    await rj.site(session, run, rj.SHOP, lane=Lane.ACCEPT)
    parts = await rj.site(session, run, rj.PARTS, lane=Lane.ACCEPT)
    await rj.asked(session, run)
    dry = Dry({rj.SHOP: rj.YES, rj.PARTS: rj.NO_PARTS}, paid=1)

    outcome = await sr.rejudge(session, run.id, rj.deps(dry))

    assert dry.asked == [rj.SHOP, rj.PARTS]
    assert (outcome["state"], outcome["error"]) == (STOPPED, f"пересуд остановлен: {NO_CREDITS}")
    assert await lane_of(session, parts) == Lane.ACCEPT  # не «на проверку: сбой» — ждёт
    block = (await rj.report(session, run))[KEY]
    assert block["sites"][str(parts.id)][1] is None

    # «Продолжить пересуд» после пополнения: маршрут кладёт «идёт» — задача берёт только несуженных.
    await write_block(session, run.id, block | {"state": GOING, "error": None})
    await session.commit()
    paid = runs.Model({rj.PARTS: rj.NO_PARTS})
    outcome = await sr.rejudge(session, run.id, rj.deps(paid))

    assert paid.asked == [rj.PARTS]
    assert outcome["state"] == DONE
    assert await lane_of(session, parts) == Lane.REJECT


async def test_a_rejudge_by_cached_facts_asks_no_model_and_needs_no_check(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Правка поля гипотезы — пересуд по фактам кэша, разбор описания — в отчёте: модель не зовётся,
    и пустой счёт его не держит."""
    owner = await runs.hypothesis(session)
    run = await runs.make_run(session, owner)
    await candidates(session, run.id, owner.id, runs.SHOP)
    await judge_run(
        session,
        run_id=run.id,
        segment=runs.SEGMENT,
        mode=JudgeMode(run.mode),
        client=judge(runs.Model({runs.SHOP: CHILE_SHOP}, scope=PORTUGAL)),
        home=None,
    )
    owner.other_countries = "accept"
    await session.commit()
    provider = answering(monkeypatch, no_credits)

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", json={"digest": segment_digest(runs.SEGMENT)}, headers=headers
    )

    assert response.status_code == 202, response.text
    assert provider.calls == 0
    assert len(jobs.enqueued) == 1


# --- помощник описания --------------------------------------------------------------------------


async def test_the_helper_names_the_empty_account_instead_of_try_later(
    session: AsyncSession,
) -> None:
    model, seen = helper_client(lambda _request: no_credits())

    with pytest.raises(sh.HelperFailedError) as failed:
        await sh.help_draft(
            session, "Магазины велосипедов в Мюнхене", own=usage_cap.sales_cap(), client=model
        )

    assert len(seen) == 1  # «нет кредитов» не повторяется
    assert str(failed.value) == sh.BROKE
    assert NO_CREDITS_WORDS in str(failed.value)
    assert "попробуйте" not in str(failed.value)
