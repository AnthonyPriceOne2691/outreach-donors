"""Стоп-лист поставщиков («стоп-лист доноров» требования): одно правило для экрана
и консоли, со сроком.

До сверки ТЗ 11.10.2026 было три расхождения, и каждое видно только на базе:
обход брал и истёкшие записи консоли, импорт консолью не ставил срока вовсе,
а поставщик с экрана «Стоп-лист» не мешал ни обходу, ни переводу — держал
только письма ему самому.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.cli import advertisers as cli
from backend.features.core.domain import (
    AuditAction,
    CrawlOutcome,
    MessageStatus,
    Stage,
    StopReason,
    SuppressionReason,
    Verdict,
)
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel, SupplierDonorModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.crawl import supplier_import, suppliers
from backend.features.crawl.promote import promote
from backend.features.crawl.targets import choose
from backend.features.letters import stoplist
from backend.features.runs.exclusions import ExclusionReason, Exclusions
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

NOW = datetime(2026, 10, 11, 21, 30, tzinfo=UTC)
YEAR_AGO = NOW - timedelta(days=366)


async def _domain(session: AsyncSession, host: str) -> DomainModel:
    found = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    if found is not None:
        return found
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    return domain


async def _marked(
    session: AsyncSession,
    host: str,
    *,
    reason: SuppressionReason = SuppressionReason.SUPPLIER,
    stage: Stage | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Запись экрана «Стоп-лист» по домену."""
    domain = await _domain(session, host)
    session.add(
        SuppressionModel(domain_id=domain.id, reason=reason, stage=stage, expires_at=expires_at)
    )
    await session.flush()


class TestOneRule:
    async def test_both_paths_count_and_only_while_in_force(self, session: AsyncSession) -> None:
        session.add(SupplierDonorModel(host="listed.example.test"))
        session.add(SupplierDonorModel(host="listed-old.example.test", expires_at=YEAR_AGO))
        await _marked(session, "screen.example.test")
        await _marked(session, "screen-stage2.example.test", stage=Stage.ADVERTISERS)
        await _marked(session, "screen-donors-only.example.test", stage=Stage.DONORS)
        await _marked(session, "screen-old.example.test", expires_at=YEAR_AGO)
        await _marked(session, "manual.example.test", reason=SuppressionReason.MANUAL)
        session.add(
            SuppressionModel(email="ads@mail.example.test", reason=SuppressionReason.SUPPLIER)
        )
        await session.flush()

        found = await suppliers.supplier_hosts(session, now=NOW)

        assert found == {
            "listed.example.test",
            "screen.example.test",
            "screen-stage2.example.test",
        }


async def _priced(session: AsyncSession, host: str) -> None:
    domain = await make_donor(session, host)
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    donor.last_price = Decimal("120")
    donor.last_price_at = NOW - timedelta(days=5)
    await session.flush()


class TestCrawlTargets:
    async def test_a_screen_supplier_is_not_crawled(self, session: AsyncSession) -> None:
        await _priced(session, "partner.example.test")
        await _priced(session, "free.example.test")
        await _marked(session, "partner.example.test", expires_at=NOW + timedelta(days=90))

        targets = await choose(session, now=NOW)

        assert targets.hosts == ["free.example.test"]
        assert targets.supplier == ["partner.example.test"]

    async def test_an_expired_listed_supplier_is_crawled_again(self, session: AsyncSession) -> None:
        """Окно «размещались за 12 мес.» съехало — донор снова наш источник."""
        await _priced(session, "former.example.test")
        session.add(SupplierDonorModel(host="former.example.test", expires_at=YEAR_AGO))
        await session.flush()

        targets = await choose(session, now=NOW)

        assert targets.hosts == ["former.example.test"]
        assert targets.supplier == []


async def _bought_on(session: AsyncSession, donor: str, target: str) -> None:
    run = CrawlRunModel(
        host=donor,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages_opened=3,
        articles=3,
    )
    session.add(run)
    await session.flush()
    session.add(
        CandidateModel(
            crawl_run_id=run.id,
            donor_host=donor,
            target_root=target,
            points=6,
            verdict=Verdict.BOUGHT,
            reasons=["rel=sponsored +5"],
            links=1,
            pages=1,
            best_page_url=f"https://{donor}/post",
            best_anchor="Acme",
        )
    )
    await session.flush()


