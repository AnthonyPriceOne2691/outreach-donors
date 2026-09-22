"""Команда сборки пула ключей.

Второй из двух режимов: оператор либо приносит свой список, либо
запускает сборку пресетом, и вся механика — углы, добор, дедупликация —
происходит внутри. Наружу торчит только выбор пресета, рынка и потолка
(docs/KEYWORD_MODES.md).

Расход называется в конце. Генерация дешёвая, но молчать про неё нельзя:
дорогой станет выдача по этим ключам, и решение «брать или перегенерить»
принимается здесь, до траты.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from backend.features.keywords.angles import PRESETS, UnknownPresetError
from backend.features.keywords.client import KeygenClient
from backend.features.keywords.generator import PoolBuilder, PoolReport

EXIT_OK = 0
EXIT_EMPTY = 3


def _print_report(pool_size: int, cap: int, report: PoolReport) -> None:
    data = report.as_dict()
    print(f"\nСобрано ключей:      {pool_size} из {cap}")
    print(f"Запрошено у модели:  {data['asked']}")
    print(f"Пришло:              {data['received']}")
    print(f"Отсеяно фильтром:    {data['rejected']}")
    print(f"Почти-дублей убрано: {data['near_duplicates']}")
    print(f"Кругов добора:       {data['rounds']}")
    print(f"Вызовов модели:      {data['calls']}, токенов {data['tokens']}")
    if report.refusals:
        # Печатается только когда есть что сказать, но печатается всегда,
        # когда есть: пул, собранный наполовину из-за отказов, внешне
        # неотличим от пула, который модель честно не смогла набрать.
        print(f"Отказов модели:      {data['refusals']}")
        for reason in dict.fromkeys(report.refusals):
            print(f"  {reason}")
    print("\nПо углам:")
    for angle, count in report.per_angle.items():
        print(f"  {angle:22} {count}")


async def cmd_keywords(args: argparse.Namespace) -> int:
    """Собрать пул и записать его в файл."""
    client = KeygenClient()
    print(f"Модель: {client.model}. Пресет: {args.preset}, рынок: {args.country}.")

    try:
        pool = await PoolBuilder(client).build(
            cap=args.cap,
            country=args.country,
            language=args.language,
            preset_name=args.preset,
        )
    except UnknownPresetError as exc:
        print(str(exc))
        return EXIT_EMPTY
    finally:
        await client.aclose()

    if not pool.keywords:
        print("Ни одного ключа не собрано — смотреть лог: модель не ответила или всё отсеяно.")
        return EXIT_EMPTY

    # Запись в файл — операция блокирующая, и в асинхронной команде ей
    # место в отдельном потоке: иначе на большом пуле подвиснет цикл.
    out = Path(args.out)
    await asyncio.to_thread(out.write_text, "\n".join(pool.keywords) + "\n", encoding="utf-8")

    _print_report(len(pool.keywords), args.cap, pool.report)
    print(f"\nКлючи записаны: {out}")
    if pool.report.rejected:
        print("\nПримеры отсеянного (по ним настраивается промпт):")
        for phrase, reason in list(pool.report.rejected.items())[:5]:
            print(f"  {phrase[:48]:50} — {reason}")
    return EXIT_OK


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser("keywords", help="собрать пул ключей моделью по пресету")
    parser.add_argument(
        "--preset",
        default="wide",
        choices=sorted(PRESETS),
        help="обкатанный набор углов; свободный ввод угла не предусмотрен намеренно",
    )
    parser.add_argument("--country", required=True, help="рынок: «Philippines», «Germany»")
    parser.add_argument("--language", default="English", help="язык запросов: «Filipino», «German»")
    parser.add_argument("--cap", type=int, default=100, help="сколько ключей нужно")
    parser.add_argument("--out", required=True, help="файл со списком ключей на выходе")
