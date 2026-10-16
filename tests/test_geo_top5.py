"""`outreach geo-top5`: докупка топ-5 стран донорам с неполной разбивкой.

До 16.10.2026 запрос по странам не шёл, когда верхняя страна пакета совпадала со
страной прогона, и у донора оставалась одна страна из пяти. Команда докупает
остальные — разово, по «да» человека. Проверяется то, за что она отвечает деньгами
и данными: показ ничего не покупает и не пишет; с `--yes` расход ложится в журнал,
как у прогона, кап держится до первого запроса и по ходу, статус и решение
человека не трогаются, сбой одного донора не роняет остальных, отказ «повтор не
поможет» останавливает, а обрыв посреди списка не теряет купленного.

Провайдер — подставным транспортом клиента в форме настоящего ответа: остаток квоты
и страны с заголовками расхода.
"""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.cli.geo_top5 import run_geo_top5
from backend.cli.main import main
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import UnitsCost
from backend.features.core.domain import DonorStatus, Stage, UsageProvider
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import AhrefsCapModel, UsageRecordModel
from backend.features.donors.collect import DomainResult
from backend.features.donors.geo import CountryShare, GeoVerdict
from backend.features.donors.geo_top5 import buy_top5, plan_top5
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Metrics
from backend.features.runs.ahrefs_key import cycle_of, day_words
from backend.features.runs.repository import RunRepository
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.conftest import TEST_DSN

#: Трафик домена: доли полной разбивки — от него (`geo.share_base`).
TRAFFIC = 10_000
#: Топ стран, который отдаёт запрос по странам: США первыми.
TOP5 = [
    {"country": "us", "org_traffic": 5500},
    {"country": "gb", "org_traffic": 1200},
    {"country": "ca", "org_traffic": 800},
]
#: Остаток у провайдера. Кап — от ключа (16.10.2026): весь лимит ключа — 100 000, как кап
#: в замечании ТЗ; суженный — строкой смены с экрана «Расход» (`AhrefsCapModel`).
QUOTA: dict[str, object] = {
    "units_limit_workspace": 6_400_000,
    "units_usage_workspace": 0,
    "units_limit_api_key": 100_000,
    "units_usage_api_key": 0,
}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повторы клиента проверяются числом попыток, а не часами."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", instant)


class Ahrefs:
    """Провайдер-заглушка: остаток квоты — бесплатно, страны — с ценой в заголовках.

    `refusals` — код отказа по домену; `broken` — домен, на котором рвётся связь
    посреди списка (не ответ провайдера, а обрыв процесса)."""

    def __init__(
        self,
        countries: dict[str, list[dict[str, Any]]] | None = None,
        *,
        refusals: dict[str, int] | None = None,
        broken: str | None = None,
        cost: int = 55,
        quota: dict[str, object] | None = None,
    ) -> None:
        self.quota = quota or QUOTA
        self.countries = countries or {}
        self.refusals = refusals or {}
        self.broken = broken
        self.cost = cost
        self.asked: list[str] = []
        self.quota_asked = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("limits-and-usage"):
            self.quota_asked += 1
            return httpx.Response(200, json={"limits_and_usage": self.quota})
        params = dict(request.url.params)
        assert params["limit"] == "5", "страны — только топ-5: без лимита запрос стоит 1650"
        host = params["target"]
        self.asked.append(host)
        if host == self.broken:
            raise RuntimeError("обрыв посреди списка")
        if host in self.refusals:
            return httpx.Response(self.refusals[host], text="refused")
        price = {"x-api-units-cost-total-actual": str(self.cost)}
        return httpx.Response(200, json={"metrics": self.countries.get(host, TOP5)}, headers=price)

    def client(self) -> AhrefsClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self), base_url="https://api.test")
        return AhrefsClient(api_key="k", http=http)


def _args(*, yes: bool = False, accepted_only: bool = False, limit: int | None = None) -> Any:
    return argparse.Namespace(yes=yes, accepted_only=accepted_only, limit=limit)


