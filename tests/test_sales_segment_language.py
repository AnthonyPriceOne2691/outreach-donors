"""Язык сайта против языка писем (судья сегмента v3): сначала письменность, потом особые буквы,
`<html lang>` не решает; доля, а не одна буква; язык без своих букв — «не понять», а не отказ;
двуязычный сайт и переключатель на версию на языке писем не режутся.

Тексты выдуманы, ниши разные — мастерская ремонта велосипедов, стоматология, мебель (случаи
синтетики eval), — письменности тоже: кириллица (русский, украинский, казахский, белорусский),
латиница (английский, польский, чешский, турецкий), арабское письмо и иврит справа налево, японское,
китайское и тайское без пробелов, греческое, грузинское, деванагари. Модель — подставным
транспортом: её звать по такому сайту не должны.
"""

from __future__ import annotations

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.donors.home_signals import read_home
from backend.features.sales import segment_judge as sj
from backend.features.sales import segment_language as sl
from backend.features.sales.segment_judge import SiteText
from backend.features.sales.segment_models import Decider, Lane
from backend.features.sales.segment_window import Judged, JudgeTally, count
from tests.segment_facts_fakes import decide_any, facts
from tests.test_sales_segment_judge import client, says

#: Один и тот же смысл — мастерская ремонта велосипедов — на разных языках: ниша одна, разница —
#: только в буквах.
RU = (
    "Мастерская «Велоремонт» ремонтирует велосипеды любых марок за один день. Мы меняем цепи, "
    "тормоза и колёса, настраиваем переключатели и проверяем раму. Запишитесь на ремонт на сайте "
    "или приезжайте в мастерскую без записи. Гарантия на работу полгода, запчасти всегда есть."
)
UK = (
    "Майстерня «Велоремонт» ремонтує велосипеди будь-яких марок за один день. Ми міняємо ланцюги, "
    "гальма та колеса, налаштовуємо перемикачі й перевіряємо раму. Запишіться на ремонт на сайті "
    "або приїжджайте до майстерні без запису. Гарантія на роботу пів року, запчастини завжди є."
)
KK = (
    "«Велоремонт» шеберханасы кез келген маркадағы велосипедтерді бір күнде жөндейді. Біз "
    "шынжырларды, тежегіштерді және дөңгелектерді ауыстырамыз, ауыстырғыштарды реттейміз. "
    "Жөндеуге сайтта жазылыңыз немесе шеберханаға жазылусыз келіңіз. Жұмысқа жарты жыл кепілдік "
    "береміз, бөлшектер әрқашан бар."
)
BE = (
    "Майстэрня «Велярамонт» рамантуе веласіпеды любых марак за адзін дзень. Мы мяняем ланцугі, "
    "тармазы і колы, наладжваем пераключальнікі і правяраем раму. Запішыцеся на рамонт на сайце "
    "або прыязджайце ў майстэрню без запісу. Гарантыя на працу паўгода, запчасткі заўсёды ёсць."
)
EN = (
    "Our bike workshop repairs bicycles of any brand in one day. We replace chains, brakes and "
    "wheels, tune the gears and check the frame. Book a repair on the website or come to the "
    "workshop without booking. Six months of warranty on our work, spare parts always in stock."
)
PL = (
    "Nasz warsztat rowerowy naprawia rowery każdej marki w ciągu jednego dnia. Wymieniamy łańcuchy, "
    "hamulce i koła, regulujemy przerzutki i sprawdzamy ramę. Zapisz się na naprawę na stronie "
    "albo przyjedź do warsztatu bez zapisu. Gwarancja na pracę pół roku, części zawsze dostępne."
)
CS = (
    "Naše cyklistická dílna opraví kola jakékoli značky během jednoho dne. Měníme řetězy, brzdy a "
    "kola, seřizujeme přehazovačky a kontrolujeme rám. Objednejte se na opravu na webu nebo přijeďte "
    "do dílny bez objednání. Záruka na práci půl roku, náhradní díly vždy skladem."
)
TR = (
    "Bisiklet atölyemiz her markadan bisikleti bir günde onarıyor. Zincirleri, frenleri ve "
    "tekerlekleri değiştiriyor, vitesleri ayarlıyor ve şasiyi kontrol ediyoruz. Onarım için sitede "
    "kayıt olun ya da randevusuz atölyemize gelin. İşçiliğe altı ay garanti, yedek parça her zaman var."
)
DE = (
    "Unsere Fahrradwerkstatt repariert Fahrräder aller Marken an einem Tag. Wir wechseln Ketten, "
    "Bremsen und Räder, stellen die Schaltung ein und prüfen den Rahmen. Buchen Sie eine Reparatur "
    "auf der Website oder kommen Sie ohne Termin vorbei. Sechs Monate Garantie auf unsere Arbeit."
)
AR = (
    "ورشة الدراجات لدينا تصلح الدراجات من جميع الماركات في يوم واحد. نغير السلاسل والفرامل "
    "والعجلات ونضبط ناقل الحركة ونفحص الإطار. احجز موعد الإصلاح على الموقع أو تعال إلى الورشة "
    "بدون موعد. ضمان ستة أشهر على العمل وقطع الغيار متوفرة دائما."
)
JA = (
    "私たちの自転車工房は、どのメーカーの自転車でも一日で修理します。チェーン、ブレーキ、"
    "車輪を交換し、変速機を調整してフレームを点検します。サイトで修理を予約するか、予約なしで"
    "工房にお越しください。作業には半年の保証があり、部品はいつも在庫しています。"
)


