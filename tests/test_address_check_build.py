"""Проверка адреса перед первым письмом — на настоящей базе, через сборку очереди.

Проверяльщик подставной (сеть и Hunter не трогаются), модель — тоже: она запоминает, кому
писала письмо. Проверяется то, ради чего проверка стоит до модели: на несуществующий адрес
письмо не собирается и модель не зовётся, берётся следующий адрес домена; проверенный адрес
второй раз не спрашивается; сбой и потолок — письма нет и причина названа; рубильник
выключен — сборка та же, что до проверки.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.config import contacts as contacts_cfg
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.contacts import address_check, manual
from backend.features.contacts.preference import DEAD
from backend.features.contacts.provider import (
    ProviderDomainRefusedError,
    ProviderError,
    ProviderQuotaError,
)
from backend.features.contacts.verifier import StillCheckingError, Verdict
from backend.features.core import hunter_cap, usage
from backend.features.core.domain import ContactSource, MessageStatus, Stage, UsageProvider
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import address_gate, attempts, batch
from backend.features.letters.building import BuildReport, BuildRequest, QueueBuilder
from backend.features.letters.repository import LetterRepository
from backend.features.letters.rewrite import RewriteResult
from backend.features.letters.transport import NullTransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender
from tests.test_letters_advertisers import make_advertiser, priced_donor

SITE = "site.example.test"
VALID = Verdict("valid", 93)


class FakeVerifier:
    """Подставной проверяльщик: слово или отказ по адресу, остальным — «существует».
    Запоминает, кого спросили."""

    def __init__(self, answers: dict[str, Verdict | Exception] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[str] = []

    async def verify(self, email: str) -> Verdict:
        self.asked.append(email)
        answer = self.answers.get(email, VALID)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeRewriter:
    """Модель: запоминает, кому писала; `fail` — на этом сайте падает."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.fail = fail
        self.seen: list[str] = []

    async def rewrite(self, rendered: object, about: object) -> RewriteResult:
        host = getattr(about, "host", "")
        self.seen.append(host)
        if host == self.fail:
            raise RuntimeError(f"модель легла на {host}")
        return RewriteResult(zones={"greeting": "Good afternoon to you,"}, tokens_spent=50)


@pytest.fixture(autouse=True)
def _check_on(monkeypatch: pytest.MonkeyPatch, filled_legal: None) -> None:
    """Проверка включена у обоих этапов, как на бою; месячный потолок задан."""
    monkeypatch.setattr(contacts_cfg, "VERIFY_BEFORE_LETTER", {"donors": True, "advertisers": True})
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "1000")


async def _build(
    session: AsyncSession,
    verifier: FakeVerifier,
    rewriter: FakeRewriter | None = None,
    *,
    stage: Stage = Stage.DONORS,
) -> BuildReport:
    builder = QueueBuilder(session, rewriter or FakeRewriter(), verifier=verifier)  # type: ignore[arg-type]
    return await builder.build(BuildRequest(campaign_name="Проверка адресов", stage=stage))


async def _address(session: AsyncSession, email: str) -> ContactModel:
    found = await session.scalar(select(ContactModel).where(ContactModel.email == email))
    assert found is not None
    await session.refresh(found)
    return found


async def _add_address(
    session: AsyncSession,
    domain: DomainModel,
    email: str,
    *,
    source: ContactSource = ContactSource.PAGE,
    status: str | None = None,
    checked_days_ago: int | None = None,
) -> ContactModel:
    at = None if checked_days_ago is None else datetime.now(UTC) - timedelta(days=checked_days_ago)
    contact = ContactModel(
        domain_id=domain.id, email=email, source=source, verification_status=status, verified_at=at
    )
    session.add(contact)
    await session.flush()
    return contact


async def _letters_to(session: AsyncSession) -> list[str]:
    """Адреса писем очереди в порядке записи."""
    rows = await session.scalars(
        select(ContactModel.email)
        .join(MessageModel, MessageModel.contact_id == ContactModel.id)
        .order_by(MessageModel.id)
    )
    return list(rows)


async def _spent(session: AsyncSession) -> list[tuple[str, int | None, Stage | None]]:
    rows = await session.execute(
        select(UsageRecordModel.operation, UsageRecordModel.units, UsageRecordModel.stage).where(
            UsageRecordModel.provider == UsageProvider.HUNTER
        )
    )
    return [tuple(row) for row in rows.all()]  # type: ignore[misc]


