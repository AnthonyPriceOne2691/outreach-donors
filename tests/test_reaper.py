"""Процесс разбора мёртвых: один проход — прогоны Этапа 1 и обходы Этапа 2.

Правила разбора проверены в своих файлах (`test_run_lifecycle.py`,
`test_crawl_lifecycle.py`); здесь — проводка процесса: своя база, очередь
и то, что отказ очереди не роняет проход. Очередь подменена.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from backend.config import storage
from backend.features.core.domain import CrawlStatus, Stage
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.outgoing_attachment import OutgoingAttachmentModel
from backend.features.core.models.outreach import CampaignModel, ThreadModel
from backend.features.crawl.repository import queue_crawl
from backend.features.letters.outgoing_store import OutgoingFiles
from backend.features.ops import silence
from backend.workers import reaper
from sqlalchemy import func, select, text
from tests.conftest import TEST_DSN, make_donor
from tests.test_outgoing_files import PDF
from tests.test_send_race import committed_sessions


@pytest.fixture(autouse=True)
def _base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)


async def test_sweep_resumes_a_dead_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    resumed: list[int] = []
    monkeypatch.setattr(reaper, "job_alive", lambda _job: False)
    monkeypatch.setattr(reaper, "job_failure", lambda _job: None)
    monkeypatch.setattr(
        reaper, "enqueue_crawl", lambda run_id: resumed.append(run_id) or f"next-{run_id}"
    )

    async with committed_sessions() as factory:
        async with factory() as session:
            run = await queue_crawl(session, "dead.example.test", by=None)
            run.status, run.job_id = CrawlStatus.RUNNING, "gone"
            await session.commit()
            await session.execute(
                text("UPDATE crawl_runs SET updated_at = now() - interval '10 minutes'")
            )
            await session.commit()

        await reaper.sweep()

        async with factory() as session:
            after = await session.get(CrawlRunModel, run.id)
    assert resumed == [run.id]
    assert after is not None
    assert (after.status, after.job_id, after.resumes) == (
        CrawlStatus.QUEUED,
        f"next-{run.id}",
        1,
    )


def test_silent_queue_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> str:
        raise ConnectionError("Redis недоступен")

    class _Queue:
        enqueue = staticmethod(refuse)

    monkeypatch.setattr(reaper, "enqueue_crawl", refuse)
    monkeypatch.setattr(reaper, "runs_queue", _Queue)

    assert reaper._enqueue_crawl(5) is None
    assert reaper._enqueue(5) is None


async def test_watch_reports_silence_and_tells_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тревоги базы — одной сессией, к ним — опрос провайдеров, итог — ленте тревог.
    Что сеть идёт после закрытия сессии — `tests/test_mail_watch_seams.py`."""
    seen: list[object] = []
    told: list[object] = []

    async def alarms(session: object) -> list[str]:
        seen.append(session)
        return ["тревога базы"]

    async def with_providers(found: list[str]) -> list[str]:
        return ["провайдер молчит", *found]

    async def tell(found: object) -> None:
        told.append(found)

    monkeypatch.setattr(silence, "alarms", alarms)
    monkeypatch.setattr(silence, "with_providers", with_providers)
    monkeypatch.setattr(reaper.FEED, "tell", tell)

    async with committed_sessions():
        await reaper.watch()

    assert (len(seen), told) == (1, [["провайдер молчит", "тревога базы"]])


def test_main_runs_all_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    async def every(_interval: float, _work: Any, *, name: str) -> None:
        started.append(name)

    monkeypatch.setattr(reaper, "every", every)
    monkeypatch.setattr(reaper, "check_storage", lambda: None)

    reaper.main()

    assert started == [
        "Разбор мёртвых прогонов",
        "Сторож тишины",
        "Повтор передачи лидов продаж",
        "Чистка брошенных файлов ответа",
    ]


async def test_abandoned_files_pass_removes_them_and_says_which(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Проход своей сессией по настоящей базе: брошенный файл уходит, номер — полем журнала."""
    async with committed_sessions() as factory:
        async with factory() as session:
            domain = await make_donor(session, "files.example.test")
            campaign = CampaignModel(name="Рассылка файлов", stage=Stage.DONORS, status="draft")
            session.add(campaign)
            await session.flush()
            thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
            session.add(thread)
            await session.flush()
            row = await OutgoingFiles(session).keep(thread.id, "price.pdf", PDF, by=None)
            await session.commit()
            await session.execute(
                text("UPDATE outgoing_attachments SET created_at = now() - interval '8 days'")
            )
            await session.commit()

        with caplog.at_level(logging.INFO, logger=reaper.__name__):
            await reaper.drop_abandoned_files()

        async with factory() as session:
            left = await session.scalar(select(func.count(OutgoingAttachmentModel.id)))
    assert left == 0
    (said,) = [record for record in caplog.records if "брошенные" in record.getMessage()]
    assert (getattr(said, "files", None), getattr(said, "threads", None)) == ([row.id], [thread.id])


async def test_abandoned_files_pass_is_quiet_when_nothing_is_left(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with committed_sessions():
        with caplog.at_level(logging.INFO, logger=reaper.__name__):
            await reaper.drop_abandoned_files()
    assert [record for record in caplog.records if "брошенные" in record.getMessage()] == []
