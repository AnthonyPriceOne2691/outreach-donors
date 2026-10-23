"""Что уходит и приходит по маршрутам обходов Этапа 2."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from backend.config import crawl as crawl_cfg
from backend.features.core.domain import CrawlOutcome, CrawlStatus, PriceSource, StopReason
from backend.features.crawl.board import CrawlLine, DonorLine

#: Сколько доноров ставится одним нажатием. Обходчиков четыре, обход — до
#: получаса: полсотни доноров — это уже шесть часов очереди, и больше за раз
#: человек осознанно не выбирает.
MAX_HOSTS = 50


class CrawlCeilings(BaseModel):
    """Потолки обхода донора, кроме страниц (`max_pages` — полем рядом): попытки, время
    и пауза между страницами. Числами настроек (`config/crawl.py`), а не копией в экране:
    значок обхода называет потолок, в который упёрся обход, «i» панели — все.

    Модель берёт числа сама, поэтому их несёт любая строка обхода — и в ответе
    «Приёмки по донорам», который собирается своим маршрутом.
    """

    max_attempts: int = Field(default_factory=lambda: crawl_cfg.MAX_ATTEMPTS_PER_DONOR)
    max_seconds: float = Field(default_factory=lambda: crawl_cfg.MAX_SECONDS_PER_DONOR)
    #: Наша пауза между страницами, с: меньше не ждём, что бы ни просил сайт.
    delay_sec: float = Field(default_factory=lambda: crawl_cfg.DELAY_SEC)
    #: Больше этой паузы не ждём, даже если сайт просит.
    max_delay_sec: float = Field(default_factory=lambda: crawl_cfg.MAX_DELAY_SEC)


class CrawlRow(CrawlCeilings):
    """Один обход: где он и чем кончился. `max_pages` — потолок, к которому
    идёт ход «340 из 1000»."""

    id: int
    host: str
    status: CrawlStatus
    outcome: CrawlOutcome | None = None
    stop_reason: StopReason | None = None
    pages: int
    max_pages: int
    articles: int
    links: int | None = None
    resumes: int
    requested_by: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    #: Почему обход стоит, продолжен или остановлен — словами человека.
    reason: str | None = None
    #: Доля закрытых страниц от всех запросов обхода, 0…1; пусто — не мерили
    #: (обход идёт или записан до замера). `requests` — сколько запросов за ней.
    blocked_share: float | None = None
    requests: int | None = None
    #: Доля 403/429/5xx/таймаут за последние `window` запросов — мера здоровья ТЗ.
    failure_share: float | None = None
    window: int | None = None
    #: Чего каскаду не хватило — уровень обхода → словами (`fetch.Degradation`).
    degradation: dict[str, str] = Field(default_factory=dict)
    #: Почему кончился — из отчёта законченного обхода; у идущего и у записанного до
    #: отчёта пусто. Откуда брались адреса: `sitemap`, `links` или `sitemap+links`.
    source: str | None = None
    #: Дочитана ли карта сайта: `False` — «больше адресов нет» сказано по неполной карте.
    sitemap_complete: bool | None = None
    #: Пауза, которую просит robots.txt сайта, с; пусто — не просит.
    crawl_delay: float | None = None
    #: Сколько обход шёл, с, вместе с продолжениями.
    elapsed_sec: float | None = None
    #: Свой срок обхода, с, если он его записал; пусто — срок общий (`max_seconds`).
    time_budget_sec: float | None = None

    @classmethod
    def of(cls, line: CrawlLine, *, max_pages: int) -> CrawlRow:
        return cls(
            id=line.id,
            host=line.host,
            status=line.status,
            outcome=line.outcome,
            stop_reason=line.stop_reason,
            pages=line.pages,
            max_pages=max_pages,
            articles=line.articles,
            links=line.links,
            resumes=line.resumes,
            requested_by=line.requested_by,
            created_at=line.created_at,
            finished_at=line.finished_at,
            reason=line.reason,
            blocked_share=line.blocked_share,
            requests=line.requests,
            failure_share=line.failure_share,
            window=line.window,
            degradation=line.degradation,
            source=line.source,
            sitemap_complete=line.sitemap_complete,
            crawl_delay=line.crawl_delay,
            elapsed_sec=line.elapsed_sec,
            time_budget_sec=line.time_budget_sec,
        )


class DonorRow(BaseModel):
    """Донор, которого можно обойти: цена — основание, обход — что о нём знаем.
    Валюта — рядом с ценой, источник — из ответа или вручную."""

    host: str
    price: float | None = None
    priced_at: datetime | None = None
    crawl: CrawlRow | None = None
    currency: str | None = None
    source: PriceSource | None = None

    @classmethod
    def of(cls, line: DonorLine, *, max_pages: int) -> DonorRow:
        return cls(
            host=line.host,
            price=float(line.price) if line.price is not None else None,
            priced_at=line.priced_at,
            crawl=CrawlRow.of(line.crawl, max_pages=max_pages) if line.crawl else None,
            currency=line.currency,
            source=line.source,
        )


class TargetsView(CrawlCeilings):
    """Кого можно обойти и кого нельзя — числами и словами, что делать. Потолки обхода —
    для «i» панели: она называет их и тогда, когда строк обходов нет."""

    donors: list[DonorRow]
    no_price: int
    stale_price: int
    supplier: int
    notes: list[str] = Field(default_factory=list)
    max_pages: int
    #: Сколько обходчиков слушает очередь. `None` — не спросили; 0 — поставленное
    #: никто не возьмёт.
    workers: int | None = None
    #: Мера здоровья ТЗ — окно и пороги: экран называет их в пояснении панели, а не
    #: держит свою копию чисел.
    health_window: int | None = None
    slow_down_share: float | None = None
    stop_share: float | None = None
    #: Потолок срока сайту с долгой паузой (`crawl/pace.py`), с: «i» панели называет его
    #: рядом с общим сроком.
    max_seconds_slow: float | None = None


class CrawlsView(BaseModel):
    rows: list[CrawlRow]
    active: int
    workers: int | None = None


class LaunchBody(BaseModel):
    """Кого обойти: домены из списка доноров со свежей ценой."""

    hosts: list[str] = Field(min_length=1)

    @field_validator("hosts")
    @classmethod
    def _not_too_many(cls, hosts: list[str]) -> list[str]:
        if len(hosts) > MAX_HOSTS:
            raise PydanticCustomError(
                "too_many_hosts",
                f"За раз ставим не больше {MAX_HOSTS} доноров, выбрано {len(hosts)}: "
                "обходчиков четыре, обход — до получаса",
            )
        return hosts


class LaunchResult(BaseModel):
    """Что поставлено, что уже шло, что не принято и почему."""

    queued: dict[str, int]
    busy: list[str]
    failed: list[str]
    refused: dict[str, str]
    workers: int | None = None
