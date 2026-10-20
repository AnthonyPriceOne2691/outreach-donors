"""Строка вердикта прогона: запись, повтор, пересуд того, у кого строки нет, и ревизия.

Вердикт прогона пишет его очередь (`RunReview.verdicts`): прогон судит свои домены для
своей очереди. Прогон, у которого строк нет, — сделанный до 20.10.2026, — судится по
сохранённому, когда его спрашивают: так его очередь кладёт консоль, так решение человека
узнаёт вердикт прогона, где домен приняли.
"""

from __future__ import annotations

from typing import Any

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.core.domain import DonorStatus, RunStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel, RunVerdictModel
from backend.features.donors.geo import GeoRule
from backend.features.donors.rejudge import ONLY_DR
from backend.features.donors.verdict import Thresholds, big_site_reason
from backend.features.review.accepted import settle_accepted, settle_returned
from backend.features.review.candidates import Decision, RunReview
from backend.features.runs.refusals import Threshold
from backend.features.runs.repository import RunRepository
from backend.features.runs.verdicts import CHUNK, Judging, RunVerdict, RunVerdicts
from sqlalchemy import func, insert, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.donor_metrics import measured
from tests.migration_helpers import load_migration

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
REVISION = "c9c37509854f_run_verdicts.py"
#: Причина отказа по региону — словами вердикта, как её пишет замер прогона Канады.
NOT_IN_CA = "CA не входит в топ-5 и даёт меньше 20%"


async def _run(session: AsyncSession, country: str = "us") -> RunModel:
    repository = RunRepository(session)
    settings = await repository.create_settings(
        T, geo_top_n=5, geo_min_share=0.2, metrics_ttl_days=90, price_ttl_days=150, units_cap=1
    )
    return await repository.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=["crm"],
        country=country,
        status=RunStatus.DONE,
    )


async def _rows(session: AsyncSession, run: RunModel) -> dict[str, tuple[Any, ...]]:
    rows = await session.execute(
        select(
            DomainModel.host,
            RunVerdictModel.status,
            RunVerdictModel.cause,
            RunVerdictModel.reason,
            RunVerdictModel.measured,
        )
        .join(DomainModel, DomainModel.id == RunVerdictModel.domain_id)
        .where(RunVerdictModel.run_id == run.id)
    )
    return {host: tuple(rest) for host, *rest in rows.tuples().all()}


async def _donor(session: AsyncSession, domain: DomainModel) -> DonorModel:
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    await session.refresh(donor)
    return donor


class TestSave:
    async def test_cause_is_the_threshold_of_the_refusal(self, session: AsyncSession) -> None:
        run = await _run(session, "ca")
        for host in ("big.test", "far.test", "fit.test"):
            await make_donor(session, host, review=None)

        await RunVerdicts(session).save(
            run,
            {
                "big.test": RunVerdict(DonorStatus.UNSUITABLE, big_site_reason(80), measured=True),
                "far.test": RunVerdict(DonorStatus.UNSUITABLE, NOT_IN_CA),
                "fit.test": RunVerdict(DonorStatus.SUITABLE, "CA на 1-м месте по трафику (70%)"),
                "never-seen.test": RunVerdict(DonorStatus.SUITABLE, "без строки домена"),
            },
        )

        rows = await _rows(session, run)
        assert rows["big.test"][:2] == ("unsuitable", Threshold.BIG.value)
        assert rows["far.test"][:2] == ("unsuitable", Threshold.GEO.value)
        assert rows["fit.test"][:3] == ("suitable", None, "CA на 1-м месте по трафику (70%)")
        assert "never-seen.test" not in rows, "вердикт — только у домена, который есть в базе"

    async def test_a_measured_row_is_not_overwritten_by_a_rejudge(
        self, session: AsyncSession
    ) -> None:
        """Продолжение прогона застаёт купленное первой попыткой свежим. Пересуд по
        сохранённому купленный вердикт не перетирает: крупный сайт с DR 80,4 лежит в колонке
        числом 80 и пересудом стал бы «не проверен» (ревью 20.10.2026)."""
        run = await _run(session)
        await make_donor(session, "site.test", review=None)
        verdicts = RunVerdicts(session)
        big = RunVerdict(DonorStatus.UNSUITABLE, big_site_reason(80), measured=True)
        await verdicts.save(run, {"site.test": big})

        await verdicts.save(run, {"site.test": RunVerdict(DonorStatus.UNCHECKED, ONLY_DR)})

        assert (await _rows(session, run))["site.test"] == (
            "unsuitable",
            Threshold.BIG.value,
            big_site_reason(80),
            True,
        )
        assert await verdicts.save(run, {}) == 0

    async def test_a_new_measurement_rewrites_the_row(self, session: AsyncSession) -> None:
        run = await _run(session)
        await make_donor(session, "site.test", review=None)
        verdicts = RunVerdicts(session)
        await verdicts.save(run, {"site.test": RunVerdict(DonorStatus.UNCHECKED, ONLY_DR)})

        await verdicts.save(
            run, {"site.test": RunVerdict(DonorStatus.UNSUITABLE, "DR 9 ниже 20", measured=True)}
        )

        assert (await _rows(session, run))["site.test"] == (
            "unsuitable",
            "dr",
            "DR 9 ниже 20",
            True,
        )

    async def test_thousands_of_rows_go_in_chunks(self, session: AsyncSession) -> None:
        """6 000 вердиктов — шесть параметров на строку: одним запросом это 36 000 при
        потолке Postgres 32 767, и прогон падал после оплаченной выдачи (ревью 20.10.2026)."""
        run = await _run(session)
        hosts = [f"bulk-{number}.test" for number in range(6 * CHUNK)]
        await session.execute(insert(DomainModel), [{"host": host} for host in hosts])
        verdicts = RunVerdicts(session)
        weak = RunVerdict(DonorStatus.UNSUITABLE, "DR 9 ниже 20", measured=True)

        assert await verdicts.save(run, dict.fromkeys(hosts, weak)) == len(hosts)

        written = await session.scalar(
            select(func.count())
            .select_from(RunVerdictModel)
            .where(RunVerdictModel.run_id == run.id)
        )
        assert written == len(hosts)
        assert len(await verdicts.of(run.id, hosts)) == len(hosts)
        judging = Judging("us", T, GeoRule(top_n=5, min_share=0.2))
        assert await verdicts.rejudged(run, hosts, judging) == {}, "доноров нет — судить нечем"


