"""Команда `donor-add`: донор, заведённый вручную, с ценой, которую агентство знает само.

Требование Этапа 2 — «по кому запускаем — только доноры с известной ценой (из
базы или заведённые вручную)». Порядок работы и отказы — в ядре
(`features/donors/manual_price.py`), общие с кнопками карточки донора и панели
«Обход доноров»; здесь — доводы и печать. Ahrefs и другие платные сервисы
команда не зовёт.

    outreach donor-add --host example.com --price 150 --by anna@ours.example
    outreach donor-add --host https://www.example.com/blog --price 120.50 \\
        --currency EUR --note "прайс агентства" --by anna@ours.example
"""

from __future__ import annotations

import argparse

from sqlalchemy.ext.asyncio import AsyncSession

from backend.cli.prune import in_session
from backend.features.donors.manual_price import (
    DEFAULT_CURRENCY,
    MAX_NOTE,
    ManualPriceError,
    enter_host,
    manual_price,
)

EXIT_OK = 0
EXIT_REFUSED = 2


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "donor-add",
        help="завести донора вручную с ценой, которую знаете (без Ahrefs и без писем)",
    )
    parser.add_argument(
        "--host", required=True, help="домен или адрес сайта; приводится к корню, как у прогона"
    )
    parser.add_argument("--price", required=True, help="цена размещения числом: 150 или 150.50")
    parser.add_argument(
        "--currency",
        default=DEFAULT_CURRENCY,
        help=f"валюта: код или знак (по умолчанию {DEFAULT_CURRENCY})",
    )
    parser.add_argument(
        "--note", help=f"откуда цена: «прайс агентства», «LinkDetective» — до {MAX_NOTE} знаков"
    )
    parser.add_argument("--by", required=True, help="кто знает цену: почта сотрудника")


async def run_donor_add(session: AsyncSession, args: argparse.Namespace) -> int:
    """Завести донора на готовой сессии и сказать, что дальше."""
    try:
        price = manual_price(args.price, args.currency, args.note, by=args.by)
        entered = await enter_host(session, args.host, price)
    except ManualPriceError as exc:
        print(f"Донор не заведён: {exc}")
        return EXIT_REFUSED
    await session.commit()
    what = "заведён вручную" if entered.created else "уже был донором — записана цена"
    note = f" ({price.note})" if price.note else ""
    print(
        f"Донор {entered.host} (№{entered.donor_id}) {what}: {price.amount} {price.currency}{note}."
    )
    if entered.suitable:
        print(
            "Обход Этапа 2 его берёт: «Обход доноров» на экране «Рекламодатели» "
            f"или outreach crawl {entered.host} --queue."
        )
    else:
        print("По порогам отбора он не годен — обход Этапа 2 его не возьмёт.")
    return EXIT_OK


async def cmd_donor_add(args: argparse.Namespace) -> int:
    """Завести донора вручную с ценой."""
    return await in_session(lambda session: run_donor_add(session, args))
