"""Ход прогона сбора (тест прогона 14.10): стадия — с начала работы этапа, ход — в
`report.progress`, живое «сделано» судьи и адресов, пульс, конец прохода у готового и у
остановленного, ход выдачи «X из Y ключей», номера задач до опроса, честное слово о повторной
покупке в быстром режиме, расход проходов — суммой.

Сеть — подделки, как у прогона (`test_sales_segment_run`): выдача с протоколом
`SerpProvider`, модель — подставной транспорт, сайты — `httpx.MockTransport`. Домены выдуманы.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from backend.features.contacts.search import SearchReport
from backend.features.core.domain import UsageProvider
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales import (
    segment_counts,
    segment_jobs,
    segment_progress,
    segment_run,
    segment_serp,
)
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    Stage,
)
from backend.features.serp.dataforseo import SerpError
from backend.features.serp.modes import SerpMode
from backend.features.serp.protocol import SerpProgress, SerpResult, SerpWatch
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import TEST_DSN
from tests.test_sales_segment_run import (
    ANSWERS,
    KEYWORD,
    PARTS,
    SHOP,
    TITLES,
    Model,
    Serp,
    Sites,
    _network,
    go,
    hypothesis,
    leads,
    make_run,
    serp,
    web,
)

__all__ = ["_network", "web"]  # подделки сети и сайтов — те же, что у прогона

Run = SalesSegmentRunModel


def rows(*hosts: str) -> dict[str, list[SerpResult]]:
    return {
        KEYWORD: [
            SerpResult(i + 1, f"https://{host}/catalog", TITLES[host], f"О сайте {host}")
            for i, host in enumerate(hosts)
        ]
    }


# --- стадия — с начала работы ---------------------------------------------------------------


@dataclass
class Watched(Serp):
    """Выдача, которая смотрит на строку прогона, пока её покупают."""

    session: AsyncSession | None = None
    run: Run | None = None
    seen: list[tuple[str, str, bool]] = field(default_factory=list)

    async def search(
        self,
        keywords: Sequence[str],
        country: str,
        *,
        depth_pages: int = 1,
        watch: SerpWatch | None = None,
    ) -> dict[str, list[SerpResult]]:
        assert self.run is not None
        assert self.session is not None
        progress = self.run.report["progress"]
        self.seen.append((self.run.stage, progress["stage"], self.run in self.session.dirty))
        return await super().search(keywords, country, depth_pages=depth_pages, watch=watch)


async def test_each_stage_is_set_when_its_work_begins_and_committed_before_it(
    session: AsyncSession, web: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Выдача шла шесть минут, а экран стоял «в очереди»: стадия ставилась после этапа. Теперь
    этап отмечен в строке — и закоммичен — до своей работы."""
    run = await make_run(session, await hypothesis(session))
    found = Watched(rows(SHOP, PARTS).copy(), session=session, run=run)
    stages: dict[str, tuple[str, bool]] = {}

    class Judged(Model):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            stages.setdefault("judge", (run.stage, run in session.dirty))
            return super().__call__(request)

    original_search = segment_run.search_contacts
    original_leads = segment_run.make_leads

    async def search(session_: AsyncSession, **kwargs: Any) -> SearchReport:
        stages["contacts"] = (run.stage, run in session.dirty)
        return await original_search(session_, **kwargs)

    async def made(session_: AsyncSession, run_: Run, verifier: Any, **kw: Any) -> dict[str, Any]:
        stages["leads"] = (run.stage, run in session.dirty)
        return await original_leads(session_, run_, verifier, **kw)

    monkeypatch.setattr(segment_run, "search_contacts", search)
    monkeypatch.setattr(segment_run, "make_leads", made)

    await go(session, run, found, Judged(ANSWERS))

    assert found.seen == [(Stage.SERP, Stage.SERP, False)]
    assert stages == {
        "judge": (Stage.JUDGE, False),
        "contacts": (Stage.CONTACTS, False),
        "leads": (Stage.LEADS, False),
    }
    assert (run.stage, run.report["progress"]["stage"]) == (Stage.DONE, Stage.LEADS)


