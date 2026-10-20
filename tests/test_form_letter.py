"""Текст письма для формы донора — тот же, что ушёл бы ему письмом, на настоящей базе.

Модель подменена: проверяется, что ей уходит то же, что у сборки писем (сайт, страна
прогона, ключи, по которым он нашёлся, цитата сайта), и что из ответа собирается то же
письмо — шаблон рассылки, подстановки, проверка на метрики, — и что текст хранится и
расход записан. Ревизия схемы гоняется вниз и вверх здесь же.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.contacts.forms import UnknownFormError
from backend.features.core import usage
from backend.features.core.domain import ContactStatus, DonorStatus, RunStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.form_letter import FormLetterModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import CampaignModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.donors.verdict import Thresholds
from backend.features.letters import form_letter
from backend.features.letters.draft import default_draft, to_text
from backend.features.letters.rewrite import RewriteResult
from backend.features.runs.repository import RunRepository
from backend.workers import contact_jobs
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.migration_helpers import columns_down_and_up

pytestmark = pytest.mark.asyncio

HOST = "form.example.test"


class FakeRewriter:
    """Модель-заглушка: переписывает приветствие и запоминает, что ей сказали о сайте."""

    def __init__(self, *, zones: dict[str, str] | None = None, tokens: int = 150) -> None:
        self.zones = {"greeting": "Good afternoon,"} if zones is None else zones
        self.tokens = tokens
        self.about: list[Any] = []

    async def rewrite(self, _rendered: object, about: object) -> RewriteResult:
        self.about.append(about)
        return RewriteResult(zones=dict(self.zones), tokens_spent=self.tokens)

    async def aclose(self) -> None:
        return None


async def _form_donor(
    session: AsyncSession, *, quote: str | None = "Новости малого бизнеса"
) -> int:
    domain = DomainModel(host=HOST, judge_quote=quote)
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=DonorStatus.SUITABLE,
        dr=50,
        review="accepted",
        contact_status=ContactStatus.FORM_ONLY,
    )
    session.add(donor)
    await session.flush()
    return int(donor.id)


async def _accepted_in_run(session: AsyncSession, donor_id: int, country: str) -> int:
    donor = await session.get(DonorModel, donor_id)
    assert donor is not None
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
        country=country,
        keywords=["малый бизнес"],
        candidates={"found_by": {HOST: ["малый бизнес", "налоги ип"]}},
    )
    session.add(run)
    await session.flush()
    session.add(RunCandidateModel(run_id=run.id, domain_id=donor.domain_id, status="accepted"))
    await session.flush()
    return int(run.id)


async def test_letter_is_the_donor_first_letter_and_is_kept(
    session: AsyncSession, filled_legal: None
) -> None:
    donor_id = await _form_donor(session)
    await _accepted_in_run(session, donor_id, "DE")
    rewriter = FakeRewriter()

    letter = await form_letter.build(session, donor_id, rewriter=rewriter, by="оператор")  # type: ignore[arg-type]

    [about] = rewriter.about
    assert about.host == HOST
    assert about.country == "de"
    assert about.niche == ("малый бизнес", "налоги ип")
    assert about.about == "Новости малого бизнеса"
    assert letter.subject == f"Guest article on {HOST}"
    assert letter.body.startswith("Good afternoon,")
    assert "I run content partnerships" in letter.body  # неизменяемая зона шаблона
    assert letter.based_on == form_letter.DEFAULT_TEXT
    assert letter.tokens == 150
    assert letter.built_by == "оператор"
    spent = await session.scalar(
        select(UsageRecordModel.units).where(UsageRecordModel.operation == form_letter.OPERATION)
    )
    assert spent == 150


async def test_text_of_the_latest_donor_campaign_is_used(
    session: AsyncSession, filled_legal: None
) -> None:
    draft = default_draft()
    zones = {zone.name: zone.text for zone in draft.zones}
    zones["offer"] = "We place stories for a few travel brands and pay per article."
    session.add(CampaignModel(stage=Stage.DONORS, name="старая", letter_template=None))
    session.add(
        CampaignModel(
            stage=Stage.DONORS,
            name="осень",
            letter_template=to_text("Ask about {{host}}", zones),
        )
    )
    session.add(CampaignModel(stage=Stage.DONORS, name="price_recheck", letter_template=None))
    donor_id = await _form_donor(session)

    letter = await form_letter.build(session, donor_id, rewriter=FakeRewriter())  # type: ignore[arg-type]

    assert letter.based_on == "текст рассылки «осень»"
    assert letter.subject == f"Ask about {HOST}"
    assert "travel brands" in letter.body


async def test_campaign_of_the_donor_run_wins_over_a_later_one(
    session: AsyncSession, filled_legal: None
) -> None:
    """Текст — рассылки прогона, где донора приняли: у последней чужой рассылки другой язык
    (ревью 20.10.2026), и в форму немецкого сайта ушёл бы английский текст."""
    zones = {zone.name: zone.text for zone in default_draft().zones}
    donor_id = await _form_donor(session)
    run_id = await _accepted_in_run(session, donor_id, "DE")
    session.add(
        CampaignModel(
            stage=Stage.DONORS,
            name="Германия",
            run_id=run_id,
            letter_template=to_text("Frage zu {{host}}", zones),
        )
    )
    await session.flush()
    session.add(
        CampaignModel(
            stage=Stage.DONORS, name="позже, другой прогон", letter_template=to_text("Hi", zones)
        )
    )
    await session.flush()

    letter = await form_letter.build(session, donor_id, rewriter=FakeRewriter())  # type: ignore[arg-type]

    assert letter.based_on == "текст рассылки «Германия»"
    assert letter.subject == f"Frage zu {HOST}"


async def test_building_again_replaces_the_text(session: AsyncSession, filled_legal: None) -> None:
    donor_id = await _form_donor(session)
    await form_letter.build(session, donor_id, rewriter=FakeRewriter())  # type: ignore[arg-type]

    again = await form_letter.build(
        session,
        donor_id,
        rewriter=FakeRewriter(zones={"greeting": "Hello there,"}, tokens=90),  # type: ignore[arg-type]
    )

    count = await session.scalar(select(func.count(FormLetterModel.id)))
    assert count == 1
    assert again.body.startswith("Hello there,")
    assert again.tokens == 90


async def test_refused_model_leaves_the_template_with_a_note(
    session: AsyncSession, filled_legal: None
) -> None:
    class Refusing(FakeRewriter):
        async def rewrite(self, _rendered: object, about: object) -> RewriteResult:
            return RewriteResult(notes=["ключ модели не задан"])

    donor_id = await _form_donor(session, quote=None)

    letter = await form_letter.build(session, donor_id, rewriter=Refusing())  # type: ignore[arg-type]

    assert letter.body.startswith("Hi,")
    assert letter.notes == "ключ модели не задан"
    assert letter.tokens == 0


async def test_only_a_donor_from_the_form_queue(session: AsyncSession) -> None:
    donor_id = await _form_donor(session)
    donor = await session.get(DonorModel, donor_id)
    assert donor is not None
    donor.contact_status = ContactStatus.NOT_FOUND
    await session.flush()

    with pytest.raises(UnknownFormError):
        await form_letter.build(session, donor_id, rewriter=FakeRewriter())  # type: ignore[arg-type]


async def test_model_spending_cap_is_checked_first(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def capped(_session: AsyncSession, **_kw: object) -> None:
        raise usage.LlmCapExceededError("потолок расхода на модель за день исчерпан")

    monkeypatch.setattr(usage, "ensure_llm_within_cap", capped)
    rewriter = FakeRewriter()
    donor_id = await _form_donor(session)

    with pytest.raises(usage.LlmCapExceededError):
        await form_letter.build(session, donor_id, rewriter=rewriter)  # type: ignore[arg-type]
    assert rewriter.about == []


class _Closable:
    async def dispose(self) -> None:
        return None


async def test_job_body_builds_and_commits(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, filled_legal: None
) -> None:
    donor_id = await _form_donor(session)
    await session.commit()
    monkeypatch.setattr(contact_jobs, "RewriteClient", FakeRewriter)
    monkeypatch.setattr(contact_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        contact_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )

    report = await contact_jobs._form_letter(donor_id, "оператор")

    assert report == {"donor_id": donor_id, "tokens": 150, "based_on": form_letter.DEFAULT_TEXT}


async def test_revision_goes_down_and_up(session: AsyncSession) -> None:
    def shift(connection: Connection) -> tuple[set[str], set[str], bool]:
        down, up = columns_down_and_up(
            connection,
            "a7c3e9f15b20_contact_forms_and_deeper.py",
            "donors",
            {"contact_deeper_at", "contact_deeper_note"},
        )
        table = connection.execute(text("SELECT to_regclass('form_letters') IS NOT NULL"))
        return down, up, bool(table.scalar())

    down, up, table = await (await session.connection()).run_sync(shift)

    assert down == set()
    assert up == {"contact_deeper_at", "contact_deeper_note"}
    assert table
