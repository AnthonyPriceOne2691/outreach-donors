"""Кому не пишем: домены, которые рекламодателями не бывают.

Список задан требованием — «wikipedia / gov / edu / крупные СМИ /
соцсети, исследования и PDF, домены DR > 80, партнёры из футера» — и
дополнен тем, что нашлось в находках обхода. Гос. и учебные зоны — общим
правилом с Этапом 1 (`donors/publisher_judge.is_public_zone`). Дополнения не догадки:
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
провайдера, и списком его не задать: DR берётся пакетом у Ahrefs при
пересчёте кандидатов, и только у тех, кого скоринг назвал «куплено»
или «спорно» (`crawl/big_sites.py`). Причина отказа — та же,
`BIG_SITE`. «Партнёры из футера» закрыты устройством обхода: подвал
в сбор ссылок не попадает вовсе.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.config import advertisers as cfg

# Модулем, а не именами: правило зовётся при проверке, и обратный импорт списков
# отсюда в судью площадки не упрётся в наполовину загруженный модуль.
from backend.features.donors import publisher_judge

#: Суффиксы зон, которым не пишут: государство и образование. Общее правило —
#: `is_public_zone` Этапа 1 (`donors/publisher_judge.py`): `gov.au`, `gov.in`, `gc.ca`,
#: `ac.jp`, `gouv.fr` и любая страна. До сверки ТЗ 11.10.2026 Этап 2 знал только эти
#: восемь, и `example.gov.au` выходил «куплено». Список остаётся тем, чего правило
#: по устройству не видит: корня, который сам — зона (`gov.uk` без имени перед ним).
#:
#: Списки ниже — настройками (`config/advertisers.py`, сверка ТЗ 11.10.2026); имена
#: и типы прежние — их читают и вне обхода.
BLOCKED_SUFFIXES: tuple[str, ...] = cfg.DENY_ZONES

#: Справочники и энциклопедии: ссылка на них редакционная всегда.
REFERENCE_DOMAINS: frozenset[str] = cfg.DENY_REFERENCE

#: Соцсети и видео: ссылка на профиль — не размещение.
SOCIAL_DOMAINS: frozenset[str] = cfg.DENY_SOCIAL

#: Инфраструктура сайта: она стояла в верхушке исходящих ссылок
#: у донора боевой ниши — регистратор с тремя сотнями ссылок и счётчик
#: с двумя. Ни тот, ни другой ничего у донора не покупал.
INFRASTRUCTURE_DOMAINS: frozenset[str] = cfg.DENY_INFRASTRUCTURE

#: Сокращатели: за ссылкой прячется кто угодно, и домен не говорит ничего.
#: В находках они шли пачкой по двенадцать ссылок и все dofollow.
SHORTENER_DOMAINS: frozenset[str] = cfg.DENY_SHORTENERS

#: Крупные СМИ: ссылка на них — цитата источника, а не размещение.
#: Список короткий намеренно: он растёт по мере находок, а не угадывается.
MAJOR_MEDIA: frozenset[str] = cfg.DENY_MEDIA

#: Расширения, за которыми документ, а не сайт с владельцем.
DOCUMENT_SUFFIXES: tuple[str, ...] = cfg.DENY_DOCUMENTS


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
    BIG_SITE = "big_site"  # DR > 80: крупный сайт ссылки не покупает (`big_sites.py`)
    HIDDEN = "hidden_advertiser"  # сеть партнёрок не назвала рекламодателя (`affiliate.py`)


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


def _public_zone(root: str) -> str | None:
    """Гос., учебная или военная зона домена — для причины отказа. `None` — не она.

    Правило — Этапа 1 (`is_public_zone`): у донора и у рекламодателя оно одно.
    Причина называет зону: «zone (.gov.au)» читается без справочника.
    """
    for suffix in BLOCKED_SUFFIXES:
        if root == suffix.lstrip(".") or root.endswith(suffix):
            return suffix
    if not publisher_judge.is_public_zone(root):
        return None
    labels = publisher_judge.dns_labels(root)
    tail = labels[-1:] if labels[-1] in publisher_judge.PUBLIC_ZONE_TLDS else labels[-2:]
    return "." + ".".join(tail)


def is_document(url: str) -> bool:
    """Ссылка ведёт на документ — PDF, таблицу, презентацию, — а не на страницу сайта.
    Параметры и якорь — не часть имени файла: `report.pdf?dl=1`, `report.pdf#page=2`."""
    return url.lower().split("#")[0].split("?")[0].endswith(DOCUMENT_SUFFIXES)


def denial_for(target_root: str, url: str = "") -> Denial | None:
    """Почему домену не пишут. `None` — кандидат, проверяем дальше.

    Сравнение по корневому домену, а не по хосту: `en.wikipedia.org`
    и `wikipedia.org` — одно и то же, и держать в списке оба значит
    однажды забыть третий. `url` — одна ссылка: документ за ней — отказ.
    Ссылок на домен бывает много, и скоринг сверяет каждую (`is_document`).
    """
    root = target_root.strip().lower()
    if not root:
        return None

    if is_document(url):
        return Denial(DenyReason.DOCUMENT, url)

    zone = _public_zone(root)
    if zone is not None:
        return Denial(DenyReason.ZONE, zone)

    for reason, domains in _BY_DOMAIN:
        if root in domains:
            return Denial(reason, root)
    return None
