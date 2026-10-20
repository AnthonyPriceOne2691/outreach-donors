"""Метрики eval судьи сегмента: случай глазами eval, доли ложных, классы ошибок, доли полос и
токены. Запуск и ворота — `scripts/eval_sales_segment.py` (как считаются пары `yes|unsure` и
варианты наблюдения — в его шапке)."""

from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import dataclass
from operator import attrgetter
from typing import Any

from backend.features.sales.segment_models import Decider, Lane
from backend.features.sales.segment_window import SPLIT_TOKENS
from scripts.eval_sales_segment_sets import WANTED

#: Класс ложного «отказ»: по кому он вышел.
CLASSES = ("segment", "language", "intermediary", "rule")


@dataclass(frozen=True, slots=True)
class Result:
    case_id: str
    hypothesis: str
    expect: str
    lane: str
    reason: str
    quote: str | None
    decided_by: str
    unanswered: bool
    tokens: int
    #: Те же токены раздельно (`segment_window.SPLIT_TOKENS`): ввод, вывод, «раздельно
    #: неизвестно», сколько из вывода — рассуждение.
    split: dict[str, int] = dataclasses.field(default_factory=dict)
    tags: tuple[str, ...] = ()
    #: Вид сайта, как его назвала модель (`segment_judge.KINDS`).
    kind: str | None = None
    #: Язык текста сайта — код или письменность словами (`segment_language.identify`): разбивка
    #: замера по языку, тем же правилом, что вердикт языка.
    site_language: str = ""

    @property
    def labeled(self) -> bool:
        """Случай с решением человека: без него — только в долях полос и токенах."""
        return bool(self.expect)

    @property
    def answers(self) -> frozenset[str]:
        """Верные ответы судьи: решение человека или любой из пары `yes|unsure`, `no|unsure`."""
        return frozenset(self.expect.split("|"))

    @property
    def right(self) -> bool:
        return self.lane in {WANTED[answer] for answer in self.answers}

    @property
    def false_accept(self) -> bool:
        """Неподходящий сайт принят без человека — он стал бы лидом."""
        return "yes" not in self.answers and self.lane == Lane.ACCEPT

    @property
    def false_reject(self) -> bool:
        """Подходящий сайт отсечён — в `enforce` потерянный лид."""
        return "yes" in self.answers and self.lane == Lane.REJECT

    @property
    def cut_by(self) -> str:
        """Чей это «отказ»: язык сайта, правило, посредник (по виду от модели) или сегмент."""
        if self.decided_by == Decider.LANGUAGE:
            return "language"
        if self.decided_by == Decider.RULE:
            return "rule"
        return "intermediary" if self.kind == "intermediary" else "segment"


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _groups(results: list[Result]) -> tuple[list[Result], list[Result], list[Result]]:
    """Знаменатели: с `yes` среди ответов, без `yes`, с `no` (пары — в шапке модуля)."""
    fits = [r for r in results if "yes" in r.answers]
    misfits = [r for r in results if "yes" not in r.answers]
    cuts = [r for r in results if "no" in r.answers]
    return fits, misfits, cuts


def _rate(group: list[Result], field: str) -> float | None:
    """Доля случаев группы, у которых признак `field` истинен."""
    return _share(sum(map(attrgetter(field), group)), len(group))


def _lanes(results: list[Result]) -> dict[str, Any]:
    """Доли полос по всем случаям: вариант (b) и нынешнее наблюдение (шапка модуля)."""
    review = sum(r.lane == Lane.REVIEW for r in results)
    judged = sum(r.lane == Lane.REJECT and r.decided_by != Decider.RULE for r in results)
    return {
        "to_human": _share(review, len(results)),
        "would_reject": _share(judged, len(results)),
        "to_human_shadow": _share(review + judged, len(results)),
    }


def _tokens(results: list[Result]) -> dict[str, Any]:
    calls = sum(r.decided_by == Decider.MODEL and r.tokens > 0 for r in results)
    total = sum(r.tokens for r in results)
    return {
        "unanswered": sum(r.unanswered for r in results),
        "tokens": total,
        **{key: sum(r.split.get(key, 0) for r in results) for key in SPLIT_TOKENS},
        "model_calls": calls,
        "tokens_per_call": round(total / calls) if calls else None,
    }


def metrics(results: list[Result]) -> dict[str, Any]:
    """Числа ворот — по случаям с разметкой: полнота «принять» и «отказ», доли ложных — от своего
    знаменателя (как считаются пары — в шапке модуля); доли полос и токены — по всем."""
    labeled = [r for r in results if r.labeled]
    fits, misfits, cuts = _groups(labeled)
    missed = Counter(r.cut_by for r in fits if r.false_reject)
    return {
        "cases": len(results),
        "labeled": len(labeled),
        "accept_recall": _rate(fits, "right"),
        "reject_recall": _rate(cuts, "right"),
        "false_accept": _rate(misfits, "false_accept"),
        "false_reject": _rate(fits, "false_reject"),
        "false_reject_by": {name: missed[name] for name in CLASSES},
        "unsure": _share(sum(r.lane == Lane.REVIEW for r in labeled), len(labeled)),
        **_lanes(results),
        **_tokens(results),
        "kinds": dict(Counter(r.kind or "—" for r in results if r.decided_by == Decider.MODEL)),
    }
