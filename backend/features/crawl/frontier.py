"""Очередь адресов обхода: сначала карта сайта, потом ссылки уже открытых страниц.

**Сначала карта.** Она отдаёт статьи сразу и свежие первыми (`sitemap.py`); обход по ссылкам
идёт кругами по навигации и до статей доходит последним.

**Потом ссылки — в ширину, без повторов** (23.10.2026). До этого с картой обход по ссылкам
не ходил вовсе: кончилась карта — кончился обход, и сайт с картой короче потолка кончался
задолго до него, хотя попытки и время оставались. Теперь внутренние ссылки копятся
всю дорогу — с главной и с каждой открытой страницы, — и когда карта кончается раньше
потолка, обход идёт по ним в порядке находки. Карты нет — по ним он и начинается. «Адреса
кончились» (`exhausted`) — когда кончились и карта, и ссылки.

**Правила адреса одни на карту и ссылки**: тот же хост или его поддомен, страница, а не файл
(`SKIP_SUFFIXES`), без `mailto:`, `tel:` и якорей; запрет robots.txt обход проверяет перед
запросом. До 23.10.2026 адреса карты фильтр файлов не проходили, и PDF из карты тратил
попытку и паузу.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urldefrag

from selectolax.parser import HTMLParser

from backend.shared.net.url_parts import join_url, parse_url

#: Расширения, за которыми страницы нет: качать их — это трафик без пользы.
SKIP_SUFFIXES: tuple[str, ...] = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".gz", ".rar", ".7z", ".tar",
    ".mp3", ".mp4", ".avi", ".mov", ".webm", ".woff", ".woff2", ".ttf",
)  # fmt: skip

#: Откуда обход брал адреса — `CrawlReport.source` и чекпоинт: карта, ссылки или карта, а
#: после неё ссылки.
SITEMAP, LINKS, BOTH = "sitemap", "links", "sitemap+links"


def crawlable(url: str, host: str) -> bool:
    """Наш ли это адрес и страница ли это вообще. Битый адрес — не наш."""
    parts = parse_url(url)
    if parts is None or parts.scheme not in ("http", "https"):
        return False
    netloc = parts.netloc.lower().split(":")[0]
    if not (netloc == host or netloc.endswith(f".{host}")):
        return False
    return not parts.path.lower().endswith(SKIP_SUFFIXES)


def same_site_links(html: str, page_url: str, host: str) -> list[str]:
    """Ссылки со страницы на свой же сайт, без якорей и дублей.

    Якорь отбрасывается до сравнения: `/post` и `/post#comments` — одна
    страница, и без этого обход платит за неё столько раз, сколько
    на ней разделов.
    """
    out: list[str] = []
    seen: set[str] = set()
    for node in HTMLParser(html).css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        joined = join_url(page_url, href)
        if joined is None:
            continue
        absolute, _ = urldefrag(joined)
        if absolute in seen or not crawlable(absolute, host):
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


@dataclass(slots=True)
class Frontier:
    """Что открыть дальше: адреса карты, пока не кончатся, потом ссылки открытых страниц.

    `seen` — всё, что когда-нибудь стояло в очереди или уже открыто: ссылка на страницу карты
    или на открытую страницу второй раз в очередь не встаёт. Счётчики — сколько страниц
    открыто с карты и сколько по ссылкам: отчёту и чекпоинту.
    """

    mapped: deque[str] = field(default_factory=deque)
    linked: deque[str] = field(default_factory=deque)
    seen: set[str] = field(default_factory=set)
    has_map: bool = False
    took_links: bool = False
    from_map: int = 0
    from_links: int = 0

    @classmethod
    def start(
        cls,
        mapped: Iterable[str],
        linked: Iterable[str],
        opened: Iterable[str],
        *,
        has_map: bool,
        took_links: bool = False,
        counts: tuple[int, int] = (0, 0),
    ) -> Frontier:
        """Очередь из карты и ссылок; открытое в неё не встаёт, повторы — тоже. `counts` —
        сколько страниц уже открыто с карты и по ссылкам (продолжение)."""
        frontier = cls(deque(mapped), has_map=has_map, took_links=took_links)
        frontier.from_map, frontier.from_links = counts
        frontier.seen.update(opened)
        frontier.seen.update(frontier.mapped)
        frontier.add(linked)
        return frontier

    def __bool__(self) -> bool:
        return bool(self.mapped or self.linked)

    def pop(self) -> tuple[str, bool]:
        """Следующий адрес и пришёл ли он по ссылке."""
        if self.mapped:
            return self.mapped.popleft(), False
        self.took_links = True
        return self.linked.popleft(), True

    def add(self, links: Iterable[str]) -> None:
        """Ссылки — в хвост очереди, каждая по одному разу: обход идёт в ширину."""
        for link in links:
            if link not in self.seen:
                self.seen.add(link)
                self.linked.append(link)

    def opened(self, *, by_link: bool) -> None:
        if by_link:
            self.from_links += 1
        else:
            self.from_map += 1

    @property
    def source(self) -> str:
        if not self.has_map:
            return LINKS
        return BOTH if self.took_links else SITEMAP

    def facts(self) -> tuple[str, int, int]:
        """Для отчёта: откуда брались адреса, сколько страниц открыто с карты и по ссылкам."""
        return self.source, self.from_map, self.from_links

    def saved(self, limit: int) -> list[str]:
        """Ссылки для чекпоинта — не больше `limit` (оставшихся попыток: больше не открыть).
        Карта в чекпоинт не едет: её продолжение читает заново."""
        return list(self.linked)[:limit]