async def test_the_stored_progress_names_the_stage_its_numbers_and_times(
    session: AsyncSession, web: Sites
) -> None:
    run = await make_run(session, await hypothesis(session))

    await go(session, run, serp(SHOP, PARTS), Model(ANSWERS))

    progress = run.report["progress"]
    # Последний этап прохода — лиды: заведён один (у магазина запчастей адреса нет).
    assert (progress["stage"], progress["done"], progress["total"]) == ("leads", 1, 1)
    started = datetime.fromisoformat(progress["pass_started_at"])
    assert started <= datetime.fromisoformat(progress["started_at"])
    assert datetime.fromisoformat(progress["started_at"]) <= datetime.fromisoformat(
        progress["updated_at"]
    )


# --- живое «сделано» судьи и адресов ----------------------------------------------------------


async def _candidates(
    session: AsyncSession, run: Run, *hosts: str
) -> list[SalesSegmentCandidateModel]:
    made = []
    for host in hosts:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
        candidate = SalesSegmentCandidateModel(
            run_id=run.id, hypothesis_id=run.hypothesis_id, domain_id=domain.id
        )
        session.add(candidate)
        made.append(candidate)
    await session.flush()
    return made


async def test_the_judge_progress_is_how_many_of_the_waiting_left_the_queue(
    session: AsyncSession,
) -> None:
    """«Судья: X из Y» — Y ждали судью на входе в этап, X — сколько из них уже не ждут."""
    run = await make_run(session, await hypothesis(session))
    first, second, third = await _candidates(
        session, run, "a.example.test", "b.example.test", "c.example.test"
    )
    run.stage = Stage.JUDGE
    run.report = {
        "progress": segment_progress.entered(Stage.JUDGE, total=3, pass_started=datetime.now(UTC))
    }
    await session.flush()

    assert (await segment_progress.live(session, run) or {})["done"] == 0

    first.lane, second.lane = Lane.ACCEPT, Lane.REJECT
    third.lane, third.unanswered = Lane.REVIEW, True  # модель не ответила — ещё ждёт судью
    await session.flush()

    assert (await segment_progress.live(session, run) or {})["done"] == 2


async def test_the_address_progress_counts_domains_with_an_outcome_of_this_stage(
    session: AsyncSession,
) -> None:
    run = await make_run(session, await hypothesis(session))
    earlier, now, waiting = await _candidates(
        session, run, "a.example.test", "b.example.test", "c.example.test"
    )
    begun = datetime.now(UTC)
    run.stage = Stage.CONTACTS
    run.report = {
        "progress": segment_progress.entered(
            Stage.CONTACTS, total=2, pass_started=begun, moment=begun
        )
    }
    earlier.contact_attempted_at = begun - timedelta(days=3)  # исход прошлого прохода
    now.contact_attempted_at = begun + timedelta(seconds=5)
    await session.flush()

    found = await segment_progress.live(session, run)

    assert found is not None
    assert (found["stage"], found["done"], found["total"]) == ("contacts", 1, 2)
    assert waiting.contact_attempted_at is None


async def test_a_run_without_a_progress_record_has_none(session: AsyncSession) -> None:
    """Прогон старше хода (или ещё не взятый задачей) — хода нет, а не нули."""
    run = await make_run(session, await hypothesis(session))

    assert await segment_progress.live(session, run) is None


# --- конец прохода -------------------------------------------------------------------------------


