"""Дверь для авторов: бренд, который принимает статьи.

**Правило по ТИПУ СТРАНИЦЫ, а не по нише.** Бренд со страницей «пишите для
нас» по способу заработка честно `sells_own`, но статьи у себя публикует —
для гест-постинга это кандидат, и решать о нём человеку, а не модели. Прогон
№18: cloudways, heimdalsecurity, userpilot, ringcentral отрезаны, хотя
выдача пришла прямо со страницы приёма авторов.

⚠ **Признак — сама страница, а не слово в адресе.** `/blog/guest-posting-
opportunities` и `/glossary/guest-blogging` — статьи ПРО гостевые посты, их
пишут те, кто продаёт инструменты; дверью они не являются. Поэтому последний
сегмент пути сверяется целиком или по началу, а не вхождением.

⚠ **Подборка площадок — тоже не дверь.** «80+ Technology Write for Us
Sites», «Top 12 Guest Post Sites»: адрес и заголовок говорят «пишите для
нас», а зовёт страница к чужим — её пишут агентства и биржи. Очередь №18:
`w3era.com/write-for-us-technology-blogs`. Отличает её заголовок: число
или «лучшие» рядом со словом «сайты».

Дверь не принимает домен сама. Она только не даёт отрезать «продаёт своё»
молча: такой домен уходит человеку с пометкой, где дверь нашлась.
"""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urlsplit

from backend.features.donors.publisher_judge import Intent, Judgement, Recommendation

#: Сегмент пути, который где угодно в себе значит «пишите для нас».
AUTHOR_SLUG_ANYWHERE = re.compile(r"write-?for-?(us|me)|writeforus")

#: Заголовок подборки площадок: число (не год) или «лучшие / топ / список»,
#: а дальше в том же куске заголовка — «сайты», «блоги», «площадки».
#: Словарь рынка, а не ниши — как у страниц для авторов ниже.
SITE_LIST = re.compile(
    r"(?:\b(?!(?:19|20)\d\d\b)\d+\+?"
    r"|\b(?:best|top|list|besten?|mejores|meilleurs|migliori|melhores|najlepsze)\b)"
    r"[^|]*?\b(?:sites|websites|blogs|platforms|opportunities|seiten|webseiten|sitios"
    r"|siti|stron[ay]?|blogów|plataformas|piattaforme)\b",
    re.IGNORECASE,
)

#: Сегмент пути целиком — страница для авторов.
AUTHOR_SLUG = re.compile(
    r"^(?:contribute(?:-to-[\w-]+)?|contributors?|contributor-(?:guidelines|program|policy)"
    r"|become-[\w-]*(?:contributor|writer|author|blogger)|write-for-[\w-]+"
    r"|guest-(?:post|blog|author|article)s?(?:-[a-z]+)?-(?:guidelines|submission|policy|rules)"
    r"(?:-[\w-]+)?"
    r"|submit-(?:a-)?(?:guest-post|article|post)"
    # Рынки: немецкий, испанский, итальянский, французский, португальский,
    # польский, нидерландский. Словарь рынка, а не ниши — как у корзины.
    r"|schreib[\w-]*-f(?:ue|u)r-uns|gastbeitr(?:ag|aege)|gastautor(?:en)?(?:-werden)?"
    r"|escrib[\w-]*-para-nosotros|colabora(?:-con-nosotros)?|scrivi-per-noi"
    r"|ecrire-pour-nous|devenir-(?:auteur|contributeur|redacteur)"
    r"|escreva-para-(?:nos|a-gente)|napisz-dla-nas|schrijf-voor-ons)$"
)

#: Те же слова в заголовке выдачи или пункте меню главной.
AUTHOR_PHRASES: tuple[str, ...] = (
    "write for us", "write for me", "become a contributor", "contribute to",
    "contributor guidelines", "guest post guidelines", "guest posting guidelines",
    "submit a guest post", "guest author", "become a writer", "become an author",
    "schreiben sie für uns", "gastautor", "escribe para nosotros",
    "scrivi per noi", "écrire pour nous", "escreva para nós", "napisz dla nas",
    "schrijf voor ons",
)  # fmt: skip

#: Пункт меню «реклама» — такая же открытая дверь, только за деньги.
#: ⚠ Только ЦЕЛЫЙ пункт: «Advertising solutions» у рекламной платформы —
#: её продукт, а не место на её сайте.
AD_MENU: frozenset[str] = frozenset({
    "advertise", "advertise with us", "advertising", "media kit", "sponsored posts",
    "sponsorship", "werben", "werbung", "mediadaten", "publicidad", "anúnciate",
    "anuncie", "pubblicità", "publicité", "reklama", "adverteren", "реклама",
})  # fmt: skip


def _last_segment(url: str) -> str:
    segment = urlsplit(url).path.lower().rstrip("/").rsplit("/", 1)[-1]
    return segment.rsplit(".", 1)[0] if "." in segment else segment


def _door_in_url(url: str | None) -> str | None:
    segment = _last_segment(url) if url else ""
    if segment and (AUTHOR_SLUG_ANYWHERE.search(segment) or AUTHOR_SLUG.match(segment)):
        return f"страница «{segment}»"
    return None


def _door_in_title(title: str | None) -> str | None:
    lowered = (title or "").lower()
    if any(phrase in lowered for phrase in AUTHOR_PHRASES):
        return "заголовок страницы зовёт авторов"
    return None


def _door_in_menu(nav: tuple[str, ...]) -> str | None:
    for item in nav:
        label = " ".join(item.lower().split())
        if label in AD_MENU or label in AUTHOR_PHRASES:
            return f"меню главной: «{item}»"
    return None


def lists_sites(title: str | None) -> bool:
    """Заголовок подборки чужих площадок: страница зовёт не к себе."""
    return title is not None and SITE_LIST.search(title) is not None


def author_door(url: str | None, title: str | None, nav: tuple[str, ...] = ()) -> str | None:
    """Где сайт зовёт авторов или рекламодателей. `None` — нигде.

    Возвращает то, что увидит человек в причине: страницу или пункт меню.
    Подборка площадок дверью страницы не считается, меню сайта — считается.
    """
    page = None if lists_sites(title) else (_door_in_url(url) or _door_in_title(title))
    return page or _door_in_menu(nav)


def opens(intent: str | None, recommendation: str | None) -> bool:
    """Открывает ли дверь этот вердикт: отказ «продаёт своё».

    Трогается только `sells_own`: посредник со страницей для авторов всё
    равно посредник, а платформы и госзоны режутся до модели с другим видом.
    """
    return intent == Intent.SELLS_OWN.value and recommendation == Recommendation.REJECT.value


def opened_reason(reason: str, door: str) -> str:
    """Причина вердикта, который дверь перевела из отказа к человеку."""
    return f"{reason} · {door} — бренд принимает статьи, посмотри"[:256]


def open_door(verdict: Judgement, door: str | None) -> Judgement:
    """Отказ «продаёт своё» при открытой двери — к человеку, а не в отказ.

    То же правило действует и позже, когда дверь нашлась проверкой меню
    главных у очереди (`donors.doors`): судья меню не видел, а правило
    от этого не меняется.
    """
    if door is None or not opens(verdict.intent.value, verdict.recommendation.value):
        return verdict
    return replace(
        verdict,
        recommendation=Recommendation.REVIEW,
        reason=opened_reason(verdict.reason, door),
    )
