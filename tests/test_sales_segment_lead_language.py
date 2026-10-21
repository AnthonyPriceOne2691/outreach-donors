"""Язык писем лиду из сайта сбора (`segment_leads.lead_language`): язык сайта, названный буквами;
не назван — язык письменности сайта из таблицы (`segment_language.LETTERS_BY_SCRIPT`: латиница —
английский); строки нет — язык писем прогона.

Полоса судьи не меняется: сайт на латинице в прогоне на кириллице по-прежнему «не понять по буквам»
и ждёт человека. Меняются язык писем лиду после «Принять» и слова вердикта — они его называют («…;
примете — английский»), и это тот же язык, что получит лид. Правило одно для любого языка прогона на
другой письменности: кириллица (русский, украинский, казахский), греческое, арабское письмо,
японское; у прогона на латинице оно молчит.

Тексты выдуманы, ниши разные — мастерская велосипедов, стоматология, мебельная мастерская;
письменности тоже: латиница (английский, немецкий, польский), кириллица, арабское письмо (арабский и
урду), иероглифы, деванагари, японское. Модель — подставным транспортом.
"""

from __future__ import annotations

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.contacts.mx import MailRoute
from backend.features.core.domain import ContactSource
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.sales import cleaning, segment_leads, segment_report, segment_undo
from backend.features.sales import segment_judge as sj
from backend.features.sales import segment_language as sl
from backend.features.sales.models import SalesLeadModel
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.segment_facts_fakes import facts
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_language import AR, DE, DENTAL, EN, JA, PL, RU, UK, site
from tests.test_sales_segment_run import Model, hypothesis, make_run

Candidate = SalesSegmentCandidateModel

#: Третья ниша — мебельная мастерская: латиница без своих букв и арабское письмо без своих букв
#: (урду: у него есть буквы, которых нет ни у арабского, ни у персидского, — язык не назван).
FURNITURE_EN = (
    "Our furniture workshop makes tables, chairs and wardrobes from solid oak and walnut. Every "
    "piece is built to your measurements and finished by hand with natural oil. Order online or "
    "visit our showroom to see the wood and talk to the maker. Most orders are ready in three "
    "weeks, and every joint has a two year warranty."
)
FURNITURE_UR = (
    "ہماری فرنیچر ورکشاپ ٹھوس لکڑی سے میزیں، کرسیاں اور الماریاں بناتی ہے۔ ہر چیز آپ کے ناپ کے "
    "مطابق تیار ہوتی ہے اور ہاتھ سے قدرتی تیل سے چمکائی جاتی ہے۔ ویب سائٹ پر آرڈر دیں یا ہمارے "
    "شوروم میں تشریف لائیں۔ زیادہ تر آرڈر تین ہفتوں میں تیار ہو جاتے ہیں اور ہر جوڑ پر دو سال کی "
    "ضمانت ہے۔"
)


def stored(text: str, menu: str | None = None) -> str:
    """Текст сайта, как его хранит кандидат после судьи: заголовок выдачи, меню и текст главной."""
    lines = [(sj.PAGE_TITLE, text[:60]), (sj.HOME_TEXT, text)]
    if menu is not None:
        lines.insert(1, (sj.HOME_MENU, menu))
    return sj.SiteText(tuple(lines)).for_model()


def letters_of(text: str, letters: str, menu: str | None = None) -> str:
    """Язык писем лиду сайта с этим текстом в прогоне с языком писем `letters`."""
    run = SalesSegmentRunModel(language=letters)
    return segment_leads.lead_language(run, Candidate(site_text=stored(text, menu)))


# --- письменность сайта — полем вердикта языка, таблица — данными ---------------------------------


