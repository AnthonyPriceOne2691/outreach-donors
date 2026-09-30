"""Словарь разделов сайта: какие слаги пробовать и на каком языке.

Вынесено из `pages.py` отдельным модулем: там — как ходить по сайту,
здесь — по каким адресам, и растут эти две темы по-разному. Словарь
пополняется от каждого боевого прогона, обход — почти никогда.

Списки не исчерпывающие и не должны быть: остальное добирается по
ссылкам с главной, где раздел назван словами (`LINK_MARKERS`).
"""

from __future__ import annotations

from backend.features.core.domain import PageKind

# Слаги по видам страниц. Списки не исчерпывающие и не должны быть:
# остальное добирается по ссылкам с главной, где раздел назван словами.
SLUGS: dict[PageKind, tuple[str, ...]] = {
    PageKind.MONEY: (
        "write-for-us", "write-for-me", "guest-post", "guest-posting", "submit-article",
        "advertise", "advertising", "advertise-with-us", "sponsored-post", "media-kit",
        "partnership", "work-with-us",
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
        # `reklama` — это и есть страница, где называют цену.
        PageKind.MONEY: ("reklama", "razmeshchenie-reklamy", "sotrudnichestvo", "uslugi"),
        PageKind.CONTACT: ("kontakty", "kontakt", "obratnaya-svyaz", "svyazatsya-s-nami"),
        PageKind.ABOUT: ("o-nas", "o-proekte", "o-sayte", "redakciya", "redakciya-sayta"),
        PageKind.LEGAL: ("politika-konfidencialnosti", "usloviya-ispolzovaniya", "pravila"),
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
        PageKind.CONTACT: ("kontakt", "kontakt-impressum"),
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
        "redak", "iklan", "hubungi", "tentang", "syarat", "kebijakan", "ketentuan",
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

#: Все слаги вида на всех языках. Нужны при опознании УЖЕ скачанной
#: страницы: её могли найти по ссылке с главной, а не угадать, и язык
#: ссылки тогда неизвестен.
ALL_SLUGS: dict[PageKind, frozenset[str]] = {
    kind: frozenset(SLUGS[kind])
    | frozenset(slug for by_kind in LOCALIZED_SLUGS.values() for slug in by_kind.get(kind, ()))
    for kind in WALK_ORDER
}
