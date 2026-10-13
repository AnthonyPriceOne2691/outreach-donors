"""Итог очистки лидов продаж (`cleaning.clean`): числа прохода и слова для консоли.

Своим модулем, а не в `cleaning.py`: очистка стоит у предела длины файла, а итог — отдельная
забота: что проход сделал и как это сказать консоли (`lines`) и строке задачи на экране
(`clean_jobs.report_of`, слова там строит экран). Каждый исход — своим числом: «готово»,
«отклонено» по причинам, «не подтверждено» (`unknown`, `unconfirmed.py`) и «не проверено»
(отказ сервиса) — человек должен видеть, куда ушёл каждый лид.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from backend.features.sales.models import RejectionReason

#: Причина отказа словами — для сводки и консоли. Экран берёт код.
REASON_LABELS = {
    RejectionReason.DUPLICATE: "дубль",
    RejectionReason.STOPLIST: "стоп-лист продаж",
    RejectionReason.UNSUBSCRIBED: "отписка",
    RejectionReason.OTHER_DIRECTION: "домен в работе у другого направления",
    RejectionReason.UNUSABLE: "негодный адрес",
    RejectionReason.NO_MAIL: "домен не принимает почту",
    RejectionReason.UNDELIVERABLE: "адрес не существует",
    RejectionReason.UNCONFIRMED: "адрес не подтверждён дважды",
}


@dataclass
class CleaningReport:
    """Сводка прохода: каждый исход — отдельным числом, отказы — по причинам."""

    checked: int = 0
    ready: int = 0
    #: Код причины → сколько лидов.
    rejected: Counter[str] = field(default_factory=Counter)
    #: Адрес не подтверждён (`unknown`) в первый раз: к письмам не идут, статус прежний,
    #: очистка проверит их снова не раньше `recheck_on`.
    unconfirmed: int = 0
    recheck_on: date | None = None
    #: Проверка не выполнена: остались в прежнем статусе (`cleaning._unverified`).
    unverified: int = 0
    #: Доменов, по которым DNS не ответил: они прошли дальше непроверенными.
    mx_unknown: int = 0
    #: Вердиктов получено и сколько из них платных единиц.
    verified: int = 0
    paid_units: int = 0
    verifier: str = ""
    #: Платная часть остановлена: почему. Непроверенные лиды — в прежнем статусе.
    stopped: str | None = None

    @property
    def rejected_total(self) -> int:
        return sum(self.rejected.values())


def lines(report: CleaningReport) -> list[str]:
    """Сводка прохода для консоли: каждый исход — числом, отказы — по причинам словами."""
    if not report.checked:
        return ["Очистка продаж: лидов, ждущих очистки, нет — проверять нечего."]
    said = [
        f"Очистка продаж: проверено {report.checked}; готово {report.ready}; "
        f"отклонено {report.rejected_total}; не подтверждено {report.unconfirmed}; "
        f"не проверено {report.unverified}."
    ]
    said += [
        f"  {REASON_LABELS[reason]}: {count}"
        for reason in RejectionReason
        if (count := report.rejected.get(reason.value))
    ]
    if report.mx_unknown:
        said.append(
            f"  DNS не ответил по доменам: {report.mx_unknown} — адреса прошли дальше непроверенными"
        )
    if report.verified:
        said.append(f"  вердиктов проверяльщика: {report.verified}, платных: {report.paid_units}")
    return said + _waiting(report)


def _waiting(report: CleaningReport) -> list[str]:
    """Кто ждёт и почему: адрес не подтверждён — дня перепроверки, не проверен — следующей
    очистки или снятой причины остановки."""
    said = []
    if report.unconfirmed and report.recheck_on is not None:
        said.append(
            "\nАдрес не подтверждён (unknown) — к письмам такие лиды не идут: очистка проверит "
            f"их снова не раньше {report.recheck_on:%d.%m.%Y}, второй такой ответ отклонит лида."
        )
    if report.stopped:
        said.append(
            f"\nПлатная часть остановлена: {report.stopped}. Непроверенные лиды остались "
            "в прежнем статусе — повторите очистку, когда причина снята."
        )
    elif report.unverified:
        said.append(
            "\nНепроверенные лиды остались в прежнем статусе: следующая очистка повторит их."
        )
    return said