@pytest.mark.parametrize(
    ("text", "letters", "script", "then"),
    [
        (EN, "ru", "LATIN", "en"),
        (FURNITURE_EN, "kk", "LATIN", "en"),
        (FURNITURE_UR, "ru", "ARABIC", ""),
        (DENTAL["zh"], "en", "HAN", ""),
        (UK, "ru", "CYRILLIC", ""),
        (PL, "en", "LATIN", ""),
        (PL, "ru", "LATIN", ""),
        (AR, "en", "ARABIC", ""),
        (JA, "ru", "KANA", ""),
        (DENTAL["el"], "ru", "GREEK", ""),
    ],
    ids=["latin-unnamed", "latin-unnamed-kk", "arabic-unnamed", "han-unnamed", "cyrillic-named",
         "latin-named", "latin-named-in-a-cyrillic-run", "arabic-named", "kana-named",
         "greek-named"],
)  # fmt: skip
def test_the_mismatch_carries_the_script_of_the_site_and_the_letters_from_the_table(
    text: str, letters: str, script: str, then: str
) -> None:
    """Письменность — кодом в вердикте языка, а не разбором слов «латиница»: у названного языка и у
    неназванного, в той же письменности, что язык писем, и в другой. Язык писем лиду из таблицы
    (`then`) — только у неназванного: польский назван буквами и его не получает, хоть и латиница."""
    found = sl.mismatch(text, letters)

    assert found is not None
    assert (found.script, found.then) == (script, then)


@pytest.mark.parametrize(
    ("script", "letters", "code"),
    [
        ("LATIN", "ru", "en"),
        ("LATIN", "uk", "en"),
        ("LATIN", "kk", "en"),
        ("LATIN", "ru-RU", "en"),
        ("LATIN", "el", "en"),
        ("LATIN", "ar", "en"),
        ("LATIN", "ja", "en"),
        ("LATIN", "en", None),
        ("LATIN", "de", None),
        ("ARABIC", "ru", None),
        ("HAN", "en", None),
        ("DEVANAGARI", "uk", None),
        ("LATIN", "xx", None),
    ],
    ids=["latin-ru", "latin-uk", "latin-kk", "latin-ru-region", "latin-el", "latin-ar", "latin-ja",
         "latin-en-silent", "latin-de-silent", "arabic-no-row", "han-no-row", "devanagari-no-row",
         "unknown-letters"],
)  # fmt: skip
def test_the_letters_of_an_unnamed_site_come_from_the_table_by_its_script(
    script: str, letters: str, code: str | None
) -> None:
    """Латиница — английский для любого языка писем на другой письменности; у языка писем на
    латинице правило молчит (сайт своей письменности проверка и так не режет); письменность без
    строки и незнакомый язык писем — `None`: язык прогона."""
    assert sl.letters_by_script(script, letters) == code


def test_each_row_of_the_table_is_a_language_written_in_that_script() -> None:
    """Строка таблицы — язык этой письменности: «латиница → русский» был бы ошибкой данных."""
    assert sl.LETTERS_BY_SCRIPT
    for script, code in sl.LETTERS_BY_SCRIPT.items():
        assert sl.BY_CODE[code].script == script


# --- язык писем лиду ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "letters"),
    [
        (EN, "ru"),
        (DENTAL["en"], "uk"),
        (FURNITURE_EN, "kk"),
        (EN, "el"),
        (DENTAL["en"], "ar"),
        (FURNITURE_EN, "ja"),
        (DE, "ru"),
    ],
    ids=["bike-ru", "dental-uk", "furniture-kk", "bike-el", "dental-ar", "furniture-ja",
         "german-without-umlauts-ru"],
)  # fmt: skip
def test_a_latin_site_in_a_run_on_another_script_gets_english_letters(
    text: str, letters: str
) -> None:
    """Английский среди латиницы буквами не назвать — письма лиду на английском, а не на языке
    прогона: в прогоне на кириллице, греческом, арабском, японском. Немецкий с редкими умлаутами по
    буквам тоже не назван — и ему английское письмо, а не письмо другой письменностью."""
    assert letters_of(text, letters) == "en"


@pytest.mark.parametrize(
    ("text", "letters", "menu", "language"),
    [
        (RU, "ru", None, "ru"),
        (UK, "ru", None, "uk"),
        (PL, "ru", None, "pl"),
        (AR, "ru", None, "ar"),
        (DENTAL["el"], "uk", None, "el"),
        (EN, "en", None, "en"),
        (DENTAL["en"], "de", None, "de"),
        (FURNITURE_UR, "ru", None, "ru"),
        (DENTAL["zh"], "ru", None, "ru"),
        (DENTAL["hi"], "uk", None, "uk"),
        (f"{RU} {EN}", "ru", None, "ru"),
        (f"{DENTAL['en']} {DENTAL['ru']}", "ru", None, "ru"),
        (FURNITURE_EN, "ru", "Home · Prices · EN · RU", "ru"),
    ],
    ids=["same-language", "named-cyrillic", "named-latin", "named-arabic", "named-by-greek-script",
         "latin-in-an-en-run", "latin-in-a-de-run", "arabic-script-without-a-row",
         "han-without-a-row", "devanagari-without-a-row", "bilingual-ru-en", "bilingual-en-ru",
         "switcher-to-the-letters-language"],
)  # fmt: skip
def test_otherwise_the_lead_language_is_as_before(
    text: str, letters: str, menu: str | None, language: str
) -> None:
    """Язык, названный буквами, — он, и раньше таблицы (польский — польский, хоть и латиница); сайт
    на языке писем, двуязычная страница, переключатель на язык писем, письменность без строки и
    прогон на латинице — язык прогона."""
    assert letters_of(text, letters, menu) == language


