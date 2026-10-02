"""Словарь разделов сайта: какие слаги пробовать и на каком языке.

Вынесено из `pages.py` отдельным модулем: там — как ходить по сайту,
здесь — по каким адресам, и растут эти две темы по-разному. Словарь
пополняется от каждого боевого прогона, обход — почти никогда.

Списки не исчерпывающие и не должны быть: остальное добирается по
ссылкам с главной, где раздел назван словами (`LINK_MARKERS`).
"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import parse_qsl, unquote

from backend.features.core.domain import PageKind
from backend.shared.net.url_parts import parse_url

# Слаги по видам страниц. Списки не исчерпывающие и не должны быть:
# остальное добирается по ссылкам с главной, где раздел назван словами.
SLUGS: dict[PageKind, tuple[str, ...]] = {
    PageKind.MONEY: (
        "write-for-us", "write-for-me", "guest-post", "guest-posting", "submit-article",
        "advertise", "advertising", "advertise-with-us", "sponsored-post", "media-kit",
        "partnership", "work-with-us",
        # Ниже — для опознания ссылки и вида страницы: круговой обход до них
        # не доходит, так что бюджет угадывания они не трогают (ревью #120).
        "sponsorship", "sponsored-content", "work-with-me", "partner-with-us", "contribute",
        "become-a-contributor", "guest-blogging", "guest-author", "rate-card",
    ),
    PageKind.CONTACT: (
        "contact", "contacts", "contact-us", "contactus", "get-in-touch",
        "kontakt", "kontak", "contacto", "contatti", "hubungi-kami",
        # Пресса и поддержка отвечают людьми, а не формой: с этих страниц
        # в боевом прогоне снялись press@ и support@.
        "press", "press-room", "media", "support", "help",
    ),
    PageKind.ABOUT: (
        "about", "about-us", "aboutus", "team", "our-team", "imprint", "impressum",
        "masthead", "staff", "authors", "editorial-guidelines",
    ),
    # Правовые: оператора сайта указывают там, где обязаны, а не там,
    # где удобно. Вес у таких адресов низкий, но это лучше, чем платный
    # запрос ради того же самого.
    PageKind.LEGAL: (
        "terms", "terms-of-service", "terms-and-conditions", "terms-of-use",
        "privacy", "privacy-policy", "disclaimer", "legal", "user-agreement",
    ),
}  # fmt: skip

#: Порядок видов страниц при обходе — по убыванию ценности адреса.
WALK_ORDER = (PageKind.MONEY, PageKind.CONTACT, PageKind.ABOUT, PageKind.LEGAL)

# Слаги на языке сайта. Отдельным словарём, а не вперемешку с основными,
# по одной причине: потолок попыток на домен (24) меньше числа слагов, и
# круговой обход доходит примерно до шестого слага каждого вида. Дописать
# локализованные слаги в конец списка — значит не пробовать их никогда;
# `hubungi-kami` на десятой позиции уже сейчас не пробуется ни разу.
# Поэтому язык сайта определяется по главной, и его слаги идут ПЕРВЫМИ.
#
# Половина базы, ради которой всё делается, — не латинская: Индонезия,
# арабские страны, Таиланд. Другая половина — СНГ, и русских слагов не было
# ни в одном из двух прежних скрейперов, хотя это 46 % площадок.
LOCALIZED_SLUGS: dict[str, dict[PageKind, tuple[str, ...]]] = {
    "ru": {
        # `reklama` — это и есть страница, где называют цену. Кириллица — в конце
        # каждого вида: угадывать её круговой обход почти не доходит, но раздел,
        # найденный по ссылке с главной (`/реклама/`, `/контакты/`), без неё
        # получал вид главной, то есть вес вчетверо меньше заслуженного.
        PageKind.MONEY: (
            "reklama", "razmeshchenie-reklamy", "sotrudnichestvo", "uslugi",
            "реклама", "размещение-рекламы", "сотрудничество",
        ),
        PageKind.CONTACT: (
            "kontakty", "kontakt", "obratnaya-svyaz", "svyazatsya-s-nami",
            "контакты", "обратная-связь", "связаться-с-нами",
        ),
        PageKind.ABOUT: (
            "o-nas", "o-proekte", "o-sayte", "redakciya", "redakciya-sayta",
            "о-нас", "о-проекте", "редакция",
        ),
        PageKind.LEGAL: (
            "politika-konfidencialnosti", "usloviya-ispolzovaniya", "pravila",
            "политика-конфиденциальности", "пользовательское-соглашение",
        ),
    },
    "id": {
        PageKind.MONEY: ("iklan", "pasang-iklan", "jasa-iklan"),
        PageKind.CONTACT: ("hubungi-kami", "hubungi", "kontak", "page/kontak", "redaksi"),
        PageKind.ABOUT: ("tentang-kami", "tentang", "profil"),
        PageKind.LEGAL: (
            "syarat-ketentuan", "syarat-dan-ketentuan", "kebijakan-privasi", "ketentuan-layanan",
        ),
    },
    "ar": {
        PageKind.MONEY: ("أعلن-معنا",),
        PageKind.CONTACT: ("اتصل-بنا", "اتصل", "تواصل-معنا"),
        PageKind.ABOUT: ("من-نحن",),
        PageKind.LEGAL: ("سياسة-الخصوصية",),
    },
    "th": {
        PageKind.CONTACT: ("ติดต่อเรา", "ติดต่อ"),
        PageKind.ABOUT: ("เกี่ยวกับเรา",),
    },
    "es": {
        PageKind.MONEY: ("publicidad", "anunciate"),
        PageKind.CONTACT: ("contacto", "contactanos", "contactenos"),
        PageKind.ABOUT: ("quienes-somos", "sobre-nosotros", "nosotros"),
        PageKind.LEGAL: ("aviso-legal", "politica-de-privacidad"),
    },
    "pt": {
        PageKind.MONEY: ("publicidade", "anuncie"),
        PageKind.CONTACT: ("contato", "contatos", "fale-conosco"),
        PageKind.ABOUT: ("sobre", "sobre-nos", "quem-somos"),
        PageKind.LEGAL: ("politica-de-privacidade",),
    },
    "fr": {
        PageKind.MONEY: ("publicite", "annoncer"),
        PageKind.CONTACT: ("contactez-nous", "nous-contacter", "contact"),
        PageKind.ABOUT: ("a-propos", "qui-sommes-nous"),
        PageKind.LEGAL: ("mentions-legales", "politique-de-confidentialite"),
    },
    "de": {
        PageKind.MONEY: ("werbung", "mediadaten"),
        PageKind.CONTACT: ("kontakt", "kontakt-impressum", "presse"),
        PageKind.ABOUT: ("impressum", "ueber-uns", "team"),
        PageKind.LEGAL: ("datenschutz", "datenschutzerklaerung", "agb"),
    },
}  # fmt: skip

# Зона домена как подсказка о языке. Слабее, чем `lang` у главной, и
# спрашивается только когда её нет. Многоязычные зоны (`.be`, `.ch`, `.ca`,
# родовой `.co`) намеренно отсутствуют: угадывание там вредит.
LANGUAGE_BY_TLD: dict[str, str] = {
    "ru": "ru", "su": "ru", "by": "ru", "kz": "ru", "ua": "ru", "uz": "ru",
    "kg": "ru", "tj": "ru", "md": "ru", "am": "ru", "az": "ru", "ge": "ru",
    "xn--p1ai": "ru",
    "id": "id",
    "th": "th",
    "ae": "ar", "sa": "ar", "eg": "ar", "qa": "ar", "kw": "ar", "jo": "ar",
    "dz": "ar", "ma": "ar", "ly": "ar", "om": "ar", "bh": "ar", "iq": "ar",
    "es": "es", "mx": "es", "cl": "es", "pe": "es", "ve": "es", "ec": "es",
    "bo": "es", "uy": "es", "py": "es", "gt": "es", "cr": "es", "pa": "es",
    "do": "es", "ar": "es",
    "br": "pt", "pt": "pt",
    "fr": "fr",
    "de": "de", "at": "de",
}  # fmt: skip

#: Слова разделов на языках сайтов — для поиска раздела по ТЕКСТУ ссылки.
#: Стоят дешевле слагов: главная скачана всё равно, а её ссылки разбираются
#: без единого лишнего запроса. Для не-латинских сайтов это главный путь,
#: потому что до угадывания слагов бюджет попыток может и не дожить.
_LINK_WORDS: frozenset[str] = frozenset(
    {
        "write for us", "advertise", "contact", "about us", "guest post", "terms",
        "контакты", "реклама", "о нас", "о проекте", "редакция", "сотрудничество",
        # Слова целиком, а не основа `redak`: раздел узнаётся по словам
        # (`names_section`), и основа не совпала бы ни с одним из них.
        "redaksi", "redakcja", "redakce", "redakcia", "redakciya", "redaktion",
        "iklan", "hubungi", "tentang", "syarat", "kebijakan", "ketentuan",
        "اتصل", "أعلن", "ติดต่อ", "publicidad", "publicidade", "contato",
        "werbung", "impressum", "publicite", "contactez",
    }
)  # fmt: skip

# Те же слова для поиска по ссылкам главной: там раздел может лежать
# по адресу вида /p/12345, и угадать его по слагу нельзя.
LINK_MARKERS: frozenset[str] = (
    frozenset(slug for slugs in SLUGS.values() for slug in slugs)
    | frozenset(
        slug for by_kind in LOCALIZED_SLUGS.values() for slugs in by_kind.values() for slug in slugs
    )
    | _LINK_WORDS
)

#: Узнаются, но не угадываются. Страница о cookie адресов редакции не даёт,
#: ходить за ней незачем, но встреченная по ссылке она правовая: адрес
#: с неё — оператор данных, а не редакция (ревью #144: `/cookie-policy`
#: без «legal» в пути считалась главной).
RECOGNIZED_ONLY: dict[PageKind, tuple[str, ...]] = {
    PageKind.LEGAL: ("cookie-policy", "cookies", "cookie-notice", "cookie-statement"),
}

#: Все слаги вида на всех языках. Нужны при опознании УЖЕ скачанной
#: страницы: её могли найти по ссылке с главной, а не угадать, и язык
#: ссылки тогда неизвестен.
ALL_SLUGS: dict[PageKind, frozenset[str]] = {
    kind: frozenset(SLUGS[kind])
    | frozenset(slug for by_kind in LOCALIZED_SLUGS.values() for slug in by_kind.get(kind, ()))
    | frozenset(RECOGNIZED_ONLY.get(kind, ()))
    for kind in WALK_ORDER
}

# Раздел узнаётся по СЛОВАМ, а не по подстроке. Подстрока работала, пока
# слаги были английскими, и то с огрехами (`press` внутри `wordpress`);
# с локальными словами она стала ловить заголовки статей: `uslugi` внутри
# `gosuslugi`, `o-nas` внутри `contract-to-nasa`, `sobre` и `pravila`
# в каждой второй новости. Статья, принятая за раздел, отдавала чужой
# адрес из текста как контакт донора и съедала бюджет обхода.
#
# Разделители — явным списком, а не `\W`: знаки огласовки тайского
# и арабского для `\w` не буквы, и `\W+` резал бы слово пополам.
_SEPARATORS = re.compile(r"[\s\-_.,;:!?/\\|&+~'\"()\[\]{}«»“”„…–—·•،]+")

# Не слова раздела: расширения страниц и номера (`contact-us-2`, `/2024/05/`).
_NOISE_WORDS = frozenset({"html", "htm", "php", "asp", "aspx", "jsp", "shtml"})

# Сколько лишних слов терпит название раздела. `reklama-na-sayte` и
# `contact-us-today` — раздел; `tudo-sobre-o-caso-1` и заголовок в пять
# слов — статья. Слова раздела при этом стоят с края: в `novye-pravila-
# parkovki` правила посередине, и это новость, а не правила сайта.
_EXTRA_WORDS = 2

# Длинное слово раздела узнаётся и как начало или конец слова: `contacta`,
# `kontaktformular`, `pressekontakt`, `supportcenter`. Живой прогон 30.09.2026
# на донорах из базы нашёл, что точное сравнение теряло `/contacta/`, который
# подстрока находила. Короткие слова (`sobre`, `uslugi`, `iklan`, `press`)
# так не сравниваются: внутри чужих слов они сидят случайно — `gosuslugi`,
# `wordpress`, `knoblauchpresse`.
_STEM_MIN_LETTERS = 7

# Последнее слово многословного маркера узнаётся и в форме: `guest-posts`,
# `submit-articles`, `privacy-policies`. Ревью #120: точное сравнение теряло
# страницы продажи размещения во множественном числе — для гест-постинга самые
# ценные. Окончание → чем заменить; основа короче трёх букв не берётся, иначе
# `about-uses` становится `about-us`.
_ENDINGS = (("ies", "y"), ("es", ""), ("s", ""), ("ing", ""), ("ing", "e"), ("ed", ""), ("ed", "e"))

# Текст ссылки бывает длиннее названия раздела: «Advertise with The Verge»,
# «Contact the editorial team» — имя издания после слова раздела, а у WordPress
# без ЧПУ (`?page_id=12`) текст — единственный признак. Длинное слово раздела
# В НАЧАЛЕ текста узнаётся во фразе до пяти слов; короткие слова (`sobre`,
# `iklan`) так не сравниваются — ими начинаются заголовки.
_LEAD_MAX_WORDS = 5


def _words(text: str) -> tuple[str, ...]:
    return tuple(
        word
        for word in _SEPARATORS.split(text.lower())
        if word and not word.isdigit() and word not in _NOISE_WORDS
    )


@lru_cache(maxsize=32)
def _markers_by_length(markers: frozenset[str]) -> dict[int, frozenset[tuple[str, ...]]]:
    """Маркеры словами, по числу слов. Слаг `page/kontak` — по последней части."""
    grouped: dict[int, set[tuple[str, ...]]] = {}
    for marker in markers:
        words = _words(marker.rsplit("/", 1)[-1])
        if words:
            grouped.setdefault(len(words), set()).add(words)
        if len(words) > 1:
            # Слитная форма — тоже маркер: `mediakit`, `writeforus`, `guestpost`.
            grouped.setdefault(1, set()).add(("".join(words),))
    return {length: frozenset(group) for length, group in grouped.items()}


@lru_cache(maxsize=32)
def _stems(markers: frozenset[str]) -> tuple[str, ...]:
    """Однословные маркеры, достаточно длинные, чтобы узнаваться внутри слова."""
    single = _markers_by_length(markers).get(1, frozenset())
    return tuple(sorted(words[0] for words in single if len(words[0]) >= _STEM_MIN_LETTERS))


def names_section(text: str, markers: frozenset[str]) -> bool:
    """Называет ли короткий текст раздел: маркер в начале или в конце."""
    words = _words(text)
    return _marker_at_edge(words, markers) or _stem_at_edge(words, markers)


def _marker_at_edge(words: tuple[str, ...], markers: frozenset[str]) -> bool:
    total = len(words)
    return any(
        length <= total <= length + _EXTRA_WORDS
        and (_known(words[:length], known) or _known(words[total - length :], known))
        for length, known in _markers_by_length(markers).items()
    )


def _known(part: tuple[str, ...], known: frozenset[tuple[str, ...]]) -> bool:
    """Маркер целиком — или многословный с последним словом в форме."""
    if part in known:
        return True
    if len(part) < 2:
        return False
    head, last = part[:-1], part[-1]
    return any((*head, base) in known for base in _bases(last))


def _bases(word: str) -> tuple[str, ...]:
    """Начальные формы слова: `posts` → `post`, `policies` → `policy`."""
    return tuple(
        word[: len(word) - len(ending)] + replacement
        for ending, replacement in _ENDINGS
        if word.endswith(ending) and len(word) - len(ending) + len(replacement) >= 3
    )


def _stem_at_edge(words: tuple[str, ...], markers: frozenset[str]) -> bool:
    if not 0 < len(words) <= 1 + _EXTRA_WORDS:
        return False
    edges = {words[0], words[-1]}
    return any(
        word.startswith(stem) or word.endswith(stem) for word in edges for stem in _stems(markers)
    )


def link_text_names_section(text: str, markers: frozenset[str]) -> bool:
    """Текст ссылки: как `names_section`, плюс длинное слово раздела в начале фразы."""
    if names_section(text, markers):
        return True
    words = _words(text)
    return 0 < len(words) <= _LEAD_MAX_WORDS and words[0].startswith(_stems(markers))


def url_names_section(url: str, markers: frozenset[str]) -> bool:
    """Ведёт ли адрес в раздел: любой сегмент пути или значение параметра.

    Параметры — ради старых движков: `index.php?page=contact` подстрока
    находила, и терять такие сайты при переходе на слова незачем.
    """
    parsed = parse_url(url)
    if parsed is None:
        return False
    parts = [*unquote(parsed.path).split("/"), *(value for _, value in parse_qsl(parsed.query))]
    return any(names_section(part, markers) for part in parts)