#: Вторая ниша — стоматологическая клиника: письменности, на которых пишет один язык, и те, где язык
#: по буквам не назвать.
DENTAL = {
    "ru": (
        "Наша стоматологическая клиника лечит взрослых и детей. Мы ставим импланты, отбеливаем зубы и "
        "лечим каналы на современном оборудовании. Запишитесь на приём на сайте или позвоните нам, "
        "наша команда с радостью поможет. Первый осмотр бесплатный для каждого нового пациента."
    ),
    "en": (
        "Our dental clinic treats adults and children. We place implants, whiten teeth and treat "
        "root canals with modern equipment. Book an appointment on the website or call us, our team "
        "will be happy to help. The first check-up is free for every new patient."
    ),
    "he": (
        "מרפאת השיניים שלנו מטפלת במבוגרים ובילדים. אנו מבצעים השתלות, הלבנה וטיפולי שורש עם ציוד "
        "מודרני. קבעו תור באתר או התקשרו אלינו, והצוות שלנו ישמח לעזור. בדיקה ראשונה ללא תשלום לכל "
        "מטופל חדש, ותור דחוף אפשר לקבל באותו היום."
    ),
    "th": (
        "คลินิกทันตกรรมของเราดูแลผู้ใหญ่และเด็ก เราทำรากฟันเทียม ฟอกสีฟัน และรักษารากฟันด้วยเครื่องมือ"
        "ที่ทันสมัย นัดหมายผ่านเว็บไซต์หรือโทรหาเรา ทีมงานของเรายินดีช่วยเหลือ ตรวจครั้งแรกฟรีสำหรับ"
        "ผู้ป่วยใหม่ทุกคน และเรามีบริการนัดหมายด่วนภายในวันเดียวกัน"
    ),
    "el": (
        "Το οδοντιατρείο μας φροντίζει ενήλικες και παιδιά. Κάνουμε εμφυτεύματα, λεύκανση και "
        "θεραπείες ρίζας με σύγχρονο εξοπλισμό. Κλείστε ραντεβού στην ιστοσελίδα ή τηλεφωνήστε μας, "
        "η ομάδα μας θα χαρεί να βοηθήσει. Ο πρώτος έλεγχος είναι δωρεάν για κάθε νέο ασθενή."
    ),
    "ka": (
        "ჩვენი სტომატოლოგიური კლინიკა მკურნალობს მოზრდილებსა და ბავშვებს. ვაკეთებთ იმპლანტებს, "
        "კბილების გათეთრებას და არხების მკურნალობას თანამედროვე აღჭურვილობით. ჩაეწერეთ ვებგვერდზე "
        "ან დაგვირეკეთ, ჩვენი გუნდი სიამოვნებით დაგეხმარებათ. პირველი გასინჯვა უფასოა."
    ),
    "zh": (
        "我们的牙科诊所为成人和儿童提供治疗。我们使用现代设备进行种植牙、牙齿美白和根管治疗。"
        "请在网站上预约或给我们打电话，我们的团队很乐意为您提供帮助。每位新患者的首次检查免费。"
    ),
    "hi": (
        "हमारा दंत चिकित्सालय वयस्कों और बच्चों का इलाज करता है। हम आधुनिक उपकरणों के साथ इम्प्लांट, "
        "दांतों को सफेद करना और रूट कैनाल उपचार करते हैं। वेबसाइट पर अपॉइंटमेंट बुक करें या हमें फोन "
        "करें, हमारी टीम मदद करके खुश होगी। हर नए मरीज के लिए पहली जांच मुफ्त है और जरूरी इलाज उसी "
        "दिन मिलता है।"
    ),
}


