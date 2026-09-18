"""Прогон целиком: ключи на входе, база доноров и отчёт на выходе.

Порядок шагов продиктован ценой, а не удобством изложения.

    ключи → выдача → нормализация → дедупликация
          → отсев уже проверенных
          → смета и проверка капа
          → трёхступенчатый сбор пачками
          → отчёт со сверкой сметы и факта

Два свойства, ради которых всё это и написано.

**Повторный прогон почти бесплатен.** Домены со свежими данными отсеиваются
до сметы, а не после: иначе сервис пугал бы ценой прогонов, которые на деле
ничего не стоят, и кэш был бы не виден пользователю.

**Кап проверяется до первого платного запроса.** Узнать о превышении на
середине прогона — значит уже потратить половину.

**Остаток берётся у Ahrefs, а не из своей таблицы.** Ключ общий с сервисом
соседней системой, и в чужие базы системы не лезут. Своя таблица расхода знает
только про наши траты — по ней нельзя судить, сколько осталось. Бесплатный
эндпоинт провайдера отдаёт правду по обоим лимитам сразу. Не смогли его
спросить — не тратим.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import (
    Quota,
    RunEstimate,
    UnitsCost,
    UsageCollector,
    estimate_run,
)
from backend.features.core.domain import DonorStatus, RunStatus, Stage
from backend.features.donors.collect import collect
from backend.features.donors.host import normalize_host
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds
from backend.features.runs.repository import RunRepository
from backend.features.serp.protocol import SerpProvider

logger = logging.getLogger(__name__)


class CapExceededError(RuntimeError):
    """Прогон дороже, чем осталось юнитов. Не запускаем."""


class QuotaUnavailableError(RuntimeError):
    """Остаток узнать не удалось. Тратить вслепую нельзя."""


async def units_left(client: AhrefsClient, *, cap: int | None = None) -> int:
    """Сколько юнитов можно потратить: меньшее из остатка провайдера и нашего капа.

    Кап ограничивает нас добровольно, остаток провайдера — жёстко.
    Меньшее из двух и есть бюджет прогона.
    """
    try:
        quota = Quota.from_payload(await client.limits_and_usage())
    except (AhrefsError, OSError) as exc:
        raise QuotaUnavailableError(
            "Не удалось узнать остаток юнитов у Ahrefs. Прогон не запускается: "
            "тратить, не зная остатка, значит рисковать лимитом соседней системы "
            "на том же ключе."
        ) from exc

    logger.info(
        "Остаток Ahrefs: %s (ключ %s из %s, пространство %s из %s)",
        quota.available,
        quota.key_used,
        quota.key_limit,
        quota.workspace_used,
        quota.workspace_limit,
    )
    return min(quota.available, cap) if cap is not None else quota.available


class FreshnessSource(Protocol):
    """Кто знает, по каким доменам данные ещё годны."""

    async def fresh_hosts(self, hosts: Sequence[str]) -> set[str]:
        """Домены с непросроченными метриками — за них уже заплачено."""
        ...


@dataclass(frozen=True, slots=True)
class Candidates:
    """Что дала выдача после нормализации и дедупликации."""

    hosts: list[str]
    keywords: int
    results: int
    empty_keywords: list[str]
    dropped: int
    """Строк выдачи, из которых не удалось получить домен."""

    @property
    def duplicates(self) -> int:
        """Сколько адресов схлопнулось в уже известные домены. Это и есть
        экономия дедупликации: каждый схлопнутый — непотраченные юниты."""
        return self.results - self.dropped - len(self.hosts)


@dataclass(frozen=True, slots=True)
class RunPlan:
    """Смета прогона до его запуска."""

    candidates: Candidates
    fresh: list[str]
    new: list[str]
    estimate: RunEstimate

    @property
    def savings_from_cache(self) -> int:
        """Во что обошёлся бы прогон, если бы срока годности не было."""
        return estimate_run(len(self.candidates.hosts)).total - self.estimate.total


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


async def gather_candidates(
    provider: SerpProvider,
    keywords: Sequence[str],
    country: str,
    *,
    depth_pages: int = 1,
) -> Candidates:
    """Выдача по ключам → уникальные корневые домены.

    Порядок сохраняется: первым идёт домен, встреченный выше в выдаче.
    На отладке это удобнее случайного порядка множества.
    """
    answer = await provider.search(keywords, country, depth_pages=depth_pages)

    seen: dict[str, None] = {}
    results = 0
    dropped = 0
    empty: list[str] = []

    for keyword, rows in answer.items():
        if not rows:
            empty.append(keyword)
        for row in rows:
            results += 1
            host = normalize_host(row.url)
            if not host:
                dropped += 1
                continue
            seen.setdefault(host, None)

    return Candidates(
        hosts=list(seen),
        keywords=len(answer),
        results=results,
        empty_keywords=empty,
        dropped=dropped,
    )


async def plan_run(
    candidates: Candidates,
    freshness: FreshnessSource,
    *,
    units_left: int,
) -> RunPlan:
    """Смета и проверка капа. Бросает `CapExceededError`, если не помещаемся.

    Свежие домены отсеиваются до сметы: платить за них не придётся,
    и показывать их в цене прогона было бы обманом.
    """
    fresh = await freshness.fresh_hosts(candidates.hosts)
    new = [h for h in candidates.hosts if h not in fresh]
    estimate = estimate_run(len(new))

    if estimate.total > units_left:
        raise CapExceededError(
            f"Прогон обойдётся в {estimate.total} юнитов, доступно {units_left}. "
            f"Новых доменов {len(new)} из {len(candidates.hosts)}; "
            f"сократите список ключей или поднимите кап."
        )

    return RunPlan(
        candidates=candidates,
        fresh=sorted(fresh),
        new=new,
        estimate=estimate,
    )


@dataclass(frozen=True, slots=True)
class RunDeps:
    """Исполнители: источник выдачи, клиент провайдера и два репозитория."""

    provider: SerpProvider
    client: AhrefsClient
    donors: DonorRepository
    runs: RunRepository


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Чего хотим от прогона."""

    keywords: Sequence[str]
    country: str
    thresholds: Thresholds
    settings_id: int
    stage: Stage = Stage.DONORS
    cap: int | None = None
    depth_pages: int = 1


