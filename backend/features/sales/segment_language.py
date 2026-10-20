"""Язык сайта против языка писем прогона — по буквам видимого текста, без модели.

Сбор пишет лидам на языке писем прогона (`SalesSegmentRunModel.language`), а выдача рынка приносит
и сайты на соседних языках: письмо на одном языке сайту на другом — мимо. Судья сегмента о языке не
спрашивается (промпт: «не суди по языку»), его сверяет этот модуль — одинаково для любой ниши и пары
языков, данными (`LANGUAGES`), а не правилом под язык.

**Решают буквы.** Сначала письменность: у языка писем своя (кириллица, латиница, греческое,
арабское письмо …), и сайт, где её букв меньше `SCRIPT_SHARE_MIN`, — на другом языке. В той же
письменности — буквы, которых в языке писем нет (`Language.letters`): украинские і/ї/є/ґ, белорусская
ў, казахские ә/ғ/қ/ң/ө/ұ/ү/һ, польские ą/ę/ł/ń/ś/ź/ż, чешские ř/ů/ě, турецкие ğ/ş/ı и т. д. Слово с
такой буквой — не на языке писем; доля таких слов ниже `FOREIGN_SHARE_MIN` — не уверены, не режем.
Двуязычная страница — не уверены, не режем, и видно это двумя способами: слов с буквами одного
языка писем (русские ы/э/ъ/ё против украинского) больше `OWN_SHARE_MAX` от чужих — или кусков текста
(фраз, строк) без единой чужой буквы не меньше `MIXED_CHUNKS_MIN`. Второе нужно, когда у языка писем
своих букв против языка сайта нет (у казахского алфавита есть все русские буквы): в чистом тексте почти
в каждой фразе из пяти слов есть особая буква, в двуязычном — фразы на языке писем без неё.

**Порог доли, а не одна буква**: имя, адрес или цитата на другом языке есть на любом сайте. Языки, у
которых особые буквы редки (немецкий, испанский, французский против английского), буквами уверенно
не отличаются — проверка их пропускает, а не режет: промах здесь — прежнее поведение, ложный отказ —
потерянный лид.

**Как называется язык сайта** (`_named`). Письменность одного языка называет его сама (`SCRIPT_NAMES`:
кана — японский, хангыль — корейский, греческое письмо, иврит, грузинское, армянское, тайское). В
письменности нескольких языков (латиница, кириллица, арабское письмо) язык называют только особые
буквы — те, что есть не у всех языков письменности (`_core`); лучший по буквам, при равенстве — частый
(порядок таблицы). **Язык без своих букв** (английский среди латиницы, текст из одних общих букв,
иероглифы без каны, деванагари) по буквам не назвать: в той же письменности, что язык писем, проверка
молчит, а на другой письменности — «не понять» (`Mismatch.code` пуст): сайт точно не на языке писем,
но на каком — решает человек, а не отказ.

**Переключатель на версию на языке писем — не отказ** (`switches_to`): «RU», «Рус», «English» в меню
или в тексте главной — у сайта есть версия, на которой и пишем. `<html lang>` не решает и не называет
язык: шаблоны врут (сайт на украинском с `lang="ru"` от шаблона и наоборот), а решение обязано
повторяться по сохранённому тексту, без страницы, — когда заводится лид (`segment_leads`).

Несовпадение — не жёсткое правило: судья выносит по нему вердикт «отказал бы» словами (язык назван)
или «не понять» (не назван) — `segment_judge.by_language`, — а режим решает, что с ним делать, как с
вердиктом модели.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache

from backend.features.sales.lead_values import language_code

#: Меньше знаков в словах — текст слишком короткий, чтобы быть уверенным: сниппет из пары строк не
#: решает. Считаются буквы и знаки при них (огласовки деванагари и тайского — знаки, а не буквы);
#: иероглиф и слог хангыля — почти слово: идут за `DENSE_WEIGHT`.
MIN_LETTERS = 150
DENSE_WEIGHT = 3
DENSE_SCRIPTS = frozenset({"HAN", "KANA", "HANGUL"})
#: Слов письменности языка писем меньше — сравнивать буквы в ней не на чем.
MIN_WORDS = 20
#: Букв письменности языка писем меньше этой доли — сайт на другой письменности. Латинские имена,
#: адреса и меню на английском у кириллического сайта долю не роняют: их меньше половины.
SCRIPT_SHARE_MIN = 0.2
#: Доля слов с буквами, которых в языке писем нет, — от неё сайт «на другом языке». У чистого
#: украинского текста против русского — около трети слов, у казахского — около половины, у польского
#: против английского — больше трети; у двуязычной страницы пополам — вдвое меньше.
FOREIGN_SHARE_MIN = 0.2
#: Слов с буквами только языка писем — не больше этой доли от чужих слов: больше — страница
#: двуязычная (русские ы/э/ъ/ё рядом с украинскими і/ї/є), не режем.
OWN_SHARE_MAX = 0.25
#: Язык называется, если его буквы объясняют столько слов; иначе — письменностью.
NAMED_SHARE_MIN = 0.8
#: Слов с особыми буквами письменности (не у всех её языков) меньше этой доли — язык по буквам не
#: назвать: одно «café» не делает английский текст французским.
NAMED_SPECIAL_MIN = 0.1
#: Кусок текста — фраза или строка между знаками конца (`_CHUNK_END`); судят куски от стольких слов:
#: в меню и заголовке из двух слов особой буквы может не быть и у чистого текста.
CHUNK_WORDS = 5
#: Кусков без единой чужой буквы — не меньше этой доли: страница двуязычная. У чистого украинского
#: текста таких кусков около десятой, у польского — меньше, у страницы пополам — половина.
MIXED_CHUNKS_MIN = 1 / 3
#: Куски меньше трёх — судить о смеси не по чему.
MIN_CHUNKS = 3
_CHUNK_END = re.compile(r"[.!?;:·•|\n]+")


@dataclass(frozen=True, slots=True)
class Language:
    """Язык: код ISO 639-1, имя по-русски, письменность и буквы, как сайт называет его версию."""

    code: str
    name: str
    #: Письменность — первое слово имени буквы в Юникоде (`script_of`).
    script: str
    #: Буквы алфавита строчными; пусто — алфавита в таблице нет (иероглифы, хангыль, деванагари):
    #: язык называет только письменность одного языка (`SCRIPT_NAMES`).
    letters: str
    #: Как переключатель версий называет язык: код, сокращение, самоназвание, имя по-английски и
    #: по-русски. Код из двух латинских букв — только заглавными («RU», «Ru»): строчные «de», «en» —
    #: предлоги других языков, «ru» — зона адреса в тексте.
    switch: tuple[str, ...]
    #: Ещё письменности языка: японский пишет и каной, и иероглифами.
    also: tuple[str, ...] = ()


_LATIN = "abcdefghijklmnopqrstuvwxyz"
_RU = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"

#: Источник букв — алфавиты языков (Википедия, статьи «… алфавит»/«… alphabet»); буквы, которые
#: язык берёт только в чужих именах, не включены. Порядок — при равенстве имя берёт первый: самый
#: частый язык письменности — раньше (`_named`).
LANGUAGES: tuple[Language, ...] = (
    Language("en", "английский", "LATIN", _LATIN, ("EN", "eng", "english", "англ", "английский", "англійська", "ағылшынша")),
    Language("de", "немецкий", "LATIN", _LATIN + "äöüß", ("DE", "deu", "ger", "deutsch", "german", "немецкий")),
    Language("fr", "французский", "LATIN", _LATIN + "àâæçéèêëîïôœùûüÿ", ("FR", "fra", "français", "francais", "french", "французский")),
    Language("es", "испанский", "LATIN", _LATIN + "áéíñóúü", ("ES", "esp", "español", "espanol", "spanish", "испанский")),
    Language("pt", "португальский", "LATIN", _LATIN + "áâãàçéêíóôõú", ("PT", "por", "português", "portugues", "portuguese", "португальский")),
    Language("it", "итальянский", "LATIN", _LATIN + "àèéìíîòóùú", ("IT", "ita", "italiano", "italian", "итальянский")),
    Language("nl", "нидерландский", "LATIN", _LATIN + "áéëèíïóöúü", ("NL", "nld", "nederlands", "dutch", "нидерландский")),
    Language("pl", "польский", "LATIN", _LATIN + "ąćęłńóśźż", ("PL", "pol", "polski", "polish", "польский")),
    Language("cs", "чешский", "LATIN", _LATIN + "áčďéěíňóřšťúůýž", ("CS", "CZ", "ces", "čeština", "cestina", "czech", "чешский")),
    Language("sk", "словацкий", "LATIN", _LATIN + "áäčďéíĺľňóôŕšťúýž", ("SK", "slk", "slovenčina", "slovak", "словацкий")),
    Language("hu", "венгерский", "LATIN", _LATIN + "áéíóöőúüű", ("HU", "hun", "magyar", "hungarian", "венгерский")),
    Language("ro", "румынский", "LATIN", _LATIN + "ăâîșțşţ", ("RO", "ron", "română", "romana", "romanian", "румынский")),
    Language("tr", "турецкий", "LATIN", _LATIN + "çğıöşüâîû", ("TR", "tur", "türkçe", "turkce", "turkish", "турецкий")),
    Language("az", "азербайджанский", "LATIN", _LATIN + "çəğıöşü", ("AZ", "aze", "azərbaycan", "azerbaijani", "азербайджанский")),
    Language("uz", "узбекский", "LATIN", _LATIN + "ʻ", ("UZ", "uzb", "oʻzbek", "uzbek", "узбекский")),
    Language("lt", "литовский", "LATIN", _LATIN + "ąčęėįšųūž", ("LT", "lit", "lietuvių", "lietuviu", "lithuanian", "литовский")),
    Language("lv", "латышский", "LATIN", _LATIN + "āčēģīķļņšūž", ("LV", "lav", "latviešu", "latviesu", "latvian", "латышский")),
    Language("et", "эстонский", "LATIN", _LATIN + "äöõüšž", ("ET", "EE", "est", "eesti", "estonian", "эстонский")),
    Language("fi", "финский", "LATIN", _LATIN + "äöåšž", ("FI", "fin", "suomi", "finnish", "финский")),
    Language("sv", "шведский", "LATIN", _LATIN + "åäöé", ("SV", "SE", "swe", "svenska", "swedish", "шведский")),
    Language("da", "датский", "LATIN", _LATIN + "æøåé", ("DA", "DK", "dan", "dansk", "danish", "датский")),
    Language("no", "норвежский", "LATIN", _LATIN + "æøåéèêóòô", ("NO", "NB", "nor", "norsk", "norwegian", "норвежский")),
    Language("is", "исландский", "LATIN", _LATIN + "áðéíóúýþæö", ("IS", "isl", "íslenska", "icelandic", "исландский")),
    Language("hr", "хорватский", "LATIN", _LATIN + "čćđšž", ("HR", "hrv", "hrvatski", "croatian", "хорватский")),
    Language("sl", "словенский", "LATIN", _LATIN + "čšž", ("SL", "SI", "slv", "slovenščina", "slovenian", "словенский")),
    Language("vi", "вьетнамский", "LATIN", _LATIN + "àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ", ("VI", "VN", "vie", "vietnamese", "вьетнамский")),
    Language("ru", "русский", "CYRILLIC", _RU, ("RU", "rus", "рус", "русский", "russian", "русская", "російська", "орысша")),
    Language("uk", "украинский", "CYRILLIC", "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя", ("UA", "UK", "ukr", "укр", "українська", "украинский", "ukrainian")),
    Language("be", "белорусский", "CYRILLIC", "абвгдеёжзійклмнопрстуўфхцчшыьэюя", ("BE", "BY", "бел", "беларуская", "белорусский", "belarusian")),
    Language("kk", "казахский", "CYRILLIC", _RU + "әғқңөұүһі", ("KK", "KZ", "kaz", "қаз", "каз", "қазақ", "қазақша", "казахский", "kazakh")),
    Language("ky", "киргизский", "CYRILLIC", _RU + "ңөү", ("KY", "KG", "kir", "кыр", "кыргызча", "киргизский", "kyrgyz")),
    Language("tg", "таджикский", "CYRILLIC", "абвгғдеёжзиӣйкқлмнопрстуӯфхҳчҷшъэюя", ("TG", "TJ", "тоҷ", "тоҷикӣ", "таджикский", "tajik")),
    Language("mn", "монгольский", "CYRILLIC", _RU + "өү", ("MN", "mon", "монгол", "монгольский", "mongolian")),
    Language("bg", "болгарский", "CYRILLIC", "абвгдежзийклмнопрстуфхцчшщъьюя", ("BG", "bul", "бг", "български", "болгарский", "bulgarian")),
    Language("sr", "сербский", "CYRILLIC", "абвгдђежзијклљмнњопрстћуфхцчџш", ("SR", "RS", "srb", "срп", "српски", "сербский", "serbian")),
    Language("mk", "македонский", "CYRILLIC", "абвгдѓежзѕијклљмнњопрстќуфхцчџш", ("MK", "mkd", "мкд", "македонски", "македонский", "macedonian")),
    Language("el", "греческий", "GREEK", "αβγδεζηθικλμνξοπρστυφχψωάέήίόύώϊϋΐΰς", ("EL", "GR", "ελ", "ελληνικά", "греческий", "greek")),
    Language("hy", "армянский", "ARMENIAN", "", ("HY", "AM", "arm", "հայ", "հայերեն", "армянский", "armenian")),
    Language("ka", "грузинский", "GEORGIAN", "", ("KA", "GE", "geo", "ქარ", "ქართული", "грузинский", "georgian")),
    Language("he", "иврит", "HEBREW", "", ("HE", "IL", "heb", "עב", "עברית", "иврит", "hebrew")),
    Language("ar", "арабский", "ARABIC", "ءآأؤإئابةتثجحخدذرسزشصضطظعغفقكلمنهوىي", ("AR", "ara", "عربي", "العربية", "арабский", "arabic")),
    Language("fa", "персидский", "ARABIC", "ءآأؤإئابتثجحخدذرزژسشصضطظعغفقکگلمنهوییپچ", ("FA", "IR", "per", "فارسی", "персидский", "persian", "farsi")),
    Language("hi", "хинди", "DEVANAGARI", "", ("HI", "hin", "हिंदी", "हिन्दी", "хинди", "hindi")),
    Language("th", "тайский", "THAI", "", ("TH", "tha", "ไทย", "тайский", "thai")),
    Language("zh", "китайский", "HAN", "", ("ZH", "CN", "chi", "中文", "简体", "繁體", "китайский", "chinese")),
    Language("ja", "японский", "KANA", "", ("JA", "JP", "jpn", "日本語", "японский", "japanese"), also=("HAN",)),
    Language("ko", "корейский", "HANGUL", "", ("KO", "KR", "kor", "한국어", "корейский", "korean")),
)  # fmt: skip

BY_CODE: dict[str, Language] = {language.code: language for language in LANGUAGES}
#: Письменности, на которых пишет один язык таблицы, — они сами называют язык. Иероглифы (китайский и
#: японский), деванагари (хинди, маратхи, непали), латиница, кириллица и арабское письмо — нет.
SCRIPT_NAMES = {
    "KANA": "ja", "HANGUL": "ko", "GREEK": "el", "HEBREW": "he", "GEORGIAN": "ka",
    "ARMENIAN": "hy", "THAI": "th",
}  # fmt: skip

#: Письменность словами — когда язык в ней не назван буквами.
SCRIPT_WORDS = {
    "LATIN": "латиница", "CYRILLIC": "кириллица", "GREEK": "греческое письмо",
    "ARABIC": "арабское письмо", "HEBREW": "еврейское письмо", "HAN": "иероглифы",
    "KANA": "японская кана", "HANGUL": "хангыль", "THAI": "тайское письмо",
    "DEVANAGARI": "деванагари", "GEORGIAN": "грузинское письмо", "ARMENIAN": "армянское письмо",
}  # fmt: skip
#: Имена Юникода, у которых первое слово — не письменность языка: «CJK UNIFIED IDEOGRAPH»,
#: «KATAKANA-HIRAGANA PROLONGED SOUND MARK» (знак долготы японского).
_SCRIPT_ALIASES = {
    "CJK": "HAN",
    "IDEOGRAPHIC": "HAN",
    "HIRAGANA": "KANA",
    "KATAKANA": "KANA",
    "KATAKANA-HIRAGANA": "KANA",
}


@lru_cache(maxsize=4096)
def script_of(char: str) -> str | None:
    """Письменность буквы — первое слово её имени в Юникоде; знак-модификатор (ʻ, ˈ) — ничей: он
    в слове, но письменность слова не решает."""
    first = unicodedata.name(char, "").split(" ", 1)[0]
    if not first or first == "MODIFIER":
        return None
    return _SCRIPT_ALIASES.get(first, first)


def _runs(text: str) -> list[str]:
    """Слова текста как написаны: буквы и знаки при них подряд (огласовки, ударения, вирамы)."""
    runs: list[str] = []
    run: list[str] = []
    for char in unicodedata.normalize("NFKC", text):
        if unicodedata.category(char)[0] in "LM":
            run.append(char)
        elif run:
            runs.append("".join(run))
            run = []
    if run:
        runs.append("".join(run))
    return runs


def _words(text: str) -> list[str]:
    """Слова текста строчными."""
    return [run.lower() for run in _runs(text)]


def _letters(word: str) -> str:
    return "".join(char for char in word if unicodedata.category(char)[0] == "L")


def _size(words: list[str]) -> int:
    """Сколько знаков в словах текста — с весом иероглифов и слогов хангыля (`DENSE_WEIGHT`)."""
    return sum(
        len(word) * (DENSE_WEIGHT if _script(word) in DENSE_SCRIPTS else 1) for word in words
    )


def _script(word: str) -> str | None:
    """Письменность слова — письменность большинства его букв."""
    found = Counter(script for char in _letters(word) if (script := script_of(char)) is not None)
    return found.most_common(1)[0][0] if found else None


@dataclass(frozen=True, slots=True)
class Mismatch:
    """Сайт уверенно не на языке писем: на каком он (код, если язык назван буквами) — словами."""

    #: Код языка сайта; пусто — назвать язык буквами нельзя, известна только письменность.
    code: str | None
    #: «украинский» — или письменность словами («латиница»).
    said: str
    #: Язык писем словами: «русский».
    letters: str = ""

    @property
    def words(self) -> str:
        """Вердикт словами: «язык сайта — украинский, письма — русский»; язык не назван —
        «язык сайта — не понять по буквам (латиница), письма — русский»."""
        said = self.said if self.code else f"не понять по буквам ({self.said})"
        return f"язык сайта — {said}, письма — {self.letters}"


def _foreign(word: str, language: Language) -> bool:
    return any(char not in language.letters for char in _letters(word))


@lru_cache(maxsize=64)
def _core(script: str) -> frozenset[str]:
    """Буквы, которые есть у всех языков письменности в таблице: по ним язык не назвать."""
    alphabets = [frozenset(item.letters) for item in LANGUAGES if item.script == script]
    return frozenset.intersection(*alphabets) if alphabets else frozenset()


def _best(words: list[str], tongues: list[Language]) -> Language | None:
    """Язык, чьи буквы объясняют больше слов (не меньше `NAMED_SHARE_MIN`); ничья — порядок таблицы."""
    covered = [sum(not _foreign(word, language) for word in words) for language in tongues]
    best = max(covered)
    return tongues[covered.index(best)] if best >= NAMED_SHARE_MIN * len(words) else None


def _named(words: list[str], script: str) -> Language | None:
    """Язык слов письменности `script` (шапка модуля): письменность одного языка — он; иначе особые
    буквы и лучший по ним язык, при равенстве — порядок таблицы; особых букв мало — `None`."""
    if (code := SCRIPT_NAMES.get(script)) is not None:
        return BY_CODE[code]
    tongues = [item for item in LANGUAGES if item.script == script and item.letters]
    if not tongues or not words:
        return None
    core = _core(script)
    special = sum(any(char not in core for char in _letters(word)) for word in words)
    return _best(words, tongues) if special >= NAMED_SPECIAL_MIN * len(words) else None


def _other_script(counts: Counter[str], words: list[str]) -> Mismatch:
    """Сайт на другой письменности: язык — по буквам главной письменности текста."""
    script = counts.most_common(1)[0][0]
    if script == "HAN" and counts["KANA"] >= 0.1 * (counts["HAN"] + counts["KANA"]):
        script = "KANA"
    named = _named([word for word in words if _script(word) == script], script)
    if named is None:
        return Mismatch(None, SCRIPT_WORDS.get(script, script.lower()))
    return Mismatch(named.code, named.name)


def _mixed(text: str, letters: Language) -> bool:
    """Страница двуязычная по кускам: фраз от `CHUNK_WORDS` слов без единой чужой буквы — не меньше
    `MIXED_CHUNKS_MIN`. Чистый текст на другом языке почти в каждой фразе несёт особую букву."""
    chunks = [
        [word for word in _words(chunk) if _script(word) == letters.script]
        for chunk in _CHUNK_END.split(text)
    ]
    judged = [chunk for chunk in chunks if len(chunk) >= CHUNK_WORDS]
    if len(judged) < MIN_CHUNKS:
        return False
    clean = sum(not any(_foreign(word, letters) for word in chunk) for chunk in judged)
    return clean >= MIXED_CHUNKS_MIN * len(judged)


def _bilingual(
    text: str, own: list[str], foreign: list[str], pair: tuple[Language, Language]
) -> bool:
    """Страница на двух языках: слов с буквами одного языка писем много против чужих — или фраз без
    единой чужой буквы много (`_mixed`)."""
    letters, named = pair
    only_ours = set(letters.letters) - set(named.letters)
    ours = sum(any(char in only_ours for char in word) for word in own)
    return ours > OWN_SHARE_MAX * len(foreign) or _mixed(text, letters)


def _same_script(text: str, words: list[str], letters: Language) -> Mismatch | None:
    """Та же письменность: доля слов с буквами, которых в языке писем нет, и чей язык их
    объясняет. Двуязычная страница — не уверены (шапка модуля)."""
    own = [word for word in words if _script(word) == letters.script]
    if len(own) < MIN_WORDS or not letters.letters:
        return None
    foreign = [word for word in own if _foreign(word, letters)]
    if len(foreign) < FOREIGN_SHARE_MIN * len(own):
        return None
    named = _named(foreign, letters.script)
    if (
        named is None
        or named.code == letters.code
        or _bilingual(text, own, foreign, (letters, named))
    ):
        return None
    return Mismatch(named.code, named.name)


def mismatch(text: str, letters: str) -> Mismatch | None:
    """Сайт уверенно не на языке писем `letters` (код, `ru-RU` — тоже `ru`) — на каком; `None` — на
    нём, не уверены (текста мало, страница смешанная, язык буквами не отличить) или язык писем
    не знаком таблице. Переключатель версий здесь не смотрится — `switches_to`."""
    language = BY_CODE.get(language_code(letters) or "")
    if language is None:
        return None
    words = _words(text)
    counts = Counter(
        script for word in words for char in _letters(word) if (script := script_of(char))
    )
    if _size(words) < MIN_LETTERS:
        return None
    ours = sum(counts[script] for script in (language.script, *language.also))
    if ours < SCRIPT_SHARE_MIN * sum(counts.values()):
        found: Mismatch | None = _other_script(counts, words)
    else:
        found = _same_script(text, words, language)
    return None if found is None else replace(found, letters=language.name)


def identify(text: str) -> str:
    """Язык текста одним словом — для разбивки замеров по языку сайта: код, если язык назван (шапка
    модуля), иначе письменность словами; без букв — «—»."""
    words = _words(text)
    counts = Counter(script for word in words if (script := _script(word)) is not None)
    if not counts:
        return "—"
    found = _other_script(counts, words)
    return found.code or found.said


def _switch_word(token: str, word: str) -> bool:
    """Слово переключателя в связном тексте: код из двух латинских букв — только заглавными («RU»,
    «Ru»): строчные «de», «en» — предлоги, «ru» — зона адреса; остальное — без учёта регистра."""
    if len(word) == 2 and word.isascii():
        return token[:1].isupper() and token.lower() == word.lower()
    return token.lower() == word.lower()


#: Пункты меню и блоки текста главной стоят через « · » (меню — `segment_text.site_text`, текст —
#: разбор главной), строки — переводом строки.
_ITEMS = re.compile(r"\s+[·|•/]\s+|\n")


def switches_to(text: str, letters: str) -> bool:
    """В тексте главной (меню, видимый текст) есть переключатель на версию на языке писем: сайт
    двуязычный, письмо на языке писем ему подходит. Пункт меню из одного слова — переключатель в
    любом регистре («lt · en · ru · lv»), в связном тексте — по правилу `_switch_word`."""
    language = BY_CODE.get(language_code(letters) or "")
    if language is None:
        return False
    words = {word.lower() for word in language.switch}
    for item in _ITEMS.split(text):
        runs = _runs(item)
        if len(runs) == 1 and runs[0].lower() in words:
            return True
        if any(_switch_word(found, word) for found in runs for word in language.switch):
            return True
    return False


def language_name(letters: str) -> str | None:
    """Язык писем по-русски («русский») — `None`, если таблица его не знает."""
    found = BY_CODE.get(language_code(letters) or "")
    return None if found is None else found.name
