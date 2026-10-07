"""Доля страны не выше 100%: трафик домена и трафик стран приходят разными
запросами и расходятся (боевой прогон: «Канада · 128%» на экране доноров).

Правило — `geo.share_base`: база доли — трафик домена или сумма стран, если
она больше. Проверяется в форме ответа провайдера (через поддельный транспорт
клиента), на обоих путях к доле — полной разбивке и верхней стране из пакета, —
и на записанных донорах: пересчёт без провайдера (`outreach geo-shares`).
"""

from __future__ import annotations

import argparse

import pytest
from backend.cli.geo_shares import run_geo_shares
from backend.cli.main import main
from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.collect import DomainResult
from backend.features.donors.geo import build_breakdown, check_geo, share_base
from backend.features.donors.geo_recount import apply_recount, plan_recount
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Metrics
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN
from tests.test_collect import GOOD, Fake, _run, _with_top
from tests.test_geo import ROWS, TOTAL

#: Трафик домена из пакетного анализа меньше, чем одна Канада из отчёта по
#: странам, — форма случая с экрана: доля Канады по-старому 12 800 / 10 000.
DOMAIN_TRAFFIC = 10_000
COUNTRIES: list[dict[str, object]] = [
    {"country": "CA", "org_traffic": 12_800},
    {"country": "us", "org_traffic": 900},
    {"country": "gb", "org_traffic": 300},
]


class TestShareBase:
    def test_domain_traffic_while_countries_fit_in_it(self) -> None:
        """Обычный случай не меняется: пять стран покрывают часть трафика,
        и база — весь трафик домена, а не их сумма."""
        assert share_base(TOTAL, (row["org_traffic"] for row in ROWS)) == TOTAL
        assert build_breakdown(ROWS, TOTAL)[0].share == pytest.approx(0.656, abs=0.001)

    def test_sum_of_countries_when_it_is_larger(self) -> None:
        assert share_base(DOMAIN_TRAFFIC, [12_800, 900, 300]) == 14_000

    def test_no_share_above_one_and_the_rule_still_holds(self) -> None:
        breakdown = build_breakdown(COUNTRIES, DOMAIN_TRAFFIC)

        assert [item.country for item in breakdown] == ["ca", "us", "gb"]
        assert breakdown[0].org_traffic == 12_800
        assert breakdown[0].share == pytest.approx(12_800 / 14_000)
        assert all(item.share <= 1 for item in breakdown)
        assert sum(item.share for item in breakdown) == pytest.approx(1.0)
        # Место в топе — по трафику, не по доле: решение то же, слова — без 128%.
        verdict = check_geo("ca", breakdown)
        assert verdict.passed
        assert verdict.reason == "CA на 1-м месте по трафику (91%)"
        assert not check_geo("de", breakdown).passed


class TestBothPathsToTheShare:
    async def test_paid_country_report_larger_than_the_domain(self) -> None:
        """Верхней страны в пакете нет — страны покупаются отчётом, и он
        насчитал Канаде больше, чем пакет — всему домену."""
        fake = Fake(
            {"maple.example.test": {**GOOD, "org_traffic": DOMAIN_TRAFFIC}},
            {"maple.example.test": COUNTRIES},
        )

        [result] = await _run(fake, ["maple.example.test"], country="ca")

        assert fake.by_country_hosts == ["maple.example.test"]
        assert result.status is DonorStatus.SUITABLE
        top = result.breakdown[0]
        assert (top.country, top.org_traffic) == ("ca", 12_800)
        assert top.share == pytest.approx(12_800 / 14_000)

    async def test_free_top_country_larger_than_the_domain(self) -> None:
        """Верхняя страна из пакета — та же беда без покупки: колонка страны
        и трафик домена в одной строке ответа, а числа расходятся."""
        row = _with_top({**GOOD, "org_traffic": DOMAIN_TRAFFIC}, "ca", 12_800)
        fake = Fake({"maple.example.test": row}, {})

        [result] = await _run(fake, ["maple.example.test"], country="ca")

        assert fake.by_country_hosts == [], "верхней страны хватило — платить не за что"
        assert result.geo is not None
        assert result.geo.partial
        assert result.breakdown[0].share == 1.0


