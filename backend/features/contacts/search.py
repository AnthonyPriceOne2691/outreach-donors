"""Поиск контактов пачкой: один путь для консоли и для кнопки.

Раньше весь порядок работы жил в консольной команде, и запустить поиск
мог только инженер. Это ломало главное обещание веб-слоя: сотрудник
доводит донора до цены, ни разу не обратившись к инженеру.

Три свойства, ради которых порядок именно такой, остались прежними.

**Остаток платного сервиса спрашивается до начала.** Ключ бывает общим
с соседней системой, и своя таблица про её траты ничего не знает.
Не смогли спросить — идём без платной ступени, а не вслепую.

**Пачка — чекпоинт.** Поиск по сотне доменов идёт минутами; падение
на середине не должно стоить уже пройденного. Сохранили пачку — эти
домены выпали из повторного запуска.

**Отчёт показывает каждую ступень.** Сколько доменов вошло, сколько
адресов дала, сколько стоила. Только по этим числам видно, окупается
ли порядок ступеней, — а он и есть главное решение фазы.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.contacts.browser import PlaywrightRenderer
from backend.features.contacts.ladder import ContactLadder, LadderResult
from backend.features.contacts.provider import (
    ContactProvider,
    HunterProvider,
    ProviderError,
)
from backend.features.contacts.repository import ContactRepository
from backend.shared.net.url_guard import guarded_client

logger = logging.getLogger(__name__)

#: Сколько доменов проходим одновременно. Больше — упрёмся в вежливость
#: к чужим сайтам; меньше — прогон растянется на часы.
CONCURRENCY = 8

#: Размер пачки для сохранения. Он же шаг чекпоинта.
BATCH = 20


@dataclass
class SearchReport:
    """Что дал проход. Числа — и в консоль, и на экран, и в журнал задачи."""

    pending: int = 0
    walked: int = 0
    saved: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    manual_queue_left: int = 0
    #: Что стоит сказать человеку вслух: платный сервис не настроен,
    #: браузер не поднялся, квота кончилась. Не ошибки — условия работы.
    notes: list[str] = field(default_factory=list)

    @property
    def as_report(self) -> str:
        return f"доменов {self.walked}, адресов {self.saved}"

    def as_dict(self) -> dict[str, object]:
        return {
            "pending": self.pending,
            "walked": self.walked,
            "saved": self.saved,
            "counters": dict(self.counters),
            "manual_queue_left": self.manual_queue_left,
            "notes": list(self.notes),
        }


async def _paid_step(http: httpx.AsyncClient, report: SearchReport) -> ContactProvider | None:
    """Платный сервис, если ключ есть и квота осталась.

    Отсутствие ключа — рабочий режим, а не поломка: три бесплатные
    ступени работают и без него, просто часть доменов останется
    без контакта.
    """
    if not cfg.HUNTER_API_KEY:
        report.notes.append("Платный сервис не настроен — идём по бесплатным ступеням.")
        return None

    provider = HunterProvider(http)
    try:
        quota = await provider.quota()
    except ProviderError as exc:
        logger.warning("контакты: остаток платного сервиса неизвестен (%s)", exc)
        report.notes.append("Остаток платного сервиса узнать не удалось — идём без него.")
        return None

    if quota.left <= 0:
        report.notes.append("Квота платного сервиса исчерпана — ступень пропускается.")
        return None
    report.notes.append(f"Платный сервис: осталось {quota.left} поисков.")
    return provider


async def _walk(ladder: ContactLadder, hosts: list[str]) -> list[LadderResult]:
    """Пройти пачку доменов одновременно, но не все сразу."""
    limiter = asyncio.Semaphore(CONCURRENCY)

    async def one(host: str) -> LadderResult:
        async with limiter:
            return await ladder.find(host)

    return list(await asyncio.gather(*(one(host) for host in hosts)))


async def search_contacts(
    session: AsyncSession,
    *,
    limit: int,
    use_browser: bool = False,
    paid_first: bool = False,
    no_paid: bool = False,
    on_batch: Callable[[int, int], None] | None = None,
) -> SearchReport:
    """Пройти лестницу по донорам, которым нужен контакт."""
    report = SearchReport()
    repository = ContactRepository(session)
    hosts = await repository.pending_hosts(limit=limit)
    report.pending = len(hosts)
    if not hosts:
        return report

    timeout = httpx.Timeout(cfg.PAGE_TIMEOUT_SEC, connect=cfg.PAGE_TIMEOUT_SEC)
    # Клиент с защитой исходящих: адреса приходят снаружи — из выдачи
    # и из ссылок на чужих страницах. Проверка идёт на каждом шаге
    # редиректа, иначе публичный сайт уводит нас внутрь сети одним 302.
    async with guarded_client(timeout=timeout) as http:
        provider = None if no_paid else await _paid_step(http, report)

        # Браузер поднимается один раз на проход, а не на домен: запуск
        # стоит около секунды. Не поднялся — работаем без него.
        async with PlaywrightRenderer() as renderer:
            if use_browser and renderer is None:
                report.notes.append("Браузер не поднялся — проход идёт без этой ступени.")
            ladder = ContactLadder(
                http,
                provider=provider,
                paid_first=paid_first,
                renderer=renderer if use_browser else None,
            )

            for start in range(0, len(hosts), BATCH):
                batch = hosts[start : start + BATCH]
                results = await _walk(ladder, batch)
                report.saved += await repository.save(results)
                await session.commit()
                report.walked = start + len(batch)
                if on_batch is not None:
                    on_batch(report.walked, len(hosts))

            report.counters = ladder.counters.as_report()
            report.manual_queue_left = ladder.manual_queue_left

    logger.info("контакты: %s", report.as_report)
    return report
