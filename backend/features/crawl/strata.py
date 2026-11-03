"""Решения выборки спорных по срезам: балл, признак скоринга, ниша донора.

Проект замера спорных, 02.11.2026: общая доля «не пишем» прячет, где именно ошибается
скоринг. Решения человека смещены по донорам и нише, и порог по баллу, правка веса признака
или судья решают разные срезы: «3 балла проходят, 2 — нет» видно только разбивкой. Срез —
только по случайной выборке (`crawl/sample.py`), как и сама доля (`crawl/accuracy.py`).

**Признак — по началу причины** (`crawl/scoring.py`): причины хранятся текстом, и в балл
идут те, что кончаются «+N». Причина с баллом, которой нет в таблице, — «прочее», а не
пропуск: новая причина скоринга не исчезает из разбивки молча. У строки признаков бывает
несколько, и она входит в каждый: суммы признаков больше числа строк.

**Ниша — тема сайта донора** (`domains.site_topic`); темы нет — «неизвестна».
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.domain import DomainModel
from backend.features.crawl.accuracy import Decisions
from backend.features.crawl.sample import in_sample
from backend.features.topics.taxonomy import label_of


@dataclass(frozen=True, slots=True)
class Feature:
    """Признак скоринга: ключ, слова экрана и начало причины, которой он записан."""

    key: str
    title: str
    starts: re.Pattern[str]


#: Порядок — как признаки идут в `scoring.score_link` и `_candidate`.
FEATURES: tuple[Feature, ...] = (
    Feature("paid_source", "источник в платной статье", re.compile(r"ссылка-источник в платной")),
    Feature("sponsored", "rel=sponsored", re.compile(r"rel=sponsored")),
    Feature("marker", "пометка рекламы", re.compile(r"пометка рекламного материала")),
    Feature("affiliate", "партнёрская ссылка", re.compile(r"партнёрская ссылка")),
    Feature("dofollow", "dofollow из тела", re.compile(r"dofollow из тела")),
    Feature("anchor", "коммерческий анкор", re.compile(r"коммерческий анкор")),
    Feature(
        "nofollow", "коммерческая под nofollow", re.compile(r"коммерческая ссылка под nofollow")
    ),
    Feature("pages", "на многих страницах донора", re.compile(r"ссылки с \d+ страниц донора")),
    Feature("donors", "у нескольких наших доноров", re.compile(r"встречается у \d+ наших доноров")),
    Feature("off_topic", "чужая тематика", re.compile(r"тематика не совпадает")),
)
#: Причина с баллом — кончается «+N»: остальные поясняют, а не считают.
SCORED = re.compile(r"\+\d+$")
OTHER = Feature("other", "прочее", re.compile(r""))
#: Ниша, когда темы сайта донора нет.
UNKNOWN_NICHE = "unknown"
UNKNOWN_TITLE = "неизвестна"


@dataclass(frozen=True, slots=True)
class Stratum:
    """Срез выборки спорных: строки в нём и решения по ним."""

    key: str
    title: str
    decisions: Decisions


@dataclass(frozen=True, slots=True)
class Strata:
    """Выборка спорных по баллу, признаку и нише. Срез без строк не показан."""

    points: list[Stratum]
    features: list[Stratum]
    niches: list[Stratum]


def features_of(reasons: Iterable[Any] | None) -> list[Feature]:
    """Признаки строки — по её причинам с баллом, без повторов, в порядке таблицы."""
    found: set[str] = set()
    for reason in reasons or ():
        text = str(reason).strip()
        if SCORED.search(text) is None:
            continue
        known = next((one for one in FEATURES if one.starts.match(text)), OTHER)
        found.add(known.key)
    return [one for one in (*FEATURES, OTHER) if one.key in found]


def _decided(count: Counter[tuple[str, bool | None]], key: str) -> Decisions:
    write, skip, wait = count[(key, True)], count[(key, False)], count[(key, None)]
    return Decisions(total=write + skip + wait, write=write, skip=skip)


def strata(rows: Iterable[tuple[int, list[Any] | None, bool | None, str | None]]) -> Strata:
    """Срезы из строк выборки «балл, причины, решение, тема донора»."""
    points: Counter[tuple[str, bool | None]] = Counter()
    features: Counter[tuple[str, bool | None]] = Counter()
    niches: Counter[tuple[str, bool | None]] = Counter()
    titles: dict[str, str] = {UNKNOWN_NICHE: UNKNOWN_TITLE}
    for score, reasons, confirmed, topic in rows:
        points[(str(score), confirmed)] += 1
        for feature in features_of(reasons):
            features[(feature.key, confirmed)] += 1
        niche = topic or UNKNOWN_NICHE
        titles.setdefault(niche, label_of(niche))
        niches[(niche, confirmed)] += 1
    scores = sorted({key for key, _ in points}, key=int)
    by_count = sorted(
        {key for key, _ in niches},
        key=lambda key: (key == UNKNOWN_NICHE, -_decided(niches, key).total, key),
    )
    return Strata(
        points=[Stratum(key, key, _decided(points, key)) for key in scores],
        features=[
            Stratum(one.key, one.title, _decided(features, one.key))
            for one in (*FEATURES, OTHER)
            if _decided(features, one.key).total
        ],
        niches=[Stratum(key, titles[key], _decided(niches, key)) for key in by_count],
    )


async def disputed_strata(session: AsyncSession) -> Strata:
    """Спорные случайной выборки по срезам. Тема донора — у его домена по имени, как держит
    донора кандидат (`donor_host`)."""
    rows = await session.execute(
        select(
            CandidateModel.points,
            CandidateModel.reasons,
            CandidateModel.confirmed,
            DomainModel.site_topic,
        )
        .outerjoin(DomainModel, DomainModel.host == CandidateModel.donor_host)
        .where(CandidateModel.verdict == Verdict.PENDING, in_sample())
    )
    return strata(rows.tuples().all())
