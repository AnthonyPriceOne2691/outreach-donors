"""Ревизия `ac052c21d38b`: сроки добивок [7, 14] → [7, 7], срок второй «+ 14 суток» → «+ 7».

ТЗ: добивки на 7-й и 14-й день. Срок считается от предыдущего письма, и прежнее
умолчание «7,14» отправляло вторую добивку на 21-й день (сверка ТЗ 12.10.2026). Ревизия
правит то, что записало прежнее умолчание, и только это. Срок второй добивки она
пересчитывает и у рассылок без своих сроков — они брали то же умолчание, — а сами
пустые сроки оставляет пустыми. Свои сроки рассылки, продажи и срок, который поставило
не умолчание, остаются как были.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from sqlalchemy import null, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import Null
from tests.migration_helpers import load_migration

REVISION = "ac052c21d38b_followups_on_day_7_and_14.py"

#: Когда ушло письмо, после которого ждут следующее. Некруглое — не дата-обязательство.
SENT = datetime(2026, 10, 3, 9, 41, 17, 250000, tzinfo=UTC)
DAY = timedelta(days=1)


def _revision(connection: Connection, *steps: str) -> None:
    """Шаги ревизии (`upgrade`, `downgrade`) на соединении теста — в его транзакции."""
    migration = load_migration(REVISION)
    with Operations.context(MigrationContext.configure(connection)):
        for step in steps:
            getattr(migration, step)()


@dataclass(frozen=True, slots=True)
class Before:
    """Как лежит до ревизии: рассылки по их срокам и письма по их смыслу."""

    campaigns: dict[str, int]
    letters: dict[str, int]


async def _campaign(
    session: AsyncSession, name: str, days: list[int] | Null | None, stage: Stage = Stage.DONORS
) -> int:
    """Рассылка со сроками `days`. Пустые сроки лежат тремя видами: `None` ORM пишет
    JSON `null` (так сохраняет сборка без сроков), `null()` — SQL NULL (рассылки,
    заведённые до колонки сроков), и пустой список."""
    campaign = CampaignModel(name=name, stage=stage, status="draft", followup_days=days)
    session.add(campaign)
    await session.flush()
    return campaign.id


async def _letter(
    session: AsyncSession, campaign_id: int, host: str, *, step: int, due: datetime | None
) -> int:
    """Ушедшее письмо шага `step` со сроком следующего `due`."""
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    message = MessageModel(
        campaign_id=campaign_id,
        domain_id=domain.id,
        step=step,
        status=MessageStatus.SENT,
        sent_at=SENT,
        next_action_at=due,
        subject="Guest article",
        body="Hi again,",
        idempotency_key=f"test:{host}:{step}",
    )
    session.add(message)
    await session.flush()
    return message.id


async def _as_before(session: AsyncSession) -> Before:
    """Рассылки с прежним умолчанием у обоих этапов почты, пустые всех трёх видов,
    свои сроки и продажи — с умолчанием и без сроков."""
    campaigns = {
        "доноры [7, 14]": await _campaign(session, "Доноры", [7, 14]),
        "рекламодатели [7, 14]": await _campaign(session, "Офферы", [7, 14], Stage.ADVERTISERS),
        "пустые (JSON null)": await _campaign(session, "Без сроков", None),
        "пустые (SQL NULL)": await _campaign(session, "Старая", null(), Stage.ADVERTISERS),
        "пустые []": await _campaign(session, "Пустой список", []),
        "свои [10, 14]": await _campaign(session, "Свои сроки", [10, 14]),
        "продажи [7, 14]": await _campaign(session, "Продажи", [7, 14], Stage.SALES),
        "продажи без сроков": await _campaign(session, "Продажи пустые", None, Stage.SALES),
    }
    old = campaigns["доноры [7, 14]"]
    letters = {
        "вторая по формуле": await _letter(
            session, old, "a.example.test", step=1, due=SENT + 14 * DAY
        ),
        "первая": await _letter(session, old, "b.example.test", step=0, due=SENT + 7 * DAY),
        "перенесённая": await _letter(
            session, old, "c.example.test", step=1, due=SENT + 14 * DAY + timedelta(hours=1)
        ),
        "погашенная": await _letter(session, old, "d.example.test", step=1, due=None),
        "вторая рекламодателю": await _letter(
            session,
            campaigns["рекламодатели [7, 14]"],
            "e.example.test",
            step=1,
            due=SENT + 14 * DAY,
        ),
        "вторая без сроков (JSON null)": await _letter(
            session, campaigns["пустые (JSON null)"], "f.example.test", step=1, due=SENT + 14 * DAY
        ),
        "вторая без сроков (SQL NULL)": await _letter(
            session, campaigns["пустые (SQL NULL)"], "i.example.test", step=1, due=SENT + 14 * DAY
        ),
        "вторая без сроков []": await _letter(
            session, campaigns["пустые []"], "j.example.test", step=1, due=SENT + 14 * DAY
        ),
        "перенесённая без сроков": await _letter(
            session,
            campaigns["пустые (JSON null)"],
            "k.example.test",
            step=1,
            due=SENT + 14 * DAY + timedelta(hours=1),
        ),
        "вторая продаж без сроков": await _letter(
            session, campaigns["продажи без сроков"], "l.example.test", step=1, due=SENT + 14 * DAY
        ),
        "вторая своих сроков": await _letter(
            session, campaigns["свои [10, 14]"], "g.example.test", step=1, due=SENT + 14 * DAY
        ),
        "вторая продаж": await _letter(
            session, campaigns["продажи [7, 14]"], "h.example.test", step=1, due=SENT + 14 * DAY
        ),
    }
    return Before(campaigns=campaigns, letters=letters)


async def _days(session: AsyncSession, before: Before) -> dict[str, list[int] | None]:
    rows = await session.execute(
        select(CampaignModel.id, CampaignModel.followup_days).where(
            CampaignModel.id.in_(before.campaigns.values())
        )
    )
    found = dict(rows.tuples().all())
    return {name: found[campaign_id] for name, campaign_id in before.campaigns.items()}


async def _due(session: AsyncSession, before: Before) -> dict[str, timedelta | None]:
    """Срок следующего письма — сколько после ухода этого."""
    rows = await session.execute(
        select(MessageModel.id, MessageModel.next_action_at).where(
            MessageModel.id.in_(before.letters.values())
        )
    )
    found = dict(rows.tuples().all())
    return {
        name: None if found[message_id] is None else found[message_id] - SENT
        for name, message_id in before.letters.items()
    }


AS_BEFORE_DAYS = {
    "доноры [7, 14]": [7, 14],
    "рекламодатели [7, 14]": [7, 14],
    "пустые (JSON null)": None,
    "пустые (SQL NULL)": None,
    "пустые []": [],
    "свои [10, 14]": [10, 14],
    "продажи [7, 14]": [7, 14],
    "продажи без сроков": None,
}
AS_BEFORE_DUE = {
    "вторая по формуле": 14 * DAY,
    "первая": 7 * DAY,
    "перенесённая": 14 * DAY + timedelta(hours=1),
    "погашенная": None,
    "вторая рекламодателю": 14 * DAY,
    "вторая без сроков (JSON null)": 14 * DAY,
    "вторая без сроков (SQL NULL)": 14 * DAY,
    "вторая без сроков []": 14 * DAY,
    "перенесённая без сроков": 14 * DAY + timedelta(hours=1),
    "вторая продаж без сроков": 14 * DAY,
    "вторая своих сроков": 14 * DAY,
    "вторая продаж": 14 * DAY,
}
#: Без сроков — рассылки донорам и рекламодателям, сроки которых пусты: ни продажи,
#: ни срок, который поставило не умолчание, в их число не входят.
WITHOUT_CADENCE = (
    "вторая без сроков (JSON null)",
    "вторая без сроков (SQL NULL)",
    "вторая без сроков []",
)
AFTER_DUE = AS_BEFORE_DUE | {
    "вторая по формуле": 7 * DAY,
    "вторая рекламодателю": 7 * DAY,
    **dict.fromkeys(WITHOUT_CADENCE, 7 * DAY),
}


class TestFollowupDaysRevision:
    async def test_old_default_becomes_day_7_and_14(self, session: AsyncSession) -> None:
        before = await _as_before(session)

        await (await session.connection()).run_sync(_revision, "upgrade")

        assert await _days(session, before) == AS_BEFORE_DAYS | {
            "доноры [7, 14]": [7, 7],
            "рекламодатели [7, 14]": [7, 7],
        }
        assert await _due(session, before) == AFTER_DUE

    async def test_campaigns_without_cadence_get_the_second_followup_on_day_14(
        self, session: AsyncSession
    ) -> None:
        """Рассылка без своих сроков брала то же умолчание «7,14» (решение 12.10.2026):
        её вторая добивка — тоже «+ 7 суток», а сами сроки остаются пустыми и дальше
        берут умолчание, уже новое. Продажи — как были."""
        before = await _as_before(session)

        await (await session.connection()).run_sync(_revision, "upgrade")

        days, due = await _days(session, before), await _due(session, before)
        empty = ("пустые (JSON null)", "пустые (SQL NULL)", "пустые []", "продажи без сроков")
        assert {name: days[name] for name in empty} == {
            name: AS_BEFORE_DAYS[name] for name in empty
        }
        assert {name: due[name] for name in due if "без сроков" in name} == {
            "вторая без сроков (JSON null)": 7 * DAY,
            "вторая без сроков (SQL NULL)": 7 * DAY,
            "вторая без сроков []": 7 * DAY,
            "перенесённая без сроков": 14 * DAY + timedelta(hours=1),
            "вторая продаж без сроков": 14 * DAY,
        }

    async def test_rerun_changes_nothing_and_downgrade_puts_it_back(
        self, session: AsyncSession
    ) -> None:
        """Повтор безопасен: [7, 14] и сроков «+ 14 суток» по формуле после первого прохода
        не осталось. Откат возвращает те же строки к прежнему умолчанию — и у рассылок
        без сроков — и не трогает остальные."""
        before = await _as_before(session)
        connection = await session.connection()
        await connection.run_sync(_revision, "upgrade")
        once = (await _days(session, before), await _due(session, before))

        await connection.run_sync(_revision, "upgrade")
        assert (await _days(session, before), await _due(session, before)) == once

        await connection.run_sync(_revision, "downgrade")
        assert await _days(session, before) == AS_BEFORE_DAYS
        assert await _due(session, before) == AS_BEFORE_DUE