class TestFreshOfTheRun:
    async def test_bought_by_this_run_keeps_the_measured_verdict(
        self, session: AsyncSession
    ) -> None:
        """Купленный этим прогоном домен продолжение судит не пересудом, а вердиктом замера;
        остальные свежие — пересудом, с записью."""
        run = await _run(session)
        await make_donor(session, "big.test", review=None)
        await measured(session, await make_donor(session, "fit.test", review=None))
        verdicts = RunVerdicts(session)
        big = RunVerdict(DonorStatus.UNSUITABLE, big_site_reason(80), measured=True)
        await verdicts.save(run, {"big.test": big})

        fresh = await verdicts.fresh(
            run, ["big.test", "fit.test"], Judging("us", T, GeoRule(top_n=5, min_share=0.2))
        )

        assert fresh["big.test"] == big
        assert fresh["fit.test"].suitable
        assert not fresh["fit.test"].measured
        assert (await _rows(session, run))["fit.test"][0] == "suitable"


class TestJudgedWhenAsked:
    async def test_a_run_without_rows_is_judged_by_stored_data_and_kept(
        self, session: AsyncSession
    ) -> None:
        """Прогон до 20.10.2026: строк нет — его домены судятся по сохранённому порогами
        его версии и его страной, и вынесенное записывается."""
        run = await _run(session, "gb")
        await measured(
            session, await make_donor(session, "us-site.test", review=None), country="us"
        )

        verdicts = await RunVerdicts(session).judged(run, ["us-site.test", "unknown.test"])

        assert verdicts["us-site.test"].status is DonorStatus.UNSUITABLE
        assert "unknown.test" not in verdicts
        assert (await _rows(session, run))["us-site.test"][0] == "unsuitable"

    async def test_asking_only_does_not_write(self, session: AsyncSession) -> None:
        run = await _run(session)
        await measured(session, await make_donor(session, "site.test", review=None))

        verdicts = await RunVerdicts(session).judged(run, ["site.test"], keep=False)

        assert verdicts["site.test"].suitable
        assert await _rows(session, run) == {}

    async def test_a_row_of_another_kind_is_not_a_verdict(self, session: AsyncSession) -> None:
        """Строку другого статуса пишет не вердикт порогов — её код вердиктом не читает и
        не падает на ней (так переживает откат кода ревизия, что пишет такие строки)."""
        run = await _run(session)
        domain = await make_donor(session, "site.test", review=None)
        session.add(RunVerdictModel(run_id=run.id, domain_id=domain.id, status="другое"))
        await session.flush()

        assert await RunVerdicts(session).of(run.id, ["site.test"]) == {}

    async def test_the_console_queue_of_an_old_run_uses_the_runs_own_verdict(
        self, session: AsyncSession
    ) -> None:
        """`outreach review-queue` кладёт очередь старого прогона: американский сайт в
        прогоне Великобритании не годен — и в очередь не идёт, хотя у домена «подходит»."""
        run = await _run(session, "gb")
        await measured(
            session, await make_donor(session, "us-site.test", review=None), country="us"
        )

        report = await RunReview(session).queue_run(run.id, ["us-site.test"])

        assert report.pending == 0
        assert await RunReview(session).queue_run(run.id, []) == type(report)()


