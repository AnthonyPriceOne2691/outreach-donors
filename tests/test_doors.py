"""Дверь сайта для авторов и рекламодателей: меню главной у очереди прогона.

Судья смотрит главную только у спорных доменов — у остальных пункт меню
«Advertise» не видно вовсе. Здесь проверяется, что проверка дверей
смотрит только тех, про кого не знаем, и что «не открылась» не
превращается в «двери нет».
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from backend.features.core.models.domain import DomainModel
from backend.features.donors import doors as doors_module
from backend.features.donors.doors import DoorCheck
from backend.features.donors.home_signals import HomeSignals, read_home
from backend.features.donors.judging import door_record
from backend.features.donors.repository import DonorRepository, JudgeRecord
from backend.features.review.candidates import RunReview
from backend.features.runs.pipeline import RunRequest, execute_run
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_execute_run import GOOD, FakeSerp, T, _ahrefs, _deps, _settings_id

ADVERTISE = '<html><head><title>Blog</title></head><body><nav><a href="/">Home</a><a href="/ads">Advertise</a></nav></body></html>'
PLAIN = '<html><head><title>Blog</title></head><body><nav><a href="/">Home</a><a href="/about">About</a></nav></body></html>'


class Homes:
    """Главные без сети: образцы и счётчик обращений."""

    def __init__(self, pages: dict[str, HomeSignals]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    async def __call__(self, host: str) -> HomeSignals:
        self.asked.append(host)
        return self.pages.get(host, HomeSignals(reached=False, error="не ответила"))


async def _door(session: AsyncSession, host: str) -> str | None:
    return await session.scalar(select(DomainModel.site_door).where(DomainModel.host == host))


class TestDoorCheck:
    async def test_menu_door_is_found_and_absence_recorded(self, session: AsyncSession) -> None:
        for host in ("seller.test", "plain.test", "closed.test"):
            await make_donor(session, host)
        homes = Homes({"seller.test": read_home(ADVERTISE), "plain.test": read_home(PLAIN)})

        report = await DoorCheck(homes)(
            DonorRepository(session), ["seller.test", "plain.test", "closed.test"]
        )

        assert await _door(session, "seller.test") == "меню главной: «Advertise»"
        assert await _door(session, "plain.test") == ""
        # Не открылась — не «двери нет»: про такой сайт мы не знаем ничего.
        assert await _door(session, "closed.test") is None
        assert report.as_dict() == {"checked": 3, "found": 1, "unreached": 1, "opened": 0}

    async def test_late_door_opens_a_sells_own_reject_as_the_judge_would(
        self, session: AsyncSession
    ) -> None:
        """Судья меню не видел и отрезал «продаёт своё»; дверь нашлась позже.
        Правило то же, что при суде: такой отказ — к человеку, не в отказ.
        Решение человека при этом не трогается."""
        brand = await make_donor(session, "brand.test")
        brand.site_intent, brand.judge_recommendation = "sells_own", "reject"
        brand.judge_reason = "продаёт свой продукт"
        other = await make_donor(session, "other.test")
        other.site_intent, other.judge_recommendation = "link_vendor", "reject"
        await session.flush()
        homes = Homes({"brand.test": read_home(ADVERTISE), "other.test": read_home(ADVERTISE)})

        report = await DoorCheck(homes)(DonorRepository(session), ["brand.test", "other.test"])

        await session.refresh(brand)
        await session.refresh(other)
        assert brand.judge_recommendation == "review"
        assert brand.judge_reason == (
            "продаёт свой продукт · меню главной: «Advertise» — бренд принимает статьи, посмотри"
        )
        assert brand.human_intent is None
        # Посредник со страницей для авторов всё равно посредник.
        assert other.judge_recommendation == "reject"
        assert report.opened == 1

    async def test_known_doors_are_not_fetched_again(self, session: AsyncSession) -> None:
        known = await make_donor(session, "known.test")
        known.site_door = ""
        await make_donor(session, "new.test")
        homes = Homes({"new.test": read_home(PLAIN)})

        await DoorCheck(homes)(DonorRepository(session), ["known.test", "new.test"])

        assert homes.asked == ["new.test"]

    async def test_every_chunk_is_committed(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Упавшая на середине проверка не теряет увиденного: после каждой
        пачки — фиксация, и повтор начинает с тех, кого ещё не смотрели."""
        monkeypatch.setattr(doors_module, "CHUNK", 2)
        for index in range(5):
            await make_donor(session, f"site{index}.test")
        homes = Homes({f"site{index}.test": read_home(PLAIN) for index in range(5)})
        commits: list[int] = []

        async def checkpoint() -> None:
            commits.append(len(homes.asked))

        await DoorCheck(homes)(
            DonorRepository(session),
            [f"site{index}.test" for index in range(5)],
            checkpoint=checkpoint,
        )

        assert commits == [2, 4, 5]


class TestJudgeRemembersTheDoor:
    def test_found_door_is_kept(self) -> None:
        assert door_record("страница «write-for-us»", None) == "страница «write-for-us»"

    def test_no_door_with_the_menu_seen_is_an_empty_string(self) -> None:
        assert door_record(None, read_home(PLAIN)) == ""

    def test_menu_not_seen_is_unknown(self) -> None:
        """Нет двери в выдаче — не значит, что её нет в меню."""
        assert door_record(None, None) is None
        assert door_record(None, HomeSignals(reached=False)) is None

    async def test_rejudging_without_a_home_keeps_the_door(self, session: AsyncSession) -> None:
        seller = await make_donor(session, "seller.test")
        seller.site_door = "меню главной: «Advertise»"
        await session.flush()
        record = JudgeRecord(intent="editorial_ads", recommendation="accept", reason="издание")

        await DonorRepository(session).save_judgements({"seller.test": record})

        await session.refresh(seller)
        assert seller.site_door == "меню главной: «Advertise»"


class TestTheRunLooksAtItsQueue:
    async def test_queue_gets_its_doors_and_the_run_records_it(self, session: AsyncSession) -> None:
        serp = FakeSerp(["https://good.com/a"])
        deps = await _deps(session, serp, _ahrefs({"good.com": GOOD}))
        homes = Homes({"good.com": read_home(ADVERTISE)})
        deps = replace(deps, review=RunReview(session), doors=DoorCheck(homes))

        report = await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        assert homes.asked == ["good.com"]
        assert await _door(session, "good.com") == "меню главной: «Advertise»"
        assert report.doors is not None
        assert report.doors.found == 1

    async def test_a_broken_check_does_not_break_the_run(self, session: AsyncSession) -> None:
        """Платное уже сделано и лежит в очереди: сбой проверки меню —
        причина в записи прогона, а не упавший прогон."""

        async def broken(host: str) -> HomeSignals:
            raise RuntimeError("сеть легла")

        serp = FakeSerp(["https://good.com/a"])
        deps = await _deps(session, serp, _ahrefs({"good.com": GOOD}))
        deps = replace(deps, review=RunReview(session), doors=DoorCheck(broken))

        report = await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        assert report.review is not None
        assert report.review.pending == 1
        assert report.doors_failure is not None
        assert "сеть легла" in report.doors_failure