class TestVerdicts:
    async def test_a_dead_address_gets_no_letter_and_the_next_one_is_taken(
        self, session: AsyncSession
    ) -> None:
        domain = await make_donor(session, SITE, email=f"first@{SITE}")
        await _add_address(session, domain, f"second@{SITE}")
        verifier = FakeVerifier({f"first@{SITE}": Verdict("invalid", 4)})
        rewriter = FakeRewriter()

        report = await _build(session, verifier, rewriter)

        assert verifier.asked == [f"first@{SITE}", f"second@{SITE}"]
        assert await _letters_to(session) == [f"second@{SITE}"]
        assert rewriter.seen == [SITE], "модель звали один раз — для живого адреса"
        dead = await _address(session, f"first@{SITE}")
        assert (dead.verification_status, dead.verification_score) == (DEAD, 0)
        assert dead.verified_at is not None
        assert report.prepared == 1
        assert report.address_check == {"не существует": 1, "существует": 1}
        assert report.unchecked is None

    async def test_no_address_left_no_model_call_and_the_site_is_counted(
        self, session: AsyncSession
    ) -> None:
        await make_donor(session, SITE, email=f"gone@{SITE}")
        rewriter = FakeRewriter()

        report = await _build(
            session, FakeVerifier({f"gone@{SITE}": Verdict("invalid", 2)}), rewriter
        )

        assert (report.prepared, rewriter.seen) == (0, [])
        assert await _letters_to(session) == []
        funnel = await LetterRepository(session).funnel_report(Stage.DONORS)
        assert funnel["ещё не писали"] == 0
        assert funnel["адрес не существует"] == 1

    async def test_a_disposable_address_is_buried_like_a_missing_one(
        self, session: AsyncSession
    ) -> None:
        await make_donor(session, SITE, email=f"temp@{SITE}")

        report = await _build(session, FakeVerifier({f"temp@{SITE}": Verdict("disposable", 9)}))

        assert report.prepared == 0
        assert (await _address(session, f"temp@{SITE}")).verification_status == DEAD

    async def test_an_existing_address_is_marked_scored_and_written(
        self, session: AsyncSession
    ) -> None:
        await make_donor(session, SITE, email=f"editor@{SITE}")

        report = await _build(session, FakeVerifier())

        contact = await _address(session, f"editor@{SITE}")
        assert (contact.verification_status, contact.verification_score) == ("valid", 93)
        assert contact.verified_at is not None
        assert await _letters_to(session) == [f"editor@{SITE}"]
        assert report.address_check == {"существует": 1}
        assert await _spent(session) == [("contacts_verify", 1, Stage.DONORS)]

    @pytest.mark.parametrize("word", ["accept_all", "unknown", "webmail"])
    async def test_an_address_that_cannot_be_checked_still_gets_the_letter(
        self, session: AsyncSession, word: str
    ) -> None:
        await make_donor(session, SITE, email=f"info@{SITE}")

        report = await _build(session, FakeVerifier({f"info@{SITE}": Verdict(word, 55)}))

        contact = await _address(session, f"info@{SITE}")
        assert (contact.verification_status, contact.verification_score) == (word, 55)
        assert await _letters_to(session) == [f"info@{SITE}"]
        assert report.address_check == {"проверить нельзя": 1}

    async def test_a_domain_the_service_refuses_cannot_be_checked_and_costs_nothing(
        self, session: AsyncSession
    ) -> None:
        await make_donor(session, SITE, email=f"info@{SITE}")
        refused = ProviderDomainRefusedError("провайдер не работает с доменом")

        report = await _build(session, FakeVerifier({f"info@{SITE}": refused}))

        assert await _letters_to(session) == [f"info@{SITE}"]
        assert report.address_check == {"проверить нельзя": 1}
        assert await _spent(session) == []


