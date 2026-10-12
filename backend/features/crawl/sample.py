"""Случайная выборка «куплено» — честный замер доли ложных рекламодателей.

Решение Anthony 12.10.2026. Очередь «куплено» идёт от сильных к слабым, и доля ложных
по решённым в ней занижена: сильные — и самые верные. Поэтому доля считается по своей
выборке, а не по всем решённым (`crawl/accuracy.py`).

**Выборка постоянная — по хэшу домена.** md5 имени домена: первые две
шестнадцатеричные цифры меньше «33» — 51 из 256, около 20% доменов. Признак
считается, а не хранится: без миграции, и домен в выборке навсегда — у какого бы
донора ни нашёлся и как бы его ни пересчитали. Сравнение — побайтное (`COLLATE "C"`):
порядок букв базы не должен решать, кто в выборке.

**Очередь «куплено» подмешивает выборку между сильными — каждой пятой строкой**,
в порядке хэша, а не балла: иначе выборка решалась бы опять от сильных к слабым.
Остальные строки — как были, сильные первыми (`crawl/review.queue`).

**Человек выборки не отличает**: в очереди её строки обычные, и ответ сервера их не
помечает — решение не должно зависеть от того, что идёт в замер.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl import review

#: Граница выборки: первые две цифры md5 домена меньше неё — 0x33 = 51 из 256, ≈ 20%.
SAMPLE_BELOW = "33"

#: Каждая какая строка очереди «куплено» — из выборки.
EVERY = 5


def _prefix(root: Any) -> Any:
    return func.substr(func.md5(root), 1, len(SAMPLE_BELOW)).collate("C")


def in_sample(root: Any = CandidateModel.target_root) -> ColumnElement[bool]:
    """Домен в выборке — условием базы."""
    result: ColumnElement[bool] = _prefix(root) < SAMPLE_BELOW
    return result


def sampled(host: str) -> bool:
    """Домен в выборке — тем же правилом в питоне. Хэш — жребий, а не защита."""
    digest = hashlib.md5(host.encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[: len(SAMPLE_BELOW)] < SAMPLE_BELOW


def mixed[Row](strong: Sequence[Row], sample: Sequence[Row], *, limit: int) -> list[Row]:
    """Каждая пятая строка — из выборки, остальные — сильные по порядку. Кончилась
    одна очередь — добирается другой: пустых мест нет."""
    rows: list[Row] = []
    strong_left, sample_left = list(strong), list(sample)
    while len(rows) < limit and (strong_left or sample_left):
        take_sample = (len(rows) + 1) % EVERY == 0
        source = sample_left if (take_sample and sample_left) or not strong_left else strong_left
        rows.append(source.pop(0))
    return rows


async def queue(
    session: AsyncSession,
    *,
    limit: int = review.PAGE_SIZE,
    include_decided: bool = False,
    verdict: Verdict = Verdict.PENDING,
) -> list[CandidateModel]:
    """Очередь ручной проверки: «куплено» — с выборкой вперемешку, спорные — как были."""
    if verdict is not Verdict.BOUGHT:
        return await review.queue(
            session, limit=limit, include_decided=include_decided, verdict=verdict
        )
    base = select(CandidateModel).where(CandidateModel.verdict == Verdict.BOUGHT)
    if not include_decided:
        base = base.where(CandidateModel.confirmed.is_(None))
    strong = await session.scalars(
        base.where(~in_sample())
        .order_by(CandidateModel.points.desc(), CandidateModel.id)
        .limit(limit)
    )
    sample = await session.scalars(
        base.where(in_sample())
        .order_by(func.md5(CandidateModel.target_root), CandidateModel.id)
        .limit(limit)
    )
    return mixed(list(strong.all()), list(sample.all()), limit=limit)
