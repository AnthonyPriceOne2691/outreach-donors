"""Команда `probe-advertiser`: пробный рекламодатель — проверить оффер Этапа 2 на себе.

Заводит рекламодателя на домене в зоне `.invalid` со своим ящиком из
предохранителя и настоящей ссылкой из обхода названного донора; дальше —
штатный путь: сборка офферов с лимитом 1, отправка, ответ с этого ящика.
Убирается целиком, с перепиской: `outreach prune --probes`. Порядок работы —
в ядре (`features/crawl/probe_advertiser.py`), здесь — доводы и печать.

    outreach probe-advertiser --email me@ours.example --donor donor.example
"""

from __future__ import annotations

import argparse
import getpass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.cli.prune import in_session
from backend.features.crawl.probe_advertiser import ProbeAdvertiser, make_probe_advertiser
from backend.features.donors.probe import PROBE_ZONE, ProbeError

EXIT_OK = 0
EXIT_REFUSED = 2

#: Рассылка в подсказке: под этим именем пробный оффер и виден в очереди.
CAMPAIGN = "Проверка оффера"


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "probe-advertiser",
        help="пробный рекламодатель со своим ящиком — проверить оффер Этапа 2 на себе",
    )
    parser.add_argument(
        "--email", required=True, help="свой ящик — обязательно из предохранителя учётки Этапа 2"
    )
    parser.add_argument(
        "--donor",
        required=True,
        help="донор с обходом и свежей ценой: из его обхода берётся ссылка оффера",
    )
    parser.add_argument(
        "--host",
        default=f"probe-advertiser{PROBE_ZONE}",
        help=f"домен пробного, только в зоне {PROBE_ZONE} "
        f"(по умолчанию probe-advertiser{PROBE_ZONE})",
    )


async def run_probe_advertiser(session: AsyncSession, args: argparse.Namespace) -> int:
    """Завести пробного рекламодателя на готовой сессии и сказать, что дальше."""
    try:
        probe = await make_probe_advertiser(
            session,
            host=args.host,
            email=args.email,
            donor=args.donor,
            author=f"консоль: {getpass.getuser()}",
        )
    except ProbeError as exc:
        print(f"Пробный рекламодатель не заведён: {exc}")
        return EXIT_REFUSED
    await session.commit()
    _print(probe)
    return EXIT_OK


def _print(probe: ProbeAdvertiser) -> None:
    state = "заведён" if probe.created else "уже был — ссылка и балл обновлены, ничего не задвоено"
    print(f"Пробный рекламодатель {probe.host} (рекламодатель №{probe.advertiser_id}) {state}.")
    print(f"  адрес:   {probe.email} (вписан руками)")
    print(
        f"  ссылка:  площадка {probe.donor_host}, страница {probe.link.page_url}, "
        f"анкор «{probe.link.anchor}» — обход №{probe.link.crawl_id}, "
        f"настоящая ссылка {probe.link.target}"
    )
    print(f"  балл:    {probe.points} — выше всех настоящих: сборка с лимитом 1 возьмёт его")
    print("\nДальше — штатный путь: «Письма → Рекламодателям», собрать с лимитом 1, или:")
    print(f'  outreach letters-build --campaign "{CAMPAIGN}" --stage advertisers --limit 1')
    print("  outreach letters                 # номер письма в очереди")
    print("  outreach letters-send --id <номер>")
    print(
        "Ответ с этого ящика ляжет лидом в «Диалоги». «Взять в работу» отправит лид в CRM, "
        "если задан вебхук лидов (OUTREACH_LEAD_WEBHOOK_URL), — пробный не брать."
    )
    print("Убрать всё это, с перепиской: outreach prune --probes (показ), затем с --yes.")


async def cmd_probe_advertiser(args: argparse.Namespace) -> int:
    """Завести пробного рекламодателя для проверки оффера Этапа 2."""
    return await in_session(lambda session: run_probe_advertiser(session, args))
