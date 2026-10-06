"""Команда `prune`: убрать прогоны с тем, что принесли только они, и пробные ответы.

Без `--yes` — только показ: что уйдёт, что останется и почему. С `--yes` — то же
с удалением, одной транзакцией и записью в журнал действий. Порядок работы —
в ядре (`features/runs/prune.py`), здесь — доводы и печать.

    outreach prune --runs 18,19,20 --replies 1
    outreach prune --runs 18,19,20 --replies 1 --yes
"""

from __future__ import annotations

import argparse
import getpass
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.runs.prune import PrunePlan, PruneRefusedError, apply_prune, plan_prune

EXIT_OK = 0
EXIT_REFUSED = 2


def _numbers(raw: str) -> list[int]:
    """«18,19, 20» → [18, 19, 20]. Не число — отказ разбора, а не тихий пропуск."""
    try:
        return [int(part) for part in raw.replace(" ", ",").split(",") if part.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"«{raw}» — нужны номера через запятую, например 18,19,20"
        ) from None


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "prune",
        help="убрать прогоны с тем, что принесли только они, и пробные ответы "
        "(без --yes — только показать)",
    )
    parser.add_argument(
        "--runs", type=_numbers, default=[], help="номера прогонов через запятую: 18,19,20"
    )
    parser.add_argument(
        "--replies",
        type=_numbers,
        default=[],
        help="номера пробных ответов через запятую — только не привязанные к переписке",
    )
    parser.add_argument(
        "--probes",
        action="store_true",
        help="липовые доноры (outreach probe-donor) целиком — с письмами, перепиской и ответами",
    )
    parser.add_argument(
        "--yes", action="store_true", help="удалить: одной транзакцией, с записью в журнал"
    )


def _print_plan(plan: PrunePlan, *, done: bool) -> None:
    if plan.runs:
        _print_runs(plan, done=done)
    for reply_id, subject in plan.replies.items():
        print(f"Ответ №{reply_id} — не привязан к переписке, тема «{subject}»")
    if plan.probes is not None:
        probes = plan.probes
        print(
            f"Липовые доноры: {len(probes.domains)} — с письмами ({probes.letters}), "
            f"перепиской ({probes.threads}) и ответами ({probes.replies}); "
            f"рассылок без них не останется: {len(probes.campaigns)}"
        )


def _print_runs(plan: PrunePlan, *, done: bool) -> None:
    print("Прогоны: " + ", ".join(f"№{n}" for n in plan.runs))
    print(f"  очередь разбора:      {plan.candidates} строк")
    settings = ", ".join(str(n) for n in plan.settings) or "нет — на них ссылаются другие"
    print(f"  версии порогов:       {settings}")
    if plan.detached:
        print("  остаются, без номера прогона:")
        for what, count in plan.detached.items():
            print(f"    {what + ':':<36} {count}")
    verb = "Удалено" if done else "Уйдёт"
    print(
        f"{verb} доменов, которые принесли только эти прогоны: {len(plan.domains)} "
        f"(с донором и адресами — адресов {plan.contacts})"
    )
    if plan.kept:
        print("Оставлены — их держит не только этот прогон:")
        for reason, count in plan.kept.items():
            print(f"  {reason + ':':<45} {count}")
    print(
        "Смета следующих прогонов берёт долю по странам и долю новых доменов "
        "из истории — останется история только оставшихся прогонов."
    )


async def run_prune(session: AsyncSession, args: argparse.Namespace) -> int:
    """Чистка на готовой сессии. Без `--yes` в базе не меняется ничего."""
    try:
        plan = await plan_prune(
            session, run_ids=args.runs, reply_ids=args.replies, probes=args.probes
        )
    except PruneRefusedError as exc:
        print(f"Чистка не выполнена: {exc}")
        return EXIT_REFUSED
    if not args.yes:
        # План только читает: без коммита сессия при закрытии отбросит всё сама.
        _print_plan(plan, done=False)
        print("\nЭто показ — в базе ничего не изменилось. Удалить — та же команда с --yes.")
        return EXIT_OK
    await apply_prune(session, plan, author=f"консоль: {getpass.getuser()}")
    await session.commit()
    _print_plan(plan, done=True)
    print("\nУдалено одной транзакцией; запись в журнале действий — data_pruned.")
    return EXIT_OK


async def in_session(work: Callable[[AsyncSession], Awaitable[int]]) -> int:
    """Одна сессия на команду: открыть, отдать работе, закрыть. Общее у чистки
    и липового донора."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await work(session)
    finally:
        await engine.dispose()


async def cmd_prune(args: argparse.Namespace) -> int:
    """Убрать названные прогоны и пробные ответы. Без `--yes` — только показ."""
    return await in_session(lambda session: run_prune(session, args))
