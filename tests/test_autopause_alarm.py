"""Ящик встал на паузу сам — одна тревога человеку (ТЗ: «ящик с ростом отказов сам встаёт на
паузу»), у ящиков всех этапов.

Пауза — мягкие сигналы продаж (окно последних писем, `outreach/health.py`) и прежнее правило
парковки доноров (доля отказов за жизнь ящика, `letters/events._park_if_burning`). Тревога одна
на паузу: следующая пачка по стоящему ящику её не повторяет, включили и ящик снова встал — новая
тревога; пауза человеком — без тревоги. Путь целиком: вебхук событий с подписью платформы,
фиксация, тревога после ответа. Гонка двух пачек — на двух настоящих соединениях со встречей
перед решением (как `tests/test_send_race.py`). Telegram подставной.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core import stages
from backend.features.core.domain import Stage, UserRole
from backend.features.core.models.outreach import SenderModel
from backend.features.core.stages import MailPolicy
from backend.features.outreach import autopause, senders
from backend.features.outreach.health import SoftSignals
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_delivery_events import _keypair
from tests.test_delivery_events_repeat import _deliver, _event, _letter
from tests.test_sales_soft_signals import NOW, _box
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import sender
from tests.test_send_race import MEETING, committed_sessions


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Подпись платформы — своей парой ключей; модуль продаж подключён, пороги по умолчанию."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(soft=SoftSignals(), watch=True))
    stages.register_sales(lambda: found)
    private, public = _keypair()
    monkeypatch.setattr("backend.config.outreach.EVENTS_PUBLIC_KEY", public)
    return private


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Тревоги автопаузы — по порядку; Telegram принимает всё."""
    said: list[str] = []

    async def telegram(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(autopause, "send_alert", telegram)
    return said


def _alarm(email: str, stage: Stage, why: str) -> str:
    return (
        f"ящик {email} (направление «{stage.value}») встал на паузу сам: {why}. Новых писем с "
        f"него нет, письма его переписок ждут. {autopause.WHAT_TO_DO}"
    )


async def test_a_sales_box_pausing_itself_is_told_once(
    client: AsyncClient, session: AsyncSession, signed: Any, told: list[str]
) -> None:
    """Третий отказ в окне — пауза и одна тревога: ящик, почему и с какими числами, что делать.
    Второй отказ той же пачки и следующая пачка по стоящему ящику тревоги не повторяют."""
    box = await _box(session, Stage.SALES, total=20, bounced=2)
    letter = await _letter(session, box)

    first = await _deliver(
        client, signed, [_event("bounce", letter, "sg-a1"), _event("bounce", letter, "sg-a2")]
    )
    second = await _deliver(client, signed, [_event("bounce", letter, "sg-a3")])

    assert [answer.json()["paused"] for answer in (first, second)] == [[box.email], []]
    why = "отказов 3 в окне 50 писем — порог 5.0%: пауза"
    assert told == [_alarm(box.email, Stage.SALES, why)]
    assert "Включить заново" in told[0]
    assert "разгон начнётся заново" in told[0]


async def test_switched_on_and_paused_again_is_a_new_alarm(
    client: AsyncClient, session: AsyncSession, signed: Any, told: list[str]
) -> None:
    box = await _box(session, Stage.SALES, total=20, bounced=2)
    letter = await _letter(session, box)
    await _deliver(client, signed, [_event("bounce", letter, "sg-b1")])

    senders.enable(box, now=NOW)
    await session.commit()
    again = await _deliver(client, signed, [_event("bounce", letter, "sg-b2")])

    assert again.json()["paused"] == [box.email]
    assert len(told) == 2
    assert all(text.startswith(f"ящик {box.email} ") for text in told)


async def test_a_donor_box_parked_by_the_old_rule_is_told_too(
    client: AsyncClient, session: AsyncSession, signed: Any, told: list[str]
) -> None:
    """Общая для всех этапов: доноры — прежним правилом по доле отказов за жизнь ящика."""
    box = await _box(session, Stage.DONORS, total=60, bounced=3)
    letter = await _letter(session, box)

    first = await _deliver(client, signed, [_event("bounce", letter, "sg-c1")])
    second = await _deliver(client, signed, [_event("bounce", letter, "sg-c2")])

    assert [answer.json()["paused"] for answer in (first, second)] == [[box.email], []]
    assert box.pause_reason == "доля отказов 7% — парковка"  # причина на экране — как была
    why = "доля отказов 7% — 4 из 60 писем за всё время ящика, порог 5%"
    assert told == [_alarm(box.email, Stage.DONORS, why)]


async def test_a_pause_by_a_human_is_no_alarm(
    client: AsyncClient,
    session: AsyncSession,
    signed: Any,
    told: list[str],
    make_user: Callable[..., Awaitable[Any]],
    sign_in: Callable[..., Awaitable[str]],
) -> None:
    """Пауза кнопкой на экране «Домены рассылки» — тревоги нет; отказ по выключенному ящику —
    тоже нет: правило его не судит."""
    box = await _box(session, Stage.SALES, total=20, bounced=2)
    letter = await _letter(session, box)
    await make_user("admin@pause.example.test", role=UserRole.ADMIN)
    token = await sign_in("admin@pause.example.test")

    off = await client.post(
        f"/api/senders/{box.id}/disable",
        json={"reason": "выключено вручную"},
        headers=bearer(token),
    )
    later = await _deliver(client, signed, [_event("bounce", letter, "sg-d1")])

    assert off.status_code == 200, off.text
    assert later.json()["paused"] == []
    assert told == []


async def test_two_batches_at_once_pause_the_box_once() -> None:
    """Две пачки событий рядом: обе прочли ящик пишущим, обе решили «пауза». Паузу ставит одна —
    вторая ждёт замка строки и видит ящик уже стоящим; тревога одна."""
    async with committed_sessions() as factory:
        async with factory() as session:
            box = await sender(session, "race@mail-sales.example.test", Stage.SALES)
            await session.commit()
        barrier = asyncio.Barrier(2)

        async def pause(tag: str) -> bool:
            async with factory() as session:
                found = await session.get(SenderModel, box.id)
                assert found is not None
                assert found.enabled
                await asyncio.wait_for(barrier.wait(), timeout=MEETING)
                paused = await autopause.pause_once(session, found, f"пауза {tag}", now=NOW)
                await session.commit()
                return paused

        results = await asyncio.gather(pause("а"), pause("б"))
        async with factory() as session:
            after = await session.get(SenderModel, box.id)

    assert sorted(results) == [False, True]
    assert after is not None
    assert not after.enabled
