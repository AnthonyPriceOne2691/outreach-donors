"""Незаконченный пересуд того же описания продолжается со своего снимка — на настоящей базе.

Пересуд — инструмент калибровки: «было → стало» по сайтам. Остановился посреди (потолок модели,
отказ, умер воркер) — снимок полос до него хранит «было» у уже пересуженных сайтов. Новый снимок
снялся бы с уже сменённых полос: «было» потерялось бы, а пересуженные сайты снова пошли бы к модели.
Поэтому «Пересудить» продолжает незаконченный пересуд того же описания с места остановки — и смета
это говорит; новый пересуд со снимком — только когда прошлый закончен или описание другое.

Маршрут — настоящий, очередь задач и её живость — подделки (`test_sales_segment_sites`), задача
пересуда — настоящий `segment_rejudge.rejudge` с подставной моделью. Домены выдуманы.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.core import usage
from backend.features.sales import segment_rejudge as sr
from backend.features.sales import segment_window
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_models import Lane, SalesSegmentRunModel
from backend.features.sales.segment_rejudge import rejudge_job_id
from backend.features.sales.segment_rejudge_report import (
    DONE,
    GOING,
    KEY,
    QUEUED,
    STOPPED,
    TOTALS,
    write_block,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_segment_rejudge import (
    NARROWER,
    NEWS,
    NO_NEWS,
    NO_PARTS,
    PARTS,
    SHOP,
    deps,
    describe,
    report,
    site,
)
from tests.test_sales_segment_run import Model
from tests.test_sales_segment_sites import (
    RUNS,
    alive,
    headers,
    jobs,
    outcomes,
    owner,
    run,
    spendable,
)

__all__ = ["alive", "headers", "jobs", "outcomes", "owner", "run", "spendable"]  # фикстуры

#: Магазин после правки описания «отказ»: его полоса сменится — «было» у него «принят».
NO_SHOP = {"fit": "no", "quote": "интернет-магазин Велодом", "why": "не тот сегмент"}
CAP = "дневной потолок вызовов модели продаж выбран"


async def rejudge_asked(
    client: AsyncClient, headers: dict[str, str], run_id: int
) -> dict[str, Any]:
    """«Пересудить»: смета — и запуск по её отпечатку, как экран. Возвращает смету."""
    found = (await client.get(f"{RUNS}/{run_id}/rejudge", headers=headers)).json()
    started = await client.post(
        f"{RUNS}/{run_id}/rejudge", json={"digest": found["estimate"]["digest"]}, headers=headers
    )
    assert started.status_code == 202, started.text
    return dict(found["estimate"])


def capped_after(monkeypatch: pytest.MonkeyPatch, allowed: int) -> None:
    """Потолок модели выбран после `allowed` проверок: проверка — до первого сайта и после
    каждого чекпоинта."""
    asked = {"n": 0}

    async def check(*_args: object, **_kwargs: object) -> None:
        asked["n"] += 1
        if asked["n"] > allowed:
            raise usage.LlmCapExceededError(CAP)

    monkeypatch.setattr(usage, "ensure_llm_within_cap", check)


def job_over(jobs: _Jobs, run_id: int) -> None:
    """Задача пересуда кончилась (воркер дошёл до конца, остановленным или нет)."""
    jobs.known[rejudge_job_id(run_id)].finish({"state": "stopped"})


@pytest.mark.parametrize("later", [timedelta(0), timedelta(days=1)], ids=["same-day", "next-day"])
async def test_a_rejudge_stopped_by_the_cap_goes_on_from_its_snapshot(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    owner: SalesHypothesisModel,
    run: SalesSegmentRunModel,
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
    later: timedelta,
) -> None:
    """Потолок остановил пересуд на первом сайте. «Пересудить» в тот же день (потолок подняли) и
    на следующий — продолжение: смета говорит «продолжить с места остановки», к модели идут только
    не пересуженные, «было» у магазина — прежнее («принят», а не его новая полоса), и второй пересуд
    в сумме пересудов не считается."""
    shop = await site(session, run, SHOP, lane=Lane.ACCEPT)
    await site(session, run, PARTS, lane=Lane.ACCEPT)
    await site(session, run, NEWS, lane=Lane.ACCEPT)
    await describe(session, owner, NARROWER)
    monkeypatch.setattr(segment_window, "BATCH", 1)
    monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_CONCURRENCY", 1)
    capped_after(monkeypatch, 1)
    await rejudge_asked(client, headers, run.id)
    moment = datetime.now(UTC)
    first = Model({SHOP: NO_SHOP}, tokens=500)
    stopped = await sr.rejudge(session, run.id, deps(first), now=moment)
    assert (stopped["state"], stopped["done"], first.asked) == (STOPPED, 1, [SHOP])
    job_over(jobs, run.id)
    capped_after(monkeypatch, 100)

    estimate = await rejudge_asked(client, headers, run.id)
    second = Model({PARTS: NO_PARTS, NEWS: NO_NEWS}, tokens=400)
    outcome = await sr.rejudge(session, run.id, deps(second), now=moment + later)

    assert (estimate["resumes"], estimate["sites"], estimate["done"]) == (True, 2, 1)
    assert estimate["lines"][0].startswith("Продолжить пересуд с места остановки: осталось 2 из 3")
    assert sorted(second.asked) == [NEWS, PARTS]
    assert (outcome["state"], outcome["total"], outcome["done"]) == (DONE, 3, 3)
    found = await report(session, run)
    assert found[KEY]["sites"][str(shop.id)] == ["accept", "reject"]  # «было» — до первой попытки
    assert found[TOTALS]["count"] == 1
    assert found[TOTALS]["tokens"] == 500 + 2 * 400


async def snapshot_block(
    session: AsyncSession, run: SalesSegmentRunModel, state: str, digest: str | None
) -> dict[str, Any]:
    """Пересуд прогона в нужном состоянии: снимок двух сайтов, первый пересужен."""
    shop = await site(session, run, SHOP, lane=Lane.REJECT)
    parts = await site(session, run, PARTS, lane=Lane.ACCEPT)
    block: dict[str, Any] = {
        "state": state,
        "sites": {str(shop.id): ["accept", "reject"], str(parts.id): ["accept", None]},
        "tokens": 500,
    }
    if digest is not None:
        block["digest"] = digest
    await write_block(session, run.id, block)
    await session.commit()
    return block


@pytest.mark.parametrize(
    ("state", "digest"),
    [(DONE, "same"), (STOPPED, "other"), (GOING, "other"), (STOPPED, None)],
    ids=["finished", "stopped-under-another-description", "cut-short-under-another", "never-began"],
)
async def test_only_a_finished_or_another_question_starts_a_new_rejudge(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    jobs: _Jobs,
    state: str,
    digest: str | None,
) -> None:
    """Новый пересуд со снятием снимка — только если прошлый закончен, описание другое или
    пересуд не начинался (снимка нет): смета так и говорит, блок — «поставлен» без снимка."""
    current = (await client.get(f"{RUNS}/{run.id}/rejudge", headers=headers)).json()["estimate"]
    await snapshot_block(session, run, state, current["digest"] if digest == "same" else digest)

    estimate = await rejudge_asked(client, headers, run.id)

    assert (estimate["resumes"], estimate["done"]) == (False, 0)
    assert estimate["lines"][0].startswith("Пересудить сайтов:")
    found = (await report(session, run))[KEY]
    assert (found["state"], "sites" in found) == (QUEUED, False)


async def test_the_estimate_of_an_unfinished_rejudge_names_what_is_left(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], run: SalesSegmentRunModel
) -> None:
    """Смета незаконченного пересуда — на сайты, до которых он не дошёл, а не на все снова."""
    current = (await client.get(f"{RUNS}/{run.id}/rejudge", headers=headers)).json()["estimate"]
    await snapshot_block(session, run, STOPPED, current["digest"])

    found = (await client.get(f"{RUNS}/{run.id}/rejudge", headers=headers)).json()["estimate"]

    assert (found["resumes"], found["sites"], found["done"], found["model"]) == (True, 1, 1, 1)
    assert found["lines"][0] == (
        "Продолжить пересуд с места остановки: осталось 1 из 2 — то же описание и снимок полос "
        "прошлого запуска: пересуженные сайты к модели второй раз не пойдут, «было» у них прежнее."
    )
