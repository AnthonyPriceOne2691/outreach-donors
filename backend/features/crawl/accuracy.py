"""Скоринг против человека: доля ложных рекламодателей и ошибка базы «всегда пишем».

Требование Этапа 2 — «не более 10% ложных рекламодателей» («Допуск ошибок»). Мера —
по образцу точности судьи Этапа 1 (`review/accuracy.py`): решение человека хранится
рядом с вердиктом скоринга и его не переписывает (`models/advertiser.py`), и по
расхождению между ними видно, как часто скоринг ошибается. До 11.10.2026 механика
была, а числа не было нигде (`okf/link-scoring.md`, «Чего мы не знаем»).

**Ложный — «куплено», которому человек сказал «не пишем».** «Куплено» уходит к письму
без человека: перевод берёт его сам (`crawl/promote.py`), и ложный среди них — оффер
тому, кто ничего не покупал.

**Спорные (2–3 балла) меряются тем же правилом** (проект замера спорных, 02.11.2026): их доля
«не пишем» — ошибка тривиальной базы «писать всем спорным». Решения в очереди легли на верх
по баллу, и почти сплошное «пишем» среди них ничего не значит: такую долю дал бы и судья,
всегда говорящий «пишем». База — то, что любой автоматический совет обязан побить.

**Доля — по случайной выборке** (`crawl/sample.py`, решение Anthony 12.10.2026): очередь
идёт от сильных к слабым, и по всем решённым доля занижена — сильные и самые верные.
Выборка — около пятой части доменов, постоянная, и очередь подмешивает её строки между
сильными. Нерешённый кандидат выборки не верный и не ложный — его не смотрели.

**Вывод — по границам доли, а не по числу решений** (`shared/stats.py`). До 03.11.2026 вывода
не было до 200 решений выборки: у «куплено» их не набралось бы никогда — выборка от двухсот
строк около сорока, и молчание контроля выглядело как «мало решений». Теперь: верхняя граница
не выше допуска — в допуске; нижняя выше — вне допуска; иначе — не доказано, и сказано,
сколько решений без «не пишем» ещё нужно для «в допуске» (`to_go`). Ровно 10 % — ещё в допуске.

**Решение — одно на строку кандидата**: ссылку донора на домен человек решает
в очереди строкой, и пересчёт весов переносит решение на новую строку
(`crawl/gate.py`). Доля поэтому меряет нынешние веса против решений человека:
правка весов, снявшая ложного с «куплено», снимает его и из доли.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.sample import in_sample
from backend.shared import stats

#: Допуск требования: не более 10% ложных рекламодателей. Ровно 10% — ещё в допуске.
FALSE_LIMIT = 0.10


class Within(StrEnum):
    """Вывод о доле против допуска — по её границам."""

    #: Верхняя граница не выше допуска: доказанно в допуске.
    YES = "within"
    #: Нижняя граница выше допуска: доказанно вне допуска.
    NO = "outside"
    #: Допуск между границами: решений мало, чтобы сказать.
    UNPROVEN = "unproven"
    #: Решений нет — мерить нечем.
    UNMEASURED = "unmeasured"


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

    @property
    def left(self) -> int:
        """Ещё не решённых — то, из чего замер может вырасти."""
        return self.total - self.decided


@dataclass(frozen=True, slots=True)
class Share:
    """Доля «не пишем» среди решённых строк выборки — с границами и выводом против допуска.

    `share`, `low`, `high` пусты — решений нет. `to_go` — сколько решений без «не пишем» ещё
    нужно для «в допуске»: только пока вывода нет; при выводе — пусто. Больше, чем `left`
    нерешённых строк выборки, — нынешней выборкой вывода не получить."""

    decisions: Decisions
    share: float | None
    low: float | None
    high: float | None
    within: Within
    to_go: int | None

    @property
    def reachable(self) -> bool:
        """Хватит ли нерешённых строк выборки, чтобы дойти до «в допуске»."""
        return self.to_go is not None and self.to_go <= self.decisions.left


def share_of(decisions: Decisions, limit: float = FALSE_LIMIT) -> Share:
    """Доля «не пишем» по решениям выборки, границы и вывод против `limit`."""
    skip, decided = decisions.skip, decisions.decided
    if decided == 0:
        return Share(
            decisions, None, None, None, Within.UNMEASURED, stats.clean_needed(0, 0, limit)
        )
    low, high = stats.lower_bound(skip, decided), stats.upper_bound(skip, decided)
    if high <= limit:
        within = Within.YES
    elif low > limit:
        within = Within.NO
    else:
        within = Within.UNPROVEN
    to_go = stats.clean_needed(skip, decided, limit) if within is Within.UNPROVEN else None
    return Share(decisions, skip / decided, low, high, within, to_go)


@dataclass(frozen=True, slots=True)
class ScoringCheck:
    """Скоринг против человека: решения по каждому вердикту и доли — по выборке.

    `bought` — «куплено» случайной выборки: доля ложных рекламодателей. `disputed` — спорные
    случайной выборки: доля «не пишем» — ошибка базы «писать всем спорным»."""

    by_verdict: dict[Verdict, Decisions]
    bought: Share = field(default_factory=lambda: share_of(Decisions()))
    disputed: Share = field(default_factory=lambda: share_of(Decisions()))

    def of(self, verdict: Verdict) -> Decisions:
        """Решения по вердикту; кандидатов с ним нет — нули, а не отказ."""
        return self.by_verdict.get(verdict, Decisions())

    @property
    def sample(self) -> Decisions:
        """«Куплено» из случайной выборки и решения по ним — по ним одним считается доля."""
        return self.bought.decisions

    @property
    def false_share(self) -> float | None:
        """Доля «не пишем» среди решённых «куплено» выборки. Решений нет — `None`, а не
        ноль: «ложных нет» и «не смотрели ни одного» — разные новости."""
        return self.bought.share

    @property
    def within_limit(self) -> Within:
        """В допуске ли доля ложных — по её границам."""
        return self.bought.within


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
    sampled_by: dict[Verdict, dict[bool | None, int]] = {}
    for verdict, confirmed, sampled, number in counted.tuples():
        by = by_verdict.setdefault(verdict, {})
        by[confirmed] = by.get(confirmed, 0) + int(number)
        if sampled:
            picked = sampled_by.setdefault(verdict, {})
            picked[confirmed] = picked.get(confirmed, 0) + int(number)
    return ScoringCheck(
        by_verdict={verdict: _decisions(by) for verdict, by in by_verdict.items()},
        bought=share_of(_decisions(sampled_by.get(Verdict.BOUGHT, {}))),
        disputed=share_of(_decisions(sampled_by.get(Verdict.PENDING, {}))),
    )
