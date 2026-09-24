"""Прогон целиком: ключи на входе, база доноров и отчёт на выходе.

Порядок шагов продиктован ценой, а не удобством изложения.

    ключи → выдача → нормализация → дедупликация
          → гейт исключений
          → отсев уже проверенных
          → смета и проверка капа
          → трёхступенчатый сбор пачками
          → отчёт со сверкой сметы и факта

Первые пять шагов живут в `planning.py`: до первой траты и решается,
что вообще будет куплено. Здесь — исполнение; итог и его запись
в прогон — `report.py`.

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import httpx

from backend.config import judge as judge_cfg
from backend.config import llm as llm_cfg
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import (
    UsageCollector,
)
from backend.features.core.domain import RunStatus, Stage
from backend.features.core.models.run import RunModel
from backend.features.donors.collect import collect
from backend.features.donors.doors import DoorCheck
from backend.features.donors.judging import JudgePass, judge_candidates
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds
from backend.features.review.candidates import RunReview
from backend.features.runs.budget import units_left
from backend.features.runs.failures import described, is_permanent
from backend.features.runs.planning import (
    Candidates,
    ExclusionSource,
    RunPlan,
    SerpText,
    gather_candidates,
    plan_run,
)
from backend.features.runs.report import RunReport, run_stats
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


async def _check_doors(
    deps: RunDeps, run: RunModel, report: RunReport, pages: Mapping[str, SerpText]
) -> None:
    """Двери очереди — страница выдачи и меню главных: кто сам продаёт
    размещение — наверх.

    Сбой здесь прогон не роняет: платное уже сделано и лежит в очереди,
    а без двери очередь просто не поднимет продающих. Причина — в запись
    прогона, а не только в лог.
    """
    if deps.doors is None or deps.review is None:
        return
    try:
        report.doors = await deps.doors(
            deps.donors,
            await deps.review.pending_hosts(run.id),
            pages=pages,
            checkpoint=deps.runs.session_commit,
        )
    except Exception as exc:
        logger.exception("Прогон %s: меню главных не посмотрели", run.id)
        report.doors_failure = described(exc)[:300]


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
            # Закрытую главную смотрим глазами индекса: `site:` у того же
            # источника выдачи, что и сам прогон.
            index=deps.provider if judge_cfg.HOME_CHECK else None,
        )

    # Вердикты сохраняются СРАЗУ, до метрик: прогон, упавший на Ahrefs,
    # не должен стоить уже оплаченных токенов. Токены — в журнал рядом.
    await deps.donors.save_judgements(outcome.verdicts)
    if outcome.summary.tokens:
        await deps.runs.record_tokens(
            run_id=run.id, operation=JUDGE_OPERATION, tokens=outcome.summary.tokens
        )
    if outcome.summary.index_usd:
        await deps.runs.record_money(
            run_id=run.id, operation=SERP_OPERATION, amount_usd=outcome.summary.index_usd
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
    if outcome.summary.unanswered:
        # Громко: домены ушли человеку без совета, а вердикт у них будет
        # только при следующем суде. Причина — что чинить.
        logger.error(
            "Прогон %s: модель не ответила судье по %s доменам — %s",
            run.id,
            outcome.summary.unanswered,
            outcome.summary.unanswered_reason,
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
        country_share=await deps.runs.country_call_share(request.country),
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

            if deps.review is not None:
                # Свежие домены тоже: прогон их нашёл, и человек должен их
                # увидеть — просто платить за них не пришлось.
                report.review = await deps.review.queue_run(run.id, [*plan.new, *plan.fresh])
                await deps.runs.session_commit()
                await _check_doors(deps, run, report, candidates.texts)
        except Exception as exc:
            failure = described(exc)
            status = _status_after(exc, run.id)
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
                stats=run_stats(report, failure, retry=status is RunStatus.RUNNING),
            )
            await deps.runs.session_commit()

        return report


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