def _partial_result(host: str) -> DomainResult:
    """Донор, как его записывал прогон до 16.10.2026: одна верхняя страна, пометка."""
    top = CountryShare("us", 8000, 0.8)
    return DomainResult(
        host,
        DonorStatus.SUITABLE,
        "US на 1-м месте по трафику (80%)",
        Metrics(dr=40, org_traffic=TRAFFIC, refdomains=500, org_keywords=2000),
        geo=GeoVerdict(True, "US на 1-м месте по трафику (80%)", "us", [top], partial=True),
        raw={"url": f"{host}/", "domain_rating": 40, "org_traffic": TRAFFIC},
    )


async def _partial(session: AsyncSession, host: str, *, accepted: bool = True) -> None:
    await DonorRepository(session).save_results([_partial_result(host)])
    if accepted:
        await session.execute(
            update(DonorModel)
            .where(
                DonorModel.domain_id
                == select(DomainModel.id).where(DomainModel.host == host).scalar_subquery()
            )
            .values(review="accepted", review_by="оператор@site.com")
        )
    await session.flush()


async def _donor(session: AsyncSession, host: str) -> DonorModel:
    """Донор как он в базе сейчас: запись команды шла запросом, а не объектом."""
    found = await session.execute(
        select(DonorModel)
        .join(DomainModel, DomainModel.id == DonorModel.domain_id)
        .where(DomainModel.host == host)
        .execution_options(populate_existing=True)
    )
    return found.scalar_one()


async def _journal(session: AsyncSession) -> list[tuple[str, int | None, int | None, Stage | None]]:
    rows = await session.execute(
        select(
            UsageRecordModel.operation,
            UsageRecordModel.units,
            UsageRecordModel.run_id,
            UsageRecordModel.stage,
        ).order_by(UsageRecordModel.id)
    )
    return list(rows.tuples().all())


