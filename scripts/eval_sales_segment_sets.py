"""Наборы eval судьи сегмента: чтение и сверка с манифестом, выгрузка сайтов прогона в разметку. Запуск и метрики — `scripts/eval_sales_segment.py`.

**Случай без разметки** (`expect` пуст) судится — по нему видны доли полос и токены, — но в
метрики точности не идёт. Такой случай обязан нести метку из `unlabeled_tags` манифеста (сайты,
которые человек не смотрел: судья принял их сам): случай, который просто забыли разметить, —
отказ набора, а не тихий пропуск.

Текст главной к набору (`--fetch-home`, `--home`) — `eval_sales_segment_home.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from backend.config import storage
from backend.features.core.models.domain import DomainModel
from backend.features.sales.models import SalesHypothesisModel as H
from backend.features.sales.segment_models import Lane
from backend.features.sales.segment_models import SalesSegmentCandidateModel as C
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

#: Решение человека → полоса, которую ждём от судьи.
WANTED = {"yes": Lane.ACCEPT, "no": Lane.REJECT, "unsure": Lane.REVIEW}
#: Что можно написать в `expect`: решение или пара «решение|не понять» — верен любой ответ пары.
EXPECTS = (*WANTED, "yes|unsure", "no|unsure")


class SetError(RuntimeError):
    """Набор не прочитан: нет файла, не тот хэш или число случаев, битый случай."""


def _checked(case: dict[str, Any], unlabeled: Sequence[str]) -> dict[str, Any]:
    for key in ("id", "hypothesis", "segment", "host", "text"):
        if not isinstance(case.get(key), str):
            raise SetError(f"{case.get('id')}: нет поля {key}")
    expect = case.get("expect")
    if expect in EXPECTS:
        return case
    if not expect and set(case.get("tags") or ()) & set(unlabeled):
        return {**case, "expect": ""}
    raise SetError(f"{case['id']}: не размечен — expect: {' | '.join(EXPECTS)}")


def load(path: Path, unlabeled: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Случаи набора; без разметки — только с меткой из `unlabeled` (шапка модуля)."""
    if not path.is_file():
        raise SetError(f"набор не найден: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [_checked(json.loads(line), unlabeled) for line in lines if line.strip()]


def golden(directory: str, manifest: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Внешний набор по манифесту: файл, число случаев, хэш и размер на гипотезу. Набор, не
    закреплённый в манифесте числом и хэшем, не судится: не тот файл прошёл бы молча."""
    spec: dict[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
    if not directory:
        raise SetError(f"набор не найден: задайте {spec['env']} — каталог вне репозитория")
    path = Path(directory) / spec["file"]
    cases = load(path, spec.get("unlabeled_tags") or ())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"Набор {path}: случаев {len(cases)}, sha256 {digest}")
    _pinned(spec, len(cases), digest)
    least = int(spec["per_hypothesis"]["min"])
    sizes = Counter(case["hypothesis"] for case in cases)
    if small := {name: count for name, count in sizes.items() if count < least}:
        raise SetError(f"набор мал: {small} — на гипотезу нужно не меньше {least} сайтов")
    if base := spec.get("baseline"):
        print(f"База манифеста: {json.dumps(base, ensure_ascii=False)}")
    return cases, spec


def _pinned(spec: dict[str, Any], count: int, digest: str) -> None:
    """Набор — тот, что закреплён в манифесте числом случаев и хэшем; не закреплён — отказ."""
    if not spec.get("sha256") or not spec.get("count"):
        raise SetError(
            f"набор не закреплён в манифесте: впишите count {count} и sha256 {digest} "
            "(их напечатала эта проверка), когда набор утверждён"
        )
    if spec["sha256"] != digest:
        raise SetError(f"набор не тот, что в манифесте: sha256 {digest} ≠ {spec['sha256']}")
    if spec["count"] != count:
        raise SetError(f"набор не тот, что в манифесте: случаев {count} ≠ {spec['count']}")


# --- выгрузка сайтов прогона в разметку ------------------------------------------------------


async def cases_of_run(session: AsyncSession, run_id: int) -> list[dict[str, Any]]:
    """Сайты прогона — случаями без решения: размечает человек (`expect`, `evidence`). Вердикта
    судьи в заготовке нет: размечающий его не видит, иначе разметка повторила бы судью."""
    rows = await session.execute(
        select(C.id, H.name, H.description, DomainModel.host, C.site_text)
        .join(DomainModel, DomainModel.id == C.domain_id)
        .join(H, H.id == C.hypothesis_id)
        .where(C.run_id == run_id, C.site_text.is_not(None))
        .order_by(C.id)
    )
    return [
        {"id": f"run{run_id}-{cid}", "hypothesis": name, "segment": segment or "", "host": host,
         "text": text, "expect": None, "evidence": "", "labeled_by": "", "labeled_at": "", "tags": []}
        for cid, name, segment, host, text in rows.tuples()
    ]  # fmt: skip


async def _export(run_id: int) -> list[dict[str, Any]]:
    """Заготовка разметки прогона из базы настроек (`STORAGE_DSN`)."""
    engine = create_async_engine(storage.DSN)
    try:
        async with async_sessionmaker(engine)() as session:
            return await cases_of_run(session, run_id)
    finally:
        await engine.dispose()


def export_to(run_id: int, out: Path | None) -> int:
    if out is None:
        print("Выгрузке нужен файл: --out set.jsonl")
        return 2
    cases = asyncio.run(_export(run_id))
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), "utf-8")
    print(f"Сайтов прогона №{run_id} к разметке: {len(cases)} → {out}")
    return 0
