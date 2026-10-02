"""Команда `contacts-refind`: заново найти адрес для названных доменов.

Без `--yes` — только показ: что записано сейчас, что нашёл бы поиск и какие
адреса снялись бы. С `--yes` — то же с записью. Порядок работы — в ядре
(`features/contacts/refind.py`), здесь — доводы и печать.
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import contacts as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.contacts.refind import NamedDomains, Refound
from backend.features.contacts.search import search_contacts

logger = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "contacts-refind",
        help="заново найти адрес для названных доменов (без --yes — только показать)",
    )
    parser.add_argument("hosts", nargs="+", help="домены через пробел")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="записать: снять прежние адреса лестницы и сохранить новый исход",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="ступень браузера для сайтов, которые не открылись обычным запросом",
    )


def _print_domain(found: Refound, *, write: bool) -> None:
    print(f"\n{found.host}")
    if found.skipped:
        print(f"  пропущен: {found.skipped}")
        return
    print(f"  было:      {', '.join(found.before) or 'адреса нет'}")
    result = found.result
    if result is None:
        print("  поиск не дошёл до домена")
        return
    if result.contact is not None:
        page = f" (страница: {result.contact.page_url})" if result.contact.page_url else ""
        print(f"  станет:    {result.contact.email}{page}")
    else:
        print(f"  станет:    адреса нет — исход {result.status.value}")
    if found.removed:
        print(f"  {'снят' if write else 'снимется'}:   {', '.join(found.removed)}")
    for email, why in found.kept:
        print(f"  останется: {email} — {why}")


async def cmd_contacts_refind(args: argparse.Namespace) -> int:
    """Заново найти адрес названным доменам. Без `--yes` в базу не пишется ничего."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            queue = NamedDomains(session, args.hosts, write=args.yes)
            report = await search_contacts(
                session,
                limit=len(queue.domains),
                use_browser=args.browser or cfg.BROWSER_ENABLED,
                # Показ не платит: за просмотр незачем, при записи спросится.
                no_paid=not args.yes,
                queue=queue,
            )
            for note in report.notes:
                print(note)
            for found in queue.domains.values():
                _print_domain(found, write=args.yes)
            if args.yes:
                await session.commit()
                print(f"\nЗаписано: доменов {report.walked}, с адресом {report.saved}.")
            else:
                await session.rollback()
                print(
                    "\nЭто показ — в базе ничего не изменилось. Платная ступень в показе "
                    "не зовётся. Записать — та же команда с --yes."
                )
    finally:
        await engine.dispose()
    return 0
