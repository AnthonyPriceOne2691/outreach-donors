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
from backend.features.donors.doors import DoorReport
from backend.features.donors.judging import JudgeSummary
from backend.features.review.candidates import QueueReport
from backend.features.runs.failures import described
from backend.features.runs.planning import RunPlan
from backend.features.runs.reasons import explained
from backend.features.runs.repository import FAILURE_KEY, REASON_KEY

# Операции, которые покрывает смета. Выдача в неё не входит: к моменту, когда
# смета показывается человеку, она уже потрачена, и включать её значило бы
# просить подтвердить трату, которой не избежать.
ESTIMATED_OPERATIONS = frozenset({"batch_metrics", "by_country"})


@dataclass(slots=True)
class RunReport:
    """Итог прогона. Отвечает на два вопроса: что получили и во что обошлось."""

    plan: RunPlan
    by_status: dict[DonorStatus, int] = field(default_factory=dict)
    reject_reasons: dict[str, int] = field(default_factory=dict)
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

    doors: DoorReport | None = None
    """Меню главных у очереди (`donors.doors`). `None` — не смотрели."""

    doors_failure: str | None = None
    """Почему меню главных не посмотрели. Прогон это не роняет: очередь
    уже лежит, просто без подъёма продающих размещение."""

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

    def record(self, status: DonorStatus, reason: str) -> None:
        self.by_status[status] = self.by_status.get(status, 0) + 1
        if status is DonorStatus.UNSUITABLE:
            key = reason.split(" ", maxsplit=1)[0] if reason else "без причины"
            self.reject_reasons[key] = self.reject_reasons.get(key, 0) + 1

    def add_usage(self, operation: str, cost: UnitsCost) -> None:
        if cost.was_free:
            self.free_by_operation[operation] = self.free_by_operation.get(operation, 0) + 1
            return
        self.spent_units += cost.billable
        self.spent_by_operation[operation] = (
            self.spent_by_operation.get(operation, 0) + cost.billable
        )


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
        "units_estimated": report.plan.estimate.total,
        "units_spent": report.spent_units,
        "units_by_operation": dict(report.spent_by_operation),
        "free_requests": dict(report.free_by_operation),
        "estimate_error": round(report.estimate_error, 3),
        "actual_pass_share": round(report.actual_pass_share, 3),
        "units_saved_by_cache": report.plan.savings_from_cache,
        "units_saved_by_gate": report.plan.savings_from_gate,
    }
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
            "from_index": summary.from_index,
            "tokens": summary.tokens,
            # В наблюдении это «сэкономил бы», во включённом — «сэкономил».
            # Число одно, и по режиму рядом видно, какое из двух.
            "units_saved": summary.units_saved,
        }
    if report.review is not None:
        stats["review"] = {"pending": report.review.pending, "carried": report.review.carried}
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
