"""Ящик встал на паузу сам — одна тревога человеку (ТЗ: «ящик с ростом отказов сам встаёт на
паузу»), у ящиков всех этапов.

Пауза — мягкие сигналы продаж (окно последних писем, `outreach/health.py`) и прежнее правило
парковки доноров (доля отказов за жизнь ящика, `letters/events._park_if_burning`). Тревога одна
на паузу: следующая пачка по стоящему ящику её не повторяет, включили и ящик снова встал — новая
тревога; пауза человеком — без тревоги. Путь целиком: вебхук событий с подписью платформы,
фиксация, тревога после ответа. Гонка двух пачек — на двух настоящих соединениях со встречей
перед решением (как `tests/test_send_race.py`). Telegram подставной.

Что делать — по правилу, которое поставило паузу (ревью #337): включение не очищает ни окно
продаж, ни долю доноров за всё время ящика, и отказ по старому письму после включения ставит
паузу снова — тревога говорит это словами своего правила, тесты держат и слова, и само поведение.
Замок паузы не держит отправку по ящику до фиксации вебхука — на двух настоящих соединениях.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage, UserRole
from backend.features.core.models.outreach import MessageModel, SenderHealthModel, SenderModel
from backend.features.core.stages import MailPolicy
from backend.features.outreach import autopause, senders
from backend.features.outreach.health import SoftSignals
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_delivery_events import _keypair
from tests.test_delivery_events_repeat import _deliver, _event, _letter
from tests.test_mail_watch import _letter as _queued_letter
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


#: Совет правила мягких сигналов продаж — окно по умолчанию (`SoftSignals.window`).
SALES_ADVICE = autopause.window_advice(SoftSignals().window)

#: Пометка тревоги о паузе: ящик сам не включится — повод есть.
MARK = f"\n{autopause.PAUSED.words}"


def _alarm(email: str, stage: Stage, why: str, advice: str) -> str:
    """Тревога о паузе — и пометка последней строкой (слово Anthony 29.10.2026)."""
    return (
        f"ящик {email} (направление «{stage.value}») встал на паузу сам: {why}. Новых писем с "
        f"него нет, письма его переписок ждут. {advice}{MARK}"
    )


async def _oldest(session: AsyncSession, box: SenderModel) -> int:
    """Самое старое письмо ящика — оно ушло в отказ задолго до паузы."""
    found = await session.scalar(
        select(MessageModel.id)
        .where(MessageModel.sender_id == box.id)
        .order_by(MessageModel.sent_at)
    )
    assert found is not None
    return int(found)


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
    assert told == [_alarm(box.email, Stage.SALES, why, SALES_ADVICE)]


async def test_switched_on_and_paused_again_is_a_new_alarm(
    client: AsyncClient, session: AsyncSession, signed: Any, told: list[str]
) -> None:
    """Совет продаж сбывается: включение окна не очищает, и отказ по старому письму, пока
    прежние отказы в окне, снова ставит ящик на паузу — новая тревога с тем же советом."""
    box = await _box(session, Stage.SALES, total=20, bounced=2)
    letter = await _letter(session, box)
    await _deliver(client, signed, [_event("bounce", letter, "sg-b1")])

    senders.enable(box, now=NOW)
    await session.commit()
    again = await _deliver(client, signed, [_event("bounce", await _oldest(session, box), "sg-b2")])

    assert again.json()["paused"] == [box.email]
    assert len(told) == 2
    assert all(text.startswith(f"ящик {box.email} ") for text in told)
    assert all(text.endswith(f"{SALES_ADVICE}{MARK}") for text in told)


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
    assert told == [_alarm(box.email, Stage.DONORS, why, autopause.LIFETIME_ADVICE)]


async def test_a_donor_box_switched_on_is_parked_again_by_an_old_letter(
    client: AsyncClient, session: AsyncSession, signed: Any, told: list[str]
) -> None:
    """Совет доноров сбывается (ревью #337): доля — за всё время ящика, включение её не обнуляет,
    и первый же отказ, даже по письму, ушедшему задолго до паузы, ставит ящик на паузу снова."""
    box = await _box(session, Stage.DONORS, total=60, bounced=3)
    await _deliver(client, signed, [_event("bounce", await _letter(session, box), "sg-e1")])

    senders.enable(box, now=NOW)
    await session.commit()
    again = await _deliver(client, signed, [_event("bounce", await _oldest(session, box), "sg-e2")])

    assert again.json()["paused"] == [box.email]
    assert len(told) == 2
    assert all(text.endswith(f"{autopause.LIFETIME_ADVICE}{MARK}") for text in told)


def test_the_advice_is_the_one_of_the_rule_that_paused() -> None:
    """Что делать — словами правила: у продаж окно последних писем (разгон заново, а окно то
    же), у доноров доля за всё время ящика — «Включить заново» без разбора ставит паузу снова,
    и обещать «разгон начнётся заново» там нельзя."""
    assert SALES_ADVICE.startswith(autopause.REVIEW)
    assert "«Включить заново»: разгон начнётся заново" in SALES_ADVICE
    assert "Окно последних 50 писем ящика включение не очищает" in SALES_ADVICE
    assert "даже по старому письму, снова поставят ящик на паузу" in SALES_ADVICE

    assert autopause.LIFETIME_ADVICE.startswith(autopause.REVIEW)
    assert "за всё время ящика" in autopause.LIFETIME_ADVICE
    assert "«Включить заново» на экране «Домены рассылки» её не обнуляет" in (
        autopause.LIFETIME_ADVICE
    )
    assert "первый же отказ, даже по старому письму, снова поставит ящик на паузу" in (
        autopause.LIFETIME_ADVICE
    )
    assert "разгон начнётся" not in autopause.LIFETIME_ADVICE


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


async def test_a_pause_does_not_hold_the_sending_of_its_box_until_the_webhook_commits() -> None:
    """Замок паузы — `FOR NO KEY UPDATE` (ревью #337): пока транзакция вебхука открыта, письмо
    получает этот ящик при отправке и журнал здоровья — строку о нём (проверки внешних ключей,
    `FOR KEY SHARE`) без ожидания. С `FOR UPDATE` оба ждали бы фиксации вебхука — здесь упали бы
    по таймауту замка."""
    async with committed_sessions() as factory:
        async with factory() as session:
            box = await sender(session, "lock@mail-sales.example.test", Stage.SALES)
            await _queued_letter(session, Stage.SALES, None, 1, status=MessageStatus.QUEUED)
            await session.commit()
            letter = await session.scalar(select(MessageModel.id))
        async with factory() as webhook, factory() as sending:
            found = await webhook.get(SenderModel, box.id)
            assert found is not None
            assert await autopause.pause_once(webhook, found, "пауза", now=NOW)
            await webhook.flush()  # пауза записана, транзакция вебхука идёт дальше

            await sending.execute(text("SET LOCAL lock_timeout = '2s'"))
            await sending.execute(
                update(MessageModel).where(MessageModel.id == letter).values(sender_id=box.id)
            )
            sending.add(
                SenderHealthModel(sender_id=box.id, kind="deferred", detail="позже", at=NOW)
            )
            await sending.commit()
            await webhook.commit()

        async with factory() as session:
            after = await session.get(SenderModel, box.id)
            taken = await session.scalar(select(MessageModel.sender_id))

    assert after is not None
    assert not after.enabled
    assert taken == box.id