# --- слова вердикта называют язык писем лиду ------------------------------------------------------


@pytest.mark.parametrize("letters", ["ru", "uk", "kk"])
def test_the_judge_sends_a_latin_site_to_a_human_and_names_the_letters_after_accept(
    letters: str,
) -> None:
    """Полоса прежняя: язык сайта на латинице не назван — «не понять», к человеку. Слова называют
    язык писем прогона и язык писем лиду после «Принять» — тот, что даст таблица письменностей."""
    verdict = sj.by_language(site(EN), letters)

    assert verdict is not None
    assert (verdict.lane, verdict.decided_by) == (Lane.REVIEW, Decider.LANGUAGE)
    assert verdict.reason == (
        f"язык сайта — не понять по буквам (латиница), письма — {sl.language_name(letters)}; "
        "примете — английский"
    )


@pytest.mark.parametrize(
    ("text", "lane", "words"),
    [
        (FURNITURE_UR, Lane.REVIEW,
         "язык сайта — не понять по буквам (арабское письмо), письма — русский"),
        (DENTAL["zh"], Lane.REVIEW,
         "язык сайта — не понять по буквам (иероглифы), письма — русский"),
        (UK, Lane.REJECT, "язык сайта — украинский, письма — русский"),
        (PL, Lane.REJECT, "язык сайта — польский, письма — русский"),
    ],
    ids=["arabic-script", "han", "named-ukrainian", "named-polish"],
)  # fmt: skip
def test_without_a_language_from_the_table_the_words_are_as_before(
    text: str, lane: Lane, words: str
) -> None:
    """Прогон на русском. Письменность без строки — «не понять» без «примете — …»: лид получит язык
    прогона, его слова и называют. Названный язык — прежние слова: лид получит язык сайта, названный
    в них, — и польский тоже, хоть он на латинице."""
    verdict = sj.by_language(site(text), "ru")

    assert verdict is not None
    assert (verdict.lane, verdict.reason) == (lane, words)


def test_a_latin_site_in_a_latin_run_has_no_language_verdict() -> None:
    """Латиница в прогоне на английском: проверка молчит — ни вердикта, ни слов."""
    assert sj.by_language(site(EN), "en") is None


# --- на базе: заведение лида и путь «на проверку» → «Принять» -------------------------------------

SEGMENT = "Стоматологические клиники: лечат сами, на своём сайте — услуги и запись на приём."
FURNITURE = "Мебельные мастерские: делают мебель сами, на своём сайте — заказ и шоурум."
SELLER = "seller@ours.example.test"


async def _mx(_host: str, **_kwargs: object) -> MailRoute:
    return MailRoute.MX


async def _accepted(
    session: AsyncSession, run: SalesSegmentRunModel, host: str, text: str
) -> Candidate:
    """Сайт прогона, принятый человеком, с живым адресом на домене и с текстом, который видел
    судья."""
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    made = Candidate(
        run_id=run.id, hypothesis_id=run.hypothesis_id, domain_id=domain.id, lane=Lane.ACCEPT,
        decided_by=Decider.HUMAN, site_text=stored(text),
    )  # fmt: skip
    session.add(made)
    session.add(ContactModel(domain_id=domain.id, email=f"hello@{host}", source=ContactSource.PAGE))
    await session.commit()
    return made


