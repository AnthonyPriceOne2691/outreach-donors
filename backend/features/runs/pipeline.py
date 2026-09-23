"""Прогон целиком: ключи на входе, база доноров и отчёт на выходе.

Порядок шагов продиктован ценой, а не удобством изложения.

    ключи → выдача → нормализация → дедупликация
          → гейт исключений
          → отсев уже проверенных
          → смета и проверка капа
          → трёхступенчатый сбор пачками
          → отчёт со сверкой сметы и факта

Первые пять шагов живут в `planning.py`: до первой траты и решается,
что вообще будет куплено. Здесь — исполнение и отчёт.

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
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx

from backend.config import judge as judge_cfg
from backend.config import llm as llm_cfg
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import (
    UnitsCost,
    UsageCollector,
)
from backend.features.core.domain import DonorStatus, RunStatus, Stage
from backend.features.core.models.run import RunModel
from backend.features.donors.collect import collect
from backend.features.donors.judging import JudgePass, JudgeSummary, judge_candidates
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds
from backend.features.runs.budget import units_left
from backend.features.runs.planning import (
    Candidates,
    ExclusionSource,
    RunPlan,
    gather_candidates,
    plan_run,
)
from backend.features.runs.repository import RunRepository
from backend.features.serp.protocol import SerpProvider
from backend.shared.logs import run_context
from backend.shared.net.url_guard import guarded_client

logger = logging.getLogger(__name__)


#: Как называется расход на выдачу в журнале. Единица — доллар:
#: основной источник берёт деньгами, а не юнитами.
SERP_OPERATION = "serp_search"
#: Токены судьи площадки — строка журнала, как у сборки ключей.
JUDGE_OPERATION = "site_judge"

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

    #: Готовая строка прогона. Её заводит тот, кто ставит задачу
    #: в очередь: между нажатием и первой тратой идут выдача и смета,
    #: и всё это время прогон должен существовать в базе.
    run: RunModel | None = None

    #: Выдача, за которую уже заплачено. Передаётся, когда её собрал
    #: вызывающий (консольная команда показывает смету до подтверждения)
    #: или когда её сохранила прошлая попытка этого же прогона. Без неё
    #: продолжение и подтверждение покупали бы выдачу второй раз.
    candidates: Candidates | None = None


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


async def _judge_candidates(
    deps: RunDeps, run: RunModel, plan: RunPlan, candidates: Candidates
) -> JudgePass | None:
    """Суд до первой траты у Ahrefs. `None` — судья выключен.

    Место вызова выбрано ценой, а не удобством: за домен, который мы всё
    равно отбросим, платить метриками незачем. В наблюдении он не режет,
    но считает сэкономленное — см. `donors/judging.py`.
    """
    # Свежие домены тоже судятся, если вердикта у них нет: иначе у базы,
    # собранной до судьи, его не появится никогда (замер 23.09 — 28 из 43
    # доменов ниши ставок). Платят за это токенами, не юнитами, и экономию
    # такие домены не дают: их метрики уже куплены.
    hosts = [*plan.new, *plan.fresh]
    if judge_cfg.MODE is judge_cfg.JudgeMode.OFF or not hosts:
        return None

    fresh = await deps.donors.fresh_judged(hosts)
    async with (
        httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S) as http,
        guarded_client(timeout=judge_cfg.HOME_TIMEOUT_SEC) as home,
    ):
        outcome = await judge_candidates(
            http,
            hosts,
            candidates.texts,
            already_judged=fresh,
            paid=plan.new,
            home_client=home if judge_cfg.HOME_CHECK else None,
        )

    # Вердикты сохраняются СРАЗУ, до метрик: прогон, упавший на Ahrefs,
    # не должен стоить уже оплаченных токенов. Токены — в журнал рядом.
    await deps.donors.save_judgements(outcome.verdicts)
    if outcome.summary.tokens:
        await deps.runs.record_tokens(
            run_id=run.id, operation=JUDGE_OPERATION, tokens=outcome.summary.tokens
        )
    await deps.runs.session_commit()
    logger.info(
        "Судья площадки (%s): судили %s, из кэша %s, отрезал бы %s (сэкономил бы %s юнитов), "
        "к человеку %s, токенов %s",
        judge_cfg.MODE.value,
        outcome.summary.judged,
        outcome.summary.from_cache,
        outcome.summary.would_cut,
        outcome.summary.units_saved,
        outcome.summary.to_review,
        outcome.summary.tokens,
    )
    return outcome


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

    candidates = await _candidates_for(deps, request, run)
    await _record_search_cost(deps, run, candidates)

    claimed = await deps.runs.claimed_units()
    budget = await units_left(deps.client, cap=request.cap, claimed=claimed)
    plan = await plan_run(
        candidates,
        deps.donors,
        units_left=budget,
        exclusions=deps.exclusions,
        stage=request.stage,
    )
    await deps.runs.set_estimate(run, plan.estimate.total)
    # Дальше каждая запись несёт идентификатор прогона, включая чужие логгеры:
    # прогоны идут параллельно, и без метки их строки не разделить.
    with run_context(run.id):
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
            judged = await _judge_candidates(deps, run, plan, candidates)
            targets = plan.new
            if judged is not None:
                report.judge = judged.summary
                if judge_cfg.MODE is judge_cfg.JudgeMode.ENFORCE:
                    # Режет ТОЛЬКО в этом режиме. В наблюдении список
                    # отрезанных посчитан и записан, но не применён.
                    targets = [host for host in plan.new if host not in judged.rejected]

            async for batch in collect(targets, deps.client, request.thresholds, request.country):
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
            "by_intent": dict(summary.by_intent),
            "by_decider": dict(summary.by_decider),
            "home_unreached": summary.home_unreached,
            "tokens": summary.tokens,
            # В наблюдении это «сэкономил бы», во включённом — «сэкономил».
            # Число одно, и по режиму рядом видно, какое из двух.
            "units_saved": summary.units_saved,
        }
    if failure is not None:
        # Причина остановки хранится рядом с цифрами, а не только в логе:
        # через неделю лог уже не найдут, а запись прогона останется.
        stats["failure"] = failure
    return stats