async def test_a_run_stopped_by_the_model_cap_ends_its_pass(
    session: AsyncSession, web: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`finished_at` — у готового и у остановленного: до 14.10 только у готового, и
    остановленный прогон выглядел незаконченным проходом."""
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

    await go(session, run, serp(SHOP), Model(ANSWERS))

    assert run.stage == Stage.STOPPED
    assert run.finished_at is not None
    # Остановлен на судье: запись хода — его, «сделано» — ни одного из одного.
    assert (run.report["progress"]["stage"], run.report["progress"]["done"]) == ("judge", 0)


async def test_a_run_whose_segment_was_erased_ends_its_pass(
    session: AsyncSession, web: Sites
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    owner.description = None
    await session.commit()

    await go(session, run, serp(SHOP), Model(ANSWERS))

    assert (run.stage, run.finished_at is not None) == (Stage.STOPPED, True)


# --- пульс ---------------------------------------------------------------------------------------


async def test_the_pulse_marks_the_run_alive_while_the_pass_goes(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Шесть минут выдачи без записи в строку прогона неотличимы от мёртвого прогона: пульс
    трогает `updated_at` своей сессией, пока проход идёт. Пауза — без сна: один удар, затем
    отмена, как в конце прохода."""
    run = await make_run(session, await hypothesis(session))
    before = run.updated_at
    factory = async_sessionmaker(bind=await session.connection(), expire_on_commit=False)
    beats: list[float] = []

    async def sleep(every: float) -> None:
        beats.append(every)
        if len(beats) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(segment_progress, "_sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await segment_progress.pulse(factory, run.id)

    await session.refresh(run)
    assert run.updated_at > before
    assert beats == [segment_progress.PULSE_SEC, segment_progress.PULSE_SEC]


async def test_the_job_beats_the_pulse_for_the_pass_and_stops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пульс идёт, пока идёт проход, и снимается в конце — и при сбое прохода."""
    seen: list[str] = []

    async def pulse(_factory: object, run_id: int, **_kw: object) -> None:
        seen.append(f"начат {run_id}")
        try:
            await asyncio.sleep(3600)
        finally:
            seen.append("снят")

    async def execute(_session: AsyncSession, run_id: int, _deps: object) -> dict[str, Any]:
        await asyncio.sleep(0)  # пульс успевает начаться
        raise ConnectionError(f"база не ответила на прогоне {run_id}")

    monkeypatch.setattr(segment_jobs, "pulse", pulse)
    monkeypatch.setattr(segment_jobs, "execute", execute)
    monkeypatch.setattr(segment_jobs, "_stop", _no_stop)
    monkeypatch.setattr("backend.config.sales.ENABLED", True)
    monkeypatch.setattr("backend.config.sales.VERIFIER_PROVIDER", "fixture")
    monkeypatch.setattr("backend.config.serp.PROVIDER", "dataforseo")
    monkeypatch.setattr("backend.config.serp.LOGIN", "login-test")
    monkeypatch.setattr("backend.config.serp.PASSWORD", "password-test")  # pragma: allowlist secret
    monkeypatch.setattr("backend.config.storage.DSN", TEST_DSN)

    with pytest.raises(ConnectionError):
        await segment_jobs.run_once(7)

    assert seen == ["начат 7", "снят"]


async def _no_stop(_factory: object, _run_id: int, _words: str) -> None:
    return None


# --- ход выдачи и номера задач -------------------------------------------------------------------


@dataclass
class Queued:
    """Выдача отложенного режима: номера задач — до опроса, первые опросы могут упасть.
    Ключ с номером из `watch.tasks` снова не ставится — как у адаптера DataForSEO."""

    results: dict[str, list[SerpResult]]
    price: float = 0.0006
    name: str = "fake-queue"
    mode: SerpMode = SerpMode.STANDARD
    spent: float = 0.0
    posted: list[tuple[str, ...]] = field(default_factory=list)
    broken_polls: int = 0
    run: Run | None = None
    seen: list[tuple[int, int | None]] = field(default_factory=list)

    async def search(
        self,
        keywords: Sequence[str],
        country: str,
        *,
        depth_pages: int = 1,
        watch: SerpWatch | None = None,
    ) -> dict[str, list[SerpResult]]:
        watch = watch or SerpWatch()
        fresh = [key for key in keywords if key not in watch.tasks]
        if fresh:
            self.posted.append(tuple(fresh))
            self.spent += self.price * len(fresh) * depth_pages
            await watch.posted({key: f"task-{n}" for n, key in enumerate(fresh)})
        await watch.progress(SerpProgress(done=0, total=len(keywords)))
        if self.broken_polls:
            self.broken_polls -= 1
            raise SerpError("Провайдер выдачи не ответил на опрос: 503")
        out = {}
        for done, key in enumerate(keywords, start=1):
            out[key] = self.results.get(key, [])
            await watch.progress(
                SerpProgress(done=done, total=len(keywords), keyword=key, cost_usd=self.price)
            )
            if self.run is not None:
                progress = self.run.report["progress"]
                self.seen.append((progress["done"], progress["total"]))
        return out


async def test_serp_progress_counts_the_keywords_that_answered(
    session: AsyncSession, web: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Покупаем выдачу: готово X из Y ключей» — по событиям источника, а не по концу покупки."""
    monkeypatch.setattr(segment_serp, "PROGRESS_EVERY_S", 0.0)
    keys = (KEYWORD, "велосипед для города", "запчасти для велосипеда")
    run = await make_run(session, await hypothesis(session), keywords=keys)
    found = Queued(rows(SHOP), run=run)

    await go(session, run, found, Model(ANSWERS))

    assert found.seen == [(1, 3), (2, 3), (3, 3)]
    assert run.report["serp"]["mode"] == "standard"


async def test_task_numbers_are_saved_before_polling_and_a_retry_does_not_buy_again(
    session: AsyncSession, web: Sites
) -> None:
    """Опрос упал после постановки: номера задач уже в строке прогона — повтор задачи
    забирает оплаченное по ним, второй покупки нет, строка расхода одна на оплату."""
    run = await make_run(session, await hypothesis(session))
    found = Queued(rows(SHOP), price=0.0045, broken_polls=1)

    with pytest.raises(SerpError):
        await go(session, run, found, Model(ANSWERS))

    await session.refresh(run)
    assert run.report["serp"]["tasks"] == {KEYWORD: "task-0"}
    assert run.report["serp"]["cost_usd"] == pytest.approx(0.0045)  # как в журнале, до повтора
    assert not segment_serp.bought(run)
    assert run.report["progress"]["note"] == (
        "выдача упала после оплаты ($0,0045): номера задач сохранены — следующий проход "
        "заберёт оплаченное по ним и второй раз не купит"
    )

    await go(session, run, found, Model(ANSWERS))

    assert found.posted == [(KEYWORD,)]  # поставлено один раз
    assert run.report["serp"]["cost_usd"] == pytest.approx(0.0045)
    assert run.report["serp"]["tasks"] == {KEYWORD: "task-0"}
    assert "note" not in run.report["progress"]
    spent = await session.scalars(
        select(UsageRecordModel.amount_usd).where(UsageRecordModel.operation == "sales_serp")
    )
    assert list(spent) == [Decimal("0.0045")]
    assert run.stage == Stage.DONE


@dataclass
class LiveBroken(Queued):
    """Быстрый режим: номеров нет, оплата — на каждый запрос; первая покупка падает после
    оплаты всех ключей."""

    mode: SerpMode = SerpMode.LIVE
    calls: int = 0

    async def search(
        self,
        keywords: Sequence[str],
        country: str,
        *,
        depth_pages: int = 1,
        watch: SerpWatch | None = None,
    ) -> dict[str, list[SerpResult]]:
        self.calls += 1
        self.spent += self.price * len(keywords) * depth_pages
        if self.calls == 1:
            raise SerpError("Провайдер выдачи оборвал соединение")
        return {key: self.results.get(key, []) for key in keywords}


async def test_a_live_purchase_that_failed_after_paying_says_it_will_be_bought_again(
    session: AsyncSession, web: Sites
) -> None:
    """У быстрого режима номеров задач нет: повтор после сбоя купит выдачу снова — прогон
    говорит это до повтора, а расход выдачи прогона — за обе покупки."""
    run = await make_run(session, await hypothesis(session))
    found = LiveBroken(rows(SHOP), price=0.002)

    with pytest.raises(SerpError):
        await go(session, run, found, Model(ANSWERS))

    await session.refresh(run)
    assert run.report["serp"]["cost_usd"] == pytest.approx(0.002)  # как в журнале, до повтора
    assert run.report["progress"]["note"] == (
        "выдача упала после оплаты ($0,0020): у быстрого режима номеров задач нет — следующий "
        "проход купит её заново"
    )

    await go(session, run, found, Model(ANSWERS))

    assert found.calls == 2
    assert run.report["serp"]["cost_usd"] == pytest.approx(0.004)
    assert run.report["serp"]["paid_usd"] == pytest.approx(0.002)
    assert run.report["serp"]["tasks"] == {}
    rows_ = await session.scalars(
        select(UsageRecordModel.amount_usd)
        .where(UsageRecordModel.operation == "sales_serp")
        .order_by(UsageRecordModel.id)
    )
    assert list(rows_) == [Decimal("0.0020"), Decimal("0.0020")]


async def test_an_old_run_with_a_bought_serp_is_not_bought_again(
    session: AsyncSession, web: Sites
) -> None:
    """Прогон до номеров задач: итог выдачи без номеров — выдача куплена, повтор её не берёт.
    Стадия тут ни при чём: и прогон «в очереди» с итогом выдачи её не покупает."""
    run = await make_run(session, await hypothesis(session))
    run.report = {"serp": {"keywords": 1, "results": 0, "hosts": 0, "cost_usd": 0.0009}}
    await session.commit()
    found = serp(SHOP)

    await go(session, run, found, Model(ANSWERS))

    assert found.calls == []
    assert run.stage == Stage.DONE


async def test_a_run_stopped_before_its_serp_buys_it_when_continued(
    session: AsyncSession, web: Sites
) -> None:
    """Остановлен до выдачи (источник не собрался) — продолжение покупает выдачу, а не идёт к
    судье без единого сайта и не кончается «готово» с нулями."""
    run = await make_run(session, await hypothesis(session))
    run.stage, run.error = Stage.STOPPED, "источник выдачи не настроен"
    await session.commit()
    found = serp(SHOP)

    await go(session, run, found, Model(ANSWERS))

    assert len(found.calls) == 1
    assert (run.stage, run.error) == (Stage.DONE, None)
    assert len(await leads(session)) == 1


async def test_a_continued_run_does_not_buy_the_serp_a_second_time(
    session: AsyncSession, web: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Остановлен потолком модели после выдачи — продолжение судит дальше, выдачу не покупает."""
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
    found = serp(SHOP)
    await go(session, run, found, Model(ANSWERS))
    assert run.stage == Stage.STOPPED

    monkeypatch.setattr("backend.config.llm.SALES_DAILY_TOKEN_CAP", 10**9)
    await go(session, run, found, Model(ANSWERS))

    assert len(found.calls) == 1
    assert run.stage == Stage.DONE
    spent = await session.scalar(
        select(func.count()).where(UsageRecordModel.operation == "sales_serp")
    )
    assert spent == 1


# --- расход проходов -------------------------------------------------------------------------------


def test_address_search_numbers_of_passes_add_up() -> None:
    first = {
        "pending": 3,
        "walked": 3,
        "saved": 1,
        "counters": {"provider_entered": 2, "pages": 3},
        "notes": ["Платный сервис: остаток квоты 50 поисков."],
    }
    second = {"pending": 0, "walked": 0, "saved": 0}

    found = segment_counts.contacts_report(segment_counts.contacts_report(None, first), second)

    assert (found["walked"], found["saved"], found["pending"]) == (3, 1, 0)
    assert found["counters"] == {"provider_entered": 2, "pages": 3}
    assert found["notes"] == ["Платный сервис: остаток квоты 50 поисков."]

    third = {"pending": 2, "walked": 2, "saved": 2, "counters": {"provider_entered": 1}}
    again = segment_counts.contacts_report(found, third)

    assert again["counters"] == {"provider_entered": 3, "pages": 3}
    assert (again["walked"], again["saved"], again["pending"]) == (5, 3, 2)


def test_cleaning_numbers_of_passes_add_up_and_the_last_state_wins() -> None:
    first = {
        "created": 2,
        "cleaning": {
            "checked": 2,
            "ready": 1,
            "rejected": {"duplicate": 1},
            "paid_units": 2,
            "stopped": "проверка остановлена: квота",
            "verifier": "live",
        },
    }
    second = {"created": 0}
    third = {
        "created": 3,
        "cleaning": {
            "checked": 3,
            "ready": 2,
            "rejected": {"duplicate": 1, "no_mail": 1},
            "paid_units": 3,
            "stopped": None,
            "verifier": "live",
        },
    }

    found = segment_run.leads_report(
        segment_run.leads_report(segment_run.leads_report(None, first), second), third
    )

    assert found["created"] == 5
    cleaning = found["cleaning"]
    assert (cleaning["checked"], cleaning["ready"], cleaning["paid_units"]) == (5, 3, 5)
    assert cleaning["rejected"] == {"duplicate": 2, "no_mail": 1}
    assert cleaning["stopped"] is None


async def test_a_second_pass_adds_its_paid_searches_instead_of_overwriting(
    session: AsyncSession, web: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    """До 14.10 «Найти адреса принятым» затирало счёт Hunter первого прохода: расход прогона
    выглядел расходом последнего прохода. Ключи прежние — их читает панель расхода."""

    async def spy(
        _session: AsyncSession, *, limit: int, queue: object, no_paid: bool
    ) -> SearchReport:
        return SearchReport(pending=limit, walked=limit, counters={"provider_entered": 2})

    monkeypatch.setattr(segment_run, "search_contacts", spy)
    run = await make_run(session, await hypothesis(session))
    found = serp(SHOP, PARTS)

    await go(session, run, found, Model(ANSWERS))
    await go(session, run, found, Model(ANSWERS))

    contacts = run.report["contacts"]
    assert contacts["counters"] == {"provider_entered": 4}
    assert contacts["walked"] == 4


# --- режим покупки выдачи ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("estimated", "bought_as"),
    [("standard", SerpMode.STANDARD), (None, SerpMode.LIVE)],
    ids=["from-the-estimate", "service-default"],
)
async def test_the_serp_mode_of_the_run_comes_from_its_estimate(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    estimated: str | None,
    bought_as: SerpMode,
) -> None:
    """Какой режим человек видел в смете на запуске, такой и покупается, а не тот, что стоит в
    настройке сервиса сейчас; прогон старше режимов — умолчание сервиса."""
    monkeypatch.setattr("backend.config.serp.MODE", "live")
    run = await make_run(session, await hypothesis(session))
    run.estimate = {"lines": ["смета"], **({"serp_mode": estimated} if estimated else {})}
    await session.commit()
    factory = async_sessionmaker(bind=await session.connection(), expire_on_commit=False)
    built: list[object] = []

    def build(_client: object, *, mode: SerpMode | None = None) -> Serp:
        built.append(mode)
        return Serp({})

    monkeypatch.setattr(segment_jobs, "build_provider", build)

    segment_jobs._serp(object(), await segment_jobs._mode(factory, run.id))  # type: ignore[arg-type]

    assert built == [bought_as]


async def test_an_unknown_serp_mode_in_the_estimate_stops_the_run_in_words(
    session: AsyncSession,
) -> None:
    run = await make_run(session, await hypothesis(session))
    run.estimate = {"lines": ["смета"], "serp_mode": "turbo"}
    await session.commit()
    factory = async_sessionmaker(bind=await session.connection(), expire_on_commit=False)

    with pytest.raises(segment_jobs.SerpNotSetError, match="режима выдачи «turbo» нет"):
        await segment_jobs._mode(factory, run.id)