@pytest.mark.parametrize(
    ("text", "letters", "language"),
    [
        (EN, "ru", "en"),
        (FURNITURE_EN, "kk", "en"),
        (UK, "ru", "uk"),
        (FURNITURE_UR, "ru", "ru"),
    ],
    ids=["latin-ru", "latin-kk", "named-uk", "arabic-script-without-a-row"],
)
async def test_a_new_lead_gets_its_letters_language_in_the_base(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, text: str, letters: str, language: str
) -> None:
    """Заведение лидов прогона (`make_leads` — и проход прогона, и повтор) пишет в лида тот же язык,
    что `lead_language`."""
    monkeypatch.setattr(cleaning, "mail_route", _mx)
    run = await make_run(session, await hypothesis(session, SEGMENT))
    run.language = letters
    site_made = await _accepted(session, run, "site-sample.com", text)

    made = await segment_leads.make_leads(session, run, FixtureVerifier())

    assert made["created"] == 1
    [lead] = (await session.scalars(select(SalesLeadModel))).all()
    assert lead.language == language
    assert segment_leads.lead_language(run, site_made) == language


def promised(words: str) -> str:
    """Язык писем лиду, который называют слова вердикта «не понять»: после «примете — », а без
    него — язык писем прогона («письма — …»)."""
    head, _, then = words.partition("; примете — ")
    return then or head.rsplit("письма — ", 1)[1]


@pytest.mark.parametrize(
    ("segment", "text", "letters", "quote", "words", "language"),
    [
        (SEGMENT, DENTAL["en"], "ru", "Our dental clinic treats adults",
         "язык сайта — не понять по буквам (латиница), письма — русский; примете — английский",
         "en"),
        (FURNITURE, FURNITURE_EN, "kk", "Our furniture workshop makes tables",
         "язык сайта — не понять по буквам (латиница), письма — казахский; примете — английский",
         "en"),
        (FURNITURE, FURNITURE_UR, "ru", "ہماری فرنیچر ورکشاپ ٹھوس لکڑی",
         "язык сайта — не понять по буквам (арабское письмо), письма — русский", "ru"),
    ],
    ids=["dental-latin-ru", "furniture-latin-kk", "furniture-urdu-ru"],
)  # fmt: skip
async def test_the_verdict_words_name_the_language_the_accepted_lead_gets(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    segment: str,
    text: str,
    letters: str,
    quote: str,
    words: str,
    language: str,
) -> None:
    """Путь целиком: настоящий проход судьи отдаёт сайт «на проверку» (полоса прежняя); «Принять»,
    отмена (сайт — обратно к человеку теми же словами) и повторное «Принять»; заведённый лид
    получает тот язык, что назвали слова: «примете — …», а без него — язык писем прогона. Язык
    решается при заведении, по тексту, который видел судья, — одинаково после любого пути к
    «принят»."""
    monkeypatch.setattr(cleaning, "mail_route", _mx)
    run = await make_run(session, await hypothesis(session, segment), mode=JudgeMode.SHADOW)
    run.language = letters
    domain = DomainModel(host="site-sample.com")
    session.add(domain)
    await session.flush()
    found = Candidate(
        run_id=run.id, hypothesis_id=run.hypothesis_id, domain_id=domain.id, lane=Lane.PENDING,
        page_title=text[:60], page_description=text,
    )  # fmt: skip
    session.add(found)
    await session.commit()
    model = Model({domain.host: facts("yes", quote, "своё дело")})
    client = sj.SegmentClient(
        httpx.AsyncClient(transport=httpx.MockTransport(model)),
        model="gpt-5-mini",
        api_key=MODEL_KEY,
    )

    await judge_run(
        session, run_id=run.id, segment=segment, mode=JudgeMode.SHADOW, client=client, home=None,
        letters=run.language,
    )  # fmt: skip

    assert (found.lane, found.decided_by, found.reason) == (Lane.REVIEW, Decider.LANGUAGE, words)
    await segment_report.decide(session, found.id, accept=True, author=SELLER)
    await session.commit()
    assert await segment_undo.undo(session, found.id) is not None
    await session.commit()
    assert (found.lane, found.reason) == (Lane.REVIEW, words)
    await segment_report.decide(session, found.id, accept=True, author=SELLER)
    session.add(
        ContactModel(domain_id=domain.id, email=f"hello@{domain.host}", source=ContactSource.PAGE)
    )
    await session.commit()

    made = await segment_leads.make_leads(session, run, FixtureVerifier())

    assert made["created"] == 1
    [lead] = (await session.scalars(select(SalesLeadModel))).all()
    assert (lead.language, lead.domain_id) == (language, domain.id)
    assert promised(words) == sl.language_name(lead.language)
