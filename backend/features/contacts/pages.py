"""Ступень 1: страницы сайта.

Порядок обхода задан ценностью адреса, а не удобством: сначала страницы,
где сидит тот, кто называет цену за размещение, потом обычные контакты,
потом правовые, где указан оператор сайта.

Четыре приёма здесь не от изящества, а от разбора боевого прогона: из
41 домена, за которые заплатили платному сервису, 14 нас не пустили,
у 7 адрес лежал на странице вне списка, до 6 обход не дошёл в рамках
бюджета. Те же грабли пройдены в соседней системе, и приёмы взяты оттуда.

**Хост пробуется в нескольких видах.** `www.` не срезается: у части
сайтов апекс не имеет записи или не редиректит, и запрос к нему просто
не доезжает. За `https` пробуется `http`: донор с протухшим сертификатом
всё ещё донор.

**Бюджет считает открытые страницы, а не запросы.** Иначе три десятка
404 по угадываемым слагам съедают его до того, как обход дойдёт до
существующей страницы контактов. Отдельный потолок попыток не даёт
зациклиться на сайте, который отвечает всем подряд.

**Правовые страницы входят в список.** Оператора сайта указывают
в «условиях» и «политике» чаще, чем на «контактах», — особенно там,
где контакты сведены к форме.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from backend.config import contacts as cfg
from backend.features.core.domain import PageKind
from backend.shared.net.url_guard import UnsafeUrlError

logger = logging.getLogger(__name__)

# Полный набор заголовков браузера, а не «почти». Проверено на шести
# доменах, закрывшихся от нас: с урезанным набором шесть отказов,
# с полным — четыре. Дешевле, чем платить за эти домены сервису.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="129", "Not=A?Brand";v="8"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

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

# Те же слова для поиска по ссылкам главной: там раздел может лежать
# по адресу вида /p/12345, и угадать его по слагу нельзя.
LINK_MARKERS: frozenset[str] = frozenset(
    slug for slugs in SLUGS.values() for slug in slugs
) | frozenset({"write for us", "advertise", "contact", "about us", "guest post", "terms"})

# Признаки контактной формы: адреса нет, но написать можно руками.
FORM_MARKERS = ("<form", "wpcf7", "gravity_form", "contact-form", "formcraft", "hs-form")

#: Порядок видов страниц при обходе — по убыванию ценности адреса.
WALK_ORDER = (PageKind.MONEY, PageKind.CONTACT, PageKind.ABOUT, PageKind.LEGAL)


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """Скачанная страница и её вид."""

    url: str
    kind: PageKind
    html: str


def _kind_of(url: str) -> PageKind:
    """Вид страницы по её адресу. Неузнанное — главная, то есть слабый вес."""
    path = urlparse(url).path.lower()
    for kind in WALK_ORDER:
        if any(slug in path for slug in SLUGS[kind]):
            return kind
    return PageKind.HOME


def home_variants(site_host: str) -> Iterator[str]:
    """Как пробовать главную: апекс и `www.`, сначала по `https`, потом `http`.

    У части сайтов апекс не имеет записи или не редиректит на `www.`;
    у части протух сертификат. И то и другое — не повод терять донора.
    """
    hosts = (site_host, f"www.{site_host}")
    for scheme in ("https", "http"):
        for host in hosts:
            yield f"{scheme}://{host}/"


def slug_urls(base: str) -> Iterator[tuple[str, PageKind]]:
    """Угадываемые адреса страниц от рабочей главной.

    Виды перебираются кругами, а не подряд: сначала первый слаг каждого
    вида, потом второй и так далее. Подряд не работает — слагов почти
    полсотни, потолок попыток вдвое меньше, и правовые страницы в конце
    списка не пробовались бы никогда. Поймано тестом: сайт с формой
    вместо контактов и адресом оператора в «условиях» оставался без
    адреса, хотя адрес был.

    Приоритет вида при этом сохраняется: внутри круга порядок прежний,
    а окончательный выбор делает вес адреса (okf/contact-ladder.md).
    """
    root = base.rstrip("/")
    longest = max(len(SLUGS[kind]) for kind in WALK_ORDER)
    for position in range(longest):
        for kind in WALK_ORDER:
            slugs = SLUGS[kind]
            if position < len(slugs):
                yield f"{root}/{slugs[position]}/", kind


def has_contact_form(html: str) -> bool:
    """Есть ли на странице форма. Ветка «форма без адреса» — ступень 4."""
    lowered = html.lower()
    return any(marker in lowered for marker in FORM_MARKERS)


class PageFetcher:
    """Качает страницы одного домена с двумя потолками.

    Потолков два, и это не перестраховка. Первый — на открытые страницы:
    столько мы готовы разобрать. Второй — на попытки: сайт отвечает 404
    на большинство угадываемых слагов, и без него бюджет уходит на
    несуществующие адреса, не дожив до существующих.

    Ошибка по одной странице — не поломка домена, а норма. Поломкой было
    бы промолчать о ней, поэтому счётчики отказов ведутся и уходят в отчёт.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_pages: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self._client = client
        self._max_pages = max_pages if max_pages is not None else cfg.MAX_PAGES_PER_DOMAIN
        self._max_attempts = (
            max_attempts if max_attempts is not None else cfg.MAX_ATTEMPTS_PER_DOMAIN
        )
        self.opened = 0  # страницы, которые удалось разобрать
        self.attempts = 0  # все запросы, включая 404 и отказы
        self.blocked = False  # сайт закрылся от нас: 401/403/429

    @property
    def exhausted(self) -> bool:
        return self.opened >= self._max_pages or self.attempts >= self._max_attempts

    async def get(self, url: str, kind: PageKind) -> FetchedPage | None:
        if self.exhausted:
            return None

        self.attempts += 1
        response = await self._request(url)
        if response is None:
            return None
        if response.status_code in (401, 403, 429):
            self.blocked = True
            logger.debug("сайт закрылся: %s ответил %s", url, response.status_code)
            return None
        if response.status_code >= 400:
            return None
        if "html" not in response.headers.get("content-type", "").lower():
            return None

        self.opened += 1
        return FetchedPage(
            url=str(response.url), kind=kind, html=response.text[: cfg.MAX_PAGE_BYTES]
        )

    async def _request(self, url: str) -> httpx.Response | None:
        """Запрос одной страницы. `None` — страница не открылась."""
        try:
            return await self._client.get(url, headers=HEADERS, follow_redirects=True)
        except httpx.HTTPError as exc:
            logger.debug("страница не открылась: %s — %r", url, exc)
        except UnsafeUrlError as exc:
            # Защита отказала — и правильно: ссылка с чужого сайта ведёт
            # на внутренний адрес или чужую схему. Это отказ ОДНОЙ страницы,
            # а не поломка прогона: 23.09.2026 опечатка `hhttps://` на одном
            # сайте оборвала поиск контактов для всех остальных.
            logger.info("страница пропущена, адрес не прошёл защиту: %s", exc)
        return None

    async def home(self, site_host: str) -> FetchedPage | None:
        """Главная в первом виде, который ответил."""
        for url in home_variants(site_host):
            page = await self.get(url, PageKind.HOME)
            if page is not None:
                return page
            if self.exhausted:
                break
        logger.debug("контакты: главная %s не открылась ни в одном виде", site_host)
        return None

    def follow(self, page: FetchedPage, links: set[str]) -> list[tuple[str, PageKind]]:
        """Ссылки со страницы, приведённые к абсолютным, — только свой домен.

        Чужой домен здесь означает соцсеть или платформу: их контактные
        страницы к нашему донору отношения не имеют.
        """
        host = urlparse(page.url).netloc.lower()
        out: list[tuple[str, PageKind]] = []
        for href in links:
            absolute = urljoin(page.url, href)
            parsed = urlparse(absolute)
            # Схема — до хоста: опечатка `hhttps://свой-домен/...` проходила
            # проверку своего домена и уходила в запрос.
            if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != host:
                continue
            out.append((absolute, _kind_of(absolute)))

        order = {kind: number for number, kind in enumerate(WALK_ORDER)}
        return sorted(out, key=lambda item: order.get(item[1], len(order)))
