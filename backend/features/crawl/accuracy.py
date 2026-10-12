"""Скоринг против человека: доля ложных рекламодателей.

Требование Этапа 2 — «не более 10% ложных рекламодателей» («Допуск ошибок»). Мера —
по образцу точности судьи Этапа 1 (`review/accuracy.py`): решение человека хранится
рядом с вердиктом скоринга и его не переписывает (`models/advertiser.py`), и по
расхождению между ними видно, как часто скоринг ошибается. До 11.10.2026 механика
была, а числа не было нигде (`okf/link-scoring.md`, «Чего мы не знаем»).

**Ложный — «куплено», которому человек сказал «не пишем».** «Куплено» уходит к письму
без человека: перевод берёт его сам (`crawl/promote.py`), и ложный среди них — оффер
тому, кто ничего не покупал. Спорные (2–3 балла) уходят только после «пишем»: там
решает человек, и их доля «пишем» говорит, насколько осторожен порог «спорно», а не
сколько писем ушло зря.

**Доля — по случайной выборке «куплено»** (`crawl/sample.py`, решение Anthony
12.10.2026): очередь идёт от сильных к слабым, и по всем решённым доля занижена —
сильные и самые верные. Выборка — около пятой части доменов, постоянная, и очередь
подмешивает её строки между сильными. Нерешённый кандидат выборки не верный и не
ложный — его не смотрели.

**Вывод «в допуске / вне допуска» — только на выборке судьи Этапа 1**
(`review/accuracy.AUTO_ACCEPT_MIN_DECISIONS`): способа замера ТЗ не задаёт, а число на
десятке решений — случай. До неё доля видна, вывода нет.

**Решение — одно на строку кандидата**: ссылку донора на домен человек решает
в очереди строкой, и пересчёт весов переносит решение на новую строку
(`crawl/gate.py`). Доля поэтому меряет нынешние веса против решений человека:
правка весов, снявшая ложного с «куплено», снимает его и из доли.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.sample import in_sample
from backend.features.review.accuracy import AUTO_ACCEPT_MIN_DECISIONS

#: Допуск требования: не более 10% ложных рекламодателей. Ровно 10% — ещё в допуске.
FALSE_LIMIT = 0.10

#: С какой выборки доле можно верить: та же, что у точности судьи Этапа 1, —
#: не своё число.
MIN_DECISIONS = AUTO_ACCEPT_MIN_DECISIONS


@dataclass(frozen=True, slots=True)
class Decisions:
    """Кандидаты одного вердикта скоринга и решения человека по ним."""

    total: int = 0
    #: «Пишем» — человек согласен: это рекламодатель.
    write: int = 0
    #: «Не пишем».
    skip: int = 0

    @property
    def decided(self) -> int:
        return self.write + self.skip


@dataclass(frozen=True, slots=True)
class ScoringCheck:
    """Скоринг против человека: решения по каждому вердикту и доля ложных — по выборке."""

    by_verdict: dict[Verdict, Decisions]
    #: «Куплено» из случайной выборки и решения по ним — по ним одним считается доля.
    sample: Decisions = field(default_factory=Decisions)

    def of(self, verdict: Verdict) -> Decisions:
        """Решения по вердикту; кандидатов с ним нет — нули, а не отказ."""
        return self.by_verdict.get(verdict, Decisions())

    @property
    def false_share(self) -> float | None:
        """Доля «не пишем» среди решённых «куплено» выборки. Решений нет — `None`, а не
        ноль: «ложных нет» и «не смотрели ни одного» — разные новости."""
        return self.sample.skip / self.sample.decided if self.sample.decided else None

    @property
    def within_limit(self) -> bool | None:
        """В допуске ли доля. Решений выборки меньше `MIN_DECISIONS` или нет вовсе —
        `None`: вывода нет, есть только число."""
        share = self.false_share
        if share is None or self.sample.decided < MIN_DECISIONS:
            return None
        return share <= FALSE_LIMIT


def _decisions(by: dict[bool | None, int]) -> Decisions:
    return Decisions(total=sum(by.values()), write=by.get(True, 0), skip=by.get(False, 0))


async def scoring_check(session: AsyncSession) -> ScoringCheck:
    """Решения человека по всем кандидатам — одним запросом: по вердикту, решению и
    выборке. Признак выборки — колонкой подзапроса: в группировке выражение с
    параметром база сочла бы другим, чем в списке колонок."""
    rows = select(
        CandidateModel.verdict.label("verdict"),
        CandidateModel.confirmed.label("confirmed"),
        in_sample().label("sampled"),
    ).subquery()
    counted = await session.execute(
        select(rows.c.verdict, rows.c.confirmed, rows.c.sampled, func.count()).group_by(
            rows.c.verdict, rows.c.confirmed, rows.c.sampled
        )
    )
    by_verdict: dict[Verdict, dict[bool | None, int]] = {}
    sample: dict[bool | None, int] = {}
    for verdict, confirmed, sampled, number in counted.tuples():
        by = by_verdict.setdefault(verdict, {})
        by[confirmed] = by.get(confirmed, 0) + int(number)
        if verdict is Verdict.BOUGHT and sampled:
            sample[confirmed] = sample.get(confirmed, 0) + int(number)
    return ScoringCheck(
        by_verdict={verdict: _decisions(by) for verdict, by in by_verdict.items()},
        sample=_decisions(sample),
    )
