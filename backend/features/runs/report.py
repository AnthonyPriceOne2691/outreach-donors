"""Итог прогона: что получили, во что обошлось и что записать в прогон.

Отдельно от исполнения (`pipeline.py`): отчёт растёт с каждым новым шагом
прогона — судья, очередь, двери сайтов, — а исполнение от этого не должно
расти вместе с ним. Здесь и сам отчёт, и то, как он ложится в запись
прогона (`run_stats`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.config import judge as judge_cfg
from backend.features.ahrefs.units import UnitsCost
from backend.features.core.domain import DonorStatus
from backend.features.crawl.niche import NicheReport
from backend.features.donors.doors import DoorReport
from backend.features.donors.judging import JudgeSummary
from backend.features.review.candidates import QueueReport
from backend.features.runs.failures import described
from backend.features.runs.planning import Candidates, RunPlan
from backend.features.runs.reasons import explained
from backend.features.runs.refusals import reject_key
from backend.features.runs.repository import FAILURE_KEY, REASON_KEY
from backend.features.topics.step import TopicSummary

# Операции, которые покрывает смета. Выдача в неё не входит: к моменту, когда
# смета показывается человеку, она уже потрачена, и включать её значило бы
# просить подтвердить трату, которой не избежать.
ESTIMATED_OPERATIONS = frozenset({"batch_metrics", "by_country"})

#: Вердикты свежих доменов в отчёте прогона (`RunReport.fresh`).
FRESH_KEY = "fresh_verdicts"


@dataclass(slots=True)
class RunReport:
    """Итог прогона. Отвечает на два вопроса: что получили и во что обошлось."""

    plan: RunPlan
    by_status: dict[DonorStatus, int] = field(default_factory=dict)
    reject_reasons: dict[str, int] = field(default_factory=dict)
    unchecked_reasons: dict[str, int] = field(default_factory=dict)
    """Почему «не проверен» — причиной целиком: «страны не получены» следующий
    прогон доберёт, «Ahrefs не знает домен» — нет. Доля «не знаю» — числом
    в отчёте, а не отсутствием записи."""

    countries_refetched: int = 0
    """Сколько доменов прогон добирал по метрикам, купленным раньше (`collect.PaidMetrics`):
    без второй покупки метрик; страны — если пороги этого прогона их пропустили."""

    fresh: Verdicts | None = None
    """Вердикты свежих доменов — этого прогона, по сохранённым метрикам и странам, без
    Ahrefs (`runs/verdicts.py`). Отдельно от купленных сейчас: по вердиктам купленных
    смета считает, сколько доменов дойдёт до запроса по странам
    (`repository.reached_countries`), а свежие стран не спрашивают. `None` — свежих
    этот прогон не судил: их нет или прогон без очереди."""

    spent_units: int = 0
    spent_by_operation: dict[str, int] = field(default_factory=dict)
    free_by_operation: dict[str, int] = field(default_factory=dict)
    """Сколько запросов обслужил кэш провайдера. Не трата, но показатель:
    по нему видно, что повторные обращения действительно бесплатны."""

    judge: JudgeSummary | None = None
    """Итог судьи площадки. `None` — судья выключен (режим `off`).

    Отдельным полем, а не смешано с отсевом по порогам: судья и пороги
    отвечают на разные вопросы, и сложив их, мы потеряли бы ровно то,
    ради чего судья заведён, — сколько мусора проходит ЧЕРЕЗ пороги."""

    review: QueueReport | None = None
    """Что прогон положил на рассмотрение человеку. `None` — очереди нет."""

    topics: TopicSummary | None = None
    """Тематика сайтов — рядом с судьёй, тем же текстом (`topics/step.py`). `None` — судья или
    тематика (`TOPIC_MODE=off`) выключены, и темы в прогоне не определялись: ноль читался бы
    «тем нет»."""

    doors: DoorReport | None = None
    """Меню главных у очереди (`donors.doors`). `None` — не смотрели."""

    niche: NicheReport | None = None
    """Бизнесы ниши из выдачи — в кандидаты в рекламодатели (`crawl/niche.py`).
    `None` — сбор не звали."""

    doors_failure: str | None = None
    """Почему меню главных не посмотрели. Прогон это не роняет: очередь
    уже лежит, просто без подъёма продающих размещение."""

    niche_failure: str | None = None
    """Почему бизнесы ниши не собрали. Прогон это не роняет: доноры оплачены и
    сохранены; сбор повторяется из старого прогона кнопкой на «Рекламодателях»."""

    @property
    def spent_on_estimated(self) -> int:
        """Траты по тем операциям, которые смета покрывает."""
        return sum(
            units
            for operation, units in self.spent_by_operation.items()
            if operation in ESTIMATED_OPERATIONS
        )

    @property
    def actual_pass_share(self) -> float:
        """Какая доля проверенных дошла до запроса по странам.

        Главный источник расхождения со сметой — именно она, а не цены.
        Смета считается по замеренной воронке, а воронка зависит от ниши:
        на сырых доменах порог проходят 39%, на выдаче по коммерческим
        ключам — заметно больше, потому что там изначально сильные сайты.
        """
        checked = len(self.plan.new)
        if not checked:
            return 0.0
        passed = self.by_status.get(DonorStatus.SUITABLE, 0) + self.by_status.get(
            DonorStatus.UNCHECKED, 0
        )
        return passed / checked

    @property
    def estimate_error(self) -> float:
        """Насколько смета разошлась с фактом.

        Сравниваются только сопоставимые траты: смета покрывает запросы
        метрик, но не выдачу. Сложив их, мы получили бы стабильное
        расхождение на стоимость выдачи и решили бы, что цены плывут,
        хотя плывёт только наша арифметика.
        """
        planned = self.plan.estimate.total
        if not planned:
            return 0.0
        return (self.spent_on_estimated - planned) / planned

    def record(self, status: DonorStatus, reason: str, *, again: bool = False) -> None:
        """Вердикт по домену. `again` — домену добирали страны (`countries_refetched`)."""
        self.countries_refetched += int(again)
        _tally(self.by_status, self.reject_reasons, self.unchecked_reasons, status, reason)

    def record_fresh(self, status: DonorStatus, reason: str) -> None:
        """Вердикт свежего домена — этого прогона, по сохранённым метрикам."""
        if self.fresh is None:
            self.fresh = Verdicts()
        _tally(
            self.fresh.by_status, self.fresh.reject_reasons, self.fresh.unchecked, status, reason
        )

    def add_usage(self, operation: str, cost: UnitsCost) -> None:
        if cost.was_free:
            self.free_by_operation[operation] = self.free_by_operation.get(operation, 0) + 1
            return
        self.spent_units += cost.billable
        self.spent_by_operation[operation] = (
            self.spent_by_operation.get(operation, 0) + cost.billable
        )


@dataclass(slots=True)
class Verdicts:
    """Вердикты доменов одним счётом: по вердикту, по причине отказа и «не проверен»."""

    by_status: dict[DonorStatus, int] = field(default_factory=dict)
    reject_reasons: dict[str, int] = field(default_factory=dict)
    unchecked: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "by_status": {status.value: count for status, count in self.by_status.items()},
            "reject_reasons": dict(self.reject_reasons),
            "unchecked_reasons": dict(self.unchecked),
        }


def _tally(
    by_status: dict[DonorStatus, int],
    rejected: dict[str, int],
    unchecked: dict[str, int],
    status: DonorStatus,
    reason: str,
) -> None:
    """Учесть вердикт: отказ — ключом причины (`refusals.reject_key`), «не проверен» —
    причиной целиком."""
    by_status[status] = by_status.get(status, 0) + 1
    if status is DonorStatus.UNSUITABLE:
        key = reject_key(reason)
        rejected[key] = rejected.get(key, 0) + 1
    elif status is DonorStatus.UNCHECKED:
        key = reason or "без причины"
        unchecked[key] = unchecked.get(key, 0) + 1


def _fresh(report: RunReport) -> dict[str, object]:
    """Вердикты свежих — своей веткой: долю дошедших до стран смета берёт из вердиктов
    купленных (`repository.reached_countries`). Свежих не судили — ветки нет."""
    return {} if report.fresh is None else {FRESH_KEY: report.fresh.as_dict()}


def _topic_stats(report: RunReport) -> dict[str, object]:
    """Ветка тем отчёта — только когда темы определялись: у выключенной тематики нулей нет.
    «Модель не ответила» — своим числом с причиной, как у судьи: экран прогонов показывает
    его меткой, а не нулём определённых."""
    return {} if report.topics is None else {"topics": report.topics.as_dict()}


def _source_of(candidates: Candidates) -> dict[str, object]:
    """Журнал списков конкурентов (`runs/competitors.candidates_of`) — без карты «кто на кого
    ссылается»: она лежит с кандидатами, а отчёт читают строкой истории. У выдачи — пусто."""
    if not candidates.source:
        return {}
    return {"source": {k: v for k, v in candidates.source.items() if k != "linked_to"}}


def run_stats(
    report: RunReport, failed: BaseException | None, *, retry: bool = False
) -> dict[str, object]:
    """Отчёт прогона в том виде, в каком его читает человек.

    Расхождение сметы с фактом попадает сюда намеренно: заметное отклонение
    значит, что цены у провайдера изменились и замер пора повторить.
    """
    candidates = report.plan.candidates
    stats: dict[str, object] = {
        "keywords": candidates.keywords,
        "serp_results": candidates.results,
        "unique_hosts": len(candidates.hosts),
        "duplicates_collapsed": candidates.duplicates,
        "urls_unparsed": candidates.dropped,
        "keywords_without_results": candidates.empty_keywords,
        # Выдача оплачена, а не пришла: провайдер не успел. Отдельно от «ничего
        # не нашлось» — экран истории показывает их меткой в строке прогона.
        "keywords_lost": candidates.lost_keywords,
        "already_fresh": len(report.plan.fresh),
        "excluded": len(report.plan.excluded),
        "excluded_by_reason": report.plan.excluded_by_reason,
        "checked_now": len(report.plan.new),
        "by_status": {status.value: count for status, count in report.by_status.items()},
        "reject_reasons": report.reject_reasons,
        "unchecked_reasons": report.unchecked_reasons,
        "countries_refetched": report.countries_refetched,
        "units_estimated": report.plan.estimate.total,
        "units_spent": report.spent_units,
        "units_by_operation": dict(report.spent_by_operation),
        "free_requests": dict(report.free_by_operation),
        "estimate_error": round(report.estimate_error, 3),
        "actual_pass_share": round(report.actual_pass_share, 3),
        "units_saved_by_cache": report.plan.savings_from_cache,
        "units_saved_by_gate": report.plan.savings_from_gate,
    }
    stats.update(_source_of(candidates))
    stats.update(_fresh(report))
    if report.judge is not None:
        # Отдельной веткой, а не строками в общем словаре: у выключенного
        # судьи нулей быть не должно. Ноль читается как «судил и никого
        # не нашёл», а это другая новость, чем «не судил вовсе».
        summary = report.judge
        stats["judge"] = {
            "mode": judge_cfg.MODE.value,
            "judged": summary.judged,
            "from_cache": summary.from_cache,
            "would_cut": summary.would_cut,
            "to_review": summary.to_review,
            # Модель не ответила — не «судил и не отрезал»: экран прогонов
            # показывает это отдельной меткой с причиной.
            "unanswered": summary.unanswered,
            "unanswered_reason": summary.unanswered_reason,
            "by_intent": dict(summary.by_intent),
            "by_decider": dict(summary.by_decider),
            "home_unreached": summary.home_unreached,
            # Судить было не по чему — ни выдачи, ни главной: вердикта нет, судится снова.
            "no_text": summary.no_text,
            "from_index": summary.from_index,
            "tokens": summary.tokens,
            # В наблюдении это «сэкономил бы», во включённом — «сэкономил».
            # Число одно, и по режиму рядом видно, какое из двух.
            "units_saved": summary.units_saved,
        }
    stats.update(_topic_stats(report))
    if report.review is not None:
        stats["review"] = {
            "pending": report.review.pending,
            "carried": report.review.carried,
            # Годны по вердикту до 11.10.2026, но в «кому не пишем»: не положены.
            "not_writing": report.review.not_writing,
        }
    if report.niche is not None:
        stats["niche"] = {"found": report.niche.found, "added": report.niche.added}
    elif report.niche_failure is not None:
        stats["niche"] = {"failure": report.niche_failure}
    if report.doors is not None or report.doors_failure is not None:
        stats["doors"] = (
            report.doors.as_dict()
            if report.doors is not None
            else {"failure": report.doors_failure}
        )
    if failed is not None:
        # Причина остановки хранится рядом с цифрами, а не только в логе:
        # через неделю лог уже не найдут, а запись прогона останется.
        # Сбой с именем класса — для поиска, причина — для экрана.
        stats[FAILURE_KEY] = described(failed)
        lead = "сбой, будет продолжен" if retry else "остановлен"
        stats[REASON_KEY] = f"{lead}: {explained(failed)}"[:500]
    return stats