def site(text: str, menu: str = "Ремонт · Цены · Контакты") -> SiteText:
    """Текст сайта судьи: заголовок выдачи, меню и текст главной."""
    return SiteText(((sj.PAGE_TITLE, text[:60]), (sj.HOME_MENU, menu), (sj.HOME_TEXT, text)))


@pytest.mark.parametrize(
    ("text", "letters", "code", "said"),
    [
        (UK, "ru", "uk", "украинский"),
        (KK, "ru", "kk", "казахский"),
        (BE, "ru", "be", "белорусский"),
        (PL, "en", "pl", "польский"),
        (CS, "en", "cs", "чешский"),
        (TR, "en", "tr", "турецкий"),
        (RU, "en", "ru", "русский"),
        (AR, "en", "ar", "арабский"),
        (JA, "ru", "ja", "японский"),
        (PL, "ru", "pl", "польский"),
        (DENTAL["he"], "en", "he", "иврит"),
        (DENTAL["th"], "en", "th", "тайский"),
        (DENTAL["el"], "ru", "el", "греческий"),
        (DENTAL["ka"], "en", "ka", "грузинский"),
        (DENTAL["ru"], "en", "ru", "русский"),
    ],
    ids=["uk-ru", "kk-ru", "be-ru", "pl-en", "cs-en", "tr-en", "ru-en", "ar-en", "ja-ru", "pl-ru",
         "dental-he-en", "dental-th-en", "dental-el-ru", "dental-ka-en", "dental-ru-en"],
)  # fmt: skip
def test_a_site_surely_in_another_language_is_named_by_its_letters(
    text: str, letters: str, code: str, said: str
) -> None:
    found = sl.mismatch(text, letters)

    assert found is not None
    assert (found.code, found.said) == (code, said)
    assert found.words == f"язык сайта — {said}, письма — {sl.language_name(letters)}"


@pytest.mark.parametrize(
    ("text", "letters"),
    [(RU, "ru"), (EN, "en"), (UK, "uk"), (PL, "pl"), (RU, "ru-RU"), (EN, "EN"),
     (DENTAL["he"], "he"), (DENTAL["el"], "el"), (DENTAL["zh"], "zh"), (DENTAL["th"], "th")],
    ids=["ru", "en", "uk", "pl", "ru-region", "en-caps", "he", "el", "zh", "th"],
)  # fmt: skip
def test_a_site_in_the_letters_language_is_not_cut(text: str, letters: str) -> None:
    assert sl.mismatch(text, letters) is None