class TestShow:
    async def test_buys_nothing_and_writes_nothing(
        self, session: AsyncSession, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await _partial(session, "one.example.test")
        await _partial(session, "two.example.test", accepted=False)
        ahrefs = Ahrefs()

        code = await run_geo_top5(session, ahrefs.client(), _args())

        printed = capsys.readouterr().out
        assert code == 0
        assert ahrefs.asked == [], "показ не покупает стран"
        assert ahrefs.quota_asked == 1, "остаток у провайдера — бесплатным запросом"
        assert await _journal(session) == []
        assert (await _donor(session, "one.example.test")).geo_partial
        assert "С неполной разбивкой по странам (доноров и кандидатов): 2" in printed
        assert "one.example.test — США, принят" in printed
        assert "two.example.test — США, не донор" in printed
        assert "Смета: 2 × 55 = 110 юнитов" in printed
        assert (
            "Кап Ahrefs: 100 000 (весь лимит ключа), потрачено нами 0, осталось 100 000" in printed
        )
        assert "Цикл: календарный месяц с 1 " in printed, "даты обнуления Ahrefs не назвал"
        assert "Можно потратить: 100 000" in printed
        assert "Это показ — ничего не куплено" in printed

    async def test_says_when_it_would_not_fit(
        self,
        session: AsyncSession,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session.add(AhrefsCapModel(units_cap=100))
        for name in ("a", "b", "c"):
            await _partial(session, f"{name}.example.test")

        code = await run_geo_top5(session, Ahrefs().client(), _args())

        printed = capsys.readouterr().out
        assert code == 0, "показ — не отказ"
        assert "С --yes не купится: не помещается: 3 × 55 = 165 юнитов" in printed
        assert "Помещается доноров: 1 — `--limit 1`" in printed

    async def test_nobody_to_buy_for(
        self, session: AsyncSession, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ahrefs = Ahrefs()

        assert await run_geo_top5(session, ahrefs.client(), _args(yes=True)) == 0
        assert "Докупать некому" in capsys.readouterr().out
        assert ahrefs.quota_asked == 0


class TestBuy:
    async def test_journal_data_and_the_verdict_untouched(
        self, session: AsyncSession, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await _partial(session, "one.example.test")
        await _partial(session, "two.example.test", accepted=False)
        ahrefs = Ahrefs()

        code = await run_geo_top5(session, ahrefs.client(), _args(yes=True))

        assert code == 0
        assert ahrefs.asked == ["one.example.test", "two.example.test"]
        # Как пишет прогон: операция клиента, юниты из заголовков, этап — доноры.
        assert await _journal(session) == [("by_country", 55, None, Stage.DONORS)] * 2
        donor = await _donor(session, "one.example.test")
        assert donor.geo_partial is False
        assert donor.geo_breakdown == [
            {"country": "us", "org_traffic": 5500, "share": 0.55},
            {"country": "gb", "org_traffic": 1200, "share": 0.12},
            {"country": "ca", "org_traffic": 800, "share": 0.08},
        ]
        assert (donor.geo, donor.geo_top_share) == ("us", 0.55)
        assert (donor.status, donor.reject_reason) == (DonorStatus.SUITABLE, None)
        assert (donor.review, donor.review_by) == ("accepted", "оператор@site.com")
        printed = capsys.readouterr().out
        assert "Докуплено: 2, записано в журнал расхода: 110" in printed

    async def test_accepted_first_and_only_on_request(self, session: AsyncSession) -> None:
        await _partial(session, "candidate.example.test", accepted=False)
        await _partial(session, "donor.example.test")

        everyone = await plan_top5(session)
        accepted = await plan_top5(session, accepted_only=True)
        first = await plan_top5(session, limit=1)

        assert [d.host for d in everyone.donors] == ["donor.example.test", "candidate.example.test"]
        assert [d.host for d in accepted.donors] == ["donor.example.test"]
        assert ([d.host for d in first.donors], first.found) == (["donor.example.test"], 2)

    async def test_a_new_top_country_is_named_and_the_verdict_stays(
        self, session: AsyncSession, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Полный ответ назвал верхней другую страну — разбивка и верхняя из ответа,
        чтобы доля рядом с ней была её долей; вердикт не пересуживается, а называется."""
        await _partial(session, "moved.example.test")
        rows = [{"country": "de", "org_traffic": 6000}, {"country": "us", "org_traffic": 3000}]

        await run_geo_top5(session, Ahrefs({"moved.example.test": rows}).client(), _args(yes=True))

        donor = await _donor(session, "moved.example.test")
        assert (donor.geo, donor.geo_top_share) == ("de", 0.6)
        assert donor.status is DonorStatus.SUITABLE
        assert "верхняя страна — Германия, а не США; вердикт не тронут" in capsys.readouterr().out

    async def test_empty_answer_keeps_the_partial_breakdown(self, session: AsyncSession) -> None:
        """Ahrefs ответил без стран — запрос оплачен и записан, разбивка — как была."""
        await _partial(session, "quiet.example.test")

        await run_geo_top5(session, Ahrefs({"quiet.example.test": []}).client(), _args(yes=True))

        donor = await _donor(session, "quiet.example.test")
        assert donor.geo_partial
        assert donor.geo_breakdown == [{"country": "us", "org_traffic": 8000, "share": 0.8}]
        assert await _journal(session) == [("by_country", 55, None, Stage.DONORS)]

    async def test_a_run_that_refreshed_the_donor_wins(self, session: AsyncSession) -> None:
        """Прогон обновил донора, пока ждали ответа: его разбивка не затирается."""
        await _partial(session, "raced.example.test")
        plan = await plan_top5(session)
        await session.execute(
            update(DonorModel).values(geo_partial=False, geo_breakdown=[{"country": "ca"}])
        )

        report = await buy_top5(session, Ahrefs().client(), plan, allowed=1_000)

        assert report.taken == ["raced.example.test"]
        assert (await _donor(session, "raced.example.test")).geo_breakdown == [{"country": "ca"}]
        assert await _journal(session) == [("by_country", 55, None, Stage.DONORS)]


class TestCap:
    async def test_does_not_fit_refused_before_the_first_request(
        self,
        session: AsyncSession,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session.add(AhrefsCapModel(units_cap=100))
        for name in ("a", "b", "c"):
            await _partial(session, f"{name}.example.test")
        ahrefs = Ahrefs()

        code = await run_geo_top5(session, ahrefs.client(), _args(yes=True))

        assert code == 3
        assert ahrefs.asked == []
        assert await _journal(session) == []
        assert "Не куплено ничего: не помещается" in capsys.readouterr().out

    async def test_the_cap_from_the_usage_screen_counts(
        self, session: AsyncSession, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Кап — от ключа, который назвал ответ Ahrefs, а сужен на экране «Расход» (строка
        `ahrefs_caps`): им меряется докупка, как и прогон (`ahrefs_cap.key_cap`). Цикл — от
        обнуления ключа, которое Ahrefs назвал датой."""
        reset = (datetime.now(UTC) + timedelta(days=10)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cycle = cycle_of(reset, datetime.now(UTC))
        session.add(AhrefsCapModel(units_cap=110))
        await session.flush()
        for name in ("a", "b", "c"):
            await _partial(session, f"{name}.example.test")
        quota = {**QUOTA, "usage_reset_date": reset.date().isoformat()}

        code = await run_geo_top5(session, Ahrefs(quota=quota).client(), _args())

        printed = capsys.readouterr().out
        assert code == 0, "показ — не отказ"
        assert (
            "Кап Ahrefs: 110 (не больше 110 при лимите ключа 100 000), потрачено нами 0, "
            "осталось 110"
        ) in printed
        assert f"Цикл ключа: с {day_words(cycle.start)}, обнуление {day_words(reset)}" in printed
        assert "не помещается: 3 × 55 = 165 юнитов" in printed

    async def test_limit_buys_what_fits(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session.add(AhrefsCapModel(units_cap=100))
        for name in ("a", "b", "c"):
            await _partial(session, f"{name}.example.test")
        ahrefs = Ahrefs()

        assert await run_geo_top5(session, ahrefs.client(), _args(yes=True, limit=1)) == 0
        assert ahrefs.asked == ["a.example.test"]
        assert await _journal(session) == [("by_country", 55, None, Stage.DONORS)]

    async def test_month_spending_counts(
        self,
        session: AsyncSession,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Кап — на цикл ключа: потраченное в цикле из него вычитается."""
        session.add(AhrefsCapModel(units_cap=110))
        await RunRepository(session).record_usage(
            run_id=None,
            operation="batch_metrics",
            cost=UnitsCost(actual=60, estimated=60, per_row=None),
            stage=Stage.DONORS,
        )
        await _partial(session, "a.example.test")
        ahrefs = Ahrefs()

        assert await run_geo_top5(session, ahrefs.client(), _args(yes=True)) == 3
        assert ahrefs.asked == []
        assert "можно потратить 50" in capsys.readouterr().out

    async def test_dearer_answers_stop_before_the_cap(
        self,
        session: AsyncSession,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Смета помещалась впритык, но провайдер взял за запрос больше: запрос,
        который по смете уже не влезает, не делается."""
        session.add(AhrefsCapModel(units_cap=165))
        for name in ("a", "b", "c"):
            await _partial(session, f"{name}.example.test")
        ahrefs = Ahrefs(cost=100)

        code = await run_geo_top5(session, ahrefs.client(), _args(yes=True))

        assert code == 3
        assert ahrefs.asked == ["a.example.test", "b.example.test"]
        assert "Остановлено по капу: записано 200 юнитов из 165" in capsys.readouterr().out

    async def test_unknown_provider_balance_is_a_refusal(
        self, session: AsyncSession, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Ключа нет — остаток у Ahrefs и размер ключа неизвестны: показ говорит это,
        покупка отказывает."""
        await _partial(session, "a.example.test")

        assert await run_geo_top5(session, None, _args()) == 0
        printed = capsys.readouterr().out
        assert "У Ahrefs: неизвестно — ключа Ahrefs нет" in printed
        assert "Кап Ahrefs: неизвестен — Ahrefs ещё не ответил — размер ключа неизвестен" in printed
        assert await run_geo_top5(session, None, _args(yes=True)) == 3
        assert "тратить вслепую нельзя" in capsys.readouterr().out


class TestFailures:
    async def test_one_failure_goes_on_and_a_permanent_one_stops(
        self, session: AsyncSession, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """500 после повторов — строка в журнал и дальше; 403 — повтор не поможет, стоп."""
        for name in ("a", "b", "c", "d"):
            await _partial(session, f"{name}.example.test")
        ahrefs = Ahrefs(refusals={"a.example.test": 500, "c.example.test": 403})

        code = await run_geo_top5(session, ahrefs.client(), _args(yes=True))

        assert code == 5
        # Временный отказ клиент повторяет сам; постоянный — нет; после стопа — тишина.
        assert ahrefs.asked == ["a.example.test"] * 4 + ["b.example.test", "c.example.test"]
        assert (await _donor(session, "a.example.test")).geo_partial
        assert not (await _donor(session, "b.example.test")).geo_partial
        assert (await _donor(session, "d.example.test")).geo_partial
        assert await _journal(session) == [("by_country", 55, None, Stage.DONORS)]
        printed = capsys.readouterr().out
        assert "Провайдер не ответил — повторный запуск их возьмёт: 1" in printed
        assert "Остановлено, повтор не поможет: c.example.test" in printed


@pytest.fixture
async def committed() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Сессии с настоящими фиксациями: обрыв проверяется тем, что осталось в базе.
    После теста база вычищается целиком — остальные тесты ждут пустые таблицы."""
    engine = create_async_engine(TEST_DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            tables = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
            ).scalars()
            listed = ", ".join(f'"{table}"' for table in tables)
            await conn.execute(text(f"TRUNCATE {listed} RESTART IDENTITY CASCADE"))
        await engine.dispose()


async def test_interruption_keeps_what_was_bought(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    """Обрыв на втором доноре: первый докуплен и записан вместе со строкой расхода."""
    async with committed() as session:
        await _partial(session, "first.example.test")
        await _partial(session, "second.example.test")
        await session.commit()

    ahrefs = Ahrefs(broken="second.example.test")
    async with committed() as session:
        with pytest.raises(RuntimeError, match="обрыв"):
            await run_geo_top5(session, ahrefs.client(), _args(yes=True))

    async with committed() as session:
        assert not (await _donor(session, "first.example.test")).geo_partial
        assert (await _donor(session, "second.example.test")).geo_partial
        assert await _journal(session) == [("by_country", 55, None, Stage.DONORS)]
        spent = await session.scalar(
            select(UsageRecordModel.provider).where(UsageRecordModel.operation == "by_country")
        )
        assert spent is UsageProvider.AHREFS


def test_named_in_the_console(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Команда собрана в консоли: без доноров с неполной разбивкой — «некому», и ни
    одного запроса к провайдеру: ключ погашен, `.env` разработчика не в счёт."""
    monkeypatch.setattr("backend.config.storage.DSN", TEST_DSN)
    monkeypatch.setattr("backend.config.storage.REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr("backend.config.ahrefs.API_KEY", "")
    monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)

    assert main(["geo-top5", "--accepted-only"]) == 0
    assert "Докупать некому" in capsys.readouterr().out


def test_limit_must_be_a_positive_number(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["geo-top5", "--limit", "0"])

    assert stopped.value.code == 2
    assert "нужно целое число от 1" in capsys.readouterr().err
