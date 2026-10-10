"""Задачи, которые выполняет воркер.

Задача — это тонкая обёртка над тем же ядром, что зовёт консольная
команда. Своей логики здесь нет намеренно: правило, появившееся в задаче,
не проверяется ни тестами ядра, ни тестами веба — оно живёт в третьем
месте, про которое вспоминают последним.

Асинхронный код запускается своим циклом событий: очередь синхронная,
и соединения базы обязаны создаваться в том же цикле, в котором
работают, — иначе первый же запрос падает на «attached to a different
loop».
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any, assert_never

import httpx
from rq import get_current_job
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_collect, check_storage
from backend.features.ahrefs.client import AhrefsClient
from backend.features.contacts.search import search_contacts
from backend.features.core.domain import Stage
from backend.features.core.usage import LlmCapExceededError
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.crawl.niche import collect as collect_niche
from backend.features.donors.doors import door_check
from backend.features.donors.repository import DonorRepository
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.rewrite import RewriteClient
from backend.features.replies import lead_handoff
from backend.features.replies.extract import ExtractClient
from backend.features.replies.pipeline import Parser
from backend.features.review.candidates import RunReview
from backend.features.runs.budget import ceiling_at_start
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.failures import described, is_permanent
from backend.features.runs.lifecycle import heartbeat
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.reasons import explained
from backend.features.runs.repository import FAILURE_KEY, REASON_KEY, RunRepository
from backend.features.runs.stopped import tell_stopped
from backend.features.runs.thresholds import thresholds_of
from backend.features.serp.factory import build_provider
from backend.shared.logs import setup_logging
from backend.shared.queue import (
    PARSE_JOB,
    parse_job_id,
    remember_job_error,
    runs_queue,
    with_retries,
)
from backend.workers.agent_jobs import after_parse

logger = logging.getLogger(__name__)


def _settled(work: Callable[[], dict[str, Any]], *, what: str) -> dict[str, Any]:
    """Постоянный отказ — итог задачи, а не падение.

    Очередь повторяет упавшую задачу (`queue.RETRY_INTERVALS`), и это верно
    для сети и 5xx. Ошибку в шаблоне, неверный ключ или настройку повтор
    не исправит: трижды прийти к тому же ответу значит трижды его спрятать.
    Такой исход возвращается с причиной, и экран показывает «не выполнена».
    """
    try:
        return work()
    except Exception as exc:
        if not is_permanent(exc):
            _remember_error(exc, what=what)
            raise
        return _given_up(exc, what=what)


def _given_up(exc: BaseException, *, what: str) -> dict[str, Any]:
    """Итог задачи, которую повтор не исправит: причина — в журнал и на экран."""
    logger.warning("%s не выполнена: %s", what, described(exc))
    return {"error": described(exc), "permanent": True}


def _remember_error(exc: BaseException, *, what: str) -> None:
    """Причина — рядом с задачей, до того как очередь отложит её на повтор.

    rq не хранит исключение попытки, ушедшей на повтор (проверено на 2.12:
    ни итога, ни `exc_info`, а мету перезаписывает своей копией задачи),
    и экран показал бы «ждёт повтора» без причины.
    """
    job = get_current_job()
    if job is None:
        logger.info("%s: не в очереди — причину сбоя запоминать негде", what)
        return
    remember_job_error(job.id, described(exc))


async def _run(run_id: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    client = AhrefsClient()
    provider = build_provider(client)
    try:
        async with factory() as session:
            runs = RunRepository(session)
            run = await runs.get(run_id)
            settings = await runs.settings_of(run)
            # Потолок — на старте, а не с нажатия: остаток месяца к этой минуте мог уйти
            # прогонам, поставленным раньше (аудит 10.10.2026, `budget.ceiling_at_start`).
            cap = await ceiling_at_start(session, run_id=run.id, promised=settings.units_cap)
            # Удары о жизни идут своей короткой сессией: длинная в это
            # время занята пачкой доменов, и ждать её значит молчать
            # ровно тогда, когда прогон работает.
            async with factory() as ticker, door_check() as doors:
                beat = asyncio.create_task(heartbeat(RunRepository(ticker), run_id))
                try:
                    report = await execute_run(
                        RunDeps(
                            provider=provider,
                            client=client,
                            donors=DonorRepository(session),
                            runs=runs,
                            exclusions=Exclusions(session),
                            review=RunReview(session),
                            doors=doors,
                            niche=lambda run_id: collect_niche(session, run_id),
                        ),
                        RunRequest(
                            keywords=list(run.keywords),
                            country=run.country,
                            # Пороги — своей строки настроек, а не умолчания конфига
                            # (аудит 10.10.2026): вердикт объясним по `settings_id`.
                            thresholds=thresholds_of(settings),
                            settings_id=run.settings_id,
                            cap=cap,
                            depth_pages=run.depth_pages,
                            run=run,
                        ),
                    )
                finally:
                    beat.cancel()
            await session.commit()
            return {
                "run": run_id,
                "checked": len(report.plan.new),
                "by_status": {status.value: n for status, n in report.by_status.items()},
                "spent_units": report.spent_units,
                "estimated_units": report.plan.estimate.total,
            }
    finally:
        await client.aclose()
        await engine.dispose()


def run_donor_search(run_id: int) -> dict[str, Any]:
    """Прогон от ключей до сохранённых доноров. Возвращает короткий итог:
    подробности всё равно лежат в базе, а в очереди им не место.

    Довод один — номер прогона. Ключи, страна, глубина и кап лежат в его
    строке: продолжение после смерти воркера ставит ту же задачу, и
    разъехаться её доводам не с чем.

    Проверки конфига здесь те же, что у консольной команды, и это не
    дублирование: задача из очереди идёт мимо неё, а прогон тратит
    деньги. Без этой строки первая же проверка проводки в докере ушла
    в живой Ahrefs и стоила 670 юнитов.
    """
    setup_logging()
    check_storage()
    return asyncio.run(_search(run_id))


async def _search(run_id: int) -> dict[str, Any]:
    """Прогон и его исход — в самом прогоне, а не только в журнале воркера.

    Экран читает причину из записи прогона. Без этой обёртки упавшая задача
    оставляла прогон «в очереди» или «идёт», и правду о нём узнавал только
    разбор мёртвых через три минуты — и то гадая.

    Причина пишется словами человека (`runs/reasons.py`): имя класса
    исключения нужно журналу, а не экрану, и лежит рядом отдельно.
    """
    try:
        check_collect()
        return await _run(run_id)
    except Exception as exc:
        if is_permanent(exc):
            # Повтор не поможет (`runs/failures.py`): закрыть сразу, с причиной.
            logger.warning("Прогон %s остановлен: %s", run_id, described(exc))
            await _mark(run_id, f"остановлен: {explained(exc)}", exc, stop=True)
            return {"run": run_id, "refused": described(exc)}
        # Не глушим: очередь должна увидеть падение, а разбор — продолжить
        # прогон с последней точки. Но причина в прогоне — уже сейчас.
        await _mark(run_id, f"сбой, будет продолжен: {explained(exc)}", exc, stop=False)
        raise


async def _mark(run_id: int, reason: str, failed: BaseException, *, stop: bool) -> None:
    """Записать причину в прогон. Своя сессия: основная могла сломаться
    вместе с задачей. Сбой записи не глушит исходную ошибку — только
    громко логируется.

    Остановка — ещё и тревога человеку, но только если прогон закрыла
    эта запись: отказ посреди сбора прогон уже закрыл сам и сам же о нём
    сказал (`runs/stopped.py`). И только после фиксации: не записалось —
    прогон остался открытым, и о нём скажет разбор мёртвых."""
    engine = create_async_engine(storage.DSN)
    closed = False
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            runs = RunRepository(session)
            run = await runs.get(run_id)
            stats = {
                **(run.stats or {}),
                REASON_KEY: reason[:500],
                FAILURE_KEY: described(failed)[:500],
            }
            if stop:
                closed = await runs.stop_run(run, stats=stats)
            else:
                await runs.save_stats(run, stats)
            await session.commit()
    except Exception:
        logger.exception("Прогон %s: причину «%s» записать не удалось", run_id, reason[:120])
    else:
        if closed:
            await tell_stopped(run_id, reason[:500])
    finally:
        await engine.dispose()


async def _build_letters(request: BuildRequest) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rewriter = RewriteClient()
    try:
        async with factory() as session:
            report = await QueueBuilder(session, rewriter).build(request)
            await session.commit()
            return {
                "prepared": report.prepared,
                "tokens": report.tokens_spent,
                "off_corridor": report.off_corridor,
                "funnel": report.funnel,
                "blocked_by": report.blocked_by,
                "notes": report.notes,
                "bad_addresses": report.bad_addresses,
            }
    finally:
        await rewriter.aclose()
        await engine.dispose()


def build_letter_queue(campaign: str, country: str = "us", **options: Any) -> dict[str, Any]:
    """Собрать очередь писем. Ничего не отправляет.

    В очередь задач вынесено потому же, почему и прогон: каждое письмо
    стоит вызова модели, полсотни писем идут минутами, и выполнять это
    внутри запроса значит потерять работу, если человек закрыл вкладку.

    Проверки конфига здесь свои — задача из очереди идёт мимо тех, что
    стоят на маршруте.

    Этап приходит строкой, а не перечислением: задача живёт в очереди
    дольше версии кода, и строка переживает выкатку, а снимок чужого
    класса — не обязательно. Задача, поставленная до этапов, — Этап 1;
    до аудиторий — рассылка по найденным ссылкам.

    Остальное — ключами с умолчаниями (`niche`, `limit`, `followup_days`,
    `letter_template`, `run_ids`, `stage`, `audience`): задача старше кода
    передаёт их так же, и новый ключ её не ломает.
    """
    setup_logging()
    check_storage()
    return _settled(
        lambda: asyncio.run(
            _build_letters(
                BuildRequest(
                    campaign_name=campaign,
                    stage=Stage(options.get("stage", Stage.DONORS.value)),
                    country=country,
                    niche=tuple(options.get("niche", ())),
                    limit=int(options.get("limit", 50)),
                    followup_days=tuple(options.get("followup_days", ())),
                    letter_template=options.get("letter_template"),
                    run_ids=tuple(options.get("run_ids", ())),
                    audience=str(options.get("audience", "links")),
                )
            )
        ),
        what="сборка писем",
    )


async def _search_contacts(
    limit: int,
    use_browser: bool,
    paid_first: bool,
    donor_id: int | None = None,
    stage: Stage = Stage.DONORS,
) -> dict[str, Any]:
    # Этап — разбором целиком, как в почте (`core/stages.py`): с «иначе доноры» новый
    # этап молча шёл бы путём доноров и платил за их ступени, а так он — ошибка mypy.
    queue_of: type[AdvertiserContactRepository] | None
    match stage:
        case Stage.DONORS:
            queue_of = None  # очередь доноров — умолчание `search_contacts`
        case Stage.ADVERTISERS:
            queue_of = AdvertiserContactRepository
        case Stage.SALES:
            raise StageWithoutContactsError(stage)
        case _:
            assert_never(stage)
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            report = await search_contacts(
                session,
                limit=limit,
                use_browser=use_browser,
                paid_first=paid_first,
                donor_id=donor_id,
                queue=None if queue_of is None else queue_of(session),
            )
            await session.commit()
            return report.as_dict()
    finally:
        await engine.dispose()


class StageWithoutContactsError(ValueError):
    """У этапа нет поиска адресов: задача отказывает, а не ищет за него донорам."""

    #: Повтор задачи это не исправит (`runs/failures.py`).
    permanent = True

    def __init__(self, stage: object) -> None:
        super().__init__(
            f"У этапа «{stage}» нет поиска адресов: задача не ищет за него ни донорам, "
            "ни рекламодателям. Чтобы искать, заведите этапу очередь в `_search_contacts` "
            "и подпись в `_contacts_what` (backend/workers/jobs.py)"
        )


def find_contacts(
    limit: int = 100,
    use_browser: bool = False,
    paid_first: bool = False,
    donor_id: int | None = None,
    stage: str = Stage.DONORS.value,
) -> dict[str, Any]:
    """Лестница контактов по донорам, которым он нужен.

    Задача, а не запрос: сотня доменов идёт минутами, и держать
    соединение всё это время значит потерять работу, если человек
    закрыл вкладку. Отчёт остаётся в результате задачи — по нему
    экран показывает, чем кончилось.

    `donor_id` — поиск с карточки одного донора. Задача та же, а не своя:
    та же лестница, те же повторы и тот же разбор исхода на экране.
    Свой путь задачи понадобился бы разбору исходов (`ops/job_outcome`)
    отдельной строкой, и без неё экран показал бы имя функции.

    `stage` — по той же причине: рекламодатели Этапа 2 идут той же задачей
    по своей очереди (`crawl/contacts.py`). Этап — строкой, как у сборки
    писем: задача живёт в очереди дольше версии кода.
    """
    setup_logging()
    check_storage()
    chosen = Stage(stage)
    return _settled(
        lambda: asyncio.run(_search_contacts(limit, use_browser, paid_first, donor_id, chosen)),
        what=_contacts_what(donor_id, chosen),
    )


def _contacts_what(donor_id: int | None, stage: Stage) -> str:
    """Чей поиск — словами, для журнала; этап — разбором целиком, как и очередь.

    Этап без поиска только называется, а отказ — в `_search_contacts`, внутри
    `_settled`: отсюда он ушёл бы мимо, и очередь трижды повторила бы его.
    """
    match stage:
        case Stage.ADVERTISERS:
            return "поиск адресов рекламодателей"
        case Stage.DONORS:
            return "поиск контактов" if donor_id is None else f"поиск адреса донора №{donor_id}"
        case Stage.SALES:
            return "поиск адресов продаж"
        case _:
            assert_never(stage)


async def _parse_reply(reply_id: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    extractor = ExtractClient()
    try:
        async with factory() as session:
            parsed = await Parser(session, extractor).parse(reply_id)
            await session.commit()
            await after_parse(session, reply_id)
            return {
                "reply": parsed.reply_id,
                "confidence": parsed.confidence,
                "stored_price": parsed.stored_price,
                "needs_review": parsed.needs_review,
                "tokens": parsed.tokens_spent,
                "skipped": parsed.skipped,
            }
    finally:
        await extractor.aclose()
        await engine.dispose()


def parse_reply(reply_id: int) -> dict[str, Any]:
    """Разобрать один ответ в цену.

    Отдельной задачей, а не внутри вебхука: вызов модели идёт секундами,
    а платформа повторяет доставку по таймауту — платный разбор в запросе
    означал бы повторные списания там, где сеть подтормозила.
    """
    setup_logging()
    check_storage()
    return _settled(lambda: _parse_or_postpone(reply_id), what=f"разбор ответа №{reply_id}")


def _parse_or_postpone(reply_id: int) -> dict[str, Any]:
    """Потолок расхода на модель — не отказ разбора, а «не сегодня».

    Для прогона потолок — постоянный отказ (`runs/failures.REFUSALS`), и
    `_settled` закрыл бы им и разбор: ответ с ценой остался бы неразобранным,
    а повторы очереди (минуты) дневной потолок не переживают. Поэтому здесь
    разбор ставится заново на начало следующих суток UTC — когда дневной
    счёт обнулится (ревью «Продаж» #158).
    """
    try:
        return asyncio.run(_parse_reply(reply_id))
    except LlmCapExceededError as exc:
        when = next_utc_day(datetime.now(UTC))
        job_id = f"{parse_job_id(reply_id)}-after-cap-{when:%Y%m%d}"
        runs_queue().enqueue_at(when, PARSE_JOB, reply_id, job_id=job_id, **with_retries())
        logger.warning("разбор ответа №%s отложен до %s: %s", reply_id, when.isoformat(), exc)
        return {"reply": reply_id, "postponed_until": when.isoformat(), "reason": str(exc)}


def next_utc_day(moment: datetime) -> datetime:
    """Начало следующих суток UTC — когда дневной потолок модели обнуляется."""
    return datetime.combine(moment.date() + timedelta(days=1), time.min, tzinfo=UTC)


async def _send_lead(reply_id: int, event_id: str) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            card = await lead_handoff.lead_card(session, reply_id)
        if card is None:
            raise lead_handoff.LeadNotTakenError(f"ответ №{reply_id} — не лид, передавать нечего")
        async with httpx.AsyncClient() as http:
            code = await lead_handoff.deliver(card, http, event_id=event_id)
        return {"lead": reply_id, "event_id": event_id, "status": code}
    finally:
        await engine.dispose()


def send_lead(reply_id: int, event_id: str) -> dict[str, Any]:
    """Передать лид на адрес вебхука CRM.

    Отдельной задачей: чужой сервер отвечает секундами или не отвечает,
    а кнопка «Взять в работу» от этого зависеть не должна. Сеть и 5xx
    очередь повторит; отказ 4xx и пустую настройку — нет (`_settled`).
    """
    setup_logging()
    check_storage()
    return _settled(
        lambda: asyncio.run(_send_lead(reply_id, event_id)), what=f"передача лида №{reply_id}"
    )
