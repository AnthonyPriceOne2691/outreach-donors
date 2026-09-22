"""Гейт отбора: кого не берём в прогон и чем это отличается от свежести.

Проверяется на настоящей базе — гейт целиком состоит из запросов, и
на подделке проверялась бы подделка.

Здесь проверяется сам гейт и его место в смете. Второй угол зрения —
**маршрут, а не результат**: что исключённый домен не доходит до
провайдера вовсе. Он живёт в `test_execute_run.py`, рядом с заглушкой
Ahrefs, которая умеет считать вызовы: при перестановке ступеней список
доноров не изменится, а счёт вырастет, и увидеть это можно только так.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import (
    MessageStatus,
    Stage,
    SuppressionReason,
    ThreadStatus,
)
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import stoplist
from backend.features.replies.repository import ReplyRepository
from backend.features.runs.exclusions import ExclusionReason, Exclusions
from backend.features.runs.planning import Candidates, plan_run
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


async def _wrote_to(
    session: AsyncSession,
    host: str,
    *,
    sent_at: datetime,
    status: MessageStatus = MessageStatus.SENT,
    thread_status: ThreadStatus = ThreadStatus.OPEN,
    stage: Stage = Stage.DONORS,
) -> DomainModel:
    """Донор, которому ушло письмо. Ответ задаётся состоянием диалога."""
    domain = await make_donor(session, host, email=f"editor@{host}")
    campaign = CampaignModel(stage=stage, name="Проверка", status="running")
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, status=thread_status)
    session.add(thread)
    await session.flush()
    contact = (
        (await session.execute(select(ContactModel).where(ContactModel.domain_id == domain.id)))
        .scalars()
        .one()
    )
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            thread_id=thread.id,
            domain_id=domain.id,
            contact_id=contact.id,
            step=0,
            status=status,
            sent_at=sent_at,
            subject="Hi",
            body="Hi",
            idempotency_key=f"donors:{host}:0",
        )
    )
    await session.flush()
    return domain


class TestWhoDoesNotEnterTheRun:
    async def test_the_stop_list_cuts_before_the_first_unit(self, session: AsyncSession) -> None:
        """Главное обещание среза. До него домен из стоп-листа проходил
        просев, метрики и страны и отсеивался только при сборке письма."""
        domain = await make_donor(session, "gone.example.test")
        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.UNSUBSCRIBED))
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["gone.example.test"], now=NOW)

        assert found == {"gone.example.test": ExclusionReason.STOPLIST}

    async def test_the_supplier_is_named_a_supplier(self, session: AsyncSession) -> None:
        """Причина у поставщика своя, хотя лежать он может в обеих таблицах.
        Оператору «в стоп-листе» и «наш поставщик» объясняют разное."""
        session.add(SupplierDonorModel(host="partner.example.test", note="текущий партнёр"))
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["partner.example.test"], now=NOW)

        assert found == {"partner.example.test": ExclusionReason.SUPPLIER}

    async def test_a_supplier_row_in_the_stop_list_keeps_its_name(
        self, session: AsyncSession
    ) -> None:
        domain = await make_donor(session, "vendor.example.test")
        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.SUPPLIER))
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["vendor.example.test"], now=NOW)

        assert found == {"vendor.example.test": ExclusionReason.SUPPLIER}

    async def test_silence_holds_for_a_year(self, session: AsyncSession) -> None:
        """Требование говорит только сроками годности данных, а молчание
        в ответ не покрывает: по одной свежести донор вернулся бы на 91-й
        день, и мы заплатили бы за метрики ради четвёртого письма."""
        await _wrote_to(session, "quiet.example.test", sent_at=NOW - timedelta(days=200))

        found = await Exclusions(session).excluded_hosts(["quiet.example.test"], now=NOW)

        assert found == {"quiet.example.test": ExclusionReason.SILENT}

    async def test_silence_runs_out(self, session: AsyncSession) -> None:
        await _wrote_to(session, "old.example.test", sent_at=NOW - timedelta(days=400))

        found = await Exclusions(session).excluded_hosts(["old.example.test"], now=NOW)

        assert found == {}

    async def test_the_one_who_answered_is_not_silent(self, session: AsyncSession) -> None:
        """Ответивший живёт по свежести цены: через 150 дней требование
        велит запросить её снова, то есть написать ему второй раз."""
        await _wrote_to(
            session,
            "talker.example.test",
            sent_at=NOW - timedelta(days=10),
            thread_status=ThreadStatus.REPLIED,
        )

        found = await Exclusions(session).excluded_hosts(["talker.example.test"], now=NOW)

        assert found == {}

    async def test_silence_on_another_stage_does_not_count(self, session: AsyncSession) -> None:
        """Этапы спрашивают разное: у донора цену, у рекламодателя
        размещение. То же правило, по которому считается «кому мы ещё
        не писали» при сборке очереди."""
        await _wrote_to(
            session,
            "other.example.test",
            sent_at=NOW - timedelta(days=30),
            stage=Stage.ADVERTISERS,
        )

        found = await Exclusions(session).excluded_hosts(
            ["other.example.test"], stage=Stage.DONORS, now=NOW
        )

        assert found == {}

    async def test_a_bounce_is_not_a_letter(self, session: AsyncSession) -> None:
        """До адресата мы не добрались. Считать это «мы ему уже писали»
        значит похоронить донора из-за мёртвого ящика."""
        await _wrote_to(
            session,
            "bounced.example.test",
            sent_at=NOW - timedelta(days=10),
            status=MessageStatus.BOUNCED,
        )

        found = await Exclusions(session).excluded_hosts(["bounced.example.test"], now=NOW)

        assert found == {}

    async def test_the_strongest_reason_wins(self, session: AsyncSession) -> None:
        """Совпасть могут все три. Донор, который отписался и молчит,
        объясняться молчанием не должен."""
        domain = await _wrote_to(session, "both.example.test", sent_at=NOW - timedelta(days=30))
        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.COMPLAINED))
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["both.example.test"], now=NOW)

        assert found == {"both.example.test": ExclusionReason.STOPLIST}

    async def test_an_address_does_not_close_the_whole_site(self, session: AsyncSession) -> None:
        """У сайта несколько адресов, и отказ секретаря — не отказ редакции.
        При сборке письма такая запись сработает: там адресат известен."""
        await make_donor(session, "site.example.test", email="info@site.example.test")
        session.add(
            SuppressionModel(email="info@site.example.test", reason=SuppressionReason.UNSUBSCRIBED)
        )
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["site.example.test"], now=NOW)

        assert found == {}

    async def test_the_other_stage_does_not_cut_this_one(self, session: AsyncSession) -> None:
        domain = await make_donor(session, "adv.example.test")
        session.add(
            SuppressionModel(
                domain_id=domain.id,
                reason=SuppressionReason.MANUAL,
                stage=Stage.ADVERTISERS,
            )
        )
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["adv.example.test"], now=NOW)

        assert found == {}


class TestTheTermOnARecord:
    async def test_an_expired_record_holds_nobody(self, session: AsyncSession) -> None:
        """«Размещались за последние 12 месяцев» — окно, а не приговор."""
        domain = await make_donor(session, "past.example.test")
        session.add(
            SuppressionModel(
                domain_id=domain.id,
                reason=SuppressionReason.SUPPLIER,
                expires_at=NOW - timedelta(days=1),
            )
        )
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["past.example.test"], now=NOW)

        assert found == {}

    async def test_a_living_term_still_holds(self, session: AsyncSession) -> None:
        domain = await make_donor(session, "still.example.test")
        session.add(
            SuppressionModel(
                domain_id=domain.id,
                reason=SuppressionReason.MANUAL,
                expires_at=NOW + timedelta(days=1),
            )
        )
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["still.example.test"], now=NOW)

        assert found == {"still.example.test": ExclusionReason.STOPLIST}

    async def test_an_expired_supplier_comes_back(self, session: AsyncSession) -> None:
        session.add(
            SupplierDonorModel(
                host="was.example.test",
                note="размещались в прошлом году",
                expires_at=NOW - timedelta(days=2),
            )
        )
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["was.example.test"], now=NOW)

        assert found == {}


class _Freshness:
    """Источник свежести, запоминающий, о чём его спросили."""

    def __init__(self, fresh: set[str]) -> None:
        self._fresh = fresh
        self.asked: list[str] = []

    async def fresh_hosts(self, hosts: Sequence[str]) -> set[str]:
        self.asked = list(hosts)
        return {h for h in hosts if h in self._fresh}


def _candidates(hosts: list[str]) -> Candidates:
    return Candidates(hosts=hosts, keywords=1, results=len(hosts), empty_keywords=[], dropped=0)


class TestTheGateStandsBeforeTheEstimate:
    async def test_the_excluded_are_not_in_the_bill(self, session: AsyncSession) -> None:
        domain = await make_donor(session, "no.example.test")
        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.MANUAL))
        await session.flush()
        freshness = _Freshness(set())

        plan = await plan_run(
            _candidates(["no.example.test", "yes.example.test"]),
            freshness,
            units_left=1_000_000,
            exclusions=Exclusions(session),
        )

        assert plan.new == ["yes.example.test"]
        assert plan.excluded == {"no.example.test": ExclusionReason.STOPLIST}
        assert plan.estimate.domains == 1
        assert plan.savings_from_gate > 0

    async def test_freshness_is_not_asked_about_the_excluded(self, session: AsyncSession) -> None:
        """Гейт бесплатен, и его ответ окончателен: спрашивать свежесть
        у домена, которому мы всё равно не напишем, незачем."""
        domain = await make_donor(session, "skip.example.test")
        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.MANUAL))
        await session.flush()
        freshness = _Freshness(set())

        await plan_run(
            _candidates(["skip.example.test", "take.example.test"]),
            freshness,
            units_left=1_000_000,
            exclusions=Exclusions(session),
        )

        assert freshness.asked == ["take.example.test"]

    async def test_the_two_savings_are_counted_apart(self, session: AsyncSession) -> None:
        """Сложив их, мы получили бы одно число «сэкономлено» и потеряли
        единственный способ увидеть, что гейт работает."""
        domain = await make_donor(session, "cut.example.test")
        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.MANUAL))
        await session.flush()

        plan = await plan_run(
            _candidates(["cut.example.test", "fresh.example.test", "new.example.test"]),
            _Freshness({"fresh.example.test"}),
            units_left=1_000_000,
            exclusions=Exclusions(session),
        )

        assert plan.considered == 2
        assert plan.savings_from_cache > 0
        assert plan.savings_from_gate > 0
        assert plan.excluded_by_reason == {"в стоп-листе": 1}

    async def test_without_a_gate_nothing_changes(self, session: AsyncSession) -> None:
        """Источник исключений необязателен — это уступка тестам, а не
        режим работы: оба боевых вызова передают его всегда."""
        plan = await plan_run(
            _candidates(["a.example.test"]), _Freshness(set()), units_left=1_000_000
        )

        assert plan.excluded == {}
        assert plan.new == ["a.example.test"]


class TestNobodyGivesATermToSomeoneElsesDecision:
    async def test_an_unsubscribe_is_recorded_forever(self, session: AsyncSession) -> None:
        """Срок появляется только у наших записей. Отписку заводит приём
        ответов, и поля срока он не заполняет — это единственная гарантия,
        что «больше не пишите» не протухнет само."""
        await make_donor(session, "bye.example.test", email="editor@bye.example.test")
        await ReplyRepository(session).suppress("editor@bye.example.test")
        await session.flush()

        row = (
            (
                await session.execute(
                    select(SuppressionModel).where(
                        SuppressionModel.email == "editor@bye.example.test"
                    )
                )
            )
            .scalars()
            .one()
        )
        assert row.expires_at is None
        assert row.reason is SuppressionReason.UNSUBSCRIBED

    async def test_a_term_in_the_past_is_refused(self, session: AsyncSession) -> None:
        """Запись, которая ничего не держит, выглядит как защита."""
        with pytest.raises(stoplist.StopListError, match="уже прошёл"):
            await stoplist.add(
                session,
                "late.example.test",
                reason=SuppressionReason.MANUAL,
                expires_at=datetime.now(UTC) - timedelta(days=1),
                author="tester",
            )
