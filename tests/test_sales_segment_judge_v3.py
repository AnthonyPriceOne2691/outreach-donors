"""Судья сегмента с v3: общие слова промпта, вид сайта в ответе, строка «Текст главной», кэш
прежней версии, вердикт языка в проходе прогона и язык лида. Правила v4 (факты и таблица кода) —
`test_sales_segment_judge_v4.py`.

Модель — подставным транспортом, сайты и тексты выдуманы (`*-sample.*`), ниши и письменности
разные.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import httpx
import pytest
from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.contacts.mx import MailRoute
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.donors.home_signals import HomeSignals
from backend.features.sales import cleaning, segment_leads
from backend.features.sales import segment_judge as sj
from backend.features.sales.models import SalesLeadModel
from backend.features.sales.segment_facts import SiteFacts
from backend.features.sales.segment_judge import SiteText
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    SalesSegmentVerdictModel,
)
from backend.features.sales.segment_text import excerpt
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.segment_facts_fakes import facts
from tests.test_sales_segment_judge import MODEL_KEY, judged
from tests.test_sales_segment_language import RU, UK
from tests.test_sales_segment_run import Model, hypothesis, make_run

Candidate = SalesSegmentCandidateModel

#: Слова, по которым угадывались бы ниша или страна живого прогона, — промпту общие слова.
NICHE_WORDS = ("carrier", "logistic", "freight", "cargo", "shipping", "kazakh", "courier")


def test_the_prompt_has_no_words_of_one_niche_or_country() -> None:
    """Правила — общие для любой ниши и страны: ни одного слова отдельной ниши. Прежнее правило о
    стране и языке из v2 снято: оно и резало сайты без страны в тексте."""
    prompt = sj.load_prompt()

    assert sj.PROMPT_VERSION == "sales-segment-v5"
    assert "matter only when the segment names them" not in prompt
    assert [word for word in NICHE_WORDS if word in prompt.lower()] == []


def test_the_answer_names_the_kind_of_the_site_from_a_closed_list() -> None:
    prompt = sj.load_prompt()

    for kind in sj.KINDS:
        assert f'- "{kind}": ' in prompt


QUOTE = "Велосипеды и запчасти"


@pytest.mark.parametrize(
    ("kind", "lane", "found"),
    [
        ("intermediary", Lane.REJECT, "intermediary"),
        (" OWN ", Lane.ACCEPT, "own"),
        ("broker", Lane.REVIEW, None),
        (None, Lane.REVIEW, None),
    ],
    ids=["intermediary", "spaces-and-case", "not-in-the-list", "absent"],
)
async def test_the_kind_comes_only_from_the_list_and_an_unasked_intermediary_is_no(
    kind: str | None, lane: Lane, found: str | None
) -> None:
    """С v4 вид сайта — факт, по которому решает код: посредник, которого сегмент не просит, —
    «нет»; вид не из списка или без него — ответ не разобран, «на проверку»."""
    body: dict[str, Any] = facts("yes", QUOTE, "магазин")
    if kind is None:
        del body["kind"]
    else:
        body["kind"] = kind

    verdict, _ = await judged(body)

    assert (verdict.lane, verdict.kind) == (lane, found)


# --- строка «Текст главной» -------------------------------------------------------------------

TEXT = "Велодом · Продаём горные и городские велосипеды · Доставка по стране и сборка бесплатно"
HOME = HomeSignals(
    reached=True, title="Велодом", description="Магазин велосипедов", nav=("Каталог",), text=TEXT
)


def test_the_home_text_is_the_last_line_capped_at_a_word(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sales_cfg, "SEGMENT_HOME_TEXT_CHARS", 40)

    home = replace(HOME, text=f"  {TEXT}  ")

    site = sj.site_text(title="Велодом", description=None, home=home)

    label, words = site.lines[-1]
    assert label == sj.HOME_TEXT
    assert words == "Велодом · Продаём горные и городские"
    assert len(words) <= 40
    assert SiteText.parse(site.for_model()) == site


def test_without_the_home_page_or_with_the_setting_at_zero_there_is_no_home_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = HomeSignals(reached=False, error="закрылась", text=TEXT)
    assert sj.HOME_TEXT not in dict(
        sj.site_text(title="Велодом", description=None, home=closed).lines
    )
    monkeypatch.setattr(sales_cfg, "SEGMENT_HOME_TEXT_CHARS", 0)
    assert sj.HOME_TEXT not in dict(
        sj.site_text(title="Велодом", description=None, home=HOME).lines
    )


def test_unspaced_text_is_cut_by_the_sign() -> None:
    assert excerpt("家具のオンラインショップです", 5) == "家具のオン"


def test_a_quote_from_the_home_text_holds_and_its_label_is_ours() -> None:
    site = sj.site_text(title="Велодом", description=None, home=HOME)

    assert site.holds("Продаём горные и городские велосипеды")
    assert sj.HOME_TEXT in sj.LABELS


# --- кэш: вердикт v2 не отдаётся v3 ---------------------------------------------------------------

SEGMENT = "Мастерские ремонта велосипедов: сами чинят велосипеды, на своём сайте — услуги и запись."
UK_HOST = "velo-maisternia-sample.com"
RU_HOST = "velo-masterskaya-sample.com"
ANSWER = facts("yes", RU[:60].rsplit(" ", 1)[0], "мастерская")


async def _candidate(
    session: AsyncSession, run: SalesSegmentRunModel, host: str, text: str
) -> Candidate:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    made = Candidate(
        run_id=run.id, hypothesis_id=run.hypothesis_id, domain_id=domain.id, lane=Lane.PENDING,
        page_title=text[:60], page_description=text,
    )  # fmt: skip
    session.add(made)
    await session.commit()
    return made


def _judge(model: Model) -> sj.SegmentClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(model))
    return sj.SegmentClient(http, model="gpt-5-mini", api_key=MODEL_KEY)


async def test_a_v2_verdict_is_not_served_to_the_v4_judge(session: AsyncSession) -> None:
    owner = await hypothesis(session, SEGMENT)
    run = await make_run(session, owner)
    site = await _candidate(session, run, RU_HOST, RU)
    session.add(
        SalesSegmentVerdictModel(
            domain_id=site.domain_id, hypothesis_id=owner.id, prompt_version="sales-segment-v2",
            segment_digest=sj.segment_digest(SEGMENT), verdict=Lane.REJECT, quote="старая цитата",
            reason="страна не указана", model="gpt-5-mini", decided_by=Decider.MODEL, tokens=600,
        )
    )  # fmt: skip
    await session.commit()
    model = Model({RU_HOST: ANSWER})

    tally = await judge_run(
        session, run_id=run.id, segment=SEGMENT, mode=JudgeMode.ENFORCE, client=_judge(model),
        home=None,
    )  # fmt: skip

    assert model.asked == [RU_HOST]
    assert (tally.from_cache, tally.judged) == (0, 1)
    assert site.lane == Lane.ACCEPT


# --- язык в проходе прогона и у лида ----------------------------------------------------------------


async def test_the_run_pass_gives_a_language_verdict_to_a_site_in_another_language(
    session: AsyncSession,
) -> None:
    """Прогон с письмами на русском: украинский сайт — «отказал бы» словами без модели, в
    наблюдении — в полосе «судья отказал бы»; русский — модели. В кэш вердиктов язык не ложится."""
    owner = await hypothesis(session, SEGMENT)
    run = await make_run(session, owner, mode=JudgeMode.SHADOW)
    run.language = "ru"
    await session.commit()
    uk = await _candidate(session, run, UK_HOST, UK)
    ru = await _candidate(session, run, RU_HOST, RU)
    model = Model({RU_HOST: ANSWER})

    tally = await judge_run(
        session, run_id=run.id, segment=SEGMENT, mode=JudgeMode.SHADOW, client=_judge(model),
        home=None, letters=run.language,
    )  # fmt: skip

    assert model.asked == [RU_HOST]
    assert (uk.lane, uk.verdict, uk.decided_by) == (
        Lane.WOULD_REJECT,
        Lane.REJECT,
        Decider.LANGUAGE,
    )
    assert uk.reason == "язык сайта — украинский, письма — русский"
    assert ru.lane == Lane.ACCEPT
    assert (tally.would_cut, tally.by_rule, tally.judged) == (1, 0, 2)
    kept = await session.scalars(select(SalesSegmentVerdictModel.domain_id))
    assert list(kept.all()) == [ru.domain_id]


async def test_a_cached_verdict_does_not_skip_the_language_of_this_run(
    session: AsyncSession,
) -> None:
    """Кэш — вердикт модели гипотезы, а язык писем у прогонов разный: вердикт из кэша по сайту на
    другом языке ложится как «отказал бы» словами, модель не зовётся."""
    owner = await hypothesis(session, SEGMENT)
    run = await make_run(session, owner, mode=JudgeMode.SHADOW)
    site = await _candidate(session, run, UK_HOST, UK)
    session.add(
        SalesSegmentVerdictModel(
            domain_id=site.domain_id, hypothesis_id=owner.id, prompt_version=sj.PROMPT_VERSION,
            segment_digest=sj.segment_digest(SEGMENT), verdict=Lane.ACCEPT, quote=UK[:40],
            reason="майстерня", model="gpt-5-mini", decided_by=Decider.MODEL, tokens=600,
            facts=SiteFacts("own", "yes", UK[:40], "майстерня").as_json(),
        )
    )  # fmt: skip
    await session.commit()
    model = Model({})

    tally = await judge_run(
        session, run_id=run.id, segment=SEGMENT, mode=JudgeMode.SHADOW, client=_judge(model),
        home=None, letters="ru",
    )  # fmt: skip

    assert model.asked == []
    assert (site.lane, site.decided_by) == (Lane.WOULD_REJECT, Decider.LANGUAGE)
    assert site.reason == "язык сайта — украинский, письма — русский"
    assert (tally.from_cache, tally.would_cut) == (1, 1)


@pytest.mark.parametrize(
    ("text", "language"),
    [
        (UK, "uk"),
        (RU, "ru"),
        (f"{UK} Мова сайту: UA · RU", "ru"),
    ],
    ids=["site-in-another-language", "same-language", "switcher-to-the-letters-language"],
)
async def test_a_lead_gets_the_site_language_when_the_site_is_surely_in_another_one(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, text: str, language: str
) -> None:
    """Сайт принят человеком из «отказал бы» по языку: письмо ему — на языке сайта, а не прогона;
    цепочки на нём нет — лид ждёт с причиной, а не уходит письмом не на том языке."""

    async def route(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.MX

    monkeypatch.setattr(cleaning, "mail_route", route)
    owner = await hypothesis(session, SEGMENT)
    run = await make_run(session, owner)
    run.language = "ru"
    site = await _candidate(session, run, UK_HOST, text)
    stored = SiteText(((sj.PAGE_TITLE, text[:60]), (sj.HOME_TEXT, text))).for_model()
    site.lane, site.site_text, site.decided_by = Lane.ACCEPT, stored, Decider.HUMAN
    session.add(ContactModel(domain_id=site.domain_id, email=f"hello@{UK_HOST}", source="page"))
    await session.commit()

    made = await segment_leads.make_leads(session, run, FixtureVerifier())

    assert made["created"] == 1
    [lead] = (await session.scalars(select(SalesLeadModel))).all()
    assert lead.language == language
    assert segment_leads.lead_language(run, site) == language
