"""Процесс добивок убирает брошенные файлы ответа: своей сессией на проход, по настоящей базе.

Правило самой уборки — без письма дольше срока, условие в самом удалении — проверено
у хранилища (`test_answer_files.py::TestAbandonedFiles`); здесь — проводка процесса:
второй цикл рядом с добивками, своя база и строка журнала с номерами.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from backend.config import storage
from backend.features.core.domain import Stage
from backend.features.core.models.outgoing_attachment import OutgoingAttachmentModel
from backend.features.core.models.outreach import CampaignModel, ThreadModel
from backend.features.letters.outgoing_store import OutgoingFiles
from backend.workers import followups
from sqlalchemy import func, select, text
from tests.conftest import TEST_DSN, make_donor
from tests.test_outgoing_files import PDF
from tests.test_send_race import committed_sessions


@pytest.fixture(autouse=True)
def _base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)


def test_followups_runs_the_files_pass_beside_the_followups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[tuple[float, str]] = []

    async def every(interval: float, _work: Any, *, name: str) -> None:
        started.append((interval, name))

    monkeypatch.setattr(followups, "every", every)
    monkeypatch.setattr(followups, "check_storage", lambda: None)

    followups.main()

    assert started == [
        (followups.POLL_INTERVAL_SEC, "Добивки"),
        (followups.ABANDONED_FILES_SEC, "Чистка брошенных файлов ответа"),
    ]


async def test_abandoned_files_pass_removes_them_and_says_which(
    caplog: pytest.LogCaptureFixture,
) -> None:
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

        with caplog.at_level(logging.INFO, logger=followups.__name__):
            await followups.drop_abandoned_files()

        async with factory() as session:
            left = await session.scalar(select(func.count(OutgoingAttachmentModel.id)))
    assert left == 0
    (said,) = [record for record in caplog.records if "брошенные" in record.getMessage()]
    assert (getattr(said, "files", None), getattr(said, "threads", None)) == ([row.id], [thread.id])


async def test_abandoned_files_pass_is_quiet_when_nothing_is_left(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with committed_sessions():
        with caplog.at_level(logging.INFO, logger=followups.__name__):
            await followups.drop_abandoned_files()
    assert [record for record in caplog.records if "брошенные" in record.getMessage()] == []
