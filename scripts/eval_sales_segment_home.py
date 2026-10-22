"""Текст главной к набору eval судьи сегмента: дочитать главные сайтов и добавить строку
«Текст главной». Запуск — `scripts/eval_sales_segment.py --fetch-home` и `--home`.

У сайтов живого набора тела главной нет: в разметку лёг текст, который видел судья прогона
(выдача и шапка главной). Главная дочитывается **тем же кодом, что у прогона** — общим
`donors/home_signals.check_home`: адреса, попытки, заглушка защиты и видимый текст
(`HomeSignals.text`) — его. И на том же языке: прогон просит главную на языке писем
(`run.language`), eval — на языке писем случая, иначе набора, тем же правилом, что у его судьи.
Файл с главными — вне репозитория, с датой дочитывания.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from backend.config import judge as judge_cfg
from backend.features.donors.home_signals import check_home
from backend.features.sales.segment_judge import HOME_TEXT, SiteText
from backend.features.sales.segment_text import excerpt
from backend.shared.net.url_guard import guarded_client

#: Главных за раз при дочитывании: замер, а не прогон.
FETCH_AT_ONCE = 8


class HomesError(RuntimeError):
    """Файла дочитанных главных нет."""


async def _fetch_one(
    client: httpx.AsyncClient, case: dict[str, Any], gate: asyncio.Semaphore, letters: str | None
) -> dict[str, Any]:
    async with gate:
        signals = await check_home(client, case["host"], language=case.get("letters") or letters)
    return {
        "id": case["id"],
        "host": case["host"],
        "fetched_at": datetime.now(UTC).date().isoformat(),
        "reached": signals.reached,
        "error": signals.error,
        "text": signals.text,
    }


async def fetch_homes(
    cases: Sequence[dict[str, Any]], letters: str | None = None
) -> list[dict[str, Any]]:
    """Главные случаев набора — общим `check_home`, клиентом с защитой исходящих адресов и сроком
    главной судьи, как у прогона (`segment_jobs`), на языке писем случая, иначе набора `letters`."""
    gate = asyncio.Semaphore(FETCH_AT_ONCE)
    async with guarded_client(timeout=judge_cfg.HOME_TIMEOUT_SEC) as client:
        tasks = [_fetch_one(client, case, gate, letters) for case in cases]
        return list(await asyncio.gather(*tasks))


def fetched_to(cases: Sequence[dict[str, Any]], out: Path, letters: str | None = None) -> int:
    """Дочитать главные набора в файл (вне репозитория) и сказать, сколько открылось."""
    rows = asyncio.run(fetch_homes(cases, letters))
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    opened = sum(bool(row["reached"]) for row in rows)
    texts = sum(bool(row["text"]) for row in rows)
    print(f"Главных дочитано: {opened} из {len(rows)}, с текстом {texts} → {out}")
    return 0


def read_homes(path: Path) -> dict[str, dict[str, Any]]:
    """Дочитанные главные: случай → строка файла (`fetch_homes`)."""
    if not path.is_file():
        raise HomesError(f"файла главных нет: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {row["id"]: row for row in rows}


def without_home(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Случаи без строки «Текст главной»: замер на пяти строках, как до v3."""
    found = []
    for case in cases:
        kept = tuple(line for line in SiteText.parse(case["text"]).lines if line[0] != HOME_TEXT)
        found.append({**case, "text": SiteText(kept).for_model()})
    return found


def with_home(
    cases: Iterable[dict[str, Any]], homes: dict[str, dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Случаи со строкой «Текст главной» из дочитанных главных — с потолком знаков `limit`, как
    у судьи (`segment_text.site_text`). Главная не открылась — случай как был."""
    found = []
    for case in without_home(cases):
        row = homes.get(case["id"]) or {}
        tail = excerpt(row.get("text"), limit) if row.get("reached") else ""
        lines = SiteText.parse(case["text"]).lines + (((HOME_TEXT, tail),) if tail else ())
        found.append({**case, "text": SiteText(lines).for_model()})
    return found
