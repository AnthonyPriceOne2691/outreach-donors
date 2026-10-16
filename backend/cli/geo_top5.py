"""Команда `geo-top5`: докупить топ-5 стран донорам, у которых спрошена только верхняя.

Без `--yes` — только показ: кому, во что обойдётся и сколько осталось по месячному
капу и у Ahrefs; ничего не покупается, в базе ничего не меняется. С `--yes` —
запрос по странам каждому по очереди, фиксация после каждого. Порядок работы — в ядре
(`features/donors/geo_top5.py`), здесь — доводы и печать.

    outreach geo-top5
    outreach geo-top5 --accepted-only --limit 50
    outreach geo-top5 --accepted-only --yes
"""

from __future__ import annotations

import argparse

from sqlalchemy.ext.asyncio import AsyncSession

from backend.cli.prune import in_session
from backend.config import ahrefs as ahrefs_cfg
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import UNITS_BY_COUNTRY
from backend.features.donors.geo_top5 import (
    Room,
    Top5Plan,
    Top5Report,
    buy_top5,
    plan_top5,
    refusal,
    room_for,
)
from backend.features.donors.wording import country_title

EXIT_OK = 0
EXIT_CAP_EXCEEDED = 3
EXIT_PROVIDER_FAILED = 5

#: Сколько доноров назвать поимённо: остальных — числом, список на тысячу
#: строк в консоли не читают.
SHOWN = 20


def _positive(text: str) -> int:
    """`--limit` — целое от единицы: ноль доноров докупать незачем."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"нужно целое число от 1, а не «{text}»") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"нужно целое число от 1, а не {value}")
    return value


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "geo-top5",
        help=f"докупить топ-5 стран донорам, у которых спрошена только верхняя "
        f"({UNITS_BY_COUNTRY} юнитов Ahrefs на донора; без --yes — только показать)",
    )
    parser.add_argument(
        "--accepted-only",
        action="store_true",
        help="только принятые человеком — доноры экрана «Доноры»",
    )
    parser.add_argument(
        "--limit", type=_positive, help="не больше стольких доноров за запуск, принятые — первыми"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="купить: запрос по странам каждому, фиксация после каждого",
    )


def _units(number: int) -> str:
    """Число с разрядами, как у сметы прогона: «27 500»."""
    return f"{number:,}".replace(",", " ")


def _print_plan(plan: Top5Plan, *, accepted_only: bool) -> None:
    whom = "принятых человеком доноров" if accepted_only else "доноров и кандидатов"
    print(f"С неполной разбивкой по странам ({whom}): {plan.found}")
    if len(plan.donors) < plan.found:
        print(f"Докупить за этот запуск: {len(plan.donors)} — принятые первыми (--limit)")
    for donor in plan.donors[:SHOWN]:
        standing = "принят" if donor.accepted else "не донор"
        print(f"  {donor.host} — {country_title(donor.geo) or 'страна неизвестна'}, {standing}")
    if len(plan.donors) > SHOWN:
        print(f"  …и ещё {len(plan.donors) - SHOWN}")
    print(
        f"\nСмета: {len(plan.donors)} × {UNITS_BY_COUNTRY} = {_units(plan.units)} юнитов "
        "(запрос по странам — по одному на донора)"
    )


def _print_room(room: Room) -> None:
    print(f"Месячный кап: {_units(room.cap)}, осталось {_units(room.cap_left)}")
    if room.claimed:
        print(f"Обещано идущими прогонами: {_units(room.claimed)}")
    if room.provider_left is None:
        print(f"У Ahrefs: неизвестно — {room.provider_unknown}")
        return
    print(f"У Ahrefs осталось: {_units(room.provider_left)} (за вычетом обещанного)")
    print(f"Можно потратить: {_units(room.free)}")


def _print_report(report: Top5Report) -> None:
    print(f"\nДокуплено: {len(report.bought)}, записано в журнал расхода: {_units(report.units)}")
    for host, before, after in report.moved:
        print(
            f"  {host}: верхняя страна — {country_title(after)}, а не "
            f"{country_title(before) or '—'}; вердикт не тронут, решать человеку"
        )
    _print_misses(report)
    if report.stopped:
        print(f"\nОстановлено, повтор не поможет: {report.stopped}.")
    if report.capped:
        print(f"\nОстановлено по капу: {report.capped}. Остальные — повторным запуском.")
    print("Статус, причина и решение человека у доноров не менялись — только страны.")


def _print_misses(report: Top5Report) -> None:
    """Кому не докуплено и почему: каждому случаю — своё «что дальше»."""
    if report.empty:
        print(f"Ahrefs не вернул стран — оставлены как были: {len(report.empty)}")
        print(f"  {', '.join(report.empty[:SHOWN])}")
    if report.taken:
        print(f"Обновил прогон, пока ждали ответа — уже полные: {len(report.taken)}")
    if report.failed:
        print(f"Провайдер не ответил — повторный запуск их возьмёт: {len(report.failed)}")
        for host, why in report.failed[:SHOWN]:
            print(f"  {host}: {why}")


async def run_geo_top5(
    session: AsyncSession, client: AhrefsClient | None, args: argparse.Namespace
) -> int:
    """Показ и докупка на готовой сессии. Без `--yes` в базе не меняется ничего и
    у Ahrefs не покупается ничего — спрашивается только остаток, это бесплатно."""
    plan = await plan_top5(session, accepted_only=args.accepted_only, limit=args.limit)
    _print_plan(plan, accepted_only=args.accepted_only)
    if not plan.donors:
        print("Докупать некому: у всех записанных разбивка полная.")
        return EXIT_OK
    room = await room_for(session, client, cap=ahrefs_cfg.UNITS_CAP)
    _print_room(room)
    refused = refusal(plan, room)
    if not args.yes:
        if refused:
            print(f"\nС --yes не купится: {refused}")
        print("\nЭто показ — ничего не куплено, в базе ничего не изменилось. Купить — с --yes.")
        return EXIT_OK
    if refused or client is None:
        print(f"\nНе куплено ничего: {refused}")
        return EXIT_CAP_EXCEEDED
    report = await buy_top5(session, client, plan, allowed=room.free)
    _print_report(report)
    if report.stopped:
        return EXIT_PROVIDER_FAILED
    return EXIT_CAP_EXCEEDED if report.capped else EXIT_OK


async def cmd_geo_top5(args: argparse.Namespace) -> int:
    """Докупить топ-5 стран донорам с неполной разбивкой. Без `--yes` — только показ."""
    client = AhrefsClient() if ahrefs_cfg.API_KEY else None
    try:
        return await in_session(lambda session: run_geo_top5(session, client, args))
    finally:
        if client is not None:
            await client.aclose()