class TestAcceptedTakesTheVerdictOfItsRun:
    async def test_accepting_does_not_spoil_a_donor_the_run_cannot_judge(
        self, session: AsyncSession
    ) -> None:
        """Заведённый руками и липовый донор метрик не имеет: прогон судить его нечем,
        и «годен» человека остаётся."""
        run = await _run(session)
        domain = await make_donor(session, "manual.test", review=None)

        assert await settle_accepted(session, run.id, [domain.id]) == 0

        assert (await _donor(session, domain)).status is DonorStatus.SUITABLE

    async def test_a_donor_fit_for_the_run_becomes_suitable(self, session: AsyncSession) -> None:
        run = await _run(session, "us")
        domain = await make_donor(session, "site.test", review=None)
        await measured(session, domain, country="us")
        donor = await _donor(session, domain)
        donor.status, donor.reject_reason = DonorStatus.UNSUITABLE, NOT_IN_CA
        await session.flush()

        assert await settle_accepted(session, run.id, [domain.id]) == 1
        assert await settle_accepted(session, run.id, []) == 0

        await session.refresh(donor)
        assert (donor.status, donor.reject_reason) == (DonorStatus.SUITABLE, None)


class TestUndoneDecisionReturnsTheMeasuredVerdict:
    """Приём поднял вердикт по прогону, где приняли; без решения подъёму не на чем стоять —
    донору снова вердикт его последнего замера (ревью 20.10.2026)."""

    async def _raised(self, session: AsyncSession) -> tuple[RunModel, RunReview, int, DonorModel]:
        """Замер Канады: «не подходит»; прогон США его предлагает, и человек принимает."""
        run = await _run(session, "us")
        domain = await make_donor(session, "site.test", review=None)
        await measured(session, domain, country="us")
        donor = await _donor(session, domain)
        donor.status, donor.reject_reason = DonorStatus.UNSUITABLE, NOT_IN_CA
        donor.measured_status, donor.measured_reason = DonorStatus.UNSUITABLE.value, NOT_IN_CA
        await session.flush()
        review = RunReview(session)
        await review.queue_run(run.id, ["site.test"])
        candidate = (await review.page(run.id, status=Decision.PENDING, show_doubtful=True)).rows[0]
        await review.decide(run.id, [candidate.candidate.id], Decision.ACCEPTED, by="op")
        await session.refresh(donor)
        assert donor.status is DonorStatus.SUITABLE
        return run, review, candidate.candidate.id, donor

    async def test_back_to_proposed_returns_the_measured_verdict(
        self, session: AsyncSession
    ) -> None:
        run, review, candidate, donor = await self._raised(session)

        await review.decide(run.id, [candidate], Decision.PENDING, by="op")

        await session.refresh(donor)
        assert (donor.status, donor.reject_reason, donor.review) == (
            DonorStatus.UNSUITABLE,
            NOT_IN_CA,
            None,
        )

    async def test_rejecting_returns_the_measured_verdict(self, session: AsyncSession) -> None:
        run, review, candidate, donor = await self._raised(session)

        await review.decide(run.id, [candidate], Decision.REJECTED, by="op")

        await session.refresh(donor)
        assert (donor.status, donor.reject_reason) == (DonorStatus.UNSUITABLE, NOT_IN_CA)

    async def test_without_a_measurement_there_is_nothing_to_return(
        self, session: AsyncSession
    ) -> None:
        """Заведённый руками замера не имеет — его вердикт снятое решение не трогает."""
        domain = await make_donor(session, "manual.test", review=None)

        assert await settle_returned(session, [domain.id]) == 0
        assert await settle_returned(session, []) == 0
        assert (await _donor(session, domain)).status is DonorStatus.SUITABLE

    async def test_a_donor_still_accepted_keeps_its_verdict(self, session: AsyncSession) -> None:
        domain = await make_donor(session, "site.test")
        donor = await _donor(session, domain)
        donor.measured_status, donor.measured_reason = DonorStatus.UNSUITABLE.value, NOT_IN_CA
        await session.flush()

        assert await settle_returned(session, [domain.id]) == 0
        assert (await _donor(session, domain)).status is DonorStatus.SUITABLE


def _down_and_up(connection: Connection) -> tuple[bool, bool, set[str], tuple[Any, ...]]:
    """Вниз и вверх; после подъёма у замеренного донора вердикт замера — его `status`."""
    migration = load_migration(REVISION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = bool(connection.scalar(text("SELECT to_regclass('run_verdicts') IS NOT NULL")))
        connection.execute(
            text("UPDATE donors SET metrics_refreshed_at = now() WHERE dr = 41"),
        )
        migration.upgrade()
    up = bool(connection.scalar(text("SELECT to_regclass('run_verdicts') IS NOT NULL")))
    keys = connection.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = to_regclass('run_verdicts') AND contype = 'u'"
        )
    )
    filled = connection.execute(
        text("SELECT dr, measured_status FROM donors WHERE dr IN (41, 42) ORDER BY dr")
    )
    return down, up, set(keys.scalars()), tuple(map(tuple, filled.all()))


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    """Откат снимает таблицу и вердикт замера — прежний код их не читает; подъём строит
    таблицу с ключом пары и даёт замеренному донору вердикт замера, а не мерившемуся — нет."""
    await make_donor(session, "measured.test", dr=41, review=None)
    await make_donor(session, "manual.test", dr=42, review=None)
    await session.flush()
    connection = await session.connection()

    down, up, keys, filled = await connection.run_sync(_down_and_up)

    assert (down, up) == (False, True)
    assert keys == {"uq_run_verdicts_run_domain"}
    assert filled == ((41, "suitable"), (42, None))