@pytest.mark.parametrize(
    ("text", "letters", "said", "then"),
    [
        (EN, "ru", "латиница", "; примете — английский"),
        (DENTAL["en"], "ru", "латиница", "; примете — английский"),
        (DENTAL["zh"], "en", "иероглифы", ""),
        (DENTAL["hi"], "ru", "деванагари", ""),
    ],
    ids=["bike-en-ru", "dental-en-ru", "dental-zh-en", "dental-hi-ru"],
)
def test_another_script_without_letters_of_its_own_is_unsure_not_a_refusal(
    text: str, letters: str, said: str, then: str
) -> None:
    """Английский среди латиницы, иероглифы без каны, деванагари — язык по буквам не назвать: сайт
    точно не на языке писем (другая письменность), но на каком — «не понять», решает человек. У
    латиницы слова называют и язык писем лиду после «Принять» (таблица письменностей)."""
    found = sl.mismatch(text, letters)

    assert found is not None
    assert (found.code, found.said) == (None, said)
    assert found.words == (
        f"язык сайта — не понять по буквам ({said}), письма — {sl.language_name(letters)}{then}"
    )


@pytest.mark.parametrize(
    ("text", "letters"),
    [(EN, "de"), (DENTAL["en"], "pl"), (EN, "fr")],
    ids=["en-for-de", "en-for-pl", "en-for-fr"],
)
def test_the_same_script_without_letters_of_another_language_is_not_judged(
    text: str, letters: str
) -> None:
    """Латиница без особых букв при письмах на немецком: английский это или немецкий без умлаутов —
    буквы не говорят; проверка молчит, а не режет."""
    assert sl.mismatch(text, letters) is None


@pytest.mark.parametrize(
    "text",
    [
        f"{UK} {RU}",
        f"{KK} {RU}",
        f"{RU} {UK}",
    ],
    ids=["uk-and-ru", "kk-and-ru", "ru-and-uk"],
)
def test_a_page_in_two_languages_is_not_cut(text: str) -> None:
    """Двуязычная страница — не уверены: у украинского против русского это видно по словам с
    ы/э/ъ/ё, у казахского (в его алфавите есть все русские буквы) — по фразам без особых букв."""
    assert sl.mismatch(text, "ru") is None


def test_a_page_of_short_items_in_two_languages_is_not_cut_by_its_words() -> None:
    """Меню и плитки по два-три слова на двух языках: фраз длиннее пяти слов нет, и смесь видна
    только по словам — русских с ы/э/ъ/ё много против украинских."""
    items = [
        "Ремонт велосипедів і самокатів", "Ремонт велосипедов и самокатов",
        "Заміна ланцюгів і тросів", "Замена цепей и тросов", "Налаштування гальм і перемикачів",
        "Настройка тормозов", "Наші майстри їх знають", "Наши мастера",
        "Ціни на ремонт і запчастини", "Цены на ремонт", "Запис онлайн і по телефону",
        "Запись онлайн", "Гарантія пів року на всі роботи", "Гарантия полгода",
        "Запчастини є завжди", "Запчасти всегда есть", "Відгуки наших клієнтів", "Отзывы клиентов",
        "Безкоштовна діагностика", "Бесплатная диагностика", "Ми працюємо щодня",
        "Мы работаем ежедневно", "Вы можете записаться", "Ёлки и ели", "Это быстро",
    ]  # fmt: skip
    text = " · ".join(items)
    ru = sl.BY_CODE["ru"]
    own = [word for word in sl._words(text) if sl._script(word) == "CYRILLIC"]
    assert sum(sl._foreign(word, ru) for word in own) >= sl.FOREIGN_SHARE_MIN * len(own)

    assert sl.mismatch(text, "ru") is None


def test_names_and_places_of_another_language_do_not_make_a_site_foreign() -> None:
    """Имя, адрес, цитата на другом языке есть на любом сайте: решает доля слов, а не буква."""
    names = f"{EN} Showrooms in München, Düsseldorf and Köln, partner workshops in Łódź and Brno."

    assert sl.mismatch(names, "en") is None


def test_a_language_whose_letters_are_rarely_special_is_let_through_not_cut() -> None:
    """Немецкий против английского буквами уверенно не отличить: умлауты — у десятой доли слов.
    Проверка такой сайт пропускает (прежнее поведение), а не режет наугад."""
    assert sl.mismatch(DE, "en") is None


