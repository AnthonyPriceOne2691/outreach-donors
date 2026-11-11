"""Совет судьи рекламодателей человеку — только после его решения (проект автоматизации спорных,
PR 6).

**До решения совета нет нигде**: ни в очереди проверки, ни в выгрузке спорных. Совет рядом с
нерешённой строкой тянул бы решение за собой, а по решениям человека меряется и скоринг, и сам
судья: «решение не должно зависеть от того, что идёт в замер» (`crawl/sample.py`). После решения
совет — для сверки глазами: что судья увидел и почему сказал так.

Совет — по последней строке суда пары, таблицей правил нынешней версии: правка таблицы меняет его
сразу, без модели (`judge_rules.advise`). Признак «донор — каталог» — тоже нынешний, по судам
донора в когорте строки (`judge_store.Catalogs`): человек видит тот же совет, что поставит пересчёт.
Строка не той формы совета не даёт.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.advertiser import AdvertiserJudgementModel, CandidateModel
from backend.features.crawl.judge_facts import LinkFacts
from backend.features.crawl.judge_rules import Advice, advise
from backend.features.crawl.judge_store import Catalogs

Judgement = AdvertiserJudgementModel


@dataclass(frozen=True, slots=True)
class JudgeAdvice:
    """Совет судьи по паре: что сказал, каким правилом, по каким цитатам и какой когортой."""

    advice: Advice
    rule: str
    words: str
    link_quote: str
    target_quote: str
    disclosure: str
    prompt_version: str
    model: str
    judged_at: datetime


def _advice_of(row: AdvertiserJudgementModel, *, catalog_donor: bool) -> JudgeAdvice | None:
    facts = LinkFacts.from_json(row.facts)
    if facts is None:
        return None
    ruled = advise(facts, catalog_donor=catalog_donor)
    return JudgeAdvice(
        ruled.advice,
        ruled.rule,
        ruled.words,
        facts.link_quote,
        facts.target_quote,
        facts.disclosure,
        row.prompt_version,
        row.model,
        row.updated_at,
    )


async def after_decision(
    session: AsyncSession, candidates: Sequence[CandidateModel]
) -> dict[int, JudgeAdvice]:
    """Советы судьи строкам, по которым человек уже решил: кандидат → совет его пары."""
    decided = [row for row in candidates if row.confirmed is not None]
    if not decided:
        return {}
    pairs = {(row.donor_host, row.target_root) for row in decided}
    found = await session.scalars(
        select(Judgement)
        .where(tuple_(Judgement.donor_host, Judgement.target_root).in_(pairs))
        .order_by(Judgement.updated_at, Judgement.id)
    )
    latest: dict[tuple[str, str], AdvertiserJudgementModel] = {}
    for row in found.all():
        latest[(row.donor_host, row.target_root)] = row
    catalogs = Catalogs(session)
    advice: dict[int, JudgeAdvice] = {}
    for candidate in decided:
        found_row = latest.get((candidate.donor_host, candidate.target_root))
        if found_row is None:
            continue
        said = _advice_of(found_row, catalog_donor=await catalogs.has(found_row))
        if said is not None:
            advice[candidate.id] = said
    return advice
