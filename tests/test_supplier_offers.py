"""Поставщик, внесённый в стоп-лист после перевода, — до сборки и отправки офферов.

Сверка с постановкой 12.10.2026 нашла: поставщик (`crawl/suppliers.supplier_hosts`)
отсекался только обходом и переводом, а сборка офферов и проверка при отправке смотрят
на строку рекламодателя — и оффер под ссылку на площадке поставщика, внесённого задним
числом, собирался и уходил его рекламодателю, чужому клиенту.

Проверяется на настоящей базе: поставщик заводится экраном «Стоп-лист»
(`letters/stoplist.add`) или прежним списком консоли — правило одно.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.features.core.domain import MessageStatus, SuppressionReason
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.letters import stoplist
from backend.features.letters.attempts import SUPPLIER_WITHDRAWN
from backend.features.letters.recipients import Recipients
from backend.features.letters.sending import Sending, WithdrawnLinkError
from backend.features.letters.transport import NullTransport
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_advertiser_withdraw import BRAND, OTHER, _advertiser, _bought, _promoted
from tests.test_letters_advertisers import (
    DONOR,
    build_offers,
    make_advertiser,
    offers,
    priced_donor,
    stage_two_sender,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


async def _supplier(session: AsyncSession, host: str, *, legacy: bool = False) -> None:
    """Донор `host` стал поставщиком — экраном «Стоп-лист» или прежним списком консоли."""
    if legacy:
        session.add(SupplierDonorModel(host=host, note="стал партнёром"))
        await session.flush()
        return
    await stoplist.add(session, host, reason=SuppressionReason.SUPPLIER, author="оператор")


async def _two_links(session: AsyncSession) -> None:
    """Рекламодатель `BRAND` со ссылками двух доноров: лучшая — у `DONOR`."""
    await priced_donor(session)
    await priced_donor(session, OTHER)
    await _bought(session, points=9)
    await _bought(session, donor=OTHER, points=5)
    await _promoted(session)


class TestTheBuild:
    @pytest.mark.parametrize("legacy", [False, True], ids=["stop-list", "console-list"])
    async def test_the_offer_goes_under_another_donors_link(
        self, session: AsyncSession, filled_legal: None, legacy: bool
    ) -> None:
        """Письмо под ссылку поставщика снято своей причиной и сборку не держит: она
        пересобирает рекламодателю ссылку из кандидатов другого донора и пишет под неё —
        на тот же адрес, новой попыткой."""
        await _two_links(session)
        await build_offers(session)
        (letter,) = await offers(session)
        assert DONOR in (letter.body or "")

        await _supplier(session, DONOR, legacy=legacy)
        rebuilt = await build_offers(session)

        advertiser = await _advertiser(session)
        assert advertiser is not None
        assert advertiser.best_donor_host == OTHER
        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (
            MessageStatus.STOPPED,
            SUPPLIER_WITHDRAWN,
        )
        assert rebuilt.prepared == 1  # type: ignore[attr-defined]
        fresh = (await offers(session))[-1]
        assert fresh.status is MessageStatus.QUEUED
        assert f"https://{OTHER}/" in (fresh.body or "")
        assert f"https://{DONOR}/" not in (fresh.body or "")
        assert fresh.contact_id == letter.contact_id, "снятое письмо адреса не занимало"
        assert fresh.idempotency_key == f"advertisers:{BRAND}:0:a2"

    async def test_the_only_link_on_a_supplier_gets_no_offer(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Других ссылок нет — рекламодателя снимает сборка, и письма ему не собирается."""
        await priced_donor(session)
        await _bought(session)
        await _promoted(session)
        await _supplier(session, DONOR)

        report = await build_offers(session)

        assert report.prepared == 0  # type: ignore[attr-defined]
        assert await offers(session) == []
        assert await _advertiser(session) is None


class TestTheSelection:
    async def test_a_supplier_platform_is_not_offered(self, session: AsyncSession) -> None:
        """Отбор не предлагает площадку поставщика и без сборки — тем же правилом,
        что обход и перевод; воронка не считает её ступенью «цена донора свежая»."""
        await priced_donor(session)
        await priced_donor(session, OTHER)
        await make_advertiser(session, "buyer-one.example.test")
        await make_advertiser(session, "buyer-two.example.test", donor_host=OTHER)
        await _supplier(session, DONOR)

        picked = await Recipients(session).advertiser_candidates(limit=10)
        funnel = await Recipients(session).advertiser_funnel()

        assert [candidate.host for candidate in picked] == ["buyer-two.example.test"]
        assert (funnel.with_link, funnel.fresh_price) == (2, 1)


class TestTheSending:
    async def test_a_queued_offer_is_withdrawn_and_the_next_build_rewrites_it(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Поставщика внесли, пока письмо ждало в очереди, а сборки с тех пор не было:
        отправка снимает его своей причиной — оно не уходило, «писали» не считается, — и
        следующая сборка пишет тому же адресату под ссылку другого донора."""
        await _two_links(session)
        await build_offers(session)
        (letter,) = await offers(session)
        await stage_two_sender(session)
        await _supplier(session, DONOR)
        await session.commit()

        with pytest.raises(WithdrawnLinkError) as refused:
            await Sending(session, NullTransport(), now=NOW).send(letter.id)

        assert str(refused.value) == (
            f"Письмо №{letter.id} снято: площадка оффера {DONOR} — поставщик агентства, его "
            f"рекламодателям не пишем. Рекламодателю {BRAND} следующая сборка напишет под "
            "ссылку другого донора, если она есть"
        )
        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (
            MessageStatus.STOPPED,
            SUPPLIER_WITHDRAWN,
        )
        assert letter.thread_id is None, "письмо не уходило — диалога с ним не было"

        rebuilt = await build_offers(session)

        assert rebuilt.prepared == 1  # type: ignore[attr-defined]
        assert f"https://{OTHER}/" in ((await offers(session))[-1].body or "")
