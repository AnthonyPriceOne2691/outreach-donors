"""Судья сегмента v3: правила промпта дословно, вид сайта в ответе, строка «Текст главной», кэш
прежней версии, вердикт языка в проходе прогона и язык лида.

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
from tests.test_sales_segment_judge import MODEL_KEY, judged
from tests.test_sales_segment_language import RU, UK
from tests.test_sales_segment_run import Model, hypothesis, make_run

Candidate = SalesSegmentCandidateModel

#: Правила v3 — дословно: правка слов промпта — новая версия и новый замер eval.
UNSEEN = (
    "- The core of the segment is the kind of business it describes: what the business does "
    'itself. The kind of the site itself is never "not visible": a site whose text does not show '
    'it to be of that kind is not a "yes". Other features the segment names (the country, region '
    "or city where the business "
    "works, the language of the site, the size of the business, the industry or the customers it "
    "serves) count only when the website text shows them. A named feature that the text does not "
    'show is "not visible", and it is never a reason for "no" or "unsure": when the core is shown '
    'and nothing contradicts the segment, answer "yes" and say in "why" what is not visible.'
)
CONTRADICTION = (
    "- A contradiction is only a direct statement that excludes what the segment asks for, such "
    'as "we work only in" a place outside the segment\'s region, or a different kind of activity. '
    "Other countries, cities or languages named in addition to the segment's ones are not a "
    "contradiction."
)
LANGUAGE = (
    "- Do not judge by the language the website text is written in: our code compares it with "
    "the language of our letters."
)
INTERMEDIARY = (
    '- Intermediaries are "no" in any field of business, unless the segment itself asks for '
    'directories, marketplaces or the like; a segment written loosely as "sites about ..." a kind '
    "of business does not ask for them. An intermediary is a marketplace, an exchange, a catalogue "
    "or directory, an aggregator, a classifieds "
    "board, a price comparison or a matching service. Signs of an intermediary in the text: many "
    "other companies, specialists or their ads; an offer to post a request that other companies "
    "answer, or to find a contractor, supplier or specialist for the customer; a rating or a "
    "comparison of companies or their prices; a calculator or an online assistant that picks among "
    "the offers of other companies. A business's own order form, own price calculator, own "
    "branches or own team are not signs of an intermediary."
)
FOREIGN = (
    "- When the segment names a country, region or city where the business works, and the "
    "website text shows only other countries, regions or cities where the business works (none "
    'of the segment\'s) without saying that it works only there, answer "unsure": a human '
    'decides. This is neither "not visible" ("yes") nor a contradiction ("no").'
)
UNSURE = (
    '- Answer "unsure" only when the text does not show what kind of site it is, or when it '
    "shows only places outside the segment's region (the rule above). A country, region, city, "
    'language, size or industry that the text does not show is not a reason for "unsure".'
)
#: Слова, по которым угадывались бы ниша или страна живого прогона, — промпту общие слова.
NICHE_WORDS = ("carrier", "logistic", "freight", "cargo", "shipping", "kazakh", "courier")


def test_the_v3_prompt_has_its_rules_verbatim_and_a_new_version() -> None:
    lines = sj.load_prompt().splitlines()

    assert sj.PROMPT_VERSION == "sales-segment-v3"
    for rule in (UNSEEN, CONTRADICTION, FOREIGN, LANGUAGE, INTERMEDIARY, UNSURE):
        assert lines.count(rule) == 1, rule[:40]
    # Чужая география без «только» — сразу за противоречием: «только там» — по-прежнему «нет».
    assert lines.index(FOREIGN) == lines.index(CONTRADICTION) + 1
    # «не понять» — после правил вида: последним словом о нём говорит правило v3, а не v1.
    assert lines.index(UNSURE) > lines.index(INTERMEDIARY)
    # Прежнее правило о стране и языке из v2 снято: оно и резало сайты без страны в тексте.
    assert not [line for line in lines if "matter only when the segment names them" in line]
    # Правила — общие для любой ниши и страны: ни одного слова отдельной ниши.
    prompt = sj.load_prompt().lower()
    assert [word for word in NICHE_WORDS if word in prompt] == []


#: Вид «посредник» и «издание» подходит только сегменту, который сам их просит: «сайты о …» — не просьба.
KIND_FIT = (
    'A site of the kind "intermediary" or "media" fits only a segment that itself asks for '
    'directories, marketplaces, media or the like; a segment written loosely as "sites about ..." '
    'or "sites related to ..." a kind of business does not ask for them.'
)


def test_the_answer_names_the_kind_of_the_site_from_a_closed_list() -> None:
    prompt = sj.load_prompt()
    assert prompt.splitlines().count(KIND_FIT) == 1

    assert '- "kind": "own", "intermediary", "media" or "unclear";' in prompt
    for kind in sj.KINDS:
        assert f'- "{kind}": ' in prompt


QUOTE = "Велосипеды и запчасти"


@pytest.mark.parametrize(
    ("kind", "found"),
    [("intermediary", "intermediary"), (" OWN ", "own"), ("broker", None), (None, None)],
    ids=["intermediary", "spaces-and-case", "not-in-the-list", "absent"],
)
async def test_the_kind_is_kept_only_from_the_list_and_never_decides_the_lane(
    kind: str | None, found: str | None
) -> None:
    body: dict[str, Any] = {"fit": "yes", "quote": QUOTE, "why": "магазин"}
    if kind is not None:
        body["kind"] = kind

    verdict, _ = await judged(body)

    assert (verdict.lane, verdict.kind) == (Lane.ACCEPT, found)


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
ANSWER = {"kind": "own", "fit": "yes", "quote": RU[:60].rsplit(" ", 1)[0], "why": "мастерская"}


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


async def test_a_v2_verdict_is_not_served_to_the_v3_judge(session: AsyncSession) -> None:
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
    наблюдении — человеку; русский — модели. В кэш вердиктов язык не ложится."""
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
    assert (uk.lane, uk.verdict, uk.decided_by) == (Lane.REVIEW, Lane.REJECT, Decider.LANGUAGE)
    assert uk.reason == f"{sj.SHADOW_CUT}язык сайта — украинский, письма — русский"
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
        )
    )  # fmt: skip
    await session.commit()
    model = Model({})

    tally = await judge_run(
        session, run_id=run.id, segment=SEGMENT, mode=JudgeMode.SHADOW, client=_judge(model),
        home=None, letters="ru",
    )  # fmt: skip

    assert model.asked == []
    assert (site.lane, site.decided_by) == (Lane.REVIEW, Decider.LANGUAGE)
    assert site.reason == f"{sj.SHADOW_CUT}язык сайта — украинский, письма — русский"
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
