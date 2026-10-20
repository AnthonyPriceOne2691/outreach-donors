"""Текст главной к набору eval судьи сегмента: дочитать главные сайтов и добавить строку
«Текст главной». Запуск — `scripts/eval_sales_segment.py --fetch-home` и `--home`.

У сайтов живого набора тела главной нет: в разметку лёг текст, который видел судья прогона
(выдача и шапка главной). Главная дочитывается **тем же кодом, что у прогона** — общим
`donors/home_signals.check_home`: адреса, попытки, заглушка защиты — его. Видимый текст общий разбор
пока не отдаёт, поэтому eval снимает HTML, который `check_home` уже скачал (ответ клиента — через
обработчик ответов httpx, у каждого сайта свой список), и читает текст `visible_text` — тем же
правилом, что предложено для общего `HomeSignals` (правка общего — в отчёте среза). Файл с
главными — вне репозитория, с датой дочитывания.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable, Sequence
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from backend.config import judge as judge_cfg
from backend.features.donors.home_signals import check_home
from backend.features.sales.segment_judge import HOME_TEXT, SiteText
from backend.features.sales.segment_text import excerpt
from backend.shared.net.url_guard import guarded_client
from selectolax.parser import HTMLParser

#: Главных за раз при дочитывании: замер, а не прогон.
FETCH_AT_ONCE = 8
#: Что не текст для глаз человека: код, стили, векторы, шаблоны, рамки — и меню (у судьи меню —
#: своя строка).
HIDDEN = ["script", "style", "noscript", "template", "svg", "iframe", "nav"]
#: Элементы, с которых на экране начинается новый блок: между блоками — « · ».
BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "button", "caption", "dd", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "label", "legend", "li", "main", "option", "p", "pre",
        "section", "summary", "table", "td", "th", "tr", "ul", "ol",
    }
)  # fmt: skip
#: Граница блока, пока пробелы не схлопнуты: перевод строки в исходнике — пробел, а не граница.
_EDGE = " "
_SPACES = re.compile(rf"[^\S{_EDGE}]+")
JOIN = " · "
#: Ответы, которые получил клиент главных в задаче одного сайта.
_SEEN: ContextVar[list[httpx.Response] | None] = ContextVar("eval_home_seen", default=None)


class HomesError(RuntimeError):
    """Файла дочитанных главных нет."""


def visible_text(html: str) -> str:
    """Видимый текст страницы одной строкой: тело без кода, стилей, векторов, шаблонов и меню,
    блоки через « · », пробелы схлопнуты, повтор блока подряд — один раз."""
    tree = HTMLParser(html)
    tree.strip_tags(HIDDEN)
    root = tree.body or tree.root
    if root is None:
        return ""
    for node in [node for node in root.traverse() if node.tag in BLOCKS]:
        node.insert_before(_EDGE)
        node.insert_after(_EDGE)
    blocks: list[str] = []
    for piece in _SPACES.sub(" ", root.text(separator=" ")).split(_EDGE):
        block = piece.strip()
        if block and (not blocks or blocks[-1] != block):
            blocks.append(block)
    return JOIN.join(blocks)


async def _keep(response: httpx.Response) -> None:
    """Ответ клиента — в список сайта, чью главную читает эта задача."""
    seen = _SEEN.get()
    if seen is not None:
        seen.append(response)


async def _fetch_one(
    client: httpx.AsyncClient, case: dict[str, Any], gate: asyncio.Semaphore
) -> dict[str, Any]:
    seen: list[httpx.Response] = []
    _SEEN.set(seen)
    async with gate:
        signals = await check_home(client, case["host"])
    page = seen[-1] if signals.reached and seen else None
    return {
        "id": case["id"],
        "host": case["host"],
        "fetched_at": datetime.now(UTC).date().isoformat(),
        "reached": signals.reached,
        "error": signals.error,
        "text": visible_text(page.text) if page is not None else "",
    }


async def fetch_homes(cases: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Главные случаев набора — общим `check_home`, клиентом с защитой исходящих адресов и сроком
    главной судьи, как у прогона (`segment_jobs`)."""
    gate = asyncio.Semaphore(FETCH_AT_ONCE)
    hooks = {"response": [_keep]}
    async with guarded_client(timeout=judge_cfg.HOME_TIMEOUT_SEC, event_hooks=hooks) as client:
        tasks = [_fetch_one(client, case, gate) for case in cases]
        return list(await asyncio.gather(*tasks))


def fetched_to(cases: Sequence[dict[str, Any]], out: Path) -> int:
    """Дочитать главные набора в файл (вне репозитория) и сказать, сколько открылось."""
    rows = asyncio.run(fetch_homes(cases))
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