class TestNoCallNeeded:
    async def test_an_address_written_by_hand_is_not_asked(self, session: AsyncSession) -> None:
        domain = await make_donor(session, SITE)
        donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
        assert donor is not None
        await manual.add(session, donor, f"owner@{SITE}")
        verifier = FakeVerifier()

        report = await _build(session, verifier)

        assert verifier.asked == []
        assert await _letters_to(session) == [f"owner@{SITE}"]
        assert report.address_check == {"проверены раньше": 1}
        assert await _spent(session) == []

    @pytest.mark.parametrize(
        ("source", "status"),
        [
            (ContactSource.PROVIDER, "valid"),  # отметка платного поиска Hunter
            (ContactSource.PAGE, "valid"),  # проверен раньше этой же проверкой
            (ContactSource.PAGE, "accept_all"),  # «проверить нельзя» — второй раз не спрашиваем
            (ContactSource.MANUAL, None),  # с этого адреса ответили или его назвал автоответ
        ],
    )
    async def test_a_fresh_mark_is_enough(
        self, session: AsyncSession, source: ContactSource, status: str | None
    ) -> None:
        domain = await make_donor(session, SITE)
        await _add_address(
            session, domain, f"known@{SITE}", source=source, status=status, checked_days_ago=10
        )
        verifier = FakeVerifier()

        await _build(session, verifier)

        assert verifier.asked == []
        assert await _letters_to(session) == [f"known@{SITE}"]

    async def test_a_mark_older_than_the_term_is_checked_again(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(contacts_cfg, "VERIFY_TTL_DAYS", 90)
        domain = await make_donor(session, SITE)
        await _add_address(session, domain, f"old@{SITE}", status="valid", checked_days_ago=91)
        verifier = FakeVerifier()

        await _build(session, verifier)

        assert verifier.asked == [f"old@{SITE}"]

    async def test_a_repeated_build_does_not_ask_or_pay_twice(self, session: AsyncSession) -> None:
        """Сборка оборвалась после проверки (модель легла): повтор письмо собирает, а адрес
        второй раз не спрашивает и расход второй раз не пишет."""
        await make_donor(session, SITE, email=f"editor@{SITE}")
        verifier = FakeVerifier()

        with pytest.raises(RuntimeError, match="модель легла"):
            await _build(session, verifier, FakeRewriter(fail=SITE))
        assert await _letters_to(session) == []

        report = await _build(session, verifier)

        assert verifier.asked == [f"editor@{SITE}"], "второй раз адрес не спрашивали"
        assert await _letters_to(session) == [f"editor@{SITE}"]
        assert report.address_check == {"проверены раньше": 1}
        assert await _spent(session) == [("contacts_verify", 1, Stage.DONORS)]

    async def test_a_crash_inside_the_pack_keeps_the_verdicts_already_paid(
        self, session: AsyncSession
    ) -> None:
        """Пачка оборвалась на втором адресе: вердикт первого уже в базе, и повтор спрашивает
        только того, по кому ответа не было."""
        await make_donor(session, "a.example.test", email="a@a.example.test", dr=70)
        await make_donor(session, "b.example.test", email="b@b.example.test", dr=60)
        broken = FakeVerifier({"b@b.example.test": RuntimeError("воркер упал")})

        with pytest.raises(RuntimeError, match="воркер упал"):
            await _build(session, broken)

        assert (await _address(session, "a@a.example.test")).verification_status == "valid"
        again = FakeVerifier()
        await _build(session, again)
        assert again.asked == ["b@b.example.test"]
        assert sorted(await _letters_to(session)) == ["a@a.example.test", "b@b.example.test"]
        assert [units for _, units, _ in await _spent(session)] == [1, 1]


class TestCheckDidNotHappen:
    async def test_a_failed_check_means_no_letter_and_says_why(self, session: AsyncSession) -> None:
        await make_donor(session, "a.example.test", email="a@a.example.test", dr=70)
        await make_donor(session, "b.example.test", email="b@b.example.test", dr=60)
        down = ProviderError("провайдер не ответил (HTTP 503)")
        rewriter = FakeRewriter()

        report = await _build(session, FakeVerifier({"a@a.example.test": down}), rewriter)

        assert await _letters_to(session) == ["b@b.example.test"]
        assert rewriter.seen == ["b.example.test"]
        assert report.unchecked == "провайдер не ответил (HTTP 503)"
        assert report.address_check == {"не проверено": 1, "существует": 1}
        assert (await _address(session, "a@a.example.test")).verification_status is None
        assert await _spent(session) == [("contacts_verify", 1, Stage.DONORS)], "отказ не стоит"

    async def test_still_checking_waits_for_the_next_build(self, session: AsyncSession) -> None:
        await make_donor(session, SITE, email=f"slow@{SITE}")
        waiting = StillCheckingError("провайдер ещё проверяет адрес")
        verifier = FakeVerifier({f"slow@{SITE}": waiting})

        report = await _build(session, verifier)
        assert (report.prepared, report.unchecked) == (0, "провайдер ещё проверяет адрес")

        verifier.answers.clear()
        assert (await _build(session, verifier)).prepared == 1

    async def test_a_quota_refusal_stops_the_check_but_marked_addresses_still_go(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "BUILD_CONCURRENCY", 1)
        await make_donor(session, "a.example.test", email="a@a.example.test", dr=70)
        await make_donor(session, "b.example.test", email="b@b.example.test", dr=60)
        known = await make_donor(session, "c.example.test", dr=50)
        await _add_address(session, known, "c@c.example.test", status="valid", checked_days_ago=1)
        verifier = FakeVerifier({"a@a.example.test": ProviderQuotaError("квота исчерпана: 0")})

        report = await _build(session, verifier)

        assert verifier.asked == ["a@a.example.test"], "после отказа квоты следующих не спрашивают"
        assert await _letters_to(session) == ["c@c.example.test"]
        assert report.unchecked == "квота исчерпана: 0"
        assert report.address_check == {"не проверено": 2, "проверены раньше": 1}
        assert await _spent(session) == []

    async def test_the_month_cap_is_shared_with_sales_and_holds(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Потолок 3, продажи выбрали 2: проверяется один адрес, второму — причина словами."""
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "3")
        usage.record(session, operation="sales_verify", units=2)
        await make_donor(session, "a.example.test", email="a@a.example.test", dr=70)
        await make_donor(session, "b.example.test", email="b@b.example.test", dr=60)
        verifier = FakeVerifier()

        report = await _build(session, verifier)

        assert verifier.asked == ["a@a.example.test"]
        assert await _letters_to(session) == ["a@a.example.test"]
        assert report.unchecked == address_check.reached(3)
        assert await address_check.month_spent(session) == 3

        again = FakeVerifier()
        assert (await _build(session, again)).prepared == 0
        assert again.asked == [], "потолок выбран — никого не спрашиваем"

    @pytest.mark.parametrize(
        ("cap", "said"),
        [("", hunter_cap.NOT_SET), ("0", address_check.STOPPED_BY_HAND)],
    )
    async def test_without_a_cap_nobody_is_asked_and_no_letter_is_built(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, cap: str, said: str
    ) -> None:
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", cap)
        await make_donor(session, SITE, email=f"editor@{SITE}")
        verifier = FakeVerifier()
        rewriter = FakeRewriter()

        report = await _build(session, verifier, rewriter)

        assert (verifier.asked, rewriter.seen) == ([], [])
        assert (report.prepared, report.unchecked) == (0, said)


class TestSwitchAndStages:
    async def test_switched_off_the_build_is_what_it_was(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            contacts_cfg, "VERIFY_BEFORE_LETTER", {"donors": False, "advertisers": True}
        )
        await make_donor(session, SITE, email=f"editor@{SITE}")
        verifier = FakeVerifier()

        report = await _build(session, verifier)

        assert verifier.asked == []
        assert await _letters_to(session) == [f"editor@{SITE}"]
        assert (report.address_check, report.unchecked) == ({}, None)
        assert (await _address(session, f"editor@{SITE}")).verification_status is None
        funnel = await LetterRepository(session).funnel_report(Stage.DONORS)
        assert not {"адрес проверен", "ждут проверки адреса"} & set(funnel)

    async def test_advertisers_are_checked_and_the_spend_is_theirs(
        self, session: AsyncSession
    ) -> None:
        await priced_donor(session)
        await make_advertiser(session, "shop.example.test", email="dead@shop.example.test")
        await make_advertiser(session, "store.example.test")
        verifier = FakeVerifier({"dead@shop.example.test": Verdict("invalid", 1)})
        rewriter = FakeRewriter()

        report = await _build(session, verifier, rewriter, stage=Stage.ADVERTISERS)

        assert await _letters_to(session) == ["marketing@store.example.test"]
        assert rewriter.seen == ["store.example.test"]
        assert report.address_check == {"не существует": 1, "существует": 1}
        assert {stage for _, _, stage in await _spent(session)} == {Stage.ADVERTISERS}
        funnel = await LetterRepository(session).funnel_report(Stage.ADVERTISERS)
        assert funnel["адрес не существует"] == 1

    async def test_the_build_takes_the_next_site_instead_of_the_dead_address(
        self, session: AsyncSession
    ) -> None:
        """Просили одно письмо, у первого сайта ящика нет: письмо получает следующий по отбору."""
        await make_donor(session, "a.example.test", email="a@a.example.test", dr=70)
        await make_donor(session, "b.example.test", email="b@b.example.test", dr=60)
        builder = QueueBuilder(
            session,
            FakeRewriter(),  # type: ignore[arg-type]
            verifier=FakeVerifier({"a@a.example.test": Verdict("invalid", 3)}),
        )

        report = await builder.build(BuildRequest(campaign_name="Одно письмо", limit=1))

        assert report.prepared == 1
        assert await _letters_to(session) == ["b@b.example.test"]


class TestQueuedLetters:
    """Письма, собранные до проверки адресов: пачка и проход автоотправки спрашивают адрес
    перед письмом тем же правилом (`address_gate.queued_refusal`)."""

    @staticmethod
    async def _queued_unchecked(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
        """Очередь, собранная при выключенной проверке; дальше проверка включена, как на бою."""
        monkeypatch.setattr(contacts_cfg, "VERIFY_BEFORE_LETTER", {})
        await _build(session, FakeVerifier())
        monkeypatch.setattr(contacts_cfg, "VERIFY_BEFORE_LETTER", {"donors": True})
        await make_sender(session, "outreach1@mail.example.test")
        await session.commit()

    @staticmethod
    def _hunter(monkeypatch: pytest.MonkeyPatch, verifier: FakeVerifier) -> FakeVerifier:
        """Пачка собирает проверяльщика сама — по настройкам: в тесте им становится подставной."""
        monkeypatch.setattr(address_gate, "HunterVerifier", lambda _http: verifier)
        return verifier

    async def test_a_letter_queued_before_the_check_is_checked_before_it_goes(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await make_donor(session, SITE, email=f"editor@{SITE}")
        await self._queued_unchecked(session, monkeypatch)
        verifier = self._hunter(monkeypatch, FakeVerifier())

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert verifier.asked == [f"editor@{SITE}"]
        assert (report.sent, dict(report.refused)) == (1, {})
        assert (await _address(session, f"editor@{SITE}")).verification_status == "valid"
        assert ("contacts_verify", 1, Stage.DONORS) in await _spent(session)

    async def test_a_queued_letter_to_a_missing_address_is_withdrawn_for_the_next_address(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        domain = await make_donor(session, SITE, email=f"first@{SITE}")
        await _add_address(session, domain, f"second@{SITE}")
        await self._queued_unchecked(session, monkeypatch)
        verifier = self._hunter(monkeypatch, FakeVerifier({f"first@{SITE}": Verdict("invalid", 3)}))

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert (report.sent, dict(report.refused)) == (0, {"адрес не существует": 1})
        letter = await session.scalar(select(MessageModel))
        assert letter is not None
        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (
            MessageStatus.STOPPED,
            attempts.ADDRESS_GONE,
        )
        assert (await _address(session, f"first@{SITE}")).verification_status == DEAD

        again = await _build(session, verifier)

        assert again.prepared == 1, "письмо не уходило — сайту пишут на следующий адрес"
        queued = await session.scalars(
            select(ContactModel.email)
            .join(MessageModel, MessageModel.contact_id == ContactModel.id)
            .where(MessageModel.status == MessageStatus.QUEUED)
        )
        assert list(queued) == [f"second@{SITE}"]

    async def test_a_failed_check_keeps_the_letter_in_the_queue_and_says_why(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await make_donor(session, SITE, email=f"editor@{SITE}")
        await self._queued_unchecked(session, monkeypatch)
        down = ProviderError("провайдер не ответил (HTTP 503)")
        self._hunter(monkeypatch, FakeVerifier({f"editor@{SITE}": down}))

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert report.sent == 0
        assert dict(report.refused) == {"адрес не проверен — провайдер не ответил (HTTP 503)": 1}
        letter = await session.scalar(select(MessageModel))
        assert letter is not None
        await session.refresh(letter)
        assert letter.status is MessageStatus.QUEUED
        assert report.left == 1

    async def test_after_a_failed_check_the_rest_of_the_batch_is_not_asked(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проход автоотправки идёт каждую минуту: сбой сервиса не должен получать запрос на
        каждое письмо очереди — после первого отказа остальные ждут следующей пачки."""
        await make_donor(session, "a.example.test", email="a@a.example.test", dr=70)
        await make_donor(session, "b.example.test", email="b@b.example.test", dr=60)
        await self._queued_unchecked(session, monkeypatch)
        down = ProviderError("провайдер не ответил (HTTP 503)")
        verifier = self._hunter(
            monkeypatch, FakeVerifier({"a@a.example.test": down, "b@b.example.test": down})
        )

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert len(verifier.asked) == 1
        assert dict(report.refused) == {"адрес не проверен — провайдер не ответил (HTTP 503)": 2}
        assert (report.sent, report.left) == (0, 2)

    async def test_still_checking_holds_its_own_letter_and_the_next_one_goes(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """«Ещё проверяет» — про один адрес, а не про сервис: его письмо ждёт, следующее
        спрашивается и уходит. Иначе вечно проверяемый адрес первым в очереди держал бы за
        собой все непроверенные письма в каждом проходе."""
        await make_donor(session, "a.example.test", email="a@a.example.test", dr=70)
        await make_donor(session, "b.example.test", email="b@b.example.test", dr=60)
        await self._queued_unchecked(session, monkeypatch)
        waiting = StillCheckingError("провайдер ещё проверяет адрес")
        verifier = self._hunter(monkeypatch, FakeVerifier({"a@a.example.test": waiting}))

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert verifier.asked == ["a@a.example.test", "b@b.example.test"]
        assert dict(report.refused) == {"адрес не проверен — провайдер ещё проверяет адрес": 1}
        assert (report.sent, report.left) == (1, 1)
        sent = await session.scalars(
            select(ContactModel.email)
            .join(MessageModel, MessageModel.contact_id == ContactModel.id)
            .where(MessageModel.status != MessageStatus.QUEUED)
        )
        assert list(sent) == ["b@b.example.test"]

    async def test_a_letter_built_with_the_check_goes_without_a_second_call(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await make_donor(session, SITE, email=f"editor@{SITE}")
        built_with = FakeVerifier()
        await _build(session, built_with)
        await make_sender(session, "outreach1@mail.example.test")
        await session.commit()
        at_send = self._hunter(monkeypatch, FakeVerifier())

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert (report.sent, built_with.asked, at_send.asked) == (1, [f"editor@{SITE}"], [])

    async def test_switched_off_the_batch_asks_nobody(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await make_donor(session, SITE, email=f"editor@{SITE}")
        await self._queued_unchecked(session, monkeypatch)
        monkeypatch.setattr(contacts_cfg, "VERIFY_BEFORE_LETTER", {"donors": False})
        verifier = self._hunter(monkeypatch, FakeVerifier())

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert (report.sent, verifier.asked) == (1, [])


class TestFunnelNumbers:
    async def test_the_funnel_counts_checked_unverifiable_and_waiting(
        self, session: AsyncSession
    ) -> None:
        checked = await make_donor(session, "a.example.test", dr=70)
        await _add_address(session, checked, "a@a.example.test", status="valid", checked_days_ago=3)
        unsure = await make_donor(session, "b.example.test", dr=60)
        await _add_address(
            session, unsure, "b@b.example.test", status="unknown", checked_days_ago=3
        )
        await make_donor(session, "c.example.test", email="c@c.example.test", dr=50)
        stale = await make_donor(session, "d.example.test", dr=40)
        await _add_address(session, stale, "d@d.example.test", status="valid", checked_days_ago=400)

        funnel = await LetterRepository(session).funnel_report(Stage.DONORS)

        assert funnel["ещё не писали"] == 4
        assert funnel["адрес проверен"] == 2
        assert funnel["не проверить"] == 1
        assert funnel["ждут проверки адреса"] == 2
        assert "адрес не существует" not in funnel

    async def test_nothing_to_say_means_no_notes(self, session: AsyncSession) -> None:
        domain = await make_donor(session, SITE)
        await _add_address(session, domain, f"a@{SITE}", status="valid", checked_days_ago=1)

        funnel = await LetterRepository(session).funnel_report(Stage.DONORS)

        assert funnel["адрес проверен"] == 1
        assert not {"не проверить", "адрес не существует", "ждут проверки адреса"} & set(funnel)

    async def test_an_address_that_bounced_after_a_letter_is_not_the_checks_doing(
        self, session: AsyncSession
    ) -> None:
        """«Адрес не существует» — про тех, кто выпал до первого письма из-за проверки; отказ
        доставки после письма считает своя строка воронки («адреса кончились»)."""
        await make_donor(session, SITE, email=f"editor@{SITE}")
        await _build(session, FakeVerifier())
        contact = await _address(session, f"editor@{SITE}")
        contact.verification_status, contact.verification_score = DEAD, 0
        await session.flush()

        funnel = await LetterRepository(session).funnel_report(Stage.DONORS)

        assert "адрес не существует" not in funnel
