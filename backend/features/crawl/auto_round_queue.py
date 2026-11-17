"""Очередь задач глазами автокруга рекламодателей: поиск адресов и сборка очереди — те же задачи и те
же номера, что у кнопок.

**Поиск адресов — задача кнопки** (`api/advertisers/routes._search`): одна на сервис, её номер — в
памяти очереди (`ADVERTISER_CONTACTS_JOB_KEY`). Идёт — круг второй не ставит, а ждёт её: второй
проход по тем же доменам заплатил бы платной ступени дважды.

**Сборка — задача кнопки «Собрать очередь» под её номером** (`api/letters/once.build_job_id`):
сборка писем по ссылке одна за раз, кнопкой или кругом. Собирает человек — круг ждёт, пока его
сборка кончится, и ставит свою: две разом собрали бы письма сверх потолка и дважды потратили модель.
Номер переживает задачу — кончившаяся убирается вместе с итогом и ставится заново (порядок
`once.enqueue_once`; оттуда не импортируется — слой выше). Задача умершего воркера числится идущей
до своего срока — живость говорит его ключ (`queue.job_alive`).

**Очередь не ответила — это не исход**: круг ждёт следующего прохода, а не ставит вторую задачу.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol

from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import DuplicateJobError
from rq.results import Result

from backend.features.core.domain import Stage
from backend.features.crawl.judge_night_queue import GOING, PassSeen, seen_of
from backend.features.crawl.niche import LINKS
from backend.shared.queue import (
    ADVERTISER_CONTACTS_JOB_KEY,
    BUILD_JOB,
    CONTACTS_JOB,
    contacts_job_id,
    contacts_queue,
    job_alive,
    remember_contacts_job,
    runs_queue,
    with_retries,
)

logger = logging.getLogger(__name__)

#: Номер сборки очереди «По найденной ссылке» — тот же, что у кнопки
#: (`api/letters/once.build_job_id(Stage.ADVERTISERS, "links")`; равенство держит тест).
BUILD_JOB_ID = f"letters-build-{Stage.ADVERTISERS.value}-{LINKS}"
#: Потолок одного прохода поиска — тот же, что у кнопки (`api/advertisers/routes.SEARCH_MAX`).
SEARCH_MAX = 1000
#: Страна писем рекламодателям — как у формы сборки: прогонов у Этапа 2 нет, и экран её не шлёт.
COUNTRY = "us"
#: Воркер задачи умер — словами итога круга.
WORKER_DIED = "воркер перезапустился посреди задачи"

#: Что вышло с постановкой сборки: поставлена; идёт чужая (кнопкой) — ждём её; очередь не ответила.
Asked = Literal["asked", "busy", "silent"]


class RoundQueue(Protocol):
    """Очередь глазами автокруга: тесты подставляют свою."""

    def search(self, limit: int) -> bool | None:
        """Поставить поиск адресов рекламодателям. `True` — поставлен или уже идёт (круг ждёт его);
        `None` — очередь не ответила."""
        ...

    def search_seen(self) -> PassSeen: ...

    def build(self, campaign: str, limit: int) -> Asked: ...

    def build_seen(self) -> PassSeen: ...


def _search_args(limit: int) -> tuple[object, ...]:
    """Доводы задачи поиска — как у кнопки: без браузера, платная ступень последней, вся очередь
    рекламодателей."""
    return (min(limit, SEARCH_MAX), False, False, None, Stage.ADVERTISERS.value)


def _build_once(jobs: Queue, campaign: str, limit: int) -> bool:
    """Поставить сборку под номером кнопки; номер занят — ложь. Сроки добивок и текст — умолчания
    (пустые доводы): рассылку круга заводит сама сборка."""
    try:
        jobs.enqueue(
            BUILD_JOB,
            campaign,
            COUNTRY,
            limit=limit,
            stage=Stage.ADVERTISERS.value,
            job_id=BUILD_JOB_ID,
            unique=True,
            **with_retries(),
        )
    except DuplicateJobError:
        logger.info("автокруг: номер сборки занят — смотрим, кончилась ли прежняя")
        return False
    return True


def _ask_build(jobs: Queue, campaign: str, limit: int) -> Asked:
    """Сборка круга; идёт другая (поставил человек) — ждём её, кончившаяся номер отдаёт."""
    if _build_once(jobs, campaign, limit):
        return "asked"
    held = jobs.fetch_job(BUILD_JOB_ID)
    if held is None:
        return "asked" if _build_once(jobs, campaign, limit) else "busy"
    # Задача умершего воркера числится идущей: живость говорит его ключ, а не статус.
    if held.get_status() in GOING and job_alive(BUILD_JOB_ID) is not False:
        return "busy"
    Result.delete_all(held)
    held.delete()
    return "asked" if _build_once(jobs, campaign, limit) else "busy"


class RqRound:
    """Поиск адресов и сборка в настоящей очереди задач."""

    def search(self, limit: int) -> bool | None:
        try:
            held = contacts_job_id(key=ADVERTISER_CONTACTS_JOB_KEY)
            if held is not None and job_alive(held) is True:
                logger.info("автокруг: поиск адресов уже идёт — ждём его", extra={"job_id": held})
                return True
            found = contacts_queue().enqueue(CONTACTS_JOB, *_search_args(limit), **with_retries())
            remember_contacts_job(str(found.id), key=ADVERTISER_CONTACTS_JOB_KEY)
        except RedisError as exc:
            logger.warning(
                "автокруг: очередь не ответила, поиск адресов не поставлен",
                extra={"error": str(exc)},
            )
            return None
        return True

    def search_seen(self) -> PassSeen:
        return seen_of(contacts_job_id(key=ADVERTISER_CONTACTS_JOB_KEY), died=WORKER_DIED)

    def build(self, campaign: str, limit: int) -> Asked:
        try:
            return _ask_build(runs_queue(), campaign, limit)
        except RedisError as exc:
            logger.warning(
                "автокруг: очередь не ответила, сборка не поставлена", extra={"error": str(exc)}
            )
            return "silent"

    def build_seen(self) -> PassSeen:
        return seen_of(BUILD_JOB_ID, died=WORKER_DIED)
