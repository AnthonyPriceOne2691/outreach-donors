"""Продающие ссылки обходов, посчитанных раньше этих чисел: бесплатно, по сохранённым ссылкам.

Панель берёт страницы и ссылки из `stats` обхода (`load_quality.py`), а кладёт их пересчёт
кандидатов (`gate.judge_run`). Звать его ради чисел нельзя: он спрашивает DR и переписывает
кандидатов новыми весами, а панели от него нужна только арифметика.

**Здесь — только чтение и одна запись в `stats`.** Домены «куплено» и их ссылки берутся у
сохранённых кандидатов обхода, страницы — из сохранённых `outlinks` тем же правилом, что у
пересчёта (`gate.selling_stats`). Ни сети, ни Ahrefs, ни записи кандидатов: вердикты и решения
людей остаются какими были. Повтор безопасен: посчитанный обход не трогается.

**Только обходы с кандидатами** — последний пригодный у каждого донора или названный по номеру.
У обхода, которого сменил новый, кандидатов нет и вердикты не сохранились: выдумывать их нечем,
и в таблице качества он остаётся с пустыми числами.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl.findings import LINK_COLUMNS
from backend.features.crawl.gate import SELLING_LINKS, SELLING_PAGES, selling_stats
from backend.features.crawl.links import OutLink


@dataclass(frozen=True, slots=True)
class Filled:
    """Что сделал проход: дополнено обходов, уже было посчитано, без кандидатов — не из чего."""

    filled: int = 0
    known: int = 0
    empty: int = 0


async def _bought(session: AsyncSession, run_id: int) -> dict[str, int] | None:
    """Домены «куплено» обхода и их ссылки; кандидатов нет вовсе — `None`: вердиктов не осталось."""
    rows = await session.execute(
        select(CandidateModel.target_root, CandidateModel.links, CandidateModel.verdict).where(
            CandidateModel.crawl_run_id == run_id
        )
    )
    found = rows.tuples().all()
    if not found:
        return None
    return {root: links for root, links, verdict in found if verdict is Verdict.BOUGHT}


async def _links(session: AsyncSession, run_id: int) -> list[OutLink]:
    """Ссылки обхода — колонками, без объектов: у витрины их десятки тысяч."""
    rows = await session.execute(select(*LINK_COLUMNS).where(OutLinkModel.crawl_run_id == run_id))
    return [OutLink(*values) for values in rows.tuples().all()]


async def fill(session: AsyncSession, runs: Iterable[CrawlRunModel]) -> Filled:
    """Дополнить `stats` обходов `runs` числами продающих ссылок. Без коммита."""
    filled = known = empty = 0
    for run in runs:
        stats = run.stats or {}
        if SELLING_PAGES in stats and SELLING_LINKS in stats:
            known += 1
            continue
        bought = await _bought(session, run.id)
        if bought is None:
            empty += 1
            continue
        run.stats = {**stats, **selling_stats(await _links(session, run.id), bought)}
        filled += 1
    await session.flush()
    return Filled(filled=filled, known=known, empty=empty)
