"""Команда поиска контактов: та же лестница, что и у кнопки в интерфейсе.

Порядок работы живёт в ядре (`features/contacts/search.py`), здесь —
только доводы командной строки и печать отчёта. Пока порядок жил тут,
запустить поиск мог только инженер: у веб-слоя своего пути к лестнице
не было вовсе.
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import contacts as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.contacts.search import SearchReport, search_contacts

logger = logging.getLogger(__name__)


def _print_report(report: SearchReport) -> None:
    counters = report.counters
    print(f"\nДоменов пройдено:      {report.walked}")
    print(f"Адресов сохранено:     {report.saved}")
    print("\nПо ступеням:")
    print(
        f"  0. MX                вошло {counters.get('mx_checked', 0)}, "
        f"не принимают почту {counters.get('mx_stopped', 0)}, "
        f"DNS молчал {counters.get('mx_unknown', 0)}"
    )
    print(
        f"  1. страницы          вошло {counters.get('pages_entered', 0)}, "
        f"нашли {counters.get('pages_found', 0)}"
    )
    print(
        f"  2. RDAP              вошло {counters.get('rdap_entered', 0)}, "
        f"нашли {counters.get('rdap_found', 0)}, не ответил {counters.get('rdap_failed', 0)}"
    )
    refused = counters.get("provider_refused", 0)
    print(
        f"  3. платный сервис    вошло {counters.get('provider_entered', 0)}, "
        f"нашли {counters.get('provider_found', 0)}" + (f", ОТКАЗАЛ {refused}" if refused else "")
    )
    print(
        f"  4. ручная очередь    форм {counters.get('form_only', 0)}, "
        f"поставлено {counters.get('manual_queued', 0)}"
    )
    print(f"\nБез контакта:          {counters.get('not_found', 0)}")
    print(f"Адресов отсеяно:       {counters.get('rejected_emails', 0)}")

    paid = counters.get("provider_entered", 0)
    free = counters.get("pages_found", 0) + counters.get("rdap_found", 0)
    if free or paid:
        print(
            f"\nБесплатные ступени закрыли {free} доменов, "
            f"на платную ушло {paid}. Порядок ступеней — okf/contact-ladder.md."
        )


async def cmd_contacts(args: argparse.Namespace) -> int:
    """Найти контакты подходящим донорам и сохранить исход по каждому."""
    check_storage()

    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            report = await search_contacts(
                session,
                limit=args.limit,
                use_browser=args.browser or cfg.BROWSER_ENABLED,
                paid_first=args.paid_first,
                no_paid=args.no_paid,
                on_batch=lambda done, total: print(f"  сохранено {done} из {total}"),
            )
            if not report.pending:
                print("Доноров, которым нужен контакт, нет — все пройдены или ещё не отобраны.")
                return 0

            # Это вход, а не итог. Называлось «Доноров без контакта» и стояло
            # рядом с итоговым «Без контакта: 0» — два разных числа под одним
            # именем в одном выводе.
            print(f"Взято в работу: {report.pending} донор(ов) без контакта")
            for note in report.notes:
                print(note)
            _print_report(report)
    finally:
        await engine.dispose()

    return 0
