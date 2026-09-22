"""Кому не пишем: домены, которые рекламодателями не бывают.

Список задан требованием — «wikipedia / gov / edu / крупные СМИ /
соцсети, исследования и PDF, домены DR > 80, партнёры из футера» — и
дополнен тем, что нашлось в находках обхода. Дополнения не догадки:
в верхушке исходящих ссылок боевой ниши стояли **регистратор домена
и счётчик посещаемости**, а среди частых получателей — соцсети
и сокращатели ссылок. Письмо любому из них ушло бы в пустоту, а доля
ложных рекламодателей ограничена десятью процентами.

**Отсев идёт до скоринга, а не после.** Домен из списка не «набрал мало
баллов» — он вообще не кандидат, и различать это важно: кандидат
с малым баллом ждёт человека, а этот не ждёт никого.

**Причина отказа называется.** «Отсеян» без причины превращает разбор
спорного случая в чтение кода; с причиной — в одну строку отчёта.

**Чего в этом списке нет и почему.** `DR > 80` требует метрики
провайдера, а её на этом шаге нет: отсев по DR живёт там, где
рекламодатель попадает в базу, и стоит юнитов. «Партнёры из футера»
закрыты устройством обхода: подвал в сбор ссылок не попадает вовсе.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: Суффиксы зон, которым не пишут: государство и образование.
BLOCKED_SUFFIXES: tuple[str, ...] = (
    ".gov", ".gov.uk", ".gov.za", ".mil", ".edu", ".ac.uk", ".ac.za", ".edu.au",
)  # fmt: skip

#: Справочники и энциклопедии: ссылка на них редакционная всегда.
REFERENCE_DOMAINS: frozenset[str] = frozenset(
    {
        "wikipedia.org", "wikimedia.org", "wiktionary.org", "britannica.com",
        "archive.org", "doi.org", "arxiv.org", "researchgate.net", "scholar.google.com",
    }
)  # fmt: skip

#: Соцсети и видео: ссылка на профиль — не размещение.
SOCIAL_DOMAINS: frozenset[str] = frozenset(
    {
        "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
        "youtube.com", "youtu.be", "tiktok.com", "pinterest.com", "reddit.com",
        "t.me", "telegram.me", "whatsapp.com", "threads.net", "vk.com",
    }
)  # fmt: skip

#: Инфраструктура сайта: она стояла в верхушке исходящих ссылок
#: у донора боевой ниши — регистратор с тремя сотнями ссылок и счётчик
#: с двумя. Ни тот, ни другой ничего у донора не покупал.
INFRASTRUCTURE_DOMAINS: frozenset[str] = frozenset(
    {
        "statcounter.com", "regery.com", "google-analytics.com", "googletagmanager.com",
        "cloudflare.com", "gravatar.com", "wordpress.org", "wordpress.com", "wp.com",
        "jquery.com", "w3.org", "schema.org", "gstatic.com", "googleapis.com",
        "monsterinsights.com", "yoast.com", "elementor.com", "cookiebot.com",
    }
)  # fmt: skip

#: Сокращатели: за ссылкой прячется кто угодно, и домен не говорит ничего.
#: В находках они шли пачкой по двенадцать ссылок и все dofollow.
SHORTENER_DOMAINS: frozenset[str] = frozenset(
    {"bit.ly", "goo.gl", "tinyurl.com", "ow.ly", "buff.ly", "cutt.ly", "t.co", "rb.gy"}
)

#: Крупные СМИ: ссылка на них — цитата источника, а не размещение.
#: Список короткий намеренно: он растёт по мере находок, а не угадывается.
MAJOR_MEDIA: frozenset[str] = frozenset(
    {
        "bbc.com", "bbc.co.uk", "cnn.com", "reuters.com", "theguardian.com",
        "nytimes.com", "forbes.com", "bloomberg.com", "espn.com", "skysports.com",
        "independent.co.uk", "dailymail.co.uk", "news24.com", "iol.co.za",
        "hbr.org", "mybroadband.co.za",
    }
)  # fmt: skip

#: Расширения, за которыми документ, а не сайт с владельцем.
DOCUMENT_SUFFIXES: tuple[str, ...] = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")


class DenyReason(StrEnum):
    """Почему домен не кандидат. Причина называется вслух."""

    ZONE = "zone"  # государство или образование
    REFERENCE = "reference"  # справочник, энциклопедия, наука
    SOCIAL = "social"  # соцсеть или видеоплощадка
    INFRASTRUCTURE = "infrastructure"  # счётчик, регистратор, движок
    SHORTENER = "shortener"  # за ссылкой прячется кто угодно
    MEDIA = "media"  # крупное СМИ: ссылка на него — цитата
    DOCUMENT = "document"  # это файл, а не сайт
    OWN_BRAND = "own_brand"  # тот же бренд донора в другой зоне


@dataclass(frozen=True, slots=True)
class Denial:
    """Отказ с причиной и тем, что именно совпало."""

    reason: DenyReason
    matched: str


#: Списки в порядке проверки. Порядок не важен для вердикта, но важен
#: для причины: она называется по первому совпадению.
_BY_DOMAIN: tuple[tuple[DenyReason, frozenset[str]], ...] = (
    (DenyReason.REFERENCE, REFERENCE_DOMAINS),
    (DenyReason.SOCIAL, SOCIAL_DOMAINS),
    (DenyReason.INFRASTRUCTURE, INFRASTRUCTURE_DOMAINS),
    (DenyReason.SHORTENER, SHORTENER_DOMAINS),
    (DenyReason.MEDIA, MAJOR_MEDIA),
)


def _label(root: str) -> str:
    """Имя домена без зоны: `sportsboom.co.za` → `sportsboom`."""
    return root.split(".", 1)[0] if "." in root else root


def is_own_brand(target_root: str, donor_root: str) -> bool:
    """Тот же бренд донора в другой зоне — не рекламодатель.

    Найдено первым прогоном по настоящим данным: `sportsboom.co.za`
    ссылался на `sportsboom.com` и попал в спорные. Корни разные,
    владелец один, и письмо ему ушло бы от его же имени.

    Сравнение по имени до зоны — приём грубый: `bet.co.za` и `bet.com`
    бывают разными компаниями. Поэтому это не отказ по списку,
    а пометка «смотрит человек» там, где имя совпало.
    """
    if not target_root or not donor_root:
        return False
    return _label(target_root) == _label(donor_root)


def denial_for(target_root: str, url: str = "") -> Denial | None:
    """Почему домену не пишут. `None` — кандидат, проверяем дальше.

    Сравнение по корневому домену, а не по хосту: `en.wikipedia.org`
    и `wikipedia.org` — одно и то же, и держать в списке оба значит
    однажды забыть третий.
    """
    root = target_root.strip().lower()
    if not root:
        return None

    if url.lower().split("?")[0].endswith(DOCUMENT_SUFFIXES):
        return Denial(DenyReason.DOCUMENT, url)

    for suffix in BLOCKED_SUFFIXES:
        if root == suffix.lstrip(".") or root.endswith(suffix):
            return Denial(DenyReason.ZONE, suffix)

    for reason, domains in _BY_DOMAIN:
        if root in domains:
            return Denial(reason, root)
    return None
