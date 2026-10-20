"""Прогон целиком: ключи на входе, база доноров и отчёт на выходе.

Порядок шагов продиктован ценой, а не удобством изложения.

    ключи → выдача → нормализация → дедупликация
          → гейт исключений
          → отсев уже проверенных
          → смета и проверка капа
          → вердикт свежих по сохранённым метрикам — страной и порогами прогона
          → трёхступенчатый сбор пачками
          → отчёт со сверкой сметы и факта

Первые пять шагов живут в `planning.py`: до первой траты и решается,
что вообще будет куплено. Здесь — исполнение; суд до первой траты —
`judge_stage.py`, итог и его запись в прогон — `report.py`, вердикт прогона
по каждому домену — `verdicts.py`, очередь человеку — `handover.py`.

Два свойства, ради которых всё это и написано.

**Повторный прогон почти бесплатен.** Домены со свежими данными отсеиваются
до сметы, а не после: иначе сервис пугал бы ценой прогонов, которые на деле
ничего не стоят, и кэш был бы не виден пользователю.

**Остаток берётся у Ahrefs, а не из своей таблицы.** Ключ общий с сервисом
соседней системой, и в чужие базы системы не лезут. Своя таблица расхода знает
только про наши траты — по ней нельзя судить, сколько осталось. Бесплатный
эндпоинт провайдера отдаёт правду по обоим лимитам сразу. Не смогли его
спросить — не тратим.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field

from backend.config import filters as filters_cfg
from backend.config import judge as judge_cfg
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import (
    UsageCollector,
)
from backend.features.core.domain import RunStatus, Stage
from backend.features.core.models.run import RunModel
from backend.features.crawl.niche import NicheReport
from backend.features.donors.collect import DomainResult, collect
from backend.features.donors.doors import DoorCheck
from backend.features.donors.geo import GeoRule
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds
from backend.features.review.candidates import RunReview
from backend.features.runs.budget import budget_of
from backend.features.runs.competitor_run import country_share_of, release_hold
from backend.features.runs.failures import is_permanent
from backend.features.runs.handover import hand_over
from backend.features.runs.judge_stage import SERP_OPERATION, judge_stage
from backend.features.runs.planning import (
    Candidates,
    ExclusionSource,
    gather_candidates,
    plan_run,
)
from backend.features.runs.report import RunReport, run_stats
from backend.features.runs.repository import REASON_KEY, RunRepository
from backend.features.runs.stopped import tell_stopped
from backend.features.runs.verdicts import Judging, measured
from backend.features.serp.funds import ensure_funds
from backend.features.serp.protocol import SerpProvider
from backend.shared.logs import run_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunDeps:
    """Исполнители: источник выдачи, клиент провайдера и два репозитория.

    Гейт исключений идёт отдельным полем и без умолчания: прогон,
    собранный без него, платил бы за домены, которым не напишет,
    и узнать об этом можно было бы только по счёту.
    """

    provider: SerpProvider
    client: AhrefsClient
    donors: DonorRepository
    runs: RunRepository
    exclusions: ExclusionSource
    #: Очередь на рассмотрение. Пусто — прогон по-старому кладёт годных
    #: сразу в базу; оба боевых вызова её передают, умолчание — уступка
    #: тестам ядра прогона, которым очередь не нужна.
    review: RunReview | None = None
    #: Меню главных у очереди. Пусто — не смотреть: тесты ядра не ходят
    #: в сеть, а боевой вызов передаёт проверку с клиентом для чужих сайтов.
    doors: DoorCheck | None = None
    #: Бизнесы ниши из выдачи — в кандидаты в рекламодатели (`crawl/niche.py`).
    #: Пусто — не собирать: тесты ядра прогона рекламодателей не ждут.
    niche: Callable[[int], Awaitable[NicheReport]] | None = None


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

    #: Правило региона и срок годности метрик — той же версии настроек, что
    #: пороги (`runs.thresholds.rules_of`), а не окружения: до 11.10.2026 их брали
    #: из конфига, и поле версии только записывалось. Оба боевых вызова передают
    #: их из версии; умолчания конфига — уступка тестам ядра прогона.
    geo: GeoRule = field(default_factory=GeoRule)
    metrics_ttl_days: int = filters_cfg.METRICS_TTL_DAYS

    #: Готовая строка прогона. Её заводит тот, кто ставит задачу
    #: в очередь: между нажатием и первой тратой идут выдача и смета,
    #: и всё это время прогон должен существовать в базе.
    run: RunModel | None = None

    #: Выдача, за которую уже заплачено. Передаётся, когда её собрал
    #: вызывающий (консольная команда показывает смету до подтверждения)
    #: или когда её сохранила прошлая попытка этого же прогона. Без неё
    #: продолжение и подтверждение покупали бы выдачу второй раз.
    candidates: Candidates | None = None
    #: Юниты Ahrefs, уже потраченные на кандидатов, — списки конкурентов (`competitor_run`):
    #: смета прогона их включает, иначе удержание и «смета против факта» их бы не видели.
    spent_before: int = 0


async def _candidates_for(deps: RunDeps, request: RunRequest, run: RunModel) -> Candidates:
    """Выдача прогона: переданная, сохранённая или купленная.

    Порядок именно такой, потому что выдача стоит денег. Переданную
    собрал вызывающий — консольная команда показывает по ней смету
    и спрашивает подтверждения; купить её второй раз значило бы платить
    за подтверждение. Сохранённая осталась от прошлой попытки того же
    прогона, которую убил умерший воркер.

    Купленная фиксируется сразу отдельной транзакцией — иначе следующая
    смерть воркера снова оставит прогон без неё.
    """
    if request.candidates is not None:
        candidates = request.candidates
    elif run.candidates:
        candidates = Candidates.restored(run.candidates)
        logger.info(
            "Прогон %s продолжается по сохранённой выдаче: %s доменов, выдача не покупается заново",
            run.id,
            len(candidates.hosts),
        )
        return candidates
    else:
        # Счёт выдачи — до покупки: на пустом прогон встал бы посреди работы (16.10.2026).
        await ensure_funds(deps.provider, len(request.keywords), request.depth_pages, run.serp_mode)
        candidates = await gather_candidates(
            deps.provider, request.keywords, request.country, depth_pages=request.depth_pages
        )

    await deps.runs.save_candidates(run, candidates.as_dict())
    await deps.runs.session_commit()
    return candidates


async def _record_search_cost(deps: RunDeps, run: RunModel, candidates: Candidates) -> None:
    """Строка расхода на выдачу — до того, как прогон пойдёт дальше.

    Упавший на метриках прогон эти деньги всё равно потратил, и журнал
    без строки показывал бы, что выдача досталась даром. Нулевая цена
    означает восстановленную выдачу: за неё уже заплачено и записано.
    """
    if not candidates.cost_usd:
        return
    await deps.runs.record_money(
        run_id=run.id, operation=SERP_OPERATION, amount_usd=candidates.cost_usd
    )
    await deps.runs.session_commit()


async def _judge_known(
    deps: RunDeps, run: RunModel, request: RunRequest, report: RunReport
) -> None:
    """Вердикт прогона без Ahrefs: исключённые гейтом — с его причиной, свежие — страной и
    порогами прогона по метрикам и странам, что лежат у домена (`runs/verdicts.py`). До
    20.10.2026 свежему шёл вердикт последнего замера, чьей бы страны он ни был, а
    исключённые оставались одним числом. Купленное этим же прогоном до сбоя продолжение
    застаёт свежим — оно остаётся вердиктом замера. Пишет очередь прогона: вердикт нужен
    ей и её резерву."""
    if deps.review is None:
        return
    await deps.review.verdicts.save_excluded(run, report.plan.excluded)
    judging = Judging(request.country, request.thresholds, request.geo)
    fresh = await deps.review.verdicts.fresh(run, report.plan.fresh, judging)
    for verdict in fresh.values():
        report.record_fresh(verdict.status, verdict.reason)
    await deps.runs.session_commit()


async def _save_batch(
    deps: RunDeps,
    run: RunModel,
    batch: Sequence[DomainResult],
    report: RunReport,
    *,
    again: Collection[str],
) -> None:
    """Пачка замера — донорам, вердиктом — прогону (`run_verdicts`) и в отчёт. `again` —
    кому добирали страны: метрики куплены раньше (`collect.PaidMetrics`)."""
    await deps.donors.save_results(batch)
    if deps.review is not None:
        await deps.review.verdicts.save(run, measured(batch))
    for result in batch:
        report.record(result.status, result.reason, again=result.host in again)


async def execute_run(deps: RunDeps, request: RunRequest) -> RunReport:
    """Прогон целиком: от списка ключей до сохранённых доноров и отчёта.

    Про обработку сбоев. Прогон закрывается статусом в любом случае — успех,
    остановка или падение. Запись, навсегда оставшаяся «идёт», выглядит как
    зависший сервис и заставляет разбираться руками в базе.

    Уже сохранённые пачки при падении не откатываются: за них заплачено, и
    повторный прогон должен их пропустить, а не оплатить второй раз.
    """
    run = request.run
    if run is None:
        # Строки нет — прогон запустили не из очереди. Заводим сразу
        # «идёт»: ждать нечего, задачу уже выполняют.
        run = await deps.runs.create_run(
            stage=request.stage,
            settings_id=request.settings_id,
            keywords=request.keywords,
            country=request.country,
            status=RunStatus.RUNNING,
        )
    else:
        await deps.runs.mark_running(run)
        # Сразу в базу. До фиксации «идёт» видно только этой транзакции, а она держит
        # строку прогона всю выдачу: пульс из своей сессии ждал её блокировку, и экран
        # минутами показывал прогон «в очереди» с замершим пульсом (разбор 14.10.2026).
        await deps.runs.session_commit()

    candidates = await _candidates_for(deps, request, run)
    await _record_search_cost(deps, run, candidates)

    # Своё обещание — не чужое удержание: продолжение после смерти воркера иначе вычитало
    # бы из остатка свою же неистраченную смету (аудит 10.10.2026).
    claimed = await deps.runs.claimed_units(exclude_run_id=run.id)
    # Ответ Ahrefs — ещё и снимок ключа: от него кап и цикл у экранов и сторожа.
    budget = await budget_of(deps.client, cap=request.cap, claimed=claimed, keep=deps.runs.keep_key)
    plan = await plan_run(
        candidates,
        deps.donors,
        units_left=budget.units,
        limit=budget.shortage,
        exclusions=deps.exclusions,
        stage=request.stage,
        # У прогона по конкурентам доля своя, как у его сметы до запуска (`competitor_run`).
        country_share=await country_share_of(deps.runs, run, request.country),
        metrics_ttl_days=request.metrics_ttl_days,
    )
    # Удержание на время списков конкурентов сменяет точная смета — той же фиксацией.
    release_hold(run)
    await deps.runs.set_estimate(run, request.spent_before + plan.estimate.total)
    # То же для сметы: дальше суд — до десятков минут, и строку прогона он держать не должен.
    await deps.runs.session_commit()
    # Дальше каждая запись несёт идентификатор прогона, включая чужие логгеры:
    # прогоны идут параллельно, и без метки их строки не разделить.
    with run_context(run.id):
        report = RunReport(plan=plan)
        status = RunStatus.DONE
        failed: BaseException | None = None

        # Клиент сообщает о тратах в копилку; прогон сливает её в журнал на каждом
        # чекпоинте. Без этой связки клиент считал бы расход в никуда, а журнал
        # оставался пустым — и разбор «на что ушли юниты» отвечал бы нулём.
        usage = UsageCollector()
        previous_sink, deps.client.on_usage = deps.client.on_usage, usage

        async def flush_usage() -> None:
            for operation, cost in usage.drain():
                report.add_usage(operation, cost)
                await deps.runs.record_usage(
                    run_id=run.id, operation=operation, cost=cost, stage=run.stage
                )

        try:
            await _judge_known(deps, run, request, report)
            judged = await judge_stage(deps, run, plan, candidates, report)
            targets = plan.new
            if judged is not None:
                report.judge = judged.summary
                if judge_cfg.MODE is judge_cfg.JudgeMode.ENFORCE:
                    # Режет ТОЛЬКО в этом режиме. В наблюдении список
                    # отрезанных посчитан и записан, но не применён.
                    targets = [host for host in plan.new if host not in judged.rejected]

            # Метрики куплены, а стран нет — прогон добирает только страны.
            paid = await deps.donors.paid_metrics(targets, ttl_days=request.metrics_ttl_days)
            async for batch in collect(
                targets,
                deps.client,
                request.thresholds,
                request.country,
                paid=paid,
                geo=request.geo,
            ):
                await _save_batch(deps, run, batch, report, again=paid.keys())
                await flush_usage()
                # Пачка сохранена — это чекпоинт: повторный прогон её пропустит.
                await deps.runs.session_commit()

            await hand_over(deps, run, [*targets, *plan.fresh], report, candidates.texts)
        except Exception as exc:
            failed = exc
            status = _status_after(exc, run.id)
            raise
        finally:
            # Траты, сделанные до сбоя, записываются обязательно: прогон, упавший
            # на середине, уже потратил, и журнал не должен об этом умолчать.
            await flush_usage()
            deps.client.on_usage = previous_sink
            await _close(deps, run, report, status=status, failed=failed)

        return report


async def _close(
    deps: RunDeps,
    run: RunModel,
    report: RunReport,
    *,
    status: RunStatus,
    failed: BaseException | None,
) -> None:
    """Записать итог прогона — любой: успех, остановку или сбой, который продолжат.

    Остановка, которую записала именно эта попытка, — тревога человеку
    (`runs/stopped.py`), и только после фиксации: сообщить об остановке,
    которая откатится вместе с транзакцией, значит соврать.
    """
    await deps.runs.session_flush()
    report.spent_units = await deps.runs.spent_units(run.id)
    stats = run_stats(report, failed, retry=status is RunStatus.RUNNING)
    closing = status is RunStatus.STOPPED and run.status is not RunStatus.STOPPED
    await deps.runs.finish_run(run, status=status, actual_units=report.spent_units, stats=stats)
    await deps.runs.session_commit()
    if closing:
        await tell_stopped(run.id, str(stats.get(REASON_KEY) or "остановлен"))


def _status_after(exc: BaseException, run_id: int) -> RunStatus:
    """Статус прогона, прерванного сбоем посередине.

    Временный сбой оставляет «идёт»: разбор мёртвых продолжит прогон
    с последней сохранённой пачки. Остановить здесь значило бы похоронить
    прогон из-за сетевой минуты. Остановка — только когда повтор не поможет.
    """
    if is_permanent(exc):
        logger.exception("Прогон %s остановлен на середине: повтор не поможет", run_id)
        return RunStatus.STOPPED
    logger.exception("Прогон %s прерван сбоем, будет продолжен", run_id)
    return RunStatus.RUNNING
