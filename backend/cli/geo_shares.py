"""Команда `geo-shares`: пересчитать доли стран у записанных доноров.

Без `--yes` — только показ: у скольких доноров и как изменится доля. С `--yes` —
запись одной транзакцией. Провайдер не зовётся: доли считаются из трафика
стран, который уже лежит в разбивке донора. Порядок работы — в ядре
(`features/donors/geo_recount.py`), здесь — доводы и печать.

    outreach geo-shares
    outreach geo-shares --yes
"""

from __future__ import annotations

import argparse

from sqlalchemy.ext.asyncio import AsyncSession

from backend.cli.prune import in_session
from backend.features.donors.geo_recount import RecountPlan, apply_recount, plan_recount
from backend.features.donors.wording import share_text

EXIT_OK = 0

#: Сколько доноров назвать поимённо: остальных — числом, список на тысячу
#: строк в консоли не читают.
SHOWN = 20


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "geo-shares",
        help="пересчитать доли стран у записанных доноров по их же числам, без Ahrefs "
        "(без --yes — только показать)",
    )
    parser.add_argument("--yes", action="store_true", help="записать: одной транзакцией")


def _print_plan(plan: RecountPlan) -> None:
    print(f"Доноров с разбивкой по странам: {plan.checked}")
    print(f"Доля изменится: {len(plan.fixes)}")
    for fix in plan.fixes[:SHOWN]:
        before = share_text(fix.before) or "—"
        print(f"  {fix.host}: верхняя страна {before} → {share_text(fix.after)}")
    if len(plan.fixes) > SHOWN:
        print(f"  …и ещё {len(plan.fixes) - SHOWN}")
    if plan.unreadable:
        print(
            f"Не пересчитать — нет трафика домена или строки без чисел, оставлены как есть: "
            f"{len(plan.unreadable)} ({', '.join(plan.unreadable[:SHOWN])})"
        )
    print("Решение по региону не меняется: место страны в топе считается по трафику, а не по доле.")


async def run_geo_shares(session: AsyncSession, args: argparse.Namespace) -> int:
    """Пересчёт на готовой сессии. Без `--yes` в базе не меняется ничего."""
    plan = await plan_recount(session)
    _print_plan(plan)
    if not args.yes:
        # План только читает: без коммита сессия при закрытии отбросит всё сама.
        print("\nЭто показ — в базе ничего не изменилось. Записать — та же команда с --yes.")
        return EXIT_OK
    written = await apply_recount(session, plan)
    await session.commit()
    print(f"\nЗаписано одной транзакцией: {written}.")
    if written < len(plan.fixes):
        print(
            f"Не записано {len(plan.fixes) - written}: их доли успел переписать прогон — "
            "уже по новому правилу."
        )
    return EXIT_OK


async def cmd_geo_shares(args: argparse.Namespace) -> int:
    """Пересчитать доли стран. Без `--yes` — только показ."""
    return await in_session(lambda session: run_geo_shares(session, args))
