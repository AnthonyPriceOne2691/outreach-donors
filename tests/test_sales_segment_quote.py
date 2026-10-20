"""Проверка цитаты судьи сегмента (`segment_quote.py`): типографика, наша подпись строки и
опечатка переписывания не в счёт, выдумка и пересказ — по-прежнему «на проверку».

Тест прогона: три сайта ушли человеку с «модель не прочла, а сочинила», хотя цитата на
сайте была — сайт и модель ставили разные кавычки и пробелы. Сверка теперь поверх общего
`keywords/hygiene.normalize`: двойные кавычки, многоточие-пропуск, знаки по краям, мягкий
перенос и нулевой ширины, ё/е, `casefold`; слова — по порядку внутри ОДНОЙ строки. Ложные «цитаты
нет», записанные в кэш вердиктов прежней сверкой, кэш больше не отдаёт. Калибровка 15.10: модель
пишет в цитату нашу подпись («Заголовок в выдаче: …») и переписывает длинное слово капсом с
ошибкой в букву — подпись снимается, опечатка прощается с высоким порогом; пересказ — нет.

Сайты и описание выдуманы, строки — разных ниш и письменностей; модель — подставным транспортом.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from backend.config.judge import JudgeMode
from backend.features.sales import segment_judge as sj
from backend.features.sales import segment_quote as sq
from backend.features.sales.segment_judge import SiteText
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentVerdictModel,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_judge import judged
from tests.test_sales_segment_judging import candidates, judge
from tests.test_sales_segment_run import SEGMENT, SHOP, Model, hypothesis, make_run

TITLE = "ООО «Образец Мебель» — мебель на заказ по России и СНГ, шоурум в Москве"
MENU = "Каталог · Доставка и сборка · Контакты"


def site(title: str = TITLE) -> SiteText:
    return SiteText(((sj.PAGE_TITLE, title), (sj.HOME_MENU, MENU)))


# --- типографика не в счёт -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "quote"),
    [
        (TITLE, 'ООО "Образец Мебель" — мебель на заказ'),
        (TITLE, "ООО “Образец Мебель” — мебель на заказ"),
        (TITLE, "„Образец Мебель“ — мебель на заказ по России"),
        (TITLE, "ООО Образец Мебель мебель на заказ"),
        (TITLE, "мебель на заказ по России … шоурум в Москве"),
        (TITLE, "мебель на заказ по России... шоурум в Москве"),
        (TITLE, "мебель на заказ по России и СНГ."),
        (TITLE, "«мебель на заказ по России и СНГ»,"),
        ("Вело\u00adсипеды и запчасти для горных трасс", "Велосипеды и запчасти для"),
        ("Вело\u200bсипеды и запчасти для горных трасс", "Велосипеды и запчасти для"),
        ("Ёлки, шишки и венки к празднику", "елки, шишки и венки к празднику"),
        ("Елки, шишки и венки к празднику", "Ёлки, шишки и венки"),
        ("GROSSE STRASSE Bäckerei Berlin", "Große Straße Bäckerei"),
        ("東京“家具”工房のサイトです", "東京家具工房のサイト"),
        ("東京“家具”工房のサイトです", "「東京家具工房のサイト」。"),
    ],
    ids=[
        "straight-quotes",
        "curly-quotes",
        "low-quotes",
        "no-quotes",
        "ellipsis-gap",
        "three-dots-gap",
        "trailing-dot",
        "quoted-with-comma",
        "soft-hyphen",
        "zero-width",
        "yo-in-site",
        "yo-in-quote",
        "casefold",
        "cjk",
        "cjk-closing-mark",
    ],
)
def test_an_honest_quote_holds_whatever_the_typography(line: str, quote: str) -> None:
    assert sq.quote_holds(quote, line)


async def test_a_typographic_quote_of_the_model_is_accepted_not_sent_to_review() -> None:
    """Как на тесте прогона: цитата есть на сайте, кавычки — другие."""
    verdict, _ = await judged(
        {"fit": "yes", "quote": 'ООО "Образец Мебель" — мебель на заказ', "why": "мастерская"},
        site(),
    )

    assert (verdict.lane, verdict.quote) == (Lane.ACCEPT, 'ООО "Образец Мебель" — мебель на заказ')


# --- выдумка — по-прежнему человеку ---------------------------------------------------------------


@pytest.mark.parametrize(
    "quote",
    [
        "лучшая мебельная фабрика Европы с 1990 года",
        # Куски из двух строк — не цитата, и через многоточие тоже.
        "шоурум в Москве Каталог Доставка и сборка",
        "шоурум в Москве … Доставка и сборка",
        # Куски одной строки, но не по порядку.
        "шоурум в Москве … мебель на заказ",
        # Слово переделано: «мебели» — не «мебель».
        "мебели на заказ по России",
    ],
    ids=["made-up", "two-lines", "two-lines-gap", "out-of-order", "changed-word"],
)
async def test_a_made_up_quote_still_goes_to_review(quote: str) -> None:
    verdict, _ = await judged({"fit": "yes", "quote": quote, "why": "мастерская"}, site())

    assert (verdict.lane, verdict.reason, verdict.quote) == (Lane.REVIEW, sj.MADE_UP, None)


@pytest.mark.parametrize(
    ("quote", "lane"),
    [
        ("Образец Мебель", Lane.REVIEW),
        ("«Образец» — Мебель", Lane.REVIEW),
        ("ООО Образец Мебель", Lane.ACCEPT),
    ],
    ids=["two-words", "two-words-and-signs", "three-words"],
)
async def test_the_quote_is_at_least_three_words_as_the_prompt_asks(quote: str, lane: Lane) -> None:
    verdict, _ = await judged({"fit": "yes", "quote": quote, "why": "мастерская"}, site())

    assert verdict.lane is lane
    if lane is Lane.REVIEW:
        assert verdict.reason == sj.SHORT_QUOTE


def test_the_minimum_is_the_lower_bound_the_prompt_names() -> None:
    """Согласовано с промптом: он просит «3 to 30 words», код не берёт короче."""
    bounds = re.search(r"(\d+) to (\d+) words", sj.load_prompt())

    assert bounds is not None
    assert int(bounds.group(1)) == sq.MIN_QUOTE_WORDS == 3


def test_a_word_of_a_script_without_spaces_counts_by_its_length() -> None:
    """Японская цитата без пробелов — не «одно слово»: иначе любой японский сайт уходил бы
    человеку с «цитата короче трёх слов»."""
    assert sq.quote_words("東京家具工房のサイト") >= sq.MIN_QUOTE_WORDS
    assert sq.quote_words("家具") < sq.MIN_QUOTE_WORDS
    assert sq.quote_words("мебель — на заказ …") == 3
    # Знаки при буквах — часть слова: хинди и арабское с огласовками — по слову, а не кусками.
    assert sq.quote_words("हिन्दी में सेवाएं") == 3
    assert sq.quote_words("مَرْحَبًا بِكُمْ هُنَا") == 3


# --- наша подпись строки в начале цитаты — не слова сайта -----------------------------------------

WORDS = "мебель на заказ по России"


@pytest.mark.parametrize("label", sj.LABELS)
@pytest.mark.parametrize(
    "written",
    ["{label}: {words}", "«{label}: {words}»", "{lower}:   {words}", '"{label}: {words}".'],
    ids=["plain", "in-quotes", "lowercase", "quoted-with-dot"],
)
async def test_our_label_at_the_start_of_the_quote_is_taken_off(label: str, written: str) -> None:
    """Модель переписывает строку вместе с подписью, хотя промпт просит её не цитировать. Каждая
    подпись — из того же списка, которым строится текст сайта (`segment_judge.LABELS`)."""
    quote = written.format(label=label, lower=label.lower(), words=WORDS)

    verdict, _ = await judged(
        {"fit": "yes", "quote": quote, "why": "мастерская"}, site_under(label)
    )

    assert (verdict.lane, verdict.quote) == (Lane.ACCEPT, WORDS)


def site_under(label: str) -> SiteText:
    return SiteText(((label, TITLE),))


@pytest.mark.parametrize(
    "quote",
    [
        # Подпись не в начале — две строки, склеенные в одну.
        f"шоурум в Москве {sj.HOME_MENU}: Каталог · Доставка и сборка",
        # Похоже на подпись, но не наша: слова не сайта.
        f"Заголовок сайта: {WORDS}",
    ],
    ids=["label-inside", "not-our-label"],
)
async def test_only_our_label_at_the_start_is_taken_off(quote: str) -> None:
    verdict, _ = await judged({"fit": "yes", "quote": quote, "why": "мастерская"}, site())

    assert (verdict.lane, verdict.reason) == (Lane.REVIEW, sj.MADE_UP)


def test_the_labels_come_from_the_site_text_builder_not_a_copy() -> None:
    """Одна точка правды: модуль цитаты списка подписей не держит — его передаёт судья."""
    source = Path(sq.__file__).read_text(encoding="utf-8")

    assert "LABELS =" not in source
    assert [label for label in sj.LABELS if f'"{label}' in source] == []


# --- опечатка переписывания: длинное слово, высокий порог, без правил языков ---------------------

GARDENS = "Professional landscaping services for private gardens"
FREIGHT = "Транспортно-экспедиционная компания полного цикла"
WEB = "شركة متخصصة في تصميم المواقع الإلكترونية"
DENTAL = "Zahnarztpraxis mit Implantologie und Prophylaxe in Köln"


@pytest.mark.parametrize(
    ("line", "quote"),
    [
        # Латиница капсом: пропущена буква в слове из 11 букв.
        (GARDENS, "PROFESSIONAL LANDSCAPNG SERVICES"),
        # Кириллица: две буквы в слове из 14 — у длинного слова правок две.
        (FREIGHT, "ТРАНСПОРТНО-ЭКСПЕДИЦОНАЯ КОМПАНИЯ ПОЛНОГО ЦИКЛА"),
        # Справа налево: хамза потеряна в слове из 11 букв.
        (WEB, "شركة متخصصة في تصميم المواقع الالكترونية"),
        # Две соседние буквы переставлены в слове из 14 букв; цитата — кусок строки.
        (DENTAL, "ZAHNARTZPRAXIS MIT IMPLANTOLOGIE"),
        # Опечатка и многоточие-пропуск вместе: куски по порядку.
        (GARDENS, "Professional landscapimg services … private gardens"),
    ],
    ids=["latin-caps", "cyrillic-two-letters", "arabic-rtl", "german-swap", "with-gap"],
)
def test_a_rewriting_typo_in_a_long_word_still_holds(line: str, quote: str) -> None:
    assert sq.quote_holds(quote, line)


@pytest.mark.parametrize(
    ("line", "quote"),
    [
        # Пересказ: те же мысли другими словами.
        (GARDENS, "Expert garden landscaping for homeowners"),
        # Те же слова не по порядку.
        (GARDENS, "SERVICES PROFESSIONAL LANDSCAPING"),
        # Слово выкинуто изнутри — сокращение, а не опечатка.
        (GARDENS, "Professional landscaping for private gardens"),
        # Опечатка в коротком слове — уже другое слово.
        (FREIGHT, "Транспортно-экспедиционная компания полного цикло"),
        # Лишняя буква не делает короткое слово сайта длинным: мерило — слово сайта.
        ("Мебель на заказ по России", "Мебельь на заказ по России"),
        # Меньше трёх слов с опечаткой: опоры для нечёткой сверки мало.
        (GARDENS, "LANDSCAPNG SERVICES"),
        # Неточное каждое длинное слово — это переписанная строка, а не опечатка.
        (FREIGHT, "ТРАНСПОРТНА-ЭКСПЕДИЦОНАЯ КОМПАНИИ ПОЛНОГО ЦИКЛА"),
        # Три правки в слове из 14 букв — больше, чем прощается.
        (DENTAL, "ZAHNARZTPRAKSIZZ MIT IMPLANTOLOGIE"),
        # Иероглифы: знак — уже слово, нечёткой сверки у письменностей без пробелов нет — даже
        # когда цитата из трёх слов и знак заменён один в длинном слове.
        (
            "東京家具工房株式会社、大阪支店、名古屋支店",
            "東京家具工場株式会社、大阪支店、名古屋支店",
        ),
    ],
    ids=[
        "paraphrase",
        "reordered",
        "word-dropped",
        "short-word",
        "short-word-grown",
        "two-words",
        "every-word-off",
        "three-edits",
        "cjk-one-sign",
    ],
)
def test_a_paraphrase_or_more_than_a_typo_does_not_hold(line: str, quote: str) -> None:
    assert not sq.quote_holds(quote, line)


async def test_a_quote_with_a_typo_is_accepted_and_kept_as_the_model_wrote_it() -> None:
    typo = "PROFESSIONAL LANDSCAPNG SERVICES"
    site_ = SiteText(((sj.PAGE_TITLE, GARDENS),))

    verdict, _ = await judged({"fit": "yes", "quote": typo, "why": "садовник"}, site_)

    assert (verdict.lane, verdict.quote) == (Lane.ACCEPT, typo)


# --- кэш: ложные «цитаты нет» прежней сверки не возвращаются --------------------------------------


async def _legacy_row(session: AsyncSession, domain_id: int, owner_id: int, **row: object) -> None:
    """Строка кэша, как её писала прежняя сверка: «на проверку», цитаты нет."""
    fields: dict[str, object] = {
        "verdict": Lane.REVIEW,
        "quote": None,
        "reason": sj.MADE_UP,
        "model": "gpt-5-mini",
        "decided_by": Decider.MODEL,
        "tokens": 587,
        "prompt_version": sj.PROMPT_VERSION,
    } | row
    session.add(
        SalesSegmentVerdictModel(
            domain_id=domain_id,
            hypothesis_id=owner_id,
            segment_digest=sj.segment_digest(SEGMENT),
            **fields,
        )
    )
    await session.commit()


async def test_an_old_false_no_quote_from_the_cache_does_not_come_back(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    await _legacy_row(session, shop.domain_id, owner.id)
    model = Model(
        {SHOP: {"fit": "yes", "quote": "интернет-магазин «Велошоп»", "why": "магазин велосипедов"}}
    )

    tally = await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert model.asked == [SHOP]
    assert (tally.from_cache, tally.judged) == (0, 1)
    assert (shop.lane, shop.quote) == (Lane.ACCEPT, "интернет-магазин «Велошоп»")
    row = await session.scalar(select(SalesSegmentVerdictModel))
    assert row is not None
    assert (row.verdict, row.quote) == (Lane.ACCEPT, "интернет-магазин «Велошоп»")


async def test_a_cached_verdict_with_a_short_quote_is_asked_again(session: AsyncSession) -> None:
    """Вердикт с цитатой в два слова прежнее правило принимало; нынешнее — нет: кэш его не отдаёт."""
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    await _legacy_row(
        session, shop.domain_id, owner.id, verdict=Lane.ACCEPT, quote="Горные велосипеды"
    )
    model = Model({SHOP: {"fit": "yes", "quote": "интернет-магазин Велошоп", "why": "магазин"}})

    await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert model.asked == [SHOP]


async def test_a_rule_verdict_from_the_cache_is_still_trusted(session: AsyncSession) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    await _legacy_row(
        session,
        shop.domain_id,
        owner.id,
        verdict=Lane.REVIEW,
        reason="выдача и главная не дали текста — судить не по чему",
        decided_by=Decider.RULE,
        model=None,
        tokens=0,
    )
    model = Model({})

    tally = await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert model.asked == []
    assert tally.from_cache == 1


async def test_an_unproven_answer_is_not_written_to_the_cache(session: AsyncSession) -> None:
    """Сочинённая цитата — «на проверку» человеку, но не вердикт: в кэш она не ложится, и
    следующая правка сверки не оставит за собой ложных «цитаты нет»."""
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    model = Model({SHOP: {"fit": "yes", "quote": "лучшие велосипеды Европы", "why": "магазин"}})

    await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert (shop.lane, shop.reason) == (Lane.REVIEW, sj.MADE_UP)
    assert await session.scalar(select(SalesSegmentVerdictModel)) is None


@pytest.mark.parametrize(
    ("version", "asked"),
    [(sj.PROMPT_VERSION, False), ("sales-segment-v1", True)],
    ids=["same-version-served", "v1-asked-again"],
)
async def test_the_cache_serves_only_verdicts_of_the_current_prompt_version(
    session: AsyncSession, version: str, asked: bool
) -> None:
    """v2 — новый вопрос модели (правило «сайт о бизнесах — не бизнес»): принятый по v1
    справочник не должен вернуться из кэша принятым. Строка v1 остаётся как была."""
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    quote = "интернет-магазин «Велошоп» горные велосипеды"
    await _legacy_row(
        session, shop.domain_id, owner.id, prompt_version=version, verdict=Lane.ACCEPT, quote=quote
    )
    model = Model({SHOP: {"fit": "no", "quote": "интернет-магазин «Велошоп»", "why": "другое"}})

    tally = await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert (model.asked == [SHOP], tally.from_cache) == (asked, int(not asked))
    assert shop.lane is (Lane.REJECT if asked else Lane.ACCEPT)
    rows = (await session.scalars(select(SalesSegmentVerdictModel))).all()
    assert {(row.prompt_version, row.verdict) for row in rows} == (
        {("sales-segment-v1", Lane.ACCEPT), (sj.PROMPT_VERSION, Lane.REJECT)}
        if asked
        else {(sj.PROMPT_VERSION, Lane.ACCEPT)}
    )


# --- знаки при буквах: ударения, огласовки, тоны — не в счёт ---------------------------------------


@pytest.mark.parametrize(
    ("quote", "line"),
    [
        (
            "Δεχομαστε ασθενεις μονο στην Αθηνα",
            "Δεχόμαστε ασθενείς μόνο στην Αθήνα και τα προάστια.",
        ),
        ("Phong kham nha khoa cho ca nha", "Phòng khám nha khoa cho cả nhà"),
        ("Zamow bezplatna wycene projektu", "Zamów bezpłatną wycenę projektu."),
        ("نحن نصنع الأثاث للمنزل", "نَحْنُ نَصْنَعُ الأَثَاثَ لِلْمَنْزِلِ والمكتب"),
        ("Елка и еж в лесу", "Ёлка и ёж в лесу"),
    ],
    ids=["greek-accents", "vietnamese-tones", "polish-ogonki", "arabic-harakat", "russian-yo"],
)
def test_marks_on_letters_the_model_drops_do_not_make_a_quote_made_up(
    quote: str, line: str
) -> None:
    """Модель роняет ударения и огласовки: в коротком слове («μόνο») это уже не «опечатка в длинном
    слове», и честная цитата уходила человеку с «модель сочинила»."""
    assert sq.quote_holds(quote, line)


@pytest.mark.parametrize(
    ("quote", "line"),
    [
        ("Принимаем пациентов только в Афинах", "Δεχόμαστε ασθενείς μόνο στην Αθήνα"),
        ("bezplatna wycena dla kazdego", "Zamów bezpłatną wycenę projektu."),
        ("Lozka i szafy", "Łóżka i szafy na wymiar"),
    ],
    ids=["translation", "paraphrase", "another-letter-in-a-short-word"],
)
def test_without_marks_a_paraphrase_or_another_letter_still_does_not_hold(
    quote: str, line: str
) -> None:
    """Знак при букве снимается, буква — нет: «ł» и «l» — разные буквы, пересказ — другие слова."""
    assert not sq.quote_holds(quote, line)
