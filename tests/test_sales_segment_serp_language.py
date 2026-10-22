"""Страница выдачи на языке писем — у сайта есть версия на нём: несовпадения языка нет, как при
переключателе «RU» в меню (`segment_language.written_in`, `segment_text.site_language`).

Главная приходит на языке загрузчика, строк у неё больше, и по всему тексту сайт звался «не понять
по буквам (латиница)», хотя поисковик показал его страницу на языке писем. Проверка положительная,
данными таблицы языков: знаков письменности языка писем в выдаче достаточно, их большинство, слов с
чужими буквами почти нет. Украинская и казахская выдача при русских письмах её не включает, короткая
выдача (заголовок, название) ничего не решает. Решение одно у судьи, пересуда и лида — по
сохранённому тексту, и только по строкам сайта.

Тексты выдуманы, ниши разные — языковая школа, ветеринарная клиника, пекарня, мебельная мастерская,
мастерская велосипедов; письменности тоже: кириллица (русский, украинский, казахский), греческое,
арабское письмо справа налево, иероглифы и тайское письмо без пробелов. Модель — подставная.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.core.models.domain import DomainModel
from backend.features.sales import segment_judge as sj
from backend.features.sales import segment_language as sl
from backend.features.sales import segment_leads
from backend.features.sales import segment_rejudge as sr
from backend.features.sales.segment_judge import SiteText
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from sqlalchemy.ext.asyncio import AsyncSession
from tests.segment_facts_fakes import facts
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_language import KK, RU
from tests.test_sales_segment_rejudge import asked, describe
from tests.test_sales_segment_run import Model, Sites, hypothesis, make_run

Candidate = SalesSegmentCandidateModel

# --- выдача: заголовок и описание, как их отдал поисковик -----------------------------------------

SCHOOL_RU = (
    "Курсы английского языка для взрослых — школа «Лингва»",
    "Учим говорить с первого занятия: группы по уровню, разговорные клубы и подготовка к "
    "экзаменам. Пробный урок бесплатно, занятия онлайн и в классе.",
)
SCHOOL_EN = (
    "Lingva — English courses for adults in small groups",
    "Learn to speak from the first lesson: groups by level, speaking clubs and exam preparation. "
    "The first lesson is free, classes online and in the city centre.",
)
VET_EL = (
    "Κτηνιατρική κλινική «Φίλος» — φροντίδα για κατοικίδια",
    "Εξετάσεις, εμβόλια και χειρουργεία για σκύλους και γάτες. Κλείστε ραντεβού online ή "
    "καλέστε μας, είμαστε ανοιχτά και τα Σαββατοκύριακα.",
)
BAKERY_AR = (
    "مخبز الياسمين — خبز طازج وحلويات شرقية",
    "نخبز الخبز والمعجنات كل صباح من دقيق طبيعي. اطلب عبر الموقع واستلم طلبك من المخبز أو مع "
    "التوصيل إلى البيت في نفس اليوم.",
)
FURNITURE_UK = (
    "Меблі з дерева на замовлення у Львові — майстерня «Дерев'яний дім»",
    "Кухні, шафи, ліжка та столи за вашими розмірами. Безкоштовний замір, власне виробництво, "
    "доставка і збирання, гарантія два роки.",
)
FURNITURE_RU = (
    "Мебель из дерева на заказ во Львове — мастерская «Деревянный дом»",
    "Кухни, шкафы, кровати и столы по вашим размерам. Бесплатный замер, собственное производство, "
    "доставка и сборка, гарантия два года.",
)
#: Украинская выдача, где слов с і/ї/є мало — шестая часть: меньше `FOREIGN_SHARE_MIN`. Такая доля у
#: короткой украинской выдачи — не редкость; язык называют её буквы.
SOFAS_UK = (
    "Дивани та крісла на замовлення — каталог з цінами",
    "Виготовлення за вашими розмірами, доставка та збирання по Львову, гарантія два роки на каркас "
    "та оббивку.",
)
BIKES_KK = (
    "«Велоремонт» шеберханасы — велосипед жөндеу",
    "Кез келген маркадағы велосипедтерді бір күнде жөндейміз: шынжыр, тежегіш, дөңгелек. Онлайн "
    "жазылыңыз немесе шеберханаға келіңіз.",
)
SCHOOL_ZH = (
    "英语学校 — 成人英语课程",
    "小班教学，外籍教师授课，第一节课免费。可在网上报名，也可以来学校咨询。周末和晚上都有课程。",
)
SCHOOL_TH = (
    "โรงเรียนสอนภาษาอังกฤษสำหรับผู้ใหญ่",
    "เรียนเป็นกลุ่มเล็กกับครูเจ้าของภาษา ทดลองเรียนฟรีครั้งแรก สมัครเรียนออนไลน์ได้ทุกวัน มีชั้นเรียนตอนเย็นและวันหยุด",
)
#: Японская выдача: кусок с кандзи в большинстве — иероглифы, с каной — кана; обе письменности —
#: японского (`Language.also`).
SCHOOL_JA = (
    "英会話教室・東京本校 — 大人のための英語レッスン",
    "少人数クラス、ネイティブ講師、無料体験レッスン。平日夜間と週末に開講しています。",
)
#: Выдача, где знаков русского хватает, но они в меньшинстве: латинский заголовок и полописания —
#: на языке писем страница не показана.
SCHOOL_MIXED = (
    "Lingva School — English, German and Spanish courses for adults",
    "Курсы английского языка для взрослых в центре города и онлайн: группы по уровню, разговорные "
    "клубы и подготовка к экзаменам. Native teachers, small groups by level, speaking clubs every "
    "Friday and exam preparation before each session of the international tests.",
)
#: Русская выдача ателье с киргизским названием: два слова из тридцати с ө — выдача на русском. С
#: третьим киргизским словом их десятая часть — буквы уже называют другой язык
#: (`NAMED_SPECIAL_MIN`).
SEWING_RU = (
    "Ателье «Көркөм Көйнөк» — пошив и ремонт одежды в Бишкеке",
    "Шьём платья, костюмы и шторы по вашим меркам. Ремонт одежды за один день, примерка в удобное "
    "время, скидка на первый заказ.",
)
SEWING_RU_EDGE = (
    "Ателье «Көркөм Көйнөк Үйү» — пошив и ремонт одежды в Бишкеке",
    "Шьём платья, костюмы и шторы по вашим меркам. Ремонт одежды за один день, примерка в удобное "
    "время, скидка новым клиентам.",
)

# --- главная: заголовок, описание, меню, текст ----------------------------------------------------

SCHOOL_HOME_EN = (
    "Lingva — language school for adults",
    "English, German and Spanish courses in small groups with native teachers.",
    "Courses · Prices · Teachers · Blog · Contacts",
    "Lingva is a language school for adults who want to speak, not just study grammar. Our "
    "teachers are native speakers with years of classroom experience, and every group has no more "
    "than eight students. Choose English, German or Spanish, from beginner to advanced level, and "
    "join a class in the morning, in the evening or at the weekend. Speaking clubs meet every "
    "Friday, and exam preparation courses run before each session of the international tests. You "
    "can study in our classrooms in the city centre or online from home with the same teacher and "
    "the same materials. The first lesson is free: book it on the website, meet the teacher and "
    "find the group that suits your level and your schedule.",
)
VET_HOME_EN = (
    "Filos Veterinary Clinic — care for your pets",
    "Check-ups, vaccinations and surgery for dogs and cats, seven days a week.",
    "Services · Our vets · Prices · Book a visit",
    "Filos is a veterinary clinic for dogs, cats and small pets, open seven days a week. Our vets "
    "run check-ups and vaccinations, treat injuries and illnesses, and perform planned and urgent "
    "surgery in our own operating room. The clinic has a laboratory, an X-ray room and ultrasound, "
    "so most tests are ready on the day of the visit. We keep a record of every pet and remind "
    "owners about the next vaccination by phone or email. Book a visit online, call the reception "
    "or come without an appointment: urgent cases are always seen first, and a vet is on call at "
    "night for emergencies. Our grooming room offers bathing, nail trimming and dental cleaning, "
    "and the shop at the reception sells food and medicines that our vets recommend for every age.",
)
BAKERY_HOME_EN = (
    "Yasmin Bakery — fresh bread and oriental sweets",
    "Bread, pastries and sweets baked every morning, with delivery across the city.",
    "Bread · Sweets · Cakes to order · Delivery · Contacts",
    "Yasmin Bakery bakes bread, pastries and oriental sweets every morning from natural flour, "
    "butter and honey. Our bakers start at four, so the shop opens with warm flatbread, croissants "
    "and baklava at seven. We bake cakes to order for birthdays and weddings, with your own design "
    "and an inscription in any language. Order on the website before noon and collect your order "
    "at the bakery, or choose delivery to your home on the same day across the city. Every product "
    "lists its ingredients, and we bake gluten free bread twice a week in a separate oven.",
)
FURNITURE_HOME_EN = (
    "Wooden House — furniture made to measure",
    "Kitchens, wardrobes, sofas and tables from solid wood, made in our own workshop.",
    "Catalogue · Prices · How we work · Contacts",
    "Wooden House is a family workshop that makes furniture from solid oak, ash and walnut for "
    "flats, houses and offices. We build kitchens, wardrobes, beds, sofas and tables to your "
    "measurements and in any colour. A designer comes to measure the room for free, brings samples "
    "of wood and fabric, and prepares a drawing and a quote within two days. Most orders take "
    "three to six weeks. Our own team delivers and assembles the furniture, cleans up after the "
    "work and explains how to care for the wood. Every piece has a two year warranty, and the "
    "fittings have five years.",
)
FURNITURE_HOME_UK = (
    "Майстерня «Дерев'яний дім» — меблі з натурального дерева",
    "Кухні, шафи, ліжка та столи за вашими розмірами з доставкою по місту.",
    "Головна · Каталог · Ціни · Як ми працюємо · Контакти",
    "Майстерня «Дерев'яний дім» виготовляє меблі з натурального дерева для квартир, будинків і "
    "офісів. Ми робимо кухні, шафи, ліжка, столи та стільці за вашими розмірами і в будь-якому "
    "кольорі. Дизайнер приїде на безкоштовний замір, покаже зразки дерева і фарби, а потім "
    "підготує проєкт і кошторис. Виготовлення займає від трьох до шести тижнів залежно від "
    "складності замовлення. Наші майстри самі доставляють і збирають меблі, прибирають після себе "
    "та пояснюють, як доглядати за деревом. На всі вироби діє гарантія два роки, а на фурнітуру — "
    "п'ять років. Завітайте до нашого шоуруму у Львові або напишіть нам у месенджер.",
)
BIKES_HOME_KK = (
    "«Велоремонт» — велосипед жөндеу шеберханасы",
    "Велосипедтерді бір күнде жөндейміз, бөлшектер әрқашан бар.",
    "Басты бет · Бағалар · Байланыс",
    KK,
)
BIKES_HOME_RU = ("Велоремонт — мастерская", "Ремонт велосипедов за один день.", "Ремонт · Цены", RU)


def site(serp: tuple[str, ...], home: tuple[str, str, str, str] | None = None) -> SiteText:
    """Текст сайта, как его видит судья и хранит кандидат: строки выдачи, затем главной."""
    lines = list(zip((sj.PAGE_TITLE, sj.PAGE_DESCRIPTION), serp, strict=False))
    if home is not None:
        lines += zip(
            (sj.HOME_TITLE, sj.HOME_DESCRIPTION, sj.HOME_MENU, sj.HOME_TEXT), home, strict=True
        )
    return SiteText(tuple(lines))


def whole(text: SiteText, letters: str) -> sl.Mismatch | None:
    """Язык по всему тексту сайта — как его мерили до правила выдачи."""
    return sl.mismatch("\n".join(words for _, words in text.lines), letters)


def lead_letters(text: SiteText, letters: str) -> str:
    """Язык писем лиду из сайта с этим текстом — по сохранённому тексту, как при заведении."""
    run = SalesSegmentRunModel(language=letters)
    return segment_leads.lead_language(run, Candidate(site_text=text.for_model()))


def serp_of(serp: tuple[str, ...]) -> str:
    return "\n".join(serp)


LATIN = "язык сайта — не понять по буквам (латиница), письма — русский; примете — английский"


# --- выдача на языке писем: положительная проверка, данными таблицы -------------------------------


@pytest.mark.parametrize(
    ("serp", "letters"),
    [(SCHOOL_RU, "ru"), (SCHOOL_RU, "ru-RU"), (VET_EL, "el"), (BAKERY_AR, "ar"),
     (FURNITURE_UK, "uk"), (BIKES_KK, "kk"), (SCHOOL_EN, "en"), (SCHOOL_ZH, "zh"),
     (SCHOOL_TH, "th"), (SCHOOL_JA, "ja")],
    ids=["ru", "ru-region", "el", "ar", "uk", "kk", "en", "zh-unspaced", "th-unspaced",
         "ja-kana-and-kanji"],
)  # fmt: skip
def test_a_serp_in_the_letters_language_is_written_in_it(
    serp: tuple[str, str], letters: str
) -> None:
    """Любая письменность: кириллица, греческое и арабское письмо, латиница — по буквам; иероглифы
    и тайское письмо без пробелов — по знакам (`_size`), а не по словам, которых у них нет."""
    assert sl.written_in(serp_of(serp), letters)


@pytest.mark.parametrize(
    ("serp", "letters"),
    [
        (FURNITURE_UK, "ru"),
        (BIKES_KK, "ru"),
        (SCHOOL_EN, "ru"),
        (SCHOOL_RU, "uk"),
        (FURNITURE_RU, "uk"),
        (SCHOOL_RU, "kk"),
        (SCHOOL_RU, "el"),
        (VET_EL, "ar"),
        (SCHOOL_RU[:1], "ru"),
        (("Lingva Pro",), "ru"),
        (("Lingva Pro — школа", SCHOOL_EN[1]), "ru"),
        (SCHOOL_MIXED, "ru"),
        (SCHOOL_RU, "xx"),
    ],
    ids=["uk-for-ru", "kk-for-ru", "en-for-ru", "ru-for-uk", "ru-for-uk-few-special-letters",
         "ru-for-kk", "ru-for-el", "el-for-ar",
         "title-only", "brand-only", "a-few-words-in-latin", "letters-in-a-minority",
         "unknown-letters"],
)  # fmt: skip
def test_a_serp_in_another_language_or_too_short_decides_nothing(
    serp: tuple[str, ...], letters: str
) -> None:
    """Украинская и казахская выдача при русских письмах (их буквы і/є, қ/ү/ө — чужие), русская при
    украинских (ы/э — чужие, а где их мало — буквы называют русский) и при казахских (чужих букв нет
    вовсе, своих казахских — тоже), другая письменность — не на языке писем. Заголовок без описания,
    одно название, пара слов языка писем в латинской выдаче, русское предложение среди латинских —
    не решают."""
    assert not sl.written_in(serp_of(serp), letters)


def cyrillic(serp: tuple[str, ...]) -> list[str]:
    return [word for word in sl._words(serp_of(serp)) if sl._script(word) == "CYRILLIC"]


def share_of(words: list[str], hit: Callable[[str], bool]) -> float:
    return sum(hit(word) for word in words) / len(words)


def test_a_short_ukrainian_serp_with_few_special_letters_is_not_taken_for_russian() -> None:
    """Выдача короткая, и доля слов с чужими буквами у неё шумит: у этой украинской — шестая часть,
    меньше порога «на другом языке» (`FOREIGN_SHARE_MIN`). Её язык называют буквы — украинский:
    такая выдача русской не считается, и сайт не уходит в русские письма."""
    russian = sl.BY_CODE["ru"]
    words = cyrillic(SOFAS_UK)
    assert share_of(words, lambda word: sl._foreign(word, russian)) < sl.FOREIGN_SHARE_MIN
    assert sl._named(words, "CYRILLIC") == sl.BY_CODE["uk"]

    assert not sl.written_in(serp_of(SOFAS_UK), "ru")
    assert sl.written_in(serp_of(SOFAS_UK), "uk")


def latin_o(text: str, count: int) -> str:
    """Первые `count` слов с «о» — с латинской «o» внутри: опечатка набора, как в живой выдаче."""
    words = text.split(" ")
    changed = 0
    for index, word in enumerate(words):
        if changed < count and "о" in word:
            words[index] = word.replace("о", "o")
            changed += 1
    assert changed == count
    return " ".join(words)


def test_typos_that_name_no_language_are_judged_by_their_share() -> None:
    """Латинская «o» внутри русского слова язык не называет — такие слова решает доля: меньше
    `FOREIGN_SHARE_MIN` — выдача на русском, ровно пятая часть — уже нет."""
    russian = sl.BY_CODE["ru"]
    title, description = SCHOOL_RU[0], f"{SCHOOL_RU[1]} Звоните."
    total = len(cyrillic((title, description)))
    assert total == 30
    edge = (title, latin_o(description, 6))
    fewer = (title, latin_o(description, 5))
    assert share_of(cyrillic(edge), lambda word: sl._foreign(word, russian)) == sl.FOREIGN_SHARE_MIN
    assert sl._named(cyrillic(edge), "CYRILLIC") == russian

    assert sl.written_in(serp_of(fewer), "ru")
    assert not sl.written_in(serp_of(edge), "ru")


def test_a_name_in_a_neighbour_language_leaves_the_serp_in_the_letters_language() -> None:
    """Имя на соседнем языке есть в любой выдаче: два слова из тридцати не мешают — выдача на
    русском. Слов с киргизскими буквами ровно десятая часть (`NAMED_SPECIAL_MIN`) — буквы называют
    другой язык, хотя доля чужих слов и меньше `FOREIGN_SHARE_MIN`."""
    russian = sl.BY_CODE["ru"]
    words = cyrillic(SEWING_RU_EDGE)
    assert share_of(words, lambda word: sl._foreign(word, russian)) == sl.NAMED_SPECIAL_MIN
    assert sl._named(cyrillic(SEWING_RU), "CYRILLIC") == russian

    assert sl.written_in(serp_of(SEWING_RU), "ru")
    assert not sl.written_in(serp_of(SEWING_RU_EDGE), "ru")


# --- язык сайта: версия на языке писем — по выдаче, как по переключателю --------------------------


@pytest.mark.parametrize(
    ("serp", "home", "letters"),
    [(SCHOOL_RU, SCHOOL_HOME_EN, "ru"), (VET_EL, VET_HOME_EN, "el"),
     (BAKERY_AR, BAKERY_HOME_EN, "ar")],
    ids=["ru-school", "el-vet", "ar-bakery"],
)  # fmt: skip
def test_a_serp_in_the_letters_language_wins_over_a_home_page_in_english(
    serp: tuple[str, str], home: tuple[str, str, str, str], letters: str
) -> None:
    """Находка тест-группы: главная на английском (её строк больше) звала сайт «не понять по буквам
    (латиница)», а поисковик показал страницу на языке писем — версия на нём есть, несовпадения нет.
    Лид получает язык писем прогона."""
    text = site(serp, home)
    before = whole(text, letters)
    assert before is not None
    assert (before.code, before.script) == (None, "LATIN")

    assert sj.site_language(text, letters) is None
    assert sj.by_language(text, letters) is None
    assert lead_letters(text, letters) == letters


def test_a_serp_in_the_letters_language_wins_over_a_home_page_in_a_language_of_its_script() -> None:
    """Письма на русском, главная на украинском, выдача на русском: та же письменность, но
    поисковик показал русскую страницу — версия на русском есть."""
    text = site(FURNITURE_RU, FURNITURE_HOME_UK)
    before = whole(text, "ru")
    assert before is not None
    assert before.code == "uk"

    assert sj.site_language(text, "ru") is None
    assert lead_letters(text, "ru") == "ru"


@pytest.mark.parametrize(
    ("serp", "home", "code", "words"),
    [
        (FURNITURE_UK, FURNITURE_HOME_UK, "uk", "язык сайта — украинский, письма — русский"),
        (BIKES_KK, BIKES_HOME_KK, "kk", "язык сайта — казахский, письма — русский"),
    ],
    ids=["ukrainian", "kazakh"],
)
def test_a_site_in_a_neighbour_language_of_the_same_script_stays_a_mismatch(
    serp: tuple[str, str], home: tuple[str, str, str, str], code: str, words: str
) -> None:
    """Выдача и главная на украинском или казахском при русских письмах — несовпадение остаётся:
    их выдача на языке писем не написана. Лид получает язык сайта."""
    text = site(serp, home)

    found = sj.site_language(text, "ru")

    assert found is not None
    assert (found.code, found.words) == (code, words)
    assert lead_letters(text, "ru") == code


@pytest.mark.parametrize(
    ("serp", "home"),
    [(SCHOOL_EN, SCHOOL_HOME_EN), (SCHOOL_RU[:1], SCHOOL_HOME_EN), (SOFAS_UK, FURNITURE_HOME_EN)],
    ids=["english-serp", "short-russian-title", "ukrainian-serp-with-few-special-letters"],
)
def test_otherwise_a_latin_site_is_still_unsure_as_before(
    serp: tuple[str, ...], home: tuple[str, str, str, str]
) -> None:
    """Выдача на английском, русский заголовок без описания, украинская выдача с редкими і/є —
    версии на языке писем выдача не показывает: «не понять по буквам (латиница)», как было, и лиду
    после «Принять» — английский."""
    text = site(serp, home)

    found = sj.site_language(text, "ru")

    assert found is not None
    assert found.words == LATIN
    assert found == whole(text, "ru")
    assert lead_letters(text, "ru") == "en"


def test_a_brand_name_in_latin_and_a_russian_home_page_is_as_before() -> None:
    """Выдача из двух слов бренда латиницей, главная на русском — сайт на языке писем, как и был:
    короткая выдача ничего не решает ни в ту, ни в другую сторону."""
    text = site(("Velodrom Pro",), BIKES_HOME_RU)

    assert whole(text, "ru") is None
    assert sj.site_language(text, "ru") is None
    assert lead_letters(text, "ru") == "ru"


# --- одно решение: строки сайта, сохранённый текст, без заглушки ----------------------------------


def test_a_line_of_our_own_words_does_not_decide_the_language_of_the_site() -> None:
    """Параллельная работа может положить в текст служебную строку со своими словами — язык сайта
    меряется только по строкам выдачи и главной (`SITE_LABELS`)."""
    text = site(SCHOOL_EN)
    note = "наши слова о сайте, а не слова сайта: главная не открылась, судья видел только выдачу"
    ours = SiteText((*text.lines, ("Служебная строка", note)))
    assert whole(ours, "ru") is None  # по всему тексту наша строка перевесила бы

    found = sj.site_language(ours, "ru")

    assert found is not None
    assert found == sj.site_language(text, "ru")
    assert found.words == LATIN


def test_the_stored_text_gives_the_same_language_as_the_judged_one() -> None:
    """Строки выдачи хранятся с подписями: текст кандидата, прочитанный обратно, даёт тот же язык,
    что текст судьи, — у пересуда, отмены решения и лида."""
    for text, letters in [
        (site(SCHOOL_RU, SCHOOL_HOME_EN), "ru"),
        (site(FURNITURE_UK, FURNITURE_HOME_UK), "ru"),
        (site(VET_EL, VET_HOME_EN), "el"),
        (site(SCHOOL_EN, SCHOOL_HOME_EN), "ru"),
    ]:
        stored = SiteText.parse(text.for_model())
        assert stored == text
        assert sj.site_language(stored, letters) == sj.site_language(text, letters)


STUB_HOME = (
    "Just a moment...",
    "Checking if the site connection is secure. lingva-sample.com needs to review the security of "
    "your connection before proceeding. Verification is taking longer than expected. Performance "
    "and security by Cloudflare.",
)


def test_a_stub_home_page_does_not_decide_the_lead_language_the_judge_did_not_see_it() -> None:
    """Главная-заглушка: судья её не видит (`SiteText.without_stub`) и по короткой выдаче языка не
    судит. Лид по сохранённому тексту решает так же — без заглушки, иначе её английские слова дали
    бы ему английские письма, о которых вердикт молчал."""
    text = SiteText(
        ((sj.PAGE_TITLE, "Курсы английского — Лингва"), (sj.HOME_TITLE, STUB_HOME[0]),
         (sj.HOME_TEXT, STUB_HOME[1]))
    )  # fmt: skip
    assert whole(text, "ru") is not None  # заглушка весом перевесила бы выдачу

    assert sj.by_language(text.without_stub(), "ru") is None  # как судит судья
    assert sj.site_language(text, "ru") is None
    assert lead_letters(text, "ru") == "ru"


# --- на базе: проход судьи и пересуд по сохранённому тексту ---------------------------------------

SCHOOL = "lingva-sample.com"
WORKSHOP = "derevo-sample.com"
ACADEMY = "academy-sample.com"
SEGMENT = "Частные школы и мастерские: сами учат или делают, на своём сайте — услуги и запись."
HOME = (
    "<html><head><title>{0}</title><meta name='description' content='{1}'></head>"
    "<body><nav>{2}</nav><main><p>{3}</p></main></body></html>"
)


def page(home: tuple[str, str, str, str]) -> str:
    title, description, menu, text = home
    links = "".join(
        f"<a href='/{index}'>{item}</a>" for index, item in enumerate(menu.split(" · "))
    )
    return HOME.format(title, description, links, text)


async def _pending(
    session: AsyncSession, run: SalesSegmentRunModel, host: str, serp: tuple[str, str]
) -> Candidate:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    found = Candidate(
        run_id=run.id, hypothesis_id=run.hypothesis_id, domain_id=domain.id, lane=Lane.PENDING,
        page_url=f"https://{host}/ru/courses", page_title=serp[0], page_description=serp[1],
    )  # fmt: skip
    session.add(found)
    return found


def _judged(*found: Candidate) -> list[tuple[object, ...]]:
    """Что лежит у сайтов после суда: полоса, вердикт, кто решил и слова."""
    return [(item.lane, item.verdict, item.decided_by, item.reason) for item in found]


def _judge(model: Model) -> sj.SegmentClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(model))
    return sj.SegmentClient(http, model="gpt-5-mini", api_key=MODEL_KEY)


async def test_the_rejudge_by_the_stored_text_says_what_the_pass_said(
    session: AsyncSession,
) -> None:
    """Проход судьи качает главную и хранит текст с подписями; пересуд главную не качает и судит
    по сохранённому тексту — по фактам из кэша и после правки описания, с моделью. Язык решается
    так же: русская выдача при английской главной — без вердикта языка, к модели; украинский сайт —
    «отказал бы» без модели; английский — «не понять». Лид получает тот же язык."""
    owner = await hypothesis(session, SEGMENT)
    run = await make_run(session, owner, mode=JudgeMode.SHADOW)
    run.language = "ru"
    sites = [
        await _pending(session, run, SCHOOL, SCHOOL_RU),
        await _pending(session, run, WORKSHOP, FURNITURE_UK),
        await _pending(session, run, ACADEMY, SCHOOL_EN),
    ]
    await session.commit()
    homes = Sites({SCHOOL: page(SCHOOL_HOME_EN), WORKSHOP: page(FURNITURE_HOME_UK),
                   ACADEMY: page(SCHOOL_HOME_EN)})  # fmt: skip
    answers = {
        SCHOOL: facts("yes", "Курсы английского языка для взрослых", "сама учит"),
        ACADEMY: facts("yes", "English courses for adults", "сама учит"),
    }
    model = Model(answers)

    await judge_run(
        session, run_id=run.id, segment=SEGMENT, mode=JudgeMode.SHADOW, client=_judge(model),
        home=httpx.AsyncClient(transport=httpx.MockTransport(homes)), letters=run.language,
    )  # fmt: skip

    passed = _judged(*sites)
    assert [line[:3] for line in passed] == [
        (Lane.ACCEPT, Lane.ACCEPT, Decider.MODEL),
        (Lane.WOULD_REJECT, Lane.REJECT, Decider.LANGUAGE),
        (Lane.REVIEW, Lane.REVIEW, Decider.LANGUAGE),
    ]
    assert passed[1][3] == "язык сайта — украинский, письма — русский"
    assert passed[2][3] == LATIN
    assert sorted(model.asked) == [ACADEMY, SCHOOL]
    assert "Заголовок главной: Lingva — language school for adults" in (sites[0].site_text or "")
    hits = len(homes.hits)

    rounds = []
    for segment in (SEGMENT, f"{SEGMENT} Без сетей и франшиз."):
        await describe(session, owner, segment)
        await asked(session, run)
        again = Model(answers)

        await sr.rejudge(session, run.id, sr.RejudgeDeps(_judge(again), None))

        for found in sites:
            await session.refresh(found)
        assert _judged(*sites) == passed
        assert len(homes.hits) == hits  # главные не качались: текст — сохранённый
        rounds.append(sorted(again.asked))
    assert rounds == [[], [ACADEMY, SCHOOL]]  # факты из кэша, затем правка описания — модель
    assert [segment_leads.lead_language(run, found) for found in sites] == ["ru", "uk", "en"]
