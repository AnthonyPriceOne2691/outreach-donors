"""Полоса тревог над вкладками «Продаж» — словами, а не кодом этапа (ревью стыков, круг 2).

С #355 тревоги сторожа о почте продаж стоят красной полосой над вкладками раздела: их читает
человек продаж, а не только админ на главной. Заголовки тревог сторожа почты называют
направление кодом этапа: «Все ящики направления «sales» на паузе», «Очередь «sales» есть —
отправить некому» (замер стенда 12.10.2026: так и стоит над вкладками и уходит в Telegram).
Код — не слово: на экранах этапы — «продажи», «доноры», «рекламодатели».

Сторож почты — общий код (`ops/mail_watch.py`): правка — PR «общее», тест — `xfail(strict)`.
Модуль продаж — подставной: сторож у продаж включён политикой (`MailPolicy(watch=True)`).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, SenderStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.core.stages import MailPolicy
from backend.features.ops import mail_watch
from backend.features.outreach.repository import EVERY_STAGE
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import sender

NOW = datetime(2026, 10, 12, 21, 7, tzinfo=UTC)


@pytest.fixture(autouse=True)
def watched(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(watch=True))
    stages.register_sales(lambda: found)
    return found


async def _queued_first_letter(session: AsyncSession) -> None:
    """Первое письмо продаж в очереди: отправить его некому, если ящики не пишут."""
    domain = DomainModel(host="lead-words.example.test")
    campaign = CampaignModel(stage=Stage.SALES, name="Слова тревог", status="running")
    session.add_all([domain, campaign])
    await session.flush()
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            domain_id=domain.id,
            step=0,
            status=MessageStatus.QUEUED,
            idempotency_key="sales:alarm-words:0",
        )
    )
    await session.flush()


async def _sales_alarms(session: AsyncSession) -> list[tuple[str, str]]:
    box = await sender(session, "alex@mail-sales-words.example.test", Stage.SALES)
    box.status = SenderStatus.PAUSED
    await _queued_first_letter(session)
    found = await mail_watch.alarms(session, NOW, stages=EVERY_STAGE)
    return [(alarm.code, alarm.title) for alarm in found if alarm.stage is Stage.SALES]


async def test_the_sales_alarms_of_a_paused_direction_are_raised(session: AsyncSession) -> None:
    """Опора теста ниже: обе тревоги встают — о них и речь."""
    codes = [code for code, _ in await _sales_alarms(session)]

    assert codes == ["all-paused:sales", "nobody-to-send:sales"]


@pytest.mark.xfail(
    strict=False,  # общий PR его закрывает — метку снять после слияния
    reason=(
        "общий код ops/mail_watch.py: заголовки тревог сторожа называют направление кодом "
        "этапа («sales») — так они стоят красной полосой над вкладками продаж (#355) и уходят "
        "в Telegram; правка — слова направления, PR «общее» (ревью стыков R7)"
    ),
)
async def test_the_alarms_over_the_sales_tabs_name_the_direction_in_words(
    session: AsyncSession,
) -> None:
    titles = [title for _, title in await _sales_alarms(session)]

    assert titles, "тревоги продаж не встали — смотри тест выше"
    assert [title for title in titles if "«sales»" in title or "продаж" not in title] == []