def _old_rows(*pairs: tuple[str, int], total: int) -> list[dict[str, object]]:
    """Разбивка, как её записывал прежний код: доля от трафика домена всегда."""
    return [
        {"country": country, "org_traffic": traffic, "share": traffic / total}
        for country, traffic in pairs
    ]


async def _donor(
    session: AsyncSession,
    host: str,
    *,
    traffic: int | None,
    breakdown: list[dict[str, object]],
    top: float | None,
) -> DonorModel:
    """Донор, записанный до правки: разбивка и доли — как лежат в базе."""
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=DonorStatus.SUITABLE,
        org_traffic=traffic,
        geo=str(breakdown[0]["country"]),
        geo_breakdown=breakdown,
        geo_top_share=top,
    )
    session.add(donor)
    await session.flush()
    return donor


@pytest.fixture
async def recorded(session: AsyncSession) -> dict[str, DonorModel]:
    """Доноры базы во всех видах, в каких их найдёт пересчёт."""
    canada = _old_rows(("ca", 12_800), ("us", 900), total=DOMAIN_TRAFFIC)
    donors = {
        # Сумма стран больше трафика домена — доли по-старому выше 100%.
        "over": await _donor(
            session, "over.example.test", traffic=DOMAIN_TRAFFIC, breakdown=canada, top=1.28
        ),
        # Верхняя страна из пакета, одна строка — тот же случай.
        "partial": await _donor(
            session,
            "partial.example.test",
            traffic=9_000,
            breakdown=_old_rows(("ca", 12_800), total=9_000),
            top=12_800 / 9_000,
        ),
        # Разбивка есть, верхней доли нет — расхождение, которое тоже чинится.
        "no-top": await _donor(
            session,
            "no-top.example.test",
            traffic=DOMAIN_TRAFFIC,
            breakdown=_old_rows(("ca", 5_000), total=DOMAIN_TRAFFIC),
            top=None,
        ),
        # Пересчитать нечем — называются, а не пропускаются молча.
        "no-traffic": await _donor(
            session, "no-traffic.example.test", traffic=None, breakdown=canada, top=1.28
        ),
        "bad-row": await _donor(
            session,
            "bad-row.example.test",
            traffic=DOMAIN_TRAFFIC,
            breakdown=[{"country": "ca", "share": 0.5}],
            top=0.5,
        ),
    }
    # Записанные нынешним кодом — тем же путём, что пишет прогон: доли уже
    # по новому правилу, и пересчёт обязан их не трогать.
    breakdown = build_breakdown(ROWS, TOTAL)
    await DonorRepository(session).save_results(
        [
            DomainResult(
                "fresh.example.test",
                DonorStatus.SUITABLE,
                "",
                Metrics(dr=60, org_traffic=TOTAL),
                geo=check_geo("us", breakdown),
            ),
            # Отсеян по DR — разбивки нет вовсе, хранится JSON-значением null.
            DomainResult(
                "weak.example.test", DonorStatus.UNSUITABLE, "DR 5 ниже 20", Metrics(dr=5)
            ),
        ]
    )
    return donors


