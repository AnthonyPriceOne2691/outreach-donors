"""Отчёт обхода донора: адреса, числа и всё, чего не хватило.

Отдельно от самого обхода: отчёт читают запись в базу, консоль, задача
очереди и пересчёт кандидатов, а обходить сайты им для этого не нужно.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.features.core.domain import CrawlOutcome, StopReason
from backend.features.crawl.links import OutLink
from backend.features.crawl.robots import RobotsStatus


@dataclass(slots=True)
class CrawlReport:
    """Отчёт обхода: адреса, числа и всё, чего не хватило.

    Деградация лежит здесь, а не в логе, намеренно: иначе обход,
    у которого не поднялся браузер, выглядит зелёным и врёт, что
    закрытые страницы проверены.
    """

    host: str
    outcome: CrawlOutcome
    stop_reason: StopReason
    pages: list[str] = field(default_factory=list)
    links: list[OutLink] = field(default_factory=list)
    articles: int = 0
    # Выброшенные кнопки «поделиться», страницы с пометкой рекламы и с датой.
    # Числом в отчёте: выброшенное молча и найденное молча одинаково не проверить.
    share_links: int = 0
    pages_labeled: int = 0
    pages_dated: int = 0
    robots_status: RobotsStatus = RobotsStatus.UNREADABLE
    crawl_delay: float | None = None
    sitemap_found: bool | None = False
    sitemap_complete: bool = False
    source: str = "none"  # откуда брались адреса: sitemap или ссылки
    elapsed_sec: float = 0.0
    slowed_down: bool = False
    health: dict[str, float | int] = field(default_factory=dict)
    by_level: dict[str, int] = field(default_factory=dict)
    degradation: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Плоская запись для колонки прогона и для отчёта замера."""
        return {
            "host": self.host,
            "outcome": self.outcome.value,
            "stop_reason": self.stop_reason.value,
            "pages_opened": len(self.pages),
            "articles": self.articles,
            "links_found": len(self.links),
            "advertisers": len({link.target_root for link in self.links}),
            "links_in_body": sum(1 for link in self.links if link.in_body),
            "share_links": self.share_links,
            "pages_labeled": self.pages_labeled,
            "pages_dated": self.pages_dated,
            # Ссылки, у которых корень домена угадан: суффикс неизвестен
            # вшитому снимку. Ноль — норма, рост — повод обновить список.
            "roots_guessed": sum(1 for link in self.links if link.root_guessed),
            "robots": self.robots_status.value,
            "crawl_delay": self.crawl_delay,
            "sitemap_found": self.sitemap_found,
            "sitemap_complete": self.sitemap_complete,
            "source": self.source,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "slowed_down": self.slowed_down,
            "degradation": self.degradation,
            "by_level": self.by_level,
            **self.health,
        }