async def _advertisers(session: AsyncSession) -> list[str]:
    rows = await session.scalars(
        select(DomainModel.host).join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
    )
    return sorted(rows.all())


class TestPromotion:
    async def test_advertisers_of_a_screen_supplier_are_not_promoted(
        self, session: AsyncSession
    ) -> None:
        await _bought_on(session, "partner.example.test", "client.example.test")
        await _marked(session, "partner.example.test")

        report = await promote(session)

        assert await _advertisers(session) == []
        assert report.skipped_supplier == 1

    async def test_an_expired_screen_supplier_no_longer_holds(self, session: AsyncSession) -> None:
        await _bought_on(session, "partner.example.test", "client.example.test")
        await _marked(session, "partner.example.test", expires_at=YEAR_AGO)

        await promote(session)

        assert await _advertisers(session) == ["client.example.test"]


async def _supplier_rows(session: AsyncSession) -> dict[str, SuppressionModel]:
    """Записи стоп-листа с причиной «поставщик» — по домену."""
    rows = await session.execute(
        select(DomainModel.host, SuppressionModel)
        .join(SuppressionModel, SuppressionModel.domain_id == DomainModel.id)
        .where(SuppressionModel.reason == SuppressionReason.SUPPLIER)
        .order_by(SuppressionModel.id)
    )
    return dict(rows.tuples().all())


async def _queued_letter(session: AsyncSession, host: str) -> MessageModel:
    """Письмо донору в очереди — как собранное сборкой."""
    domain = await _domain(session, host)
    campaign = CampaignModel(name="Доноры", stage=Stage.DONORS, status="draft")
    session.add(campaign)
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain.id,
        step=0,
        status=MessageStatus.QUEUED,
        subject="Guest article",
        body="Hi",
        idempotency_key=f"donors:{host}:0",
    )
    session.add(letter)
    await session.flush()
    return letter


