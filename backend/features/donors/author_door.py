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


def author_door(url: str | None, title: str | None, nav: tuple[str, ...] = ()) -> str | None:
    """Где сайт зовёт авторов или рекламодателей. `None` — нигде.

    Возвращает то, что увидит человек в причине: страницу или пункт меню.
    """
    return _door_in_url(url) or _door_in_title(title) or _door_in_menu(nav)


def open_door(verdict: Judgement, door: str | None) -> Judgement:
    """Отказ «продаёт своё» при открытой двери — к человеку, а не в отказ.

    Трогается только `sells_own`: посредник со страницей для авторов всё
    равно посредник, а платформы и госзоны режутся до модели с другим видом.
    """
    if door is None or verdict.intent is not Intent.SELLS_OWN:
        return verdict
    if verdict.recommendation is not Recommendation.REJECT:
        return verdict
    return replace(
        verdict,
        recommendation=Recommendation.REVIEW,
        reason=f"{verdict.reason} · {door} — бренд принимает статьи, посмотри"[:256],
    )
