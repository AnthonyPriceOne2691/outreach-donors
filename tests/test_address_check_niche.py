"""Числа проверки адреса у бизнесов ниши — на настоящей базе, через воронку экрана писем.

Адрес бизнеса ниши сборка проверяет тем же правилом, что адрес рекламодателя по ссылке
(`letters/address_gate.Picker`), а вкладка «Бизнесам ниши» об этом молчала (12.11.2026). Числа
считаются по тем же адресам, на которые сборка написала бы сейчас: бизнес без примера площадки
письма не получит — и проверки не ждёт. Выпавшие — у своей вкладки: бизнес ниши в числе по
ссылке не стоит, и наоборот.
"""

from __future__ import annotations

import pytest
from backend.config import contacts as contacts_cfg
from backend.features.contacts.verifier import Verdict
from backend.features.core.domain import RunStatus, Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.run import RunModel
from backend.features.crawl import niche
from backend.features.donors.verdict import Thresholds
from backend.features.letters.building import BuildReport, BuildRequest, QueueBuilder
from backend.features.letters.repository import LetterRepository
from backend.features.runs.repository import RunRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_address_check_build import (  # noqa: F401 — `_check_on` включает проверку этапов
    FakeRewriter,
    FakeVerifier,
    _add_address,
    _check_on,
)
from tests.test_letters_advertisers import make_advertiser, priced_donor

DONOR = "donor.example.test"
CHECKED = "checked.example.test"
UNSURE = "unsure.example.test"
FRESH = "fresh.example.test"

CHECK_KEYS = {"адрес проверен", "не проверить", "адрес не существует", "ждут проверки адреса"}


async def _businesses(session: AsyncSession, *hosts: str) -> dict[str, DomainModel]:
    """Бизнесы ниши одного прогона с решением «пишем» — без адресов: адрес кладёт тест."""
    domains = {host: DomainModel(host=host, site_intent=niche.SELLS_OWN) for host in hosts}
    session.add_all(domains.values())
    settings = await RunRepository(session).create_settings(
        Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = RunModel(
        stage=Stage.DONORS,
        settings_id=settings.id,
        status=RunStatus.DONE,
        keywords=["betting tips"],
        country="de",
        candidates={"hosts": [*hosts, DONOR]},
    )
    session.add(run)
    await session.flush()
    await niche.collect(session, run.id)
    found = await session.scalars(
        select(AdvertiserModel.id).where(
            AdvertiserModel.domain_id.in_([domain.id for domain in domains.values()])
        )
    )
    for advertiser_id in found.all():
        await niche.decide(session, advertiser_id, write=True, by="operator@example.test")
    await session.flush()
    return domains


async def _niche_funnel(session: AsyncSession) -> dict[str, int]:
    return await LetterRepository(session).funnel_report(Stage.ADVERTISERS, audience=niche.NICHE)


async def _build(session: AsyncSession, verifier: FakeVerifier, audience: str) -> BuildReport:
    builder = QueueBuilder(session, FakeRewriter(), verifier=verifier)  # type: ignore[arg-type]
    return await builder.build(
        BuildRequest(
            campaign_name=f"Проверка {audience}", stage=Stage.ADVERTISERS, audience=audience
        )
    )


async def test_the_niche_funnel_counts_checked_unverifiable_and_waiting(
    session: AsyncSession,
) -> None:
    await priced_donor(session, DONOR)
    sites = await _businesses(session, CHECKED, UNSURE, FRESH)
    await _add_address(session, sites[CHECKED], f"a@{CHECKED}", status="valid", checked_days_ago=3)
    await _add_address(session, sites[UNSURE], f"b@{UNSURE}", status="unknown", checked_days_ago=3)
    await _add_address(session, sites[FRESH], f"c@{FRESH}")

    funnel = await _niche_funnel(session)

    assert funnel["ещё не писали"] == 3
    assert funnel["адрес проверен"] == 2
    assert funnel["не проверить"] == 1
    assert funnel["ждут проверки адреса"] == 1
    assert "адрес не существует" not in funnel


async def test_a_business_the_build_would_not_write_to_does_not_wait_for_the_check(
    session: AsyncSession,
) -> None:
    """Примера площадки со свежей ценой нет — письма не будет, и адрес сборка не спросит:
    «ждут проверки» считает только тех, кого сборка возьмёт («ещё не писали»)."""
    await priced_donor(session, DONOR, priced_days_ago=None)
    sites = await _businesses(session, FRESH)
    await _add_address(session, sites[FRESH], f"c@{FRESH}")

    funnel = await _niche_funnel(session)

    assert (funnel["ещё не писали"], funnel["адрес проверен"]) == (0, 0)
    assert "ждут проверки адреса" not in funnel


async def test_a_buried_niche_address_is_counted_on_the_niche_tab_only(
    session: AsyncSession,
) -> None:
    await priced_donor(session, DONOR)
    sites = await _businesses(session, FRESH)
    await _add_address(session, sites[FRESH], f"dead@{FRESH}")
    verifier = FakeVerifier({f"dead@{FRESH}": Verdict("invalid", 1)})

    report = await _build(session, verifier, niche.NICHE)

    assert (report.prepared, report.address_check) == (0, {"не существует": 1})
    funnel = await _niche_funnel(session)
    assert (funnel["ещё не писали"], funnel["адрес не существует"]) == (0, 1)
    links = await LetterRepository(session).funnel_report(Stage.ADVERTISERS)
    assert "адрес не существует" not in links


async def test_a_buried_address_by_link_is_not_counted_on_the_niche_tab(
    session: AsyncSession,
) -> None:
    await priced_donor(session, DONOR)
    await make_advertiser(session, "shop.example.test", email="dead@shop.example.test")
    verifier = FakeVerifier({"dead@shop.example.test": Verdict("invalid", 1)})

    await _build(session, verifier, niche.LINKS)

    links = await LetterRepository(session).funnel_report(Stage.ADVERTISERS)
    assert links["адрес не существует"] == 1
    assert "адрес не существует" not in await _niche_funnel(session)


async def test_switched_off_the_niche_funnel_is_what_it_was(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        contacts_cfg, "VERIFY_BEFORE_LETTER", {"donors": True, "advertisers": False}
    )
    await priced_donor(session, DONOR)
    sites = await _businesses(session, FRESH)
    await _add_address(session, sites[FRESH], f"c@{FRESH}")

    funnel = await _niche_funnel(session)

    assert funnel["ещё не писали"] == 1
    assert not CHECK_KEYS & set(funnel)
