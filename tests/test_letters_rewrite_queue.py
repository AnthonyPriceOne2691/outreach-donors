"""Переписать шаблонные письма очереди (`letters/rewrite_queue.py`) на настоящей базе.

23.11.2026 у модели кончились деньги посреди сборки: письма встали в очередь с зонами шаблона
(отличие 0%). Проверяется то, ради чего действие: берутся только такие письма — не поправленные
человеком, не ушедшие, не уникализированные; переписываются тем же путём, что сборка; повторный
отказ модели письмо не портит и назван словами; письмо, чей текст уже не шаблон рассылки, и
письмо, взятое пачкой посреди переписывания, остаются как были.

Модель подменена: настоящей тесты не зовут.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.config import llm as llm_cfg
from backend.config import outreach as outreach_cfg
from backend.features.access.repository import AccessRepository
from backend.features.core import usage
from backend.features.core.domain import AuditAction, MessageStatus, RunStatus, Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.core.stages import SalesNotConnectedError
from backend.features.crawl.niche import NICHE
from backend.features.letters import review, rewrite_queue
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.repository import LetterRepository
from backend.features.letters.rewrite import Personalization, RewriteResult
from backend.features.letters.rewrite_queue import QueueRewriter, RewriteRequest
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import Thresholds
from backend.shared.llm import NO_CREDITS_WORDS
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_letters_advertisers import ANCHOR, PAGE, build_offers, make_advertiser, priced_donor
from tests.test_niche_letters import BOOKIE, _business
from tests.test_niche_letters import DONOR as NICHE_DONOR

HOSTS = ("a.example.test", "b.example.test", "c.example.test", "d.example.test")
GREETING = "Good afternoon to you,"
SHOP = "shop.example.test"
#: Повторный отказ, после которого модель зовут дальше: не «нет кредитов».
DOWN = "модель, отказ (можно повторить): провайдер ответил 503"


class Model:
    """Модель: отказывает адресатам из `refuse` (с нотой `note`), остальным переписывает
    приветствие. `then` — что сделать посреди вызова (пачка взяла письмо)."""

    def __init__(
        self,
        *,
        refuse: tuple[str, ...] = (),
        note: str = NO_CREDITS_WORDS,
        fail: str | None = None,
        then: object = None,
    ) -> None:
        self.refuse = refuse
        self.note = note
        self.fail = fail
        self.then = then
        self.seen: list[Personalization] = []

    async def rewrite(self, rendered: object, about: Personalization) -> RewriteResult:
        self.seen.append(about)
        if callable(self.then):
            await self.then()
        if about.host == self.fail:
            raise RuntimeError(f"модель легла на {about.host}")
        if about.host in self.refuse:
            return RewriteResult(notes=[self.note])
        return RewriteResult(zones={"greeting": GREETING}, tokens_spent=120)


def refusing_all() -> Model:
    return Model(refuse=HOSTS)


async def _donors(session: AsyncSession, hosts: tuple[str, ...] = HOSTS) -> None:
    for dr, host in zip(range(70, 20, -10), hosts, strict=False):
        await make_donor(session, host, email=f"info@{host}", dr=dr)


async def _build(session: AsyncSession, model: Model) -> None:
    builder = QueueBuilder(session, model)  # type: ignore[arg-type]
    await builder.build(BuildRequest(campaign_name="Ноябрь", niche=("home repair",)))
    await session.commit()


async def _letters(session: AsyncSession) -> dict[str, MessageModel]:
    rows = await session.execute(
        select(DomainModel.host, MessageModel)
        .join(MessageModel, MessageModel.domain_id == DomainModel.id)
        .order_by(MessageModel.id)
        .execution_options(populate_existing=True)
    )
    return dict(rows.tuples().all())


async def _rewrite(
    session: AsyncSession, model: Model, stage: Stage = Stage.DONORS, **request: object
) -> rewrite_queue.RewriteReport:
    rewriter = QueueRewriter(session, model)  # type: ignore[arg-type]
    return await rewriter.rewrite(RewriteRequest(stage=stage, **request))  # type: ignore[arg-type]


async def _edited_by_hand(session: AsyncSession, letter: MessageModel) -> None:
    """Правка руками, как её пишет маршрут: текст тот же, строка журнала — `review.EDITED`."""
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        target=f"message:{letter.id}",
        details={"действие": review.EDITED, "донор": "b.example.test"},
        stage=Stage.DONORS,
    )
    await session.commit()


@pytest.mark.usefixtures("filled_legal")
class TestWhichLetters:
    async def test_only_template_letters_nobody_touched_are_taken(
        self, session: AsyncSession
    ) -> None:
        """Шаблонное — да; поправленное человеком, ушедшее и уникализированное — нет."""
        await _donors(session)
        await _build(session, Model(refuse=HOSTS[:3]))
        letters = await _letters(session)
        await _edited_by_hand(session, letters["b.example.test"])
        letters["c.example.test"].status = MessageStatus.SENT
        await session.commit()

        found = await LetterRepository(session).templated(stage=Stage.DONORS)

        assert [row.host for row in found] == ["a.example.test"]
        assert letters["a.example.test"].uniqueness_pct == 0
        assert letters["d.example.test"].uniqueness_pct > 0
        assert await rewrite_queue.templated_count(session, Stage.DONORS) == 1
        assert await rewrite_queue.templated_count(session, Stage.ADVERTISERS) == 0
        assert await rewrite_queue.templated_count(session, Stage.SALES) == 0

    async def test_another_journal_line_about_the_letter_is_not_an_edit(
        self, session: AsyncSession
    ) -> None:
        """«Решено не писать» и прочие строки о письме — не правка: шаблонное остаётся в отборе."""
        await _donors(session, HOSTS[:1])
        await _build(session, refusing_all())
        letter = (await _letters(session))["a.example.test"]
        await AccessRepository(session).record(
            AuditAction.USER_UPDATED,
            target=f"message:{letter.id}",
            details={"действие": "решено не писать"},
        )
        await session.commit()

        assert await rewrite_queue.templated_count(session, Stage.DONORS) == 1

    async def test_sales_letters_are_not_rewritten_here(self, session: AsyncSession) -> None:
        with pytest.raises(SalesNotConnectedError, match="модуль продаж"):
            await _rewrite(session, Model(), Stage.SALES)


@pytest.mark.usefixtures("filled_legal")
class TestRewriting:
    async def test_template_letters_are_rewritten_the_way_the_build_writes(
        self, session: AsyncSession
    ) -> None:
        await _donors(session, HOSTS[:2])
        await _build(session, refusing_all())
        before = await _letters(session)
        bodies = {host: letter.body or "" for host, letter in before.items()}
        model = Model()

        report = await _rewrite(session, model)

        after = await _letters(session)
        assert (report.found, report.rewritten, report.left, report.remaining) == (2, 2, {}, 0)
        assert report.tokens_spent == 240
        for host, letter in after.items():
            assert (letter.body or "").startswith(GREETING)
            assert letter.uniqueness_pct is not None
            assert letter.uniqueness_pct > 0
            # Неизменяемые зоны и тема — прежние: модель видит только переписываемые.
            assert (letter.body or "").split("\n\n")[1:] == bodies[host].split("\n\n")[1:]
            assert letter.status is MessageStatus.QUEUED
        spent = await session.scalar(
            select(func.sum(UsageRecordModel.units)).where(
                UsageRecordModel.operation == rewrite_queue.OPERATION
            )
        )
        assert spent == 240
        assert [about.host for about in model.seen] == list(HOSTS[:2])
        assert all(about.names == (outreach_cfg.SENDER_NAME,) for about in model.seen)

    async def test_donor_is_described_as_the_build_saw_it(self, session: AsyncSession) -> None:
        """Страна и ниша — из прогона, где донора приняли, цитата сайта — от судьи."""
        domain = await make_donor(session, "a.example.test", email="info@a.example.test")
        domain.judge_quote = "Ремонт квартир своими руками"
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
            country="DE",
            keywords=["ремонт"],
            candidates={"found_by": {"a.example.test": ["ремонт", "отделка"]}},
        )
        session.add(run)
        await session.flush()
        session.add(RunCandidateModel(run_id=run.id, domain_id=domain.id, status="accepted"))
        await _build(session, refusing_all())
        model = Model()

        await _rewrite(session, model)

        [about] = model.seen
        assert (about.country, about.niche, about.about) == (
            "de",
            ("ремонт", "отделка"),
            "Ремонт квартир своими руками",
        )

    async def test_no_credits_again_leaves_letters_as_they_were_and_stops(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Денег всё ещё нет: письмо не портится, итог называет причину, и на остальные письма
        модель вхолостую не зовут."""
        monkeypatch.setattr(outreach_cfg, "BUILD_CONCURRENCY", 1)
        await _donors(session, HOSTS[:2])
        await _build(session, refusing_all())
        before = {host: letter.body for host, letter in (await _letters(session)).items()}
        model = refusing_all()

        report = await _rewrite(session, model)

        after = await _letters(session)
        assert {host: letter.body for host, letter in after.items()} == before
        assert all(letter.uniqueness_pct == 0 for letter in after.values())
        assert (report.rewritten, report.left, report.stopped) == (
            0,
            {NO_CREDITS_WORDS: 1},
            NO_CREDITS_WORDS,
        )
        assert len(model.seen) == 1
        assert report.remaining == 2

    async def test_other_refusal_is_named_and_the_rest_goes_on(self, session: AsyncSession) -> None:
        await _donors(session, HOSTS[:3])
        await _build(session, refusing_all())

        report = await _rewrite(session, Model(refuse=HOSTS[:1], note=DOWN))

        assert (report.rewritten, report.left, report.notes, report.stopped) == (
            2,
            {DOWN: 1},
            {DOWN: 1},
            None,
        )
        assert report.remaining == 1

    async def test_failed_call_does_not_lose_its_neighbours(self, session: AsyncSession) -> None:
        await _donors(session, HOSTS[:2])
        await _build(session, refusing_all())

        with pytest.raises(RuntimeError, match=r"модель легла на a\.example\.test"):
            await _rewrite(session, Model(fail="a.example.test"))

        letters = await _letters(session)
        assert letters["a.example.test"].uniqueness_pct == 0
        assert (letters["b.example.test"].body or "").startswith(GREETING)

    async def test_letter_no_longer_the_template_is_left_without_calling_the_model(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Подпись поменяли после сборки: переписать только зоны значило бы молча поменять и её."""
        await _donors(session, HOSTS[:1])
        await _build(session, refusing_all())
        monkeypatch.setattr(outreach_cfg, "SENDER_NAME", "Maria Lo")
        model = Model()

        report = await _rewrite(session, model)

        assert (report.rewritten, report.left) == (0, {rewrite_queue.DIVERGED: 1})
        assert model.seen == []

    async def test_letter_taken_by_a_batch_meanwhile_is_not_overwritten(
        self, session: AsyncSession
    ) -> None:
        await _donors(session, HOSTS[:1])
        await _build(session, refusing_all())
        letter = (await _letters(session))["a.example.test"]
        body = letter.body

        async def batch_takes_it() -> None:
            await session.execute(
                update(MessageModel)
                .where(MessageModel.id == letter.id)
                .values(status=MessageStatus.SENDING)
            )

        report = await _rewrite(session, Model(then=batch_takes_it))

        assert (report.rewritten, report.left) == (0, {rewrite_queue.GONE: 1})
        again = (await _letters(session))["a.example.test"]
        assert (again.body, again.uniqueness_pct) == (body, 0)

    async def test_the_model_cap_stops_before_the_pack(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _donors(session, HOSTS[:1])
        await _build(session, refusing_all())
        usage.record(session, operation=rewrite_queue.OPERATION, units=500)
        await session.commit()
        monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 100)
        monkeypatch.setattr(llm_cfg, "RUN_TOKEN_CAP", 0)
        model = Model()

        report = await _rewrite(session, model)

        assert model.seen == []
        assert report.rewritten == 0
        assert (report.stopped or "").startswith("потолок расхода на модель за день достигнут")


@pytest.mark.usefixtures("filled_legal")
class TestAdvertisers:
    async def test_offer_is_rewritten_under_its_link(self, session: AsyncSession) -> None:
        """Ссылка — в виде сборки (площадка строчными), оффер с ней — прежний."""
        await priced_donor(session)
        await make_advertiser(session, SHOP, donor_host="Donor.Example.test")
        await build_offers(session, Model(refuse=(SHOP,)))
        await session.commit()
        model = Model()

        report = await _rewrite(session, model, Stage.ADVERTISERS)

        [letter] = (await _letters(session)).values()
        assert (report.found, report.rewritten) == (1, 1)
        assert (letter.body or "").startswith(GREETING)
        assert PAGE in (letter.body or "")
        assert ANCHOR in (letter.body or "")
        [about] = model.seen
        assert (about.host, about.country, about.niche, about.about) == (
            SHOP,
            "us",
            (),
            None,
        )

    async def test_removed_advertiser_is_left_with_words(self, session: AsyncSession) -> None:
        await priced_donor(session)
        domain = await make_advertiser(session, SHOP)
        await build_offers(session, Model(refuse=(SHOP,)))
        await session.execute(
            update(AdvertiserModel)
            .where(AdvertiserModel.domain_id == domain.id)
            .values(best_page_url=None)
        )
        await session.commit()

        report = await _rewrite(session, Model(), Stage.ADVERTISERS)

        assert (report.rewritten, report.left) == (0, {rewrite_queue.NO_LINK: 1})


@pytest.mark.usefixtures("filled_legal")
class TestNicheBusinesses:
    async def test_niche_offer_is_rewritten_under_its_example(self, session: AsyncSession) -> None:
        await priced_donor(session, NICHE_DONOR)
        await _business(session)
        await build_offers(session, Model(refuse=(BOOKIE,)), audience=NICHE, campaign_name="Ниша")
        await session.commit()

        report = await _rewrite(session, Model(), Stage.ADVERTISERS, audience=NICHE)

        [letter] = (await _letters(session)).values()
        assert (report.found, report.rewritten) == (1, 1)
        body = letter.body or ""
        assert body.startswith(GREETING)
        assert NICHE_DONOR in body
        assert "sports betting" in body

    async def test_offer_that_can_no_longer_be_built_is_left_with_words(
        self, session: AsyncSession
    ) -> None:
        """Цена примера площадки протухла: оффера сейчас нет — письмо как было, с причиной."""
        donor = await priced_donor(session, NICHE_DONOR)
        await _business(session)
        await build_offers(session, Model(refuse=(BOOKIE,)), audience=NICHE, campaign_name="Ниша")
        await session.execute(
            update(DonorModel)
            .where(DonorModel.domain_id == donor.id)
            .values(last_price_at=datetime(2020, 1, 1, tzinfo=UTC))
        )
        await session.commit()

        report = await _rewrite(session, Model(), Stage.ADVERTISERS, audience=NICHE)

        assert (report.rewritten, report.left) == (0, {rewrite_queue.NO_OFFER: 1})
