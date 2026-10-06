"""Команда `probe-donor`: липовый донор для проверки всей цепочки на живом сервисе.

Заводит донора на домене в зоне `.invalid` со своим ящиком проверяющего и
проверочным прогоном; дальше — штатный путь: сборка по прогону, отправка, ответ
с этого ящика. Убирается целиком, с перепиской: `outreach prune --probes`.
Порядок работы — в ядре (`features/donors/probe.py`), здесь — доводы и печать.

    outreach probe-donor --email me@ours.example
"""

from __future__ import annotations

import argparse
import getpass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.cli.prune import in_session
from backend.features.donors.probe import PROBE_ZONE, ProbeError, make_probe

EXIT_OK = 0
EXIT_REFUSED = 2


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "probe-donor",
        help="липовый донор со своим ящиком — проверить цепочку письмо → ответ → цена",
    )
    parser.add_argument(
        "--email", required=True, help="свой ящик; пока стоит предохранитель — из списка"
    )
    parser.add_argument(
        "--host",
        default=f"probe{PROBE_ZONE}",
        help=f"домен липового донора, только в зоне {PROBE_ZONE} (по умолчанию probe{PROBE_ZONE})",
    )


async def run_probe_donor(session: AsyncSession, args: argparse.Namespace) -> int:
    """Завести липового донора на готовой сессии и сказать, что дальше."""
    try:
        probe = await make_probe(
            session, host=args.host, email=args.email, author=f"консоль: {getpass.getuser()}"
        )
    except ProbeError as exc:
        print(f"Липовый донор не заведён: {exc}")
        return EXIT_REFUSED
    await session.commit()
    state = "заведён" if probe.created else "уже был — повтор ничего не задвоил"
    print(f"Липовый донор {probe.host} (донор №{probe.donor_id}) {state}.")
    print(f"  адрес:              {probe.email} (вписан руками, принят)")
    print(f"  проверочный прогон: №{probe.run_id} — в рассылку по нему не попадёт никто другой")
    print("\nДальше — штатный путь:")
    print(f'  outreach letters-build --campaign "Проверка цепочки" --runs {probe.run_id} --limit 1')
    print("  outreach letters                 # номер письма в очереди")
    print("  outreach letters-send --id <номер>")
    print(
        "Ответ с этого ящика ляжет в переписку липового донора, цена — в его карточку; "
        "добивки — по сроку, если ответа нет."
    )
    print("Убрать всё это, с перепиской: outreach prune --probes (показ), затем с --yes.")
    return EXIT_OK


async def cmd_probe_donor(args: argparse.Namespace) -> int:
    """Завести липового донора для проверки цепочки."""
    return await in_session(lambda session: run_probe_donor(session, args))