class TestRecount:
    async def test_plan_finds_the_old_shares_and_names_the_unreadable(
        self, session: AsyncSession, recorded: dict[str, DonorModel]
    ) -> None:
        plan = await plan_recount(session)

        assert plan.checked == 6, "отсеянный без разбивки в пересчёт не идёт"
        assert [fix.host for fix in plan.fixes] == [
            "over.example.test",
            "partial.example.test",
            "no-top.example.test",
        ]
        assert plan.unreadable == ["no-traffic.example.test", "bad-row.example.test"]
        over, partial, no_top = plan.fixes
        assert (over.before, over.after) == (1.28, pytest.approx(12_800 / 13_700))
        assert [row["share"] for row in over.breakdown] == [
            pytest.approx(12_800 / 13_700),
            pytest.approx(900 / 13_700),
        ]
        assert partial.after == 1.0
        assert (no_top.before, no_top.after) == (None, 0.5)

    async def test_apply_writes_only_shares_and_a_second_pass_finds_nothing(
        self, session: AsyncSession, recorded: dict[str, DonorModel]
    ) -> None:
        written = await apply_recount(session, await plan_recount(session))

        assert written == 3
        over = await session.get(DonorModel, recorded["over"].id)
        assert over is not None
        await session.refresh(over)
        assert over.geo_top_share == pytest.approx(12_800 / 13_700)
        assert over.geo_breakdown is not None
        assert [row["org_traffic"] for row in over.geo_breakdown] == [12_800, 900]
        assert all(row["share"] <= 1 for row in over.geo_breakdown)
        assert (over.status, over.geo, over.org_traffic) == (
            DonorStatus.SUITABLE,
            "ca",
            DOMAIN_TRAFFIC,
        )
        untouched = recorded["no-traffic"]
        await session.refresh(untouched)
        assert untouched.geo_top_share == 1.28

        again = await plan_recount(session)
        assert again.fixes == []

    async def test_a_donor_rewritten_after_the_plan_is_not_overwritten(
        self, session: AsyncSession, recorded: dict[str, DonorModel]
    ) -> None:
        """Прогон обновил донора между планом и записью — его доли новее плана."""
        plan = await plan_recount(session)
        await session.execute(
            update(DonorModel).where(DonorModel.id == recorded["over"].id).values(geo_top_share=0.6)
        )

        written = await apply_recount(session, plan)

        assert written == 2
        share = await session.scalar(
            select(DonorModel.geo_top_share).where(DonorModel.id == recorded["over"].id)
        )
        assert share == 0.6


class TestCommand:
    async def test_without_yes_it_only_shows(
        self,
        session: AsyncSession,
        recorded: dict[str, DonorModel],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code = await run_geo_shares(session, argparse.Namespace(yes=False))

        printed = capsys.readouterr().out
        assert code == 0
        assert "Доля изменится: 3" in printed
        assert "over.example.test: верхняя страна 128% → 93%" in printed
        assert "no-top.example.test: верхняя страна — → 50%" in printed
        assert "Не пересчитать" in printed
        assert "bad-row.example.test" in printed
        assert "в базе ничего не изменилось" in printed
        await session.refresh(recorded["over"])
        assert recorded["over"].geo_top_share == 1.28

    async def test_with_yes_it_writes(
        self,
        session: AsyncSession,
        recorded: dict[str, DonorModel],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code = await run_geo_shares(session, argparse.Namespace(yes=True))

        assert code == 0
        assert "Записано одной транзакцией: 3." in capsys.readouterr().out
        await session.refresh(recorded["partial"])
        assert recorded["partial"].geo_top_share == 1.0

    async def test_many_donors_are_counted_not_listed(
        self, session: AsyncSession, capsys: pytest.CaptureFixture[str]
    ) -> None:
        for number in range(22):
            await _donor(
                session,
                f"many-{number}.example.test",
                traffic=100,
                breakdown=_old_rows(("ca", 150), total=100),
                top=1.5,
            )

        await run_geo_shares(session, argparse.Namespace(yes=False))

        printed = capsys.readouterr().out
        assert "Доля изменится: 22" in printed
        assert "…и ещё 2" in printed
        assert "many-21.example.test" not in printed

    def test_named_in_the_console(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Команда собрана в консоли и без `--yes` только показывает."""
        monkeypatch.setattr("backend.config.storage.DSN", TEST_DSN)
        monkeypatch.setattr("backend.config.storage.REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)

        assert main(["geo-shares"]) == 0
        printed = capsys.readouterr().out
        assert "Доноров с разбивкой по странам:" in printed
        assert "в базе ничего не изменилось" in printed
