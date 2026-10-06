"""Цена руками и донор, заведённый вручную, — на настоящей базе.

Требование Этапа 2: «по кому запускаем — только доноры с известной ценой (из
базы или заведённые вручную)». Проверяется то, ради чего ядро
(`donors/manual_price.py`): ввод человека проверяется словами; домен, который
ещё не донор, становится им без Ahrefs и без прогона; каждый отказ ничего не
меняет; цена лежит в тех же полях, что цена из ответа, и говорит, откуда она.

Домены, которые приводятся к корню, — из зарезервированных (`example.com`,
`example.org`): `*.test` и `.invalid` у списка суффиксов корня не имеют.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.cli.donor_add import EXIT_OK, EXIT_REFUSED, run_donor_add
from backend.cli.main import build_parser
from backend.features.core.domain import (
    AuditAction,
    DonorStatus,
    PriceSource,
    Stage,
    SuppressionReason,
)
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.advertisers import AdvertiserModel, SupplierDonorModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import SuppressionModel, UsageRecordModel
from backend.features.core.models.run import RunCandidateModel
from backend.features.donors.browse import UnknownDonorError
from backend.features.donors.manual_price import (
    DonorRefusedError,
    ManualPrice,
    ManualPriceError,
    enter_host,
    manual_price,
    price_donor,
)
from backend.features.replies.repository import ReplyRepository
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender
from tests.migration_helpers import columns_down_and_up

WHO = "anna@ours.example.test"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def price(amount: object = "150", currency: object = "USD", note: object = None) -> ManualPrice:
    return manual_price(amount, currency, note, by=WHO)


async def _donor(session: AsyncSession, host: str) -> DonorModel:
    found = await session.scalar(
        select(DonorModel).join(DomainModel).where(DomainModel.host == host)
    )
    assert found is not None
    return found


async def _count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _journal(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(AuditLogModel.details)
        .where(AuditLogModel.action == AuditAction.PRICE_REVIEWED)
        .order_by(AuditLogModel.id)
    )
    return [row or {} for row in rows.all()]


# --- ввод человека ---------------------------------------------------------------------------


class TestWhatThePersonTyped:
    @pytest.mark.parametrize(
        ("raw", "stored"),
        [
            ("150", Decimal("150.00")),
            ("150.5", Decimal("150.50")),
            (" 1 200 ", Decimal("1200.00")),
            (Decimal("99999.99"), Decimal("99999.99")),
            (75, Decimal("75.00")),
        ],
    )
    def test_a_price_is_kept_to_the_cent(self, raw: object, stored: Decimal) -> None:
        assert price(raw).amount == stored

    @pytest.mark.parametrize(
        ("raw", "said"),
        [
            ("", "Цена не указана"),
            ("сто", "«сто» — не цена"),
            ("0", "«0» — не цена"),
            ("-5", "«-5» — не цена"),
            ("NaN", "«NaN» — не цена"),
            ("Infinity", "«Infinity» — не цена"),
            # Запятая — отказ, а не догадка: «1,200» бывает и тысячей двумястами.
            ("1,200", "«1,200» — не цена"),
            ("100000", "не меньше 100 000"),
            ("150.555", "точнее копеек"),
        ],
    )
    def test_not_a_price_is_refused_in_words(self, raw: str, said: str) -> None:
        with pytest.raises(ManualPriceError, match=said):
            price(raw)

    @pytest.mark.parametrize(
        ("raw", "code"),
        [("usd", "USD"), ("$", "USD"), ("€", "EUR"), ("евро", "EUR"), ("GBP", "GBP")],
    )
    def test_currency_is_a_code_whatever_was_typed(self, raw: str, code: str) -> None:
        assert price(currency=raw).currency == code

    @pytest.mark.parametrize(
        ("raw", "said"),
        [("", "Не указана валюта"), ("UDS", "«UDS» не знакома"), ("kr", "«kr» не знакома")],
    )
    def test_unknown_currency_is_refused_not_stored(self, raw: str, said: str) -> None:
        """Незнакомое `normalize_currency` вернула бы как есть: опечатка легла бы валютой."""
        with pytest.raises(ManualPriceError, match=said):
            price(currency=raw)

    def test_note_is_one_line_and_empty_is_none(self) -> None:
        assert price(note="  прайс\nагентства  ").note == "прайс агентства"
        assert price(note="   ").note is None
        assert price(note="x" * 200).note == "x" * 200
        with pytest.raises(ManualPriceError, match="не длиннее 200 знаков, а вписано 201"):
            price(note="x" * 201)

    @pytest.mark.parametrize("who", ["anna", "@ours.example.test", "anna@", f"{'a' * 250}@x.test"])
    def test_price_is_written_on_behalf_of_an_address(self, who: str) -> None:
        with pytest.raises(ManualPriceError, match="не почта сотрудника"):
            manual_price("150", by=who)


# --- донор вручную -------------------------------------------------------------------------


class TestEnteredByHand:
    async def test_unknown_domain_becomes_an_accepted_donor_with_the_price(
        self, session: AsyncSession
    ) -> None:
        entered = await enter_host(
            session,
            "https://www.Blog.Example.org/prices?x=1",
            price("150", "€", "прайс агентства"),
            author_id=None,
            now=NOW,
        )

        assert (entered.host, entered.created, entered.suitable) == ("example.org", True, True)
        donor = await _donor(session, "example.org")
        assert entered.donor_id == donor.id
        assert (donor.status, donor.review, donor.review_by, donor.review_at) == (
            DonorStatus.SUITABLE,
            "accepted",
            WHO,
            NOW,
        )
        assert donor.entered_by == WHO
        assert (donor.last_price, donor.last_price_currency, donor.last_price_at) == (
            Decimal("150.00"),
            "EUR",
            NOW,
        )
        assert (donor.last_price_source, donor.last_price_note, donor.last_price_by) == (
            PriceSource.MANUAL,
            "прайс агентства",
            WHO,
        )
        # Ни метрик, ни расхода: Ahrefs и другие платные сервисы не звали.
        assert (donor.metrics, donor.metrics_refreshed_at, donor.last_offers) == (None, None, None)
        assert await _count(session, UsageRecordModel) == 0
        (journal,) = await _journal(session)
        assert journal == {
            "действие": "донор заведён вручную, с ценой",
            "донор": "example.org",
            "цена": "150.00",
            "валюта": "EUR",
            "откуда цена": "прайс агентства",
            "кто": WHO,
            "прежняя цена": None,
        }

    async def test_a_donor_just_gets_the_price_and_keeps_its_decision(
        self, session: AsyncSession
    ) -> None:
        domain = await make_donor(session, "example.com")
        await ReplyRepository(session).store_price(
            domain_id=domain.id,
            price=Decimal("120"),
            currency="EUR",
            offers=[{"product": "guest post", "price": "120"}],
            now=NOW - timedelta(days=30),
        )
        donor = await _donor(session, "example.com")
        donor.review_by = "принял@ours.example.test"

        entered = await enter_host(session, "example.com", price("90"), now=NOW)

        assert (entered.created, entered.suitable) == (False, True)
        assert (donor.last_price, donor.last_price_at, donor.last_offers) == (
            Decimal("90.00"),
            NOW,
            None,
        )
        assert donor.last_price_source == PriceSource.MANUAL
        # Решение — прежнее: донора принимали в очереди прогона, а не здесь.
        assert (donor.review_by, donor.entered_by) == ("принял@ours.example.test", None)
        (journal,) = await _journal(session)
        assert journal["действие"] == "цена указана вручную"
        assert journal["прежняя цена"] == "120.00 EUR, из ответа"

    async def test_a_known_domain_without_a_donor_row_is_reused(
        self, session: AsyncSession
    ) -> None:
        """Тот же сайт бывает рекламодателем: строка домена одна на обе роли."""
        domain = DomainModel(host="example.net")
        session.add(domain)
        await session.flush()
        session.add(AdvertiserModel(domain_id=domain.id))
        await session.flush()

        entered = await enter_host(session, "example.net", price(), now=NOW)

        assert entered.created is True
        assert await _count(session, DomainModel) == 1
        assert (await _donor(session, "example.net")).domain_id == domain.id

    async def test_unchecked_candidate_outside_queues_is_taken(self, session: AsyncSession) -> None:
        """Ahrefs о нём не знал — как о новом домене: слово человека, а не порогов."""
        await make_donor(session, "example.com", review=None)
        donor = await _donor(session, "example.com")
        donor.status = DonorStatus.UNCHECKED

        entered = await enter_host(session, "example.com", price(), now=NOW)

        assert entered.created is True
        assert (donor.status, donor.review, donor.entered_by) == (
            DonorStatus.SUITABLE,
            "accepted",
            WHO,
        )

    async def test_a_donor_that_says_it_does_not_sell_is_not_a_refusal(
        self, session: AsyncSession
    ) -> None:
        """«Не продаём» — ответ на письмо; цену человек знает не из письма."""
        domain = await make_donor(session, "example.com")
        domain.seller_answer, domain.seller_answer_at = "declines", NOW - timedelta(days=3)
        await session.flush()

        entered = await enter_host(session, "example.com", price(), now=NOW)

        assert entered.created is False

    async def test_from_the_card_the_stored_host_is_used_as_it_is(
        self, session: AsyncSession
    ) -> None:
        """У домена карточки корень уже ключ базы: заново он не приводится."""
        domain = await make_donor(session, "card.example.test")
        donor = await _donor(session, domain.host)

        entered = await price_donor(session, donor.id, price("200"), author_id=None, now=NOW)

        assert (entered.host, entered.created) == ("card.example.test", False)
        assert donor.last_price == Decimal("200.00")

    @pytest.mark.parametrize("donor_id", [987654, 2**40])
    async def test_card_of_nobody_is_not_found(self, session: AsyncSession, donor_id: int) -> None:
        with pytest.raises(UnknownDonorError, match=f"Донора №{donor_id} нет"):
            await price_donor(session, donor_id, price())

    async def test_a_reply_price_after_a_manual_one_says_where_it_came_from(
        self, session: AsyncSession
    ) -> None:
        """Последняя цена всегда говорит, откуда она: ответ затирает заметку и автора."""
        await enter_host(session, "example.com", price(note="прайс агентства"), now=NOW)
        donor = await _donor(session, "example.com")

        await ReplyRepository(session).store_price(
            domain_id=donor.domain_id, price=Decimal("140"), currency="USD", offers=None
        )

        assert (donor.last_price, donor.last_price_source) == (Decimal("140"), PriceSource.REPLY)
        assert (donor.last_price_note, donor.last_price_by) == (None, None)


# --- отказы: ничего не меняется ------------------------------------------------------------


async def _nothing_changed(session: AsyncSession, domains: int) -> None:
    assert await _count(session, DomainModel) == domains
    priced = await session.scalar(
        select(func.count()).select_from(DonorModel).where(DonorModel.last_price.is_not(None))
    )
    assert priced == 0
    assert await _journal(session) == []


class TestRefusals:
    @pytest.mark.parametrize("raw", ["не-адрес", "localhost", "probe.invalid", "   "])
    async def test_not_a_domain(self, session: AsyncSession, raw: str) -> None:
        with pytest.raises(ManualPriceError, match="не домен: впишите адрес сайта"):
            await enter_host(session, raw, price())
        await _nothing_changed(session, 0)

    async def test_our_own_sending_domain(self, session: AsyncSession) -> None:
        """Ящик живёт на поддомене, а донор — корень: сравниваются корни."""
        await make_sender(session, "outreach@mail.example.net")

        with pytest.raises(DonorRefusedError, match="example.net — наш домен рассылки"):
            await enter_host(session, "https://example.net/", price())
        await _nothing_changed(session, 0)

    @pytest.mark.parametrize(
        ("raw", "said"),
        [
            ("www.example.gov", "example.gov — гос. или учебная зона"),
            ("example.ac.uk", "example.ac.uk — гос. или учебная зона"),
            ("facebook.com", "facebook.com — платформа или соцсеть"),
        ],
    )
    async def test_zones_and_platforms(self, session: AsyncSession, raw: str, said: str) -> None:
        with pytest.raises(DonorRefusedError, match=said):
            await enter_host(session, raw, price())
        await _nothing_changed(session, 0)

    async def test_stop_list(self, session: AsyncSession) -> None:
        domain = await make_donor(session, "example.com")
        session.add(
            SuppressionModel(domain_id=domain.id, reason=SuppressionReason.UNSUBSCRIBED, stage=None)
        )
        await session.flush()

        with pytest.raises(DonorRefusedError, match="example.com в стоп-листе"):
            await enter_host(session, "example.com", price())
        await _nothing_changed(session, 1)

    async def test_stop_list_of_the_other_stage_does_not_count(self, session: AsyncSession) -> None:
        """Отписка от офферов Этапа 2 — про сайт-рекламодатель, а не про донора."""
        domain = await make_donor(session, "example.com")
        session.add(
            SuppressionModel(
                domain_id=domain.id,
                reason=SuppressionReason.UNSUBSCRIBED,
                stage=Stage.ADVERTISERS,
            )
        )
        await session.flush()

        assert (await enter_host(session, "example.com", price())).created is False

    async def test_supplier_donor(self, session: AsyncSession) -> None:
        session.add(SupplierDonorModel(host="example.com", note="размещались в марте"))
        await session.flush()

        with pytest.raises(DonorRefusedError, match="донор-поставщик агентства"):
            await enter_host(session, "www.example.com", price())
        await _nothing_changed(session, 0)

    async def test_rejected_by_a_human(self, session: AsyncSession) -> None:
        await make_donor(session, "example.com", review="rejected")

        with pytest.raises(DonorRefusedError, match="example.com отклонён человеком"):
            await enter_host(session, "example.com", price())
        await _nothing_changed(session, 1)
        assert (await _donor(session, "example.com")).review == "rejected"

    async def test_rejected_from_the_card_too(self, session: AsyncSession) -> None:
        domain = await make_donor(session, "card.example.test", review="rejected")
        donor = await _donor(session, domain.host)

        with pytest.raises(DonorRefusedError, match="отклонён человеком"):
            await price_donor(session, donor.id, price())
        await _nothing_changed(session, 1)

    async def test_candidate_waiting_in_a_run_queue(self, session: AsyncSession) -> None:
        """Решают в очереди прогона: там судья, выдача и причина, по которой он там."""
        runs = RunRepository(session)
        settings = await runs.create_settings(
            defaults(),
            geo_top_n=5,
            geo_min_share=0.2,
            metrics_ttl_days=90,
            price_ttl_days=150,
            units_cap=100_000,
        )
        run = await runs.create_run(
            stage=Stage.DONORS, settings_id=settings.id, keywords=["garden"], country="us"
        )
        domain = await make_donor(session, "example.com", review=None)
        session.add(RunCandidateModel(run_id=run.id, domain_id=domain.id, status="pending"))
        await session.flush()

        with pytest.raises(
            DonorRefusedError, match=f"ждёт решения в очереди прогона №{run.id}: решают там"
        ):
            await enter_host(session, "example.com", price())
        await _nothing_changed(session, 1)
        assert (await _donor(session, "example.com")).review is None

    async def test_failed_the_thresholds(self, session: AsyncSession) -> None:
        await make_donor(session, "example.com", review=None)
        donor = await _donor(session, "example.com")
        donor.status, donor.reject_reason = DonorStatus.UNSUITABLE, "dr ниже порога"

        with pytest.raises(DonorRefusedError, match=r"не прошёл пороги отбора \(dr ниже порога\)"):
            await enter_host(session, "example.com", price())
        await _nothing_changed(session, 1)
        assert (donor.status, donor.review) == (DonorStatus.UNSUITABLE, None)


# --- консоль ---------------------------------------------------------------------------------


async def test_console_enters_the_donor_and_says_what_next(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    args = build_parser().parse_args(
        [
            "donor-add",
            "--host",
            "www.example.com",
            "--price",
            "150.50",
            "--currency",
            "eur",
            "--note",
            "LinkDetective",
            "--by",
            WHO,
        ]
    )

    code = await run_donor_add(session, args)

    out = capsys.readouterr().out
    donor = await _donor(session, "example.com")
    assert code == EXIT_OK
    assert f"Донор example.com (№{donor.id}) заведён вручную: 150.50 EUR (LinkDetective)." in out
    assert "outreach crawl example.com --queue" in out
    assert (donor.last_price_by, donor.entered_by) == (WHO, WHO)

    again = build_parser().parse_args(
        ["donor-add", "--host", "example.com", "--price", "140", "--by", WHO]
    )
    assert await run_donor_add(session, again) == EXIT_OK
    assert "уже был донором — записана цена: 140.00 USD." in capsys.readouterr().out


async def test_console_refuses_in_words_and_names_unsuitable(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    refused = build_parser().parse_args(
        ["donor-add", "--host", "facebook.com", "--price", "150", "--by", WHO]
    )
    assert await run_donor_add(session, refused) == EXIT_REFUSED
    assert "Донор не заведён: facebook.com — платформа или соцсеть" in capsys.readouterr().out

    await make_donor(session, "example.com")
    (await _donor(session, "example.com")).status = DonorStatus.UNSUITABLE
    accepted = build_parser().parse_args(
        ["donor-add", "--host", "example.com", "--price", "150", "--by", WHO]
    )
    assert await run_donor_add(session, accepted) == EXIT_OK
    assert "обход Этапа 2 его не возьмёт" in capsys.readouterr().out


async def test_migration_goes_down_and_up(session: AsyncSession) -> None:
    """Ревизия, которую выкатка применит к проду, — вниз и вверх на тестовой базе."""
    connection = await session.connection()
    columns = {"last_price_source", "last_price_note", "last_price_by", "entered_by"}

    found = await connection.run_sync(
        columns_down_and_up, "7bfc6c880f0a_manual_price.py", "donors", columns
    )

    assert found == (set(), columns)