class TestImport:
    """Консоль вносит поставщиков в стоп-лист экрана (решение 12.10.2026): видны
    на «Стоп-листе», держат письма им самим, отсеиваются на обоих этапах."""

    async def test_the_list_lands_in_the_screen_stop_list(self, session: AsyncSession) -> None:
        moment = datetime.now(UTC)

        placed = await supplier_import.import_hosts(
            session, ["www.Placed.example.test # март"], by="op@site.example.test", now=moment
        )
        partners = await supplier_import.import_hosts(
            session, ["partner.example.test # партнёр"], by="op@site.example.test", forever=True
        )

        rows = await _supplier_rows(session)
        assert (placed.added, partners.added) == (1, 1)
        assert rows["placed.example.test"].expires_at == suppliers.term_end(moment)
        assert rows["placed.example.test"].stage is None, "держит на обоих этапах"
        assert rows["placed.example.test"].created_by == "op@site.example.test"
        assert rows["partner.example.test"].expires_at is None
        shown = {row.host for row in await stoplist.rows(session, stages=list(Stage))}
        assert {"placed.example.test", "partner.example.test"} <= shown
        assert await session.scalar(select(func.count(SupplierDonorModel.id))) == 0
        journal = (
            await session.scalars(
                select(AuditLogModel.details).where(
                    AuditLogModel.action == AuditAction.SUPPRESSION_ADDED
                )
            )
        ).all()
        assert any(
            entry["почему в списке"] == "март" and entry["откуда"] == supplier_import.SOURCE
            for entry in journal
            if entry is not None
        )

    async def test_a_supplier_is_cut_on_both_stages_and_not_written_to(
        self, session: AsyncSession
    ) -> None:
        moment = datetime.now(UTC)
        letter = await _queued_letter(session, "partner.example.test")

        report = await supplier_import.import_hosts(
            session, ["partner.example.test"], by="op", now=moment
        )
        stage_one = await Exclusions(session).excluded_hosts(
            ["partner.example.test"], stage=Stage.DONORS, now=moment
        )

        assert report.stopped == 1
        await session.refresh(letter)
        assert letter.status is MessageStatus.STOPPED, "письмо самому поставщику снято"
        assert stage_one == {"partner.example.test": ExclusionReason.SUPPLIER}
        assert "partner.example.test" in await suppliers.supplier_hosts(session, now=moment)

    async def test_term_is_a_setting(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("backend.config.advertisers.SUPPLIER_TERM_MONTHS", 6)
        moment = datetime.now(UTC)

        await supplier_import.import_hosts(session, ["six.example.test"], by="op", now=moment)

        row = (await _supplier_rows(session))["six.example.test"]
        assert row.expires_at == suppliers.term_end(moment, months=6)

    async def test_reimport_extends_but_never_shortens(self, session: AsyncSession) -> None:
        moment = datetime.now(UTC)
        await _marked(session, "short.example.test", expires_at=moment + timedelta(days=30))
        await _marked(session, "partner.example.test")

        report = await supplier_import.import_hosts(
            session,
            ["short.example.test", "partner.example.test", "short.example.test"],
            by="op",
            now=moment,
        )

        rows = await _supplier_rows(session)
        assert (report.added, report.renewed, report.kept) == (0, 1, 1)
        assert rows["short.example.test"].expires_at == suppliers.term_end(moment)
        assert rows["partner.example.test"].expires_at is None

    async def test_an_expired_record_stays_history(self, session: AsyncSession) -> None:
        """Истёкшая запись не продлевается — рядом заводится новая, как на экране."""
        moment = datetime.now(UTC)
        await _marked(session, "lapsed.example.test", expires_at=moment - timedelta(days=1))

        report = await supplier_import.import_hosts(
            session, ["lapsed.example.test"], by="op", now=moment
        )

        records = (
            await session.scalars(
                select(SuppressionModel).where(
                    SuppressionModel.reason == SuppressionReason.SUPPLIER
                )
            )
        ).all()
        assert report.added == 1
        assert len(records) == 2

    async def test_a_domain_stopped_for_another_reason_is_marked_in_the_old_list(
        self, session: AsyncSession
    ) -> None:
        """Отписавшемуся и так не пишут, а второй записи стоп-лист не заводит:
        поставщиком он отмечается в прежнем списке консоли — и это сказано."""
        moment = datetime.now(UTC)
        await _marked(session, "gone.example.test", reason=SuppressionReason.UNSUBSCRIBED)

        report = await supplier_import.import_hosts(
            session, ["gone.example.test # партнёр"], by="op", now=moment
        )

        listed = await session.scalar(
            select(SupplierDonorModel).where(SupplierDonorModel.host == "gone.example.test")
        )
        assert report.elsewhere == {"gone.example.test": "unsubscribed"}
        assert report.extended == {}, "отписка бессрочна — продлевать нечего"
        assert listed is not None
        assert listed.expires_at == suppliers.term_end(moment)
        assert "gone.example.test" in await suppliers.supplier_hosts(session, now=moment)

    async def test_a_shorter_record_is_extended_to_the_supplier_term(
        self, session: AsyncSession
    ) -> None:
        """Ручная запись на месяц: когда она истечёт, поставщик всё равно должен
        держаться — её срок продлевается до срока поставщика, второй записи нет."""
        moment = datetime.now(UTC)
        await _marked(
            session,
            "short.example.test",
            reason=SuppressionReason.MANUAL,
            expires_at=moment + timedelta(days=30),
        )

        report = await supplier_import.import_hosts(
            session, ["short.example.test"], by="op", now=moment
        )

        records = (
            await session.scalars(
                select(SuppressionModel)
                .join(DomainModel, DomainModel.id == SuppressionModel.domain_id)
                .where(DomainModel.host == "short.example.test")
            )
        ).all()
        later = moment + timedelta(days=40)
        assert len(records) == 1, "вторую запись на адресата стоп-лист не заводит"
        assert records[0].reason is SuppressionReason.MANUAL
        assert records[0].expires_at == suppliers.term_end(moment)
        assert report.extended == {"short.example.test": f"{suppliers.term_end(moment):%d.%m.%Y}"}
        assert "short.example.test" in await suppliers.supplier_hosts(session, now=later)
        in_force = await session.scalar(
            select(SuppressionModel.id).where(
                SuppressionModel.id == records[0].id, SuppressionModel.in_force(later)
            )
        )
        assert in_force is not None, "через 40 дней письма поставщику по-прежнему держит"

    async def test_the_report_names_the_stages_of_stopped_letters(
        self, session: AsyncSession
    ) -> None:
        """Запись снимает письма адресату всех этапов, как у экрана, — отчёт их называет."""
        moment = datetime.now(UTC)
        await _queued_letter(session, "partner.example.test")
        domain = await _domain(session, "partner.example.test")
        sales = CampaignModel(name="Продажи", stage=Stage.SALES, status="draft")
        session.add(sales)
        await session.flush()
        session.add(
            MessageModel(
                campaign_id=sales.id,
                domain_id=domain.id,
                step=0,
                status=MessageStatus.QUEUED,
                subject="Hello",
                body="Hi",
                idempotency_key="sales:partner.example.test:0",
            )
        )
        await session.flush()

        report = await supplier_import.import_hosts(
            session, ["partner.example.test"], by="op", now=moment
        )

        assert report.stopped == 2
        assert dict(report.stopped_by_stage) == {"donors": 1, "sales": 1}

    async def test_lines_without_a_domain_are_named(self, session: AsyncSession) -> None:
        report = await supplier_import.import_hosts(
            session,
            ["# шапка", "", "not a domain # ошибка в списке", "https://www.OK.example.test/x"],
            by="op",
        )

        assert report.rejected == ["not a domain # ошибка в списке"]
        assert report.added == 1
        assert report.new_domains == ["ok.example.test"]
        assert "ok.example.test" in await _supplier_rows(session)

    @pytest.mark.parametrize(
        ("moment", "months", "until"),
        [
            (datetime(2026, 1, 31, 15, 0, tzinfo=UTC), 1, datetime(2026, 2, 28, tzinfo=UTC)),
            (datetime(2027, 2, 28, 9, 0, tzinfo=UTC), 12, datetime(2028, 2, 28, tzinfo=UTC)),
            (datetime(2028, 2, 29, 9, 0, tzinfo=UTC), 12, datetime(2029, 2, 28, tzinfo=UTC)),
            (datetime(2026, 11, 30, 23, 59, tzinfo=UTC), 3, datetime(2027, 2, 28, tzinfo=UTC)),
        ],
    )
    def test_term_end_is_calendar_months(
        self, moment: datetime, months: int, until: datetime
    ) -> None:
        assert suppliers.term_end(moment, months=months) == until

    def test_the_console_names_everything_it_did(self, capsys: pytest.CaptureFixture[str]) -> None:
        report = supplier_import.SupplierImport(added=2, stopped=3)
        report.stopped_by_stage.update({"donors": 1, "sales": 2})
        report.new_domains.append("new.example.test")
        report.elsewhere["gone.example.test"] = "manual"
        report.extended["gone.example.test"] = "12.10.2027"
        report.rejected.append("not a domain")

        cli._print_import(report, forever=False, total=3)

        out = capsys.readouterr().out
        assert "Снято писем им из очереди и добивок: 3 (Этап 1 1, продажи 2)." in out
        assert "Новых доменов: 1" in out
        assert "gone.example.test: уже в стоп-листе («manual»)" in out
        assert "продлена до срока поставщика — 12.10.2027" in out
        assert "не домен — пропущено: 'not a domain'" in out
        assert "Поставщиков действует: 3." in out

    def test_console_knows_the_forever_flag(self) -> None:
        parser = argparse.ArgumentParser()
        cli.add_parser(parser.add_subparsers(dest="command"))

        plain = parser.parse_args(["suppliers-import", "list.txt", "--by", "op"])
        partners = parser.parse_args(["suppliers-import", "list.txt", "--by", "op", "--forever"])

        assert (plain.forever, partners.forever) == (False, True)
