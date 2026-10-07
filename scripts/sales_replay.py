"""Прогон версии агента продаж на входящих с известным исходом; ворота «не хуже прежней».

Запуск:

    python scripts/sales_replay.py run --set crm [--dir КАТАЛОГ] [--out прогон.json]
    python scripts/sales_replay.py run --drafts [--settle-days 3] [--out прогон.json]
    python scripts/sales_replay.py run --set crm --prompts НОВАЯ --against ПРЕЖНЯЯ
    python scripts/sales_replay.py compare прежний.json новый.json [--strict]

**Набор** — вне репозитория: каталог `SALES_REPLAY_DIR` или `--dir`; что в нём лежит и
сколько в нём случаев не меньше — манифест `scripts/data/sales_replay/manifest.toml`.
Набора нет — exit 1 «набор не найден»; пуст или мал — exit 1 словами: прогон по пустому
набору дал бы «0 расхождений». `--drafts` — живые продажи: черновики агента этапа продаж с
решением человека (`agent_drafts`).

**Версия** — промпты ситуации, черновика и судьи и таблица ходов из пакета; `--prompts
КАТАЛОГ` — файлы каталога вместо них (чего нет — из пакета). Правила судьи — код дерева.

**Ворота** (`--against` или `compare`): прежняя версия — файл прежнего прогона или каталог
прежних промптов (тогда она гонится здесь же, на тех же случаях). На общих случаях у новой
не больше ложного молчания (версия молчит, а человек ответил) и не больше черновиков с
нарушениями; `--strict` — нарушений строго меньше. Иначе exit 1 с разбором по ситуациям.
Неполный прогон (отказ модели, потолок расхода) ворота не проходит.

**Модель настоящая**: прогон тратит токены — ситуация, черновик, судья с правками — и пишет
их в расход. Ключа нет — прогон останавливается на первом случае словами.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.config import sales as sales_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.agent.guarding import Writer
from backend.features.agent.writer import AgentWriter
from backend.features.sales.agent import replay, replay_drafts, replay_gate, replay_sets
from backend.features.sales.agent.replay import Case, Prompts, Run
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

MANIFEST = Path(__file__).parent / "data" / "sales_replay" / "manifest.toml"
#: Набор по умолчанию — из резервной копии CRM, вне репозитория.
DEFAULT_SET = "crm"


def load_set(name: str, folder: Path | None) -> tuple[list[Case], str]:
    """Случаи набора из манифеста и откуда они: нет — `SetNotFoundError`, иначе `SetError`."""
    specs = replay_sets.specs(tomllib.loads(MANIFEST.read_text(encoding="utf-8")))
    spec = specs.get(name)
    if spec is None:
        raise replay.SetError(f"набора «{name}» нет в манифесте; есть: {', '.join(specs)}")
    where = folder or _home(spec)
    paths = replay_sets.located(spec, where)
    return replay_sets.read_set(spec, paths), f"набор «{name}» ({where})"


def _home(spec: replay_sets.SetSpec) -> Path | None:
    """Каталог набора: в репозитории — рядом с манифестом, вне — `SALES_REPLAY_DIR`."""
    if spec.folder is not None:
        return MANIFEST.parent / spec.folder
    return Path(sales_cfg.REPLAY_DIR) if sales_cfg.REPLAY_DIR else None


def read_run(path: Path) -> Run:
    """Файл прогона. Нет его или не та форма — `SetError` словами."""
    if not path.is_file():
        raise replay.SetError(f"прогон не найден: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise replay.SetError(f"{path}: не JSON ({exc.msg})") from None
    return replay_sets.loaded(raw, str(path))


def _against(raw: str | None) -> Run | Prompts | None:
    """Прежняя версия: файл прогона, каталог промптов или ничего."""
    if raw is None:
        return None
    path = Path(raw)
    return replay.from_folder(path) if path.is_dir() else read_run(path)


async def execute(
    session: AsyncSession,
    writer: Writer,
    args: argparse.Namespace,
    cases: Sequence[Case] | None,
    source: str,
) -> int:
    """Прогон — и ворота, если есть прежняя версия. Возвращает код выхода."""
    if cases is None:
        cases, source = await _drafts(session, args)
        if not cases:
            print("НАБОР ПУСТ: у черновиков продаж ещё нет решений людей — сравнивать не с чем")
            return 1
    against = _against(args.against)
    prompts = replay.from_folder(Path(args.prompts)) if args.prompts else replay.PACKAGED
    old = against if isinstance(against, Run) else None
    if isinstance(against, Prompts):
        old = await replay.run(session, writer, cases, source=source, prompts=against)
        await session.commit()  # расход прежней версии — тоже настоящий
    new = await replay.run(session, writer, cases, source=source, prompts=prompts)
    await session.commit()
    print("\n".join(replay_gate.summary(new)))
    if args.out:
        _save(Path(args.out), new)
    if old is None:
        return 0 if new.complete else 1
    return _gated(old, new, strict=args.strict)


def _save(path: Path, run: Run) -> None:
    """Файл прогона — рядом с набором, вне репозитория: в нём тексты черновиков."""
    path.write_text(json.dumps(replay_sets.dumped(run), ensure_ascii=False, indent=1), "utf-8")
    print(f"Прогон записан: {path}")


async def _drafts(session: AsyncSession, args: argparse.Namespace) -> tuple[list[Case], str]:
    settle = timedelta(days=args.settle_days)
    found = await replay_drafts.from_drafts(session, now=datetime.now(UTC), settle=settle)
    print(
        f"Живые продажи: случаев с исходом {len(found.cases)}; ждут решения {found.pending}; "
        f"пропусков моложе {args.settle_days} дн. {found.fresh}"
    )
    return list(found.cases), "черновики продаж (agent_drafts)"


def _gated(old: Run, new: Run, *, strict: bool) -> int:
    gate = replay_gate.compare(old, new, strict=strict)
    print("\n" + "\n".join(gate.lines))
    if gate.passed:
        print("\nВОРОТА ОТКРЫТЫ: новая версия не хуже прежней")
        return 0
    print("\nВОРОТА ЗАКРЫТЫ: " + "; ".join(gate.problems))
    return 1


async def _live(args: argparse.Namespace, cases: list[Case] | None, source: str) -> int:
    check_storage()
    engine = create_async_engine(storage.DSN)
    writer = AgentWriter()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await execute(session, writer, args, cases, source)
    finally:
        await writer.aclose()
        await engine.dispose()


def parser() -> argparse.ArgumentParser:
    """Доводы команды: `run` — прогон (и ворота с `--against`), `compare` — два файла."""
    found = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = found.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="прогнать версию на случаях с известным исходом")
    source = run.add_mutually_exclusive_group()
    source.add_argument("--set", default=DEFAULT_SET, help="набор из манифеста")
    source.add_argument("--drafts", action="store_true", help="живые продажи из agent_drafts")
    run.add_argument(
        "--dir", type=Path, default=None, help="каталог набора вместо SALES_REPLAY_DIR"
    )
    run.add_argument("--settle-days", type=int, default=replay_drafts.SETTLE.days)
    run.add_argument("--prompts", default=None, help="каталог файлов версии вместо пакета")
    run.add_argument("--against", default=None, help="прежняя версия: файл прогона или каталог")
    run.add_argument("--out", default=None, help="куда записать прогон (JSON)")
    run.add_argument("--strict", action="store_true", help="нарушений строго меньше")
    compare = commands.add_parser("compare", help="сравнить два прогона из файлов")
    compare.add_argument("old", type=Path)
    compare.add_argument("new", type=Path)
    compare.add_argument("--strict", action="store_true", help="нарушений строго меньше")
    return found


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "compare":
            return _gated(read_run(args.old), read_run(args.new), strict=args.strict)
        cases, source = (None, "") if args.drafts else load_set(args.set, args.dir)
        _against(args.against)  # прежняя версия проверяется до первого вызова модели
        if args.prompts:
            replay.from_folder(Path(args.prompts))
        return asyncio.run(_live(args, cases, source))
    except replay.SetNotFoundError as exc:
        print(f"НАБОР НЕ НАЙДЕН: {exc}")
        return 1
    except replay.SetError as exc:
        print(f"НЕ ГОДИТСЯ: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