@pytest.mark.parametrize(
    "text",
    [UK[:120], "Ремонт велосипедів", "", "Bike repair in one day — book online", DENTAL["th"][:40]],
    ids=["short", "title", "empty", "short-another-script", "short-unspaced"],
)
def test_too_little_text_decides_nothing(text: str) -> None:
    """Сниппет из пары строк не решает — ни в той же письменности, ни в другой."""
    assert sl.mismatch(text, "ru") is None


def test_an_unknown_letters_language_is_not_compared() -> None:
    assert sl.mismatch(UK, "xx") is None
    assert sl.mismatch(UK, "") is None


@pytest.mark.parametrize(
    ("menu", "letters", "switches"),
    [
        ("Головна · Ремонт · UA · RU · EN", "ru", True),
        ("Рус | Қаз", "ru", True),
        ("Русский", "ru", True),
        ("Startseite · Reparatur · DE · English", "en", True),
        ("Контакти · velo-sample.ru", "ru", False),
        ("Parcel tracking · Contact · lt · en · ru · lv", "ru", True),
        ("Prices · ru · zh · fr · de", "ru", True),
        ("Clínica de Madrid · Contacto", "de", False),
        ("We deliver en route to the coast", "en", False),
        ("Doručení · Kontakt", "en", False),
    ],
    ids=["codes", "short-names", "endonym", "english-word", "zone-in-address",
         "lowercase-menu-codes", "lowercase-codes-first", "preposition-in-a-name",
         "preposition-in-prose", "no-switcher"],
)  # fmt: skip
def test_a_switcher_to_the_letters_language_is_found_by_its_words(
    menu: str, letters: str, switches: bool
) -> None:
    """Код из двух латинских букв в связном тексте — только заглавными: «de», «en» — предлоги,
    «ru» — зона адреса; пункт меню из одного кода — в любом регистре."""
    assert sl.switches_to(menu, letters) is switches


def test_a_bilingual_site_with_a_switcher_to_the_letters_language_is_not_cut() -> None:
    assert sj.site_language(site(UK, "Головна · Ремонт · UA · RU"), "ru") is None
    found = sj.site_language(site(UK, "Головна · Ремонт · UA"), "ru")
    assert found is not None
    assert found.code == "uk"


def test_html_lang_does_not_decide_the_letters_do() -> None:
    """Шаблоны врут: `lang="ru"` у украинской страницы и `lang="uk"` у русской — решают буквы
    шапки главной, как её разбирает общий `read_home` у судьи прогона."""
    page = (
        '<html lang="{lang}"><head><title>{text}</title>'
        '<meta name="description" content="{text}"></head>'
        "<body><nav><a href='/'>Головна</a></nav></body></html>"
    )
    ukrainian = read_home(page.format(lang="ru", text=UK))
    russian = read_home(page.format(lang="uk", text=RU))

    said_uk = sj.site_language(sj.site_text(title=None, description=None, home=ukrainian), "ru")
    said_ru = sj.site_language(sj.site_text(title=None, description=None, home=russian), "ru")

    assert said_uk is not None
    assert said_uk.code == "uk"
    assert said_ru is None


# --- судья: вердикт языка до модели, режим решает -------------------------------------------------


async def test_an_unnamed_language_on_another_script_goes_to_a_human_not_cut() -> None:
    """Своё дело на языке без своих букв — человеку, а не отказ; модель спрошена раньше языка (вид
    сайта решает прежде, `after_language`), и её токены — в счёте прохода."""
    model, seen = client(says(facts("yes", "Our dental clinic treats adults", "w")))

    verdict = await decide_any(
        model, host="dental-sample.com", segment="Стоматологические клиники",
        text=site(DENTAL["en"]), letters="ru",
    )  # fmt: skip

    assert len(seen) == 1
    assert (verdict.lane, verdict.decided_by) == (Lane.REVIEW, Decider.LANGUAGE)
    assert verdict.reason == (
        "язык сайта — не понять по буквам (латиница), письма — русский; примете — английский"
    )
    assert verdict.tokens == 731
    assert verdict.model is None
    assert sj.effective(verdict, JudgeMode.ENFORCE) == (Lane.REVIEW, verdict.reason)


