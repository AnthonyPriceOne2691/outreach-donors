"""Что дали ключи: по каким запросам нашлись принятые доноры.

Прогон хранит, по каким ключам нашёлся каждый домен выдачи (`found_by`,
с 23.09.2026), а решение человека о домене лежит в очереди прогона и на
доноре. Сведя одно с другим, видно, какие ключи дают доноров, а какие —
только отказы, вендоров или ничего. Прогон 23.09.2026 на ста ключах
показал это вручную: «best X software» приносил сами продукты, а «write
for us» — площадки. Теперь это число, а не разбор адресов глазами.

**Два среза, и они отвечают на разные вопросы.**

- *Ключи прогона* — что дал этот набор: считается по очереди самого
  прогона, так же, как вкладки экрана рассмотрения. Ключ, не нашедший
  ничего, тоже в списке: пустой ключ — это тоже ответ.
- *Ключи страны* — что брать в следующий прогон: по всем прогонам страны,
  с последним решением о доноре. Домен, найденный одним ключом в трёх
  прогонах, считается один раз — иначе удачный ключ, повторённый трижды,
  выглядел бы втрое удачнее.

**Прогон без разметки — это не «ключи ничего не дали».** Прогоны до
23.09.2026 связь не хранили; для них ответ «не знаем», а не таблица нулей.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import DonorStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunCandidateModel, RunModel

#: Решения очереди и донора — строками, как в базе.
ACCEPTED, REJECTED, PENDING = "accepted", "rejected", "pending"


@dataclass(frozen=True, slots=True)
class KeywordYield:
    """Что дал один ключ."""

    keyword: str
    #: Доменов в выдаче по ключу — всех, до порогов и судьи.
    found: int
    #: Из них дошли до рассмотрения человеком (годны по порогам).
    queued: int
    accepted: int
    rejected: int
    #: Ждут решения.
    pending: int
    #: В скольких прогонах ключ был (у среза прогона — всегда 1).
    runs: int = 1


def _key(keyword: str) -> str:
    """Один ключ в разных прогонах пишут по-разному: регистр и пробелы."""
    return " ".join(keyword.lower().split())


def _found_by(run: RunModel) -> Mapping[str, Sequence[str]] | None:
    found = (run.candidates or {}).get("found_by")
    return found if found else None


def _tally(
    keyword: str,
    hosts: Collection[str],
    decisions: Mapping[str, str | None],
    *,
    runs: int = 1,
) -> KeywordYield:
    """Сосчитать один ключ. `hosts` — без повторов (их собирают множеством
    или из `found_by`, где домен один раз). `decisions`: домен → решение;
    нет домена — до рассмотрения не дошёл; `None` — дошёл, решения нет."""
    queued = [decisions[host] for host in hosts if host in decisions]
    return KeywordYield(
        keyword=keyword,
        found=len(hosts),
        queued=len(queued),
        accepted=sum(1 for decision in queued if decision == ACCEPTED),
        rejected=sum(1 for decision in queued if decision == REJECTED),
        pending=sum(1 for decision in queued if decision not in (ACCEPTED, REJECTED)),
        runs=runs,
    )


def _ranked(rows: Iterable[KeywordYield]) -> list[KeywordYield]:
    """Сначала давшие принятых, потом ждущие решения, потом по охвату."""
    return sorted(rows, key=lambda row: (-row.accepted, -row.pending, -row.found, row.keyword))


async def run_yield(session: AsyncSession, run: RunModel) -> list[KeywordYield] | None:
    """Что дали ключи прогона. `None` — прогон не хранит, какой ключ что нашёл."""
    found_by = _found_by(run)
    if found_by is None:
        return None
    rows = await session.execute(
        select(DomainModel.host, RunCandidateModel.status)
        .join(RunCandidateModel, RunCandidateModel.domain_id == DomainModel.id)
        .where(RunCandidateModel.run_id == run.id)
    )
    decisions: dict[str, str | None] = dict(rows.tuples().all())

    hosts_of: dict[str, list[str]] = {keyword: [] for keyword in run.keywords or []}
    for host, keywords in found_by.items():
        for keyword in keywords:
            hosts_of.setdefault(keyword, []).append(host)
    return _ranked(_tally(keyword, hosts, decisions) for keyword, hosts in hosts_of.items())


async def country_yield(
    session: AsyncSession, country: str, *, limit: int = 30
) -> list[KeywordYield]:
    """Ключи страны, дававшие принятых доноров, — по всем её прогонам.

    Только те, у кого принятые есть: список нужен, чтобы брать ключи
    в следующий прогон, а ключ без единого принятого туда не просится.
    Решение — последнее о доноре (`donors.review`): человек мог передумать
    в следующем прогоне, и считать надо то, что решено сейчас.
    """
    runs = await session.execute(
        select(RunModel)
        .where(RunModel.country == country.lower())
        .where(RunModel.stage == Stage.DONORS)
        .order_by(RunModel.id)
    )
    shown: dict[str, str] = {}
    hosts_of: dict[str, set[str]] = {}
    runs_of: dict[str, int] = {}
    for run in runs.scalars().all():
        found_by = _found_by(run)
        if found_by is None:
            continue
        used: set[str] = set()
        for host, keywords in found_by.items():
            for keyword in keywords:
                key = _key(keyword)
                shown.setdefault(key, keyword)
                hosts_of.setdefault(key, set()).add(host)
                used.add(key)
        for key in used:
            runs_of[key] = runs_of.get(key, 0) + 1

    everyone = set().union(*hosts_of.values()) if hosts_of else set()
    decisions = await _donor_decisions(session, everyone)
    rows = [
        _tally(shown[key], hosts, decisions, runs=runs_of.get(key, 1))
        for key, hosts in hosts_of.items()
    ]
    return _ranked(row for row in rows if row.accepted > 0)[:limit]


async def _donor_decisions(session: AsyncSession, hosts: set[str]) -> dict[str, str | None]:
    """Годные по порогам домены и последнее решение о них. Негодных нет —
    до рассмотрения они не доходят и в «дошли до очереди» не считаются."""
    if not hosts:
        return {}
    rows = await session.execute(
        select(DomainModel.host, DonorModel.review)
        .join(DonorModel, DonorModel.domain_id == DomainModel.id)
        .where(DomainModel.host.in_(sorted(hosts)))
        .where(DonorModel.status == DonorStatus.SUITABLE)
    )
    return {host: review or PENDING for host, review in rows.tuples().all()}
