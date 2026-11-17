"""Заведение прогона сбора: строка прогона, задача очереди и запись журнала — одним путём.

Зовут его «Запустить» вкладки «Сбор базы» (`api/sales/segment.start_run`) и конвейер плана
работ. Смету вызывающий считает сам, до заведения (`segment_estimate.estimate`): экран сверяет её
с той, что видел человек (отпечаток), конвейер — с потолками плана. Прогон заводится только по
смете без отказа (`LaunchRefusedError`): ключи, рынок, язык, глубина, страницы и режим судьи —
её, и сама смета ложится в строку прогона, как её видел запустивший.

**Строка прогона — в базе до постановки:** задача читает её своей сессией, и взятая воркером
раньше коммита не нашла бы прогона. Тем же коммитом уходит и то, что вызывающий положил в сессию
до заведения. Очередь не ответила — уходит строка прогона (только она), а исключение Redis —
вызывающему: прогона без задачи, который «стоит в очереди», нет.

**Постановка** — `deferred.once` в очередь вызывающего (у экрана — очередь долгих задач продаж)
под номером прогона (`segment_jobs.segment_job_id`), в пуле потоков: клиент Redis синхронный.
Номер свежего прогона занят быть не может — но молча это не теряется: `None`, и вызывающий
говорит это словами; строка прогона остаётся, как оставалась у маршрута.

**Журнал** — запуск (`RUN_STARTED`) той же транзакцией, что номер задачи в строке прогона:
гипотеза, ключи, рынок, смета словами и снятые человеком ключи. Автор — сотрудник; у конвейера
автора нет — запись без автора словами «автоматически», как у повтора по сроку
(`segment_jobs.AUTO`), и почты запустившего в строке прогона нет. Прогон плана — с номером
гипотезы плана.

**Первая покупаемая страница (`from_page`, из сметы) задаётся здесь и потом не меняется.** Номера
задач выдачи в следе покупки хранятся по ключу, без страниц (`segment_serp.SerpTrail`,
`serp/protocol`): прогон со сменённой первой страницей забрал бы по ним прежнюю выдачу со
сдвинутыми позициями. Пути правки нет ни у продолжения, ни у повтора: другие страницы — другой
прогон. Гипотеза плана (`plan_hypothesis_id`) — тоже только здесь.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from redis.exceptions import RedisError
from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core import domain
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import UserModel
from backend.features.sales import deferred
from backend.features.sales.segment_estimate import Estimate
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage

#: Действие в журнале: прогон завёл сотрудник.
STARTED = "сбор базы продаж"
#: Действие в журнале: прогон завёл конвейер плана работ, а не человек.
AUTO = "сбор базы продаж: конвейер плана работ — автоматически"


class LaunchRefusedError(RuntimeError):
    """Смета отказала — прогон по ней не заводится. Слова — отказа сметы."""


@dataclass(frozen=True, slots=True)
class Launched:
    """Заведённый прогон и номер его задачи в очереди."""

    run: SalesSegmentRunModel
    job_id: str


async def launch(
    session: AsyncSession,
    hypothesis_id: int,
    found: Estimate,
    *,
    jobs: Queue,
    author: UserModel | None = None,
    plan_hypothesis_id: int | None = None,
) -> Launched | None:
    """Завести прогон гипотезы по смете `found` и поставить его проход в `jobs`. `author` — кто
    запускает; `None` — конвейер. `plan_hypothesis_id` — чей это прогон плана работ.

    Отказ сметы — `LaunchRefusedError`, ничего не записано. Очередь не ответила — исключение
    Redis, строки прогона нет. `None` — номер задачи прогона занят: строка есть, задачи нет."""
    if found.refusal is not None:
        raise LaunchRefusedError(found.refusal)
    run = SalesSegmentRunModel(
        hypothesis_id=hypothesis_id,
        keywords=found.keywords,
        country=found.country,
        language=found.language,
        depth_pages=found.depth_pages,
        from_page=found.from_page,
        plan_hypothesis_id=plan_hypothesis_id,
        mode=found.mode,
        estimate=found.as_dict(),
        report={},
        stage=Stage.QUEUED,
        created_by=None if author is None else author.email,
    )
    session.add(run)
    await session.commit()
    try:
        job = await asyncio.to_thread(
            deferred.once, jobs, segment_job_id(run.id), SEGMENT_JOB, run.id
        )
    except RedisError:
        await session.delete(run)
        await session.commit()
        raise
    if job is None:
        return None
    run.job_id = str(job.id)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=None if author is None else author.id,
        target=f"job:{job.id}",
        details=_started(found, auto=author is None, plan_hypothesis_id=plan_hypothesis_id),
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона (`Stage` выше)
    )
    await session.commit()
    return Launched(run=run, job_id=str(job.id))


def _started(found: Estimate, *, auto: bool, plan_hypothesis_id: int | None) -> dict[str, object]:
    """Запуск в журнале действий: гипотеза, ключи, рынок и смета словами; снятые человеком
    ключи — как вписаны в смету, когда они есть; гипотеза плана — у прогона плана работ."""
    details: dict[str, object] = {
        "действие": AUTO if auto else STARTED,
        "гипотеза": found.hypothesis,
        "ключей": len(found.keywords),
        "рынок": found.country,
        "смета": " ".join(found.lines),
    }
    if found.excluded:
        details["снято ключей"] = ", ".join(f"«{phrase}»" for phrase in found.excluded)
    if plan_hypothesis_id is not None:
        details["гипотеза плана"] = plan_hypothesis_id
    return details