async def execute_run(deps: RunDeps, request: RunRequest) -> RunReport:
    """Прогон целиком: от списка ключей до сохранённых доноров и отчёта.

    Про обработку сбоев. Прогон закрывается статусом в любом случае — успех,
    остановка или падение. Запись, навсегда оставшаяся «идёт», выглядит как
    зависший сервис и заставляет разбираться руками в базе.

    Уже сохранённые пачки при падении не откатываются: за них заплачено, и
    повторный прогон должен их пропустить, а не оплатить второй раз.
    """
    candidates = await gather_candidates(
        deps.provider, request.keywords, request.country, depth_pages=request.depth_pages
    )
    budget = await units_left(deps.client, cap=request.cap)
    plan = await plan_run(candidates, deps.donors, units_left=budget)

    run = await deps.runs.create_run(
        stage=request.stage,
        settings_id=request.settings_id,
        keywords=request.keywords,
        country=request.country,
        estimated_units=plan.estimate.total,
    )
    report = RunReport(plan=plan)
    status = RunStatus.DONE
    failure: str | None = None

    # Клиент сообщает о тратах в копилку; прогон сливает её в журнал на каждом
    # чекпоинте. Без этой связки клиент считал бы расход в никуда, а журнал
    # оставался пустым — и разбор «на что ушли юниты» отвечал бы нулём.
    usage = UsageCollector()
    previous_sink, deps.client.on_usage = deps.client.on_usage, usage

    async def flush_usage() -> None:
        for operation, cost in usage.drain():
            report.add_usage(operation, cost)
            await deps.runs.record_usage(run_id=run.id, operation=operation, cost=cost)

    try:
        async for batch in collect(plan.new, deps.client, request.thresholds, request.country):
            await deps.donors.save_results(batch)
            for result in batch:
                report.record(result.status, result.reason)
            await flush_usage()
            # Пачка сохранена — это чекпоинт: повторный прогон её пропустит.
            await deps.runs.session_commit()
    except Exception as exc:
        status = RunStatus.STOPPED
        failure = f"{type(exc).__name__}: {exc}"
        logger.exception("Прогон %s остановлен на середине", run.id)
        raise
    finally:
        # Траты, сделанные до сбоя, записываются обязательно: прогон, упавший
        # на середине, уже потратил, и журнал не должен об этом умолчать.
        await flush_usage()
        deps.client.on_usage = previous_sink
        await deps.runs.session_flush()
        report.spent_units = await deps.runs.spent_units(run.id)
        await deps.runs.finish_run(
            run,
            status=status,
            actual_units=report.spent_units,
            stats=_run_stats(report, failure),
        )
        await deps.runs.session_commit()

    return report


def _run_stats(report: RunReport, failure: str | None) -> dict[str, object]:
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
        "already_fresh": len(report.plan.fresh),
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
    }
    if failure is not None:
        # Причина остановки хранится рядом с цифрами, а не только в логе:
        # через неделю лог уже не найдут, а запись прогона останется.
        stats["failure"] = failure
    return stats
