"""Задача прогона гипотезы (`run_segment`): экран ставит, воркер продаж проводит.

Тонкая обёртка над `segment_run.execute`, как у очистки (`clean_jobs.py`): своя сессия, свои
клиенты — выдача по настройке (`serp/factory.build_provider`), модель судьи, клиент главных
с защитой исходящих (адреса приходят из выдачи) и проверка адресов по настройке. Очередь
зовёт задачу путём строкой (`SEGMENT_JOB`): общий код очереди о продажах знает одну строку —
имя задачи словами (`ops/job_outcome.KINDS`).

**Отказы.** Постоянный (`permanent`: настройка, отказ провайдера по ключу, потолок модели) —
итог задачи и причина в строке прогона словами, а не три повтора. Остальное (сеть, база) —
исключение, и очередь повторит задачу: этапы закоммичены, повтор продолжает с места и не
покупает выдачу второй раз. Последний повтор не прошёл — строка прогона встаёт «остановлен»
со словами сбоя (`RETRIES_SPENT`): до 14.10 она оставалась «идёт» с пустой причиной навсегда.

**Причина — словами человека** (`stop_words`): без имени класса (`runs/reasons.explained`) и
без имён настроек — их видит администратор в журнале (тест прогона 14.10: «SerpNotSetError:
SERP_PROVIDER=…» на экране продаж).

**Пульс.** Пока проход идёт, строка прогона отмечается живой своей сессией
(`segment_progress.pulse`): шесть минут выдачи без отметки неотличимы от мёртвого прогона.

**Режим покупки выдачи — из сметы прогона** (`estimate["serp_mode"]`, `SERP_MODE_KEY`): какой
режим человек видел в смете на запуске, такой и покупается, а не тот, что стоит в настройке
сервиса сейчас. Нет его в смете (прогон старше режимов) — умолчание сервиса (`SERP_MODE`).
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import AsyncExitStack, suppress
from datetime import UTC, datetime
from typing import Any

import httpx
from rq import get_current_job
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import judge as judge_cfg
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config import serp as serp_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.ahrefs.client import AhrefsClient
from backend.features.runs.failures import described, is_permanent
from backend.features.runs.reasons import explained
from backend.features.sales.segment_estimate import SALES_OFF, SANDBOX, SERP_AHREFS, SERP_NOT_SET
from backend.features.sales.segment_judge import SegmentClient
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from backend.features.sales.segment_progress import pulse
from backend.features.sales.segment_run import RunDeps, execute
from backend.features.sales.verifier import build_verifier
from backend.features.serp.factory import AHREFS, UnknownProviderError, build_provider
from backend.features.serp.modes import SerpMode, UnknownModeError, mode_of
from backend.features.serp.protocol import SerpProvider
from backend.shared.logs import setup_logging
from backend.shared.net.url_guard import guarded_client
from backend.shared.queue import remember_job_error

logger = logging.getLogger(__name__)

#: Путь задачи строкой: очередь импортирует её в воркере продаж.
SEGMENT_JOB = "backend.features.sales.segment_jobs.run_segment"
#: Режим покупки выдачи в смете прогона (`SerpMode` строкой): его пишет смета на запуске.
SERP_MODE_KEY = "serp_mode"

#: Имя настройки в тексте отказа: `SERP_PROVIDER`, `SALES_VERIFIER_PROVIDER=live`.
_SETTING = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
#: Отказ называет настройку — на экран идут эти слова, имя и значение — в журнал.
SETTINGS_WORDS = (
    "настройка сервиса не годится — прогон не тратит, пока её не поправит администратор "
    "(что именно — в журнале сервиса)"
)
#: Повторы задачи кончились, а сбой не прошёл.
RETRIES_SPENT = (
    "сбой не прошёл и после повторов задачи: {why} — продолжите прогон, когда он пройдёт"
)


class SerpNotSetError(RuntimeError):
    """Источник выдачи не собирается по настройке. Повтор это не исправит."""

    permanent = True


class SalesOffError(RuntimeError):
    """Модуль продаж выключили после запуска: сбор не тратит. Повтор это не исправит."""

    permanent = True


def segment_job_id(run_id: int) -> str:
    """Номер задачи прогона — от прогона: один проход прогона за раз."""
    return f"sales-segment-{run_id}"


def stop_words(exc: BaseException) -> str:
    """Причина остановки для человека: без имени класса — и без имён настроек. Настройку
    поправит администратор: имя, значение и класс — в журнал, на экран — что делать."""
    words = explained(exc)
    if _SETTING.search(words) is None:
        return words
    logger.warning("сбор продаж: отказ настройки", extra={"error": described(exc)})
    return SETTINGS_WORDS


def last_try() -> bool:
    """Последняя ли попытка: повторов не осталось (rq: `retries_left` ноль или пусто) или
    задача запущена не из очереди (консоль, тест) — повторять некому."""
    job = get_current_job()
    return job is None or not job.retries_left


def _stopped_by(exc: BaseException) -> str | None:
    """Что записать в строку прогона при сбое прохода: постоянный отказ и сбой последней
    попытки — причину словами; пусто — впереди повтор задачи, прогон идёт."""
    if is_permanent(exc):
        return stop_words(exc)
    if last_try():
        return RETRIES_SPENT.format(why=stop_words(exc))
    return None


async def _stop(factory: async_sessionmaker[Any], run_id: int, words: str) -> None:
    """Причину остановки — в строку прогона: экран читает её оттуда. Проход кончился —
    `finished_at`, как у готового."""
    async with factory() as session:
        run = await session.get(SalesSegmentRunModel, run_id)
        if run is not None:
            run.stage, run.error = Stage.STOPPED, words
            run.finished_at = datetime.now(UTC)
            await session.commit()


async def _tell_stopped(factory: async_sessionmaker[Any], run_id: int, words: str) -> None:
    """`_stop`, сбой которого не подменяет причину: база не ответила и тут — запись не легла,
    а задача упадёт исходным исключением, и экран назовёт его по задаче (`segment_watch`)."""
    try:
        await _stop(factory, run_id, words)
    except Exception:
        logger.exception("сбор продаж: причина остановки не записалась", extra={"run": run_id})


async def _estimate_of(factory: async_sessionmaker[Any], run_id: int) -> dict[str, Any]:
    """Смета прогона, как её видел человек на запуске; прогона нет — пустая."""
    async with factory() as session:
        run = await session.get(SalesSegmentRunModel, run_id)
        return dict(run.estimate or {}) if run is not None else {}


async def _mode(factory: async_sessionmaker[Any], run_id: int) -> SerpMode:
    """Режим покупки выдачи прогона — из его сметы; нет в смете — умолчание сервиса.
    Незнакомый режим — постоянный отказ словами: молча взять другой нельзя, цена втрое."""
    raw = (await _estimate_of(factory, run_id)).get(SERP_MODE_KEY)
    try:
        return mode_of(raw if isinstance(raw, str) else None)
    except UnknownModeError as exc:
        raise SerpNotSetError(f"{exc} — прогон не покупает выдачу вслепую") from exc


def _serp(ahrefs: AhrefsClient, mode: SerpMode) -> SerpProvider:
    """Источник выдачи прогона в режиме из сметы. Не собирается по настройке, это Ahrefs или
    песочница (настройку сменили после сметы: юниты Ahrefs ушли бы мимо журнала,
    `SERP_AHREFS`; сайты песочницы выдуманы, а судья и адреса по ним — траты, `SANDBOX`) —
    постоянный отказ словами."""
    if serp_cfg.SANDBOX:
        raise SerpNotSetError(SANDBOX)
    try:
        serp = build_provider(ahrefs, mode=mode)
    except UnknownProviderError as exc:
        # Имена настроек — администратору в журнал, на экран — слова сметы.
        logger.warning("сбор продаж: источник выдачи не собран", extra={"error": str(exc)})
        raise SerpNotSetError(SERP_NOT_SET) from exc
    if serp.name == AHREFS:
        raise SerpNotSetError(SERP_AHREFS)
    return serp


async def _with_clients(factory: async_sessionmaker[Any], run_id: int) -> dict[str, Any]:
    """Клиенты прохода — и сам проход; клиенты закрываются при любом исходе."""
    ahrefs = AhrefsClient()
    try:
        serp = _serp(ahrefs, await _mode(factory, run_id))
        async with AsyncExitStack() as stack:
            # Модель судьи — со своим сроком (`LLM_TIMEOUT_S`), как у прогона доноров: с умолчанием
            # httpx (5 с) модель с рассуждением не успевала ни в одну из трёх попыток, а оборванный
            # запрос провайдер может довести и выставить счёт мимо журнала. Проверка адресов — на
            # своём клиенте с умолчанием, как у очистки (`clean_jobs.py`).
            model = await stack.enter_async_context(httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S))
            http = await stack.enter_async_context(httpx.AsyncClient())
            home = await stack.enter_async_context(
                guarded_client(timeout=judge_cfg.HOME_TIMEOUT_SEC)
            )
            session = await stack.enter_async_context(factory())
            deps = RunDeps(
                serp=serp,
                judge=SegmentClient(model),
                home=home if judge_cfg.HOME_CHECK else None,
                verifier=build_verifier(http),
            )
            beat = asyncio.create_task(pulse(factory, run_id))
            try:
                return await execute(session, run_id, deps)
            finally:
                beat.cancel()
                with suppress(asyncio.CancelledError):
                    await beat
                closing = getattr(serp, "aclose", None)
                if closing is not None:
                    await closing()
    finally:
        await ahrefs.aclose()


async def run_once(run_id: int) -> dict[str, Any]:
    """Один проход прогона: своя сессия и свои клиенты на задачу. Постоянный отказ и сбой
    последней попытки — причиной в строке прогона: экран читает её оттуда."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if not sales_cfg.ENABLED:
            raise SalesOffError(SALES_OFF)
        return await _with_clients(factory, run_id)
    except Exception as exc:
        if (words := _stopped_by(exc)) is not None:
            await _tell_stopped(factory, run_id, words)
        raise
    finally:
        await engine.dispose()


def run_segment(run_id: int) -> dict[str, Any]:
    """Задача очереди: провести прогон гипотезы с места, где он стоит. Писем не пишет."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(run_once(run_id))
    except Exception as exc:
        if is_permanent(exc):
            logger.warning(
                "сбор продаж: прогон не выполнен", extra={"run": run_id, "error": described(exc)}
            )
            return {"error": stop_words(exc), "permanent": True}
        if (job := get_current_job()) is not None:
            remember_job_error(job.id, described(exc))
        raise