#: Посредник и издание — «нет» на любом языке: латиница без особых букв, иероглифы и деванагари.
KIND_CASES = [
    (DENTAL["en"], "intermediary", "Our dental clinic treats adults"),
    (DENTAL["zh"], "media", "我们的牙科诊所为成人和儿童提供治疗"),
    (DENTAL["hi"], "intermediary", "हमारा दंत चिकित्सालय वयस्कों"),
]


@pytest.mark.parametrize(("text", "kind", "quote"), KIND_CASES, ids=["latin", "han", "devanagari"])
async def test_the_kind_of_the_site_decides_before_an_unnamed_language(
    text: str, kind: str, quote: str
) -> None:
    """Разбор паттернов 20.10: латиница без особых букв уводила к человеку посредника раньше, чем
    модель его видела. Теперь «нет» модели по виду сайта стоит, в наблюдении — «судья отказал бы»."""
    model, seen = client(says(facts("no", quote, "чужие клиники", kind=kind)))

    verdict = await decide_any(
        model, host="clinics-sample.com", segment="Стоматологические клиники", text=site(text),
        letters="ru",
    )  # fmt: skip

    assert len(seen) == 1
    assert (verdict.lane, verdict.decided_by, verdict.kind) == (Lane.REJECT, Decider.MODEL, kind)
    assert sj.effective(verdict, JudgeMode.SHADOW)[0] is Lane.WOULD_REJECT


@pytest.mark.parametrize(
    ("answer", "lane", "decider"),
    [
        (
            facts("no", "Our dental clinic treats adults", "другое дело"),
            Lane.REJECT,
            Decider.MODEL,
        ),
        (
            facts("unsure", "Our dental clinic treats adults", "?"),
            Lane.REVIEW,
            Decider.LANGUAGE,
        ),
        (
            facts("unsure", "Our dental clinic treats adults", "?", kind="intermediary"),
            Lane.REJECT,
            Decider.MODEL,
        ),
    ],
    ids=["own-another-business", "unclear", "intermediary-unsure"],
)
async def test_the_kind_and_the_business_decide_before_an_unnamed_language(
    answer: dict[str, object], lane: Lane, decider: Decider
) -> None:
    """С v4 вид сайта и дело — факты таблицы правил, и они стоят раньше «язык не назван»: другое
    дело и посредник не подходят на любом языке. Своё дело, которое по тексту не понять, — «не
    понять» языка, человеку."""
    model, _ = client(says(answer))

    verdict = await decide_any(
        model, host="dental-sample.com", segment="Стоматологические клиники Кипра",
        text=site(DENTAL["en"]), letters="ru",
    )  # fmt: skip

    assert (verdict.lane, verdict.decided_by) == (lane, decider)


async def test_a_silent_model_on_an_unnamed_language_is_asked_again_next_pass() -> None:
    model, _ = client(lambda request: httpx.Response(503, json={"error": {"message": "нет"}}))

    verdict = await decide_any(
        model, host="dental-sample.com", segment="Стоматологические клиники",
        text=site(DENTAL["en"]), letters="ru",
    )  # fmt: skip

    assert verdict.unanswered
    assert verdict.decided_by is Decider.MODEL


async def test_the_judge_gives_a_language_verdict_without_asking_the_model() -> None:
    model, seen = client(says(facts("yes", "x y z", "w")))

    verdict = await decide_any(
        model, host="velo-sample.com", segment="Мастерские ремонта велосипедов", text=site(UK),
        letters="ru",
    )  # fmt: skip

    assert seen == []
    assert (verdict.lane, verdict.decided_by) == (Lane.REJECT, Decider.LANGUAGE)
    assert verdict.reason == "язык сайта — украинский, письма — русский"
    assert not verdict.proven  # в кэш вердиктов гипотезы не ложится: язык писем — у прогона


async def test_without_the_letters_language_the_model_judges_as_before() -> None:
    model, seen = client(says(facts("yes", UK[:40], "мастерская")))

    verdict = await decide_any(model, host="velo-sample.com", segment="Мастерские", text=site(UK))

    assert len(seen) == 1
    assert (verdict.lane, verdict.decided_by) == (Lane.ACCEPT, Decider.MODEL)


