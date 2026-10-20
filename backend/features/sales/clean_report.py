"""Итог очистки лидов продаж (`cleaning.clean`): исход лида, числа прохода и слова для консоли.

Своим модулем, а не в `cleaning.py`: очистка стоит у предела длины файла, а итог — отдельная
забота: куда ушёл каждый лид (`Outcome`), что проход сделал (`tally`) и как это сказать консоли
(`lines`) и строке задачи на экране (`clean_jobs.report_of`, слова там строит экран). Каждый
исход — своим числом: «готово», «отклонено» по причинам, «не подтверждено» (`unknown`,
`unconfirmed.py`), «Hunter ещё проверяет» (перепроверка отложена и пойдёт сама,
`verify_again.py`), «не проверено» (отказ сервиса) и «ждут держателя» (компанию держит
непроверенный лид другой гипотезы, `company_hold.split`) — человек должен видеть, куда ушёл
каждый лид.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from backend.features.sales.models import LeadStatus, RejectionReason
from backend.features.sales.unconfirmed import recheck_on
from backend.features.sales.verifier import UNKNOWN, Verdict

#: Причина отказа словами — для сводки и консоли. Экран берёт код. «Не подтверждён» — второй
#: `unknown` или домен, с которым проверка не работает (`hunter_codes`): что именно — в словах лида.
REASON_LABELS = {
    RejectionReason.DUPLICATE: "дубль",
    RejectionReason.STOPLIST: "стоп-лист продаж",
    RejectionReason.UNSUBSCRIBED: "отписка",
    RejectionReason.OTHER_DIRECTION: "домен в работе у другого направления",
    RejectionReason.UNUSABLE: "негодный адрес",
    RejectionReason.NO_MAIL: "домен не принимает почту",
    RejectionReason.UNDELIVERABLE: "адрес не существует",
    RejectionReason.UNCONFIRMED: "адрес не подтверждён",
}
#: Ждущие держателя — словами сводки консоли (экран говорит теми же, `cleanData.cleanLine`).
AWAITING_HOLDER = "ждут: компанию держит непроверенный лид другой гипотезы"
#: Начало слов лида, чья проверка не выполнена: отказ сервиса, остановка или «ещё проверяет».
NOT_VERIFIED = "проверка не выполнена"


@dataclass(frozen=True, slots=True)
class Outcome:
    """Итог очистки одного лида: куда он ушёл и почему."""

    lead_id: int
    status: LeadStatus
    reason: RejectionReason | None = None
    note: str | None = None
    verdict: Verdict | None = None
    verifier: str = ""
    #: Проверка не выполнена у лида с вердиктом: прежний вердикт остаётся (`_unverified`).
    kept: bool = False
    #: Ждёт держателя компании — непроверенного лида другой гипотезы (`company_hold.split`).
    waits: bool = False
    #: Hunter «ещё проверяет»: когда перепроверка пойдёт сама и с какого момента идёт окно
    #: перепроверок (`verify_again.later`); пусто — отложенной перепроверки у лида нет.
    again_at: datetime | None = None
    since: datetime | None = None

    @property
    def unknown(self) -> bool:
        """Проверяльщик ответил `unknown` — адрес не подтверждён (`unconfirmed.py`)."""
        return self.verdict is not None and self.verdict.status == UNKNOWN

    @property
    def passed(self) -> bool:
        """«Готов» по этой очистке, а не прежним вердиктом (`kept`)."""
        return self.status is LeadStatus.READY and not self.kept

    @property
    def decided(self) -> bool:
        """Лид решён: отклонён или «готов» с новым вердиктом. Первый `unknown`, «проверка не
        выполнена» и ожидание — не решение: ждущих на его домене проход не будит."""
        fresh = self.verdict is not None and not self.unknown
        return self.status is LeadStatus.REJECTED or (self.passed and fresh)


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
    #: Hunter «ещё проверяет» и после переспросов: статус прежний, перепроверка пойдёт сама —
    #: первая не раньше `again_at` (`verify_again.py`).
    still_checking: int = 0
    again_at: datetime | None = None
    #: Ждут держателя компании — непроверенного лида другой гипотезы: проверку им никто не
    #: запускал, статус и вердикт прежние; разбудит их очистка, которая решит держателя.
    awaiting_holder: int = 0
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


def tally(report: CleaningReport, outcomes: Iterable[Outcome], moment: datetime) -> None:
    """Исходы — в числа сводки: каждый лид — одним исходом (последним за проход)."""
    for outcome in outcomes:
        _count(report, outcome, moment)
        if outcome.verdict is not None:
            report.verified += 1
            report.paid_units += outcome.verdict.units


def _count(report: CleaningReport, outcome: Outcome, moment: datetime) -> None:
    """Куда ушёл лид — одним числом сводки."""
    if outcome.reason is not None:
        report.rejected[outcome.reason.value] += 1
    elif outcome.waits:
        report.awaiting_holder += 1
    elif outcome.unknown:
        report.unconfirmed += 1
        report.recheck_on = recheck_on(moment)
    elif outcome.again_at is not None:
        report.still_checking += 1
        report.again_at = min(report.again_at or outcome.again_at, outcome.again_at)
    elif outcome.passed:
        report.ready += 1
    else:
        report.unverified += 1


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
    shown = (
        (
            report.still_checking,
            f"  перепроверка по сроку (Hunter ещё проверяет или не ответил): "
            f"{report.still_checking} — пойдёт сама",
        ),
        (
            report.awaiting_holder,
            f"  {AWAITING_HOLDER} — {report.awaiting_holder}: Hunter за них не платился, их решит "
            "очистка гипотезы держателя — она названа в словах лида",
        ),
        (
            report.mx_unknown,
            f"  DNS не ответил по доменам: {report.mx_unknown} — адреса прошли "
            "дальше непроверенными",
        ),
        (
            report.verified,
            f"  вердиктов проверяльщика: {report.verified}, платных: {report.paid_units}",
        ),
    )
    return said + [line for count, line in shown if count] + _waiting(report)


def _waiting(report: CleaningReport) -> list[str]:
    """Кто ждёт и почему: адрес не подтверждён — дня перепроверки, не проверен — следующей
    очистки или снятой причины остановки."""
    said = []
    if report.still_checking and report.again_at is not None:
        said.append(
            "\nHunter ещё проверяет адрес или не ответил — к письмам такие лиды пока не идут: "
            f"перепроверка пойдёт сама около {report.again_at:%d.%m %H:%M} UTC."
        )
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
