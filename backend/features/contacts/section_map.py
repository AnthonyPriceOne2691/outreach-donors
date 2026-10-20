"""Разделы сайта из его карты (sitemap.xml) — для поиска глубже.

Обычный проход знает разделы двумя путями: ссылки главной и угаданные слаги. Мимо
обоих идёт раздел, на который главная не ссылается, с адресом, которого нет в
словаре слагов, — `/p/hubungi-kami`, `/info/kontaktai`, `/pages/write-to-us`. Карта
сайта перечисляет его сама, и для одного домена по кнопке её чтение стоит пары
запросов (`site_steps.walk_pages`).

**Из карты берутся только разделы, и только короткие.** Карта блога — тысячи статей,
и статья «как связаться с банком» по словам похожа на раздел контактов. Поэтому
адрес должен называть раздел (`pages.kind_of`) и лежать не глубже двух сегментов пути:
у разделов адрес короткий, у статей — длинный. Первыми — дорогие виды (`WALK_ORDER`),
внутри вида — короче.

**Индекс карт — по одной-двум дочерним, страницы сайта первыми.** У WordPress и
плагинов SEO разделы лежат в карте страниц (`page-sitemap.xml`,
`wp-sitemap-posts-page-1.xml`), а не в карте записей: она и читается первой.

XML читается выражением, а не разбором: чужой XML с сущностями разворачивается
в гигабайты в памяти, а нам нужны только адреса из `<loc>` (как и обходу Этапа 2,
`crawl/sitemap.py`).

**Запросы к карте — запросы обхода** (`PageFetcher.text`): в счёт запросов к сайту и в
его потолок попыток. Иначе итог глубже называл бы меньше запросов, чем ушло, а сайт с
картой на десяток дочерних получал бы их сверх потолка.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from backend.features.contacts.extract import ascii_domain
from backend.features.contacts.pages import PageFetcher, kind_of
from backend.features.contacts.slugs import WALK_ORDER
from backend.features.core.domain import PageKind

#: Где карта лежит у большинства движков. Пробуется до первой ответившей.
MAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")

#: Дочерних карт индекса читаем не больше: разделы — в одной-двух.
MAX_CHILD_MAPS = 2

#: Разделов из карты отдаём не больше: остальной бюджет — ссылкам главной и слагам.
MAX_SECTIONS = 12

#: Раздел — адрес не глубже двух сегментов пути (`/about/contact/`).
MAX_DEPTH = 2

#: Сколько текста карты читаем. Карта на десятки мегабайт бывает, разделы — в начале.
MAX_MAP_CHARS = 3_000_000

_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]{1,2048})\s*(?:\]\]>)?\s*</loc>", re.I)
_IS_INDEX = re.compile(r"<sitemapindex", re.IGNORECASE)


async def _read(fetcher: PageFetcher, url: str) -> str | None:
    """Текст одной карты. Не ответила — `None`: карты у сайта может и не быть."""
    text = await fetcher.text(url)
    if text is None or "<loc" not in text[:200_000].lower():
        return None
    return text[:MAX_MAP_CHARS]


def _children(text: str) -> list[str]:
    """Дочерние карты индекса: карта страниц первой, затем остальные по порядку."""
    found = [match.group(1) for match in _LOC.finditer(text)]
    return sorted(found, key=lambda url: "page" not in url.lower())[:MAX_CHILD_MAPS]


def _same_site(url: str, site_host: str) -> bool:
    """Адрес — сайта или его поддомена. Обе стороны в ASCII (`ascii_domain`): карта пишет
    имя Юникодом, хост в базе бывает и так, и в punycode."""
    netloc, site = ascii_domain(urlsplit(url).hostname or ""), ascii_domain(site_host)
    return netloc == site or netloc.endswith(f".{site}")


def pick_sections(urls: list[str], site_host: str) -> list[tuple[str, PageKind]]:
    """Разделы из адресов карты: свой сайт, вид раздела, не глубже `MAX_DEPTH`."""
    picked: dict[str, PageKind] = {}
    for url in urls:
        if not _same_site(url, site_host):
            continue
        depth = len([part for part in urlsplit(url).path.split("/") if part])
        kind = kind_of(url)
        if kind is not PageKind.HOME and 0 < depth <= MAX_DEPTH:
            picked.setdefault(url, kind)
    order = {kind: number for number, kind in enumerate(WALK_ORDER)}
    ranked = sorted(picked.items(), key=lambda item: (order.get(item[1], len(order)), len(item[0])))
    return ranked[:MAX_SECTIONS]


async def sitemap_sections(
    fetcher: PageFetcher, home_url: str, site_host: str
) -> list[tuple[str, PageKind]]:
    """Разделы сайта из его карты. Карты нет или она не читается — пустой список."""
    for path in MAP_PATHS:
        text = await _read(fetcher, urljoin(home_url, path))
        if text is None:
            continue
        if not _IS_INDEX.search(text):
            return pick_sections([m.group(1) for m in _LOC.finditer(text)], site_host)
        urls: list[str] = []
        for child in _children(text):
            if _same_site(child, site_host) and (inner := await _read(fetcher, child)) is not None:
                urls.extend(match.group(1) for match in _LOC.finditer(inner))
        return pick_sections(urls, site_host)
    return []