async def test_rules_go_before_the_language() -> None:
    """Платформу правило режет по имени — язык её текста уже не спрашивается."""
    model, seen = client(says({}))

    verdict = await decide_any(
        model, host="youtube.com", segment="Мастерские", text=site(UK), letters="ru"
    )

    assert (verdict.lane, verdict.decided_by) == (Lane.REJECT, Decider.RULE)
    assert seen == []


@pytest.mark.parametrize(
    ("mode", "lane", "reason"),
    [
        (JudgeMode.SHADOW, Lane.WOULD_REJECT, "язык сайта — "),
        (JudgeMode.ENFORCE, Lane.REJECT, "язык сайта — "),
    ],
)
def test_a_language_verdict_is_not_a_hard_rule_the_mode_decides(
    mode: JudgeMode, lane: Lane, reason: str
) -> None:
    verdict = sj.by_language(site(UK), "ru")
    assert verdict is not None

    found_lane, found_reason = sj.effective(verdict, mode)

    assert found_lane is lane
    assert found_reason.startswith(reason)


def test_a_language_verdict_counts_as_would_cut_and_not_as_a_rule() -> None:
    verdict = sj.by_language(site(UK), "ru")
    assert verdict is not None
    tally = JudgeTally()

    count(Judged(verdict, site(UK)), tally)

    assert (tally.would_cut, tally.by_rule, tally.judged) == (1, 0, 1)


#: Персидский — арабским письмом, со своими буквами (پ چ ژ گ ک ی): для смеси с арабским.
FA_WORD = "کارگاه"


@pytest.mark.parametrize(
    ("text", "bucket"),
    [
        (f"{RU} {RU} Ми міняємо ланцюги і гальма.", "ru"),
        (f"{RU} {RU} Київ і Харків, ціни і запис.", "ru"),
        (f"{RU} {BE}", "ru"),
        (f"{UK} {BE}", "uk"),
        (f"{RU} {KK}", "kk"),
        (f"{EN} {EN} Zapisz się na naprawę na stronie.", "латиница"),
        (f"{AR} {AR} {FA_WORD}", "ar"),
    ],
    ids=["ru+uk", "ru+i", "ru+be", "uk+be", "ru+kk", "en+pl", "ar+fa"],
)
def test_a_mixed_text_is_named_only_by_letters_of_its_own(text: str, bucket: str) -> None:
    """Разбор паттернов 20.10: смесь русского с украинским называлась казахской — у казахского
    алфавита все русские буквы и «і», и смесь он «объяснял» лучше обоих. Язык называют только его
    отличительные буквы: казахский — ә/ғ/қ/ң/ө/ұ/ү/һ, одна «і» (она и украинская, и белорусская) его не
    называет. Смеси в трёх письменностях: кириллица, латиница, арабское письмо."""
    assert sl.identify(text) == bucket


def test_a_mixed_russian_page_is_not_named_kazakh_in_the_verdict_either() -> None:
    """То же правило — в словах вердикта и в языке лида: письма на английском, страница русская с
    украинскими вставками — «русский», а не «казахский»."""
    found = sl.mismatch(f"{RU} {RU} Київ і Харків, ціни і запис.", "en")

    assert found is not None
    assert (found.code, found.said) == ("ru", "русский")
    assert sl.mismatch(f"{RU} {KK}", "ru") is None  # двуязычная — не режем


@pytest.mark.parametrize(
    ("text", "bucket"),
    [(UK, "uk"), (EN, "латиница"), (DENTAL["he"], "he"), (DENTAL["zh"], "иероглифы"), ("123", "—")],
    ids=["named", "latin-unnamed", "script-named", "han-unnamed", "no-letters"],
)
def test_the_language_of_a_text_is_named_or_its_script_is(text: str, bucket: str) -> None:
    """Разбивка замеров по языку сайта — тем же правилом, что вердикт."""
    assert sl.identify(text) == bucket
