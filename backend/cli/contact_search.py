"""Команда поиска контактов: лестница по всем донорам, которым он нужен.

Три свойства, ради которых команда устроена именно так.

**Остаток платного сервиса спрашивается до начала.** Ключ может быть общим
с соседней системой, и своя таблица расхода про её траты ничего не знает.
Не смогли спросить — идём без платной ступени, а не вслепую.

**Пачка — чекпоинт.** Поиск по сотне доменов идёт минутами; падение
на середине не должно стоить уже пройденного. Сохранили пачку — эти
домены выпали из повторного запуска.

**Отчёт показывает каждую ступень.** Сколько доменов вошло, сколько адресов
дала, сколько стоила. Только по этим числам видно, окупается ли порядок
ступеней, — а он и есть главное решение фазы.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import contacts as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
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


async def _make_provider(http: httpx.AsyncClient) -> ContactProvider | None:
    """Платный сервис, если ключ есть и квота осталась.

    Отсутствие ключа — рабочий режим, а не поломка: три бесплатные ступени
    работают и без него, просто часть доменов останется без контакта.
    """
    if not cfg.HUNTER_API_KEY:
        print("Платный сервис не настроен — лестница идёт по трём бесплатным ступеням.")
        return None

    provider = HunterProvider(http)
    try:
        quota = await provider.quota()
    except ProviderError as exc:
        print(f"Остаток платного сервиса узнать не удалось ({exc}) — идём без него.")
        return None

    print(f"Платный сервис: осталось {quota.left:,} поисков.".replace(",", " "))
    if quota.left <= 0:
        print("Квота исчерпана — ступень 3 пропускается, домены останутся на следующий месяц.")
        return None
    return provider


def _print_report(ladder: ContactLadder, saved: int, total: int) -> None:
    counters = ladder.counters
    print(f"\nДоменов пройдено:      {total}")
    print(f"Адресов сохранено:     {saved}")
    print("\nПо ступеням:")
    print(
        f"  0. MX                вошло {counters.mx_checked}, "
        f"не принимают почту {counters.mx_stopped}, DNS молчал {counters.mx_unknown}"
    )
    if counters.mx_checked and counters.mx_unknown == counters.mx_checked:
        print(
            "     ВНИМАНИЕ: ступень 0 не ответила ни по одному домену — она сейчас\n"
            "     ничего не отсеивает и только тратит время. Проверить сеть и\n"
            "     CONTACTS_DNS_FALLBACK."
        )
    print(
        f"  1. страницы          вошло {counters.pages_entered}, "
        f"нашли {counters.pages_found}, запросов {counters.pages_fetched}, "
        f"закрылись от нас {counters.pages_blocked}"
    )
    print(
        f"  1б. браузер          вошло {counters.browser_entered}, нашли {counters.browser_found}"
    )
    print(
        f"  2. RDAP              вошло {counters.rdap_entered}, "
        f"нашли {counters.rdap_found}, не ответил {counters.rdap_failed}"
    )
    print(
        f"  3. платный сервис    вошло {counters.provider_entered}, нашли {counters.provider_found}"
    )
    print(f"  4. ручная очередь    форм {counters.form_only}, поставлено {counters.manual_queued}")
    print(f"\nБез контакта:          {counters.not_found}")
    print(f"Адресов отсеяно:       {counters.rejected_emails}")

    paid = counters.provider_entered
    free = counters.pages_found + counters.rdap_found
    if free or paid:
        print(
            f"\nБесплатные ступени закрыли {free} доменов, "
            f"на платную ушло {paid}. Порядок ступеней — okf/contact-ladder.md."
        )


async def _walk(ladder: ContactLadder, hosts: list[str]) -> list[LadderResult]:
    """Пройти пачку доменов одновременно, но не все сразу."""
    limiter = asyncio.Semaphore(CONCURRENCY)

    async def one(host: str) -> LadderResult:
        async with limiter:
            return await ladder.find(host)

    return list(await asyncio.gather(*(one(host) for host in hosts)))


async def cmd_contacts(args: argparse.Namespace) -> int:
    """Найти контакты подходящим донорам и сохранить исход по каждому."""
    check_storage()
    use_browser = args.browser or cfg.BROWSER_ENABLED

    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    timeout = httpx.Timeout(cfg.PAGE_TIMEOUT_SEC, connect=cfg.PAGE_TIMEOUT_SEC)

    try:
        # Клиент с защитой исходящих: адреса приходят снаружи — из выдачи,
        # из ссылок на чужих страницах, а вскоре и из поля ввода оператора.
        # Проверка идёт на каждом шаге редиректа, иначе публичный сайт уводит
        # нас внутрь сети одним ответом 302 (backend/shared/net/url_guard.py).
        async with factory() as session, guarded_client(timeout=timeout) as http:
            repository = ContactRepository(session)
            hosts = await repository.pending_hosts(limit=args.limit)
            if not hosts:
                print("Доноров, которым нужен контакт, нет — все пройдены или ещё не отобраны.")
                return 0

            print(f"Доноров без контакта: {len(hosts)}")
            if use_browser:
                print(
                    "Ступень браузера включена: она смотрит только тех, кого "
                    "обычный обход не открыл, и стоит секунд на страницу."
                )
            if args.paid_first:
                print(
                    "Порядок обратный: сначала платный сервис, добор скрейпером.\n"
                    "Быстрее в разы, но платных запросов будет столько же, сколько доменов."
                )
            provider = None if args.no_paid else await _make_provider(http)

            # Браузер поднимается один раз на прогон, а не на домен: запуск
            # стоит около секунды. Не поднялся — работаем без него.
            async with PlaywrightRenderer() as renderer:
                if use_browser and renderer is None:
                    print("Браузер не поднялся — прогон идёт без этой ступени.")
                ladder = ContactLadder(
                    http,
                    provider=provider,
                    paid_first=args.paid_first,
                    renderer=renderer if use_browser else None,
                )

                saved = 0
                for start in range(0, len(hosts), BATCH):
                    batch = hosts[start : start + BATCH]
                    results = await _walk(ladder, batch)
                    saved += await repository.save(results)
                    await session.commit()
                    print(f"  сохранено {start + len(batch)} из {len(hosts)}")

            _print_report(ladder, saved, len(hosts))
    finally:
        await engine.dispose()

    return 0
