"""Одна роль на сайт — сторона продаж после общего PR (#363): письмо снято, лид ждёт, потом пишется.

Сайт бывает донором, рекламодателем и лидом продаж. Первое письмо продаж не уходит, пока у сайта
живое письмо другого направления (`letters/site_role.py`): общая отправка снимает его с причиной,
называющей направление, а диалог продаж оставляет — к нему привязан лид. Здесь — что с этим делает
модуль продаж:

- проход автоотправки: снятое письмо — не отказ (`OtherRoleError`): нет ни «отказа каждому письму
  окна», ни строки «не ушло», второе письмо окна уходит в том же проходе, журнал — одной строкой
  снятия на письмо;
- лид ждёт, пока сайт в работе у другого направления: сборка кнопкой и автосборка письма ему не
  пишут, модель не зовётся, причина — в отчёте сборки и на экране лида;
- письмо другого направления не дошло — лид снова берётся сборкой, и первое письмо пишется заново
  на том же месте (тот же диалог, тот же ключ) и уходит.

База настоящая, почта — нулевой транспорт с памятью, модель — подделка коридора, очередь задач —
подставная. Адреса и тексты выдуманы.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import pytest
from backend.config import sales as sales_cfg
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import attempts
from backend.features.sales import (
    autobuild,
    autosend,
    autosend_jobs,
    browse,
    elsewhere,
    queue,
)
from backend.features.sales.autosend import Pass, Stop
from backend.features.sales.autosend_jobs import Watch
from backend.features.sales.models import LeadStatus, SalesThreadModel
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_autobuild import _AutoJobs
from tests.test_sales_send import _seen, _transports

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = w.NOW
MINUTE = timedelta(minutes=1)
JANE = "jane@acme.example.test"
IVAN = "ivan@gamma.example.test"
#: Ещё два лида того же сайта, что у Jane: новый (очистка не прошла) и с ушедшим письмом.
OLGA = "olga@acme.example.test"
MARK = "mark@acme.example.test"
ACME = "acme.example.test"
SELLER = "seller@ours.example.test"


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Тревоги прохода — списком; Telegram их принимает."""
    said: list[str] = []

    async def alert(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(autosend_jobs, "send_alert", alert)
    return said


async def _queued(session: AsyncSession, world: w.World, *emails: str) -> list[MessageModel]:
    """Лиды гипотезы и их первые письма — сборкой продаж, по порядку адресов."""
    for email in emails:
        await w.lead(session, world.hypothesis_id, email, name=email.split("@")[0].title())
    await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=len(emails)
    )
    rows = await session.scalars(
        select(MessageModel)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .order_by(MessageModel.id)
    )
    return list(rows)


async def _other(
    session: AsyncSession,
    domain_id: int,
    stage: Stage = Stage.DONORS,
    status: MessageStatus = MessageStatus.QUEUED,
) -> MessageModel:
    """Письмо сайту в другой роли — донору или рекламодателю, без переписки."""
    campaign = CampaignModel(stage=stage, name=f"Другая роль: {stage.value}", status="running")
    session.add(campaign)
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain_id,
        step=0,
        status=status,
        subject="Hello",
        body="Hello",
        idempotency_key=f"{stage.value}:other-{domain_id}:{campaign.id}",
    )
    session.add(letter)
    await session.flush()
    return letter


def _withdrawn_words(letter: MessageModel, stage: Stage, host: str) -> str:
    """Слова снятия общей отправки (`site_role.check`) — целиком."""
    return (
        f"Письмо №{letter.id} снято: {attempts.elsewhere_reason(stage)} ({host}) — одному сайту "
        "письма в двух ролях не уходят. Когда письмо другого направления не дойдёт, сборка "
        "напишет сайту заново"
    )


def _on_test_base(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проход `tick` — на тестовой сессии и нулевой почте; тумблеры включены, предохранитель снят."""
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    source = _transports()

    async def work(now: datetime) -> Pass:
        return await autosend.run(session, source, now=now)

    monkeypatch.setattr(autosend_jobs, "_pass", work)


# --- проход автоотправки: снятое — не отказ ------------------------------------------------------


async def test_a_letter_withdrawn_at_sending_is_no_refusal_and_the_next_letter_goes(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо Jane — старшее в окне, а её сайт в работе у доноров: общая отправка снимает его.
    Проход не считает это отказом — ни «отказа», ни остановки: письмо Ivan уходит в том же
    проходе с того же ящика (ящик ничего не отдал). Диалог продаж Jane остался — к нему
    привязан лид."""
    jane, ivan = await _queued(session, world, JANE, IVAN)
    await _other(session, jane.domain_id)
    source = _transports()

    found = await autosend.run(session, source, now=NOW)

    assert (found.stop, found.stopped, found.refused) == (None, None, {})
    assert found.sent == {ivan.id: w.SALES_BOX}
    assert found.withdrawn == {jane.id: _withdrawn_words(jane, Stage.DONORS, ACME)}
    assert [outgoing.to for outgoing in _seen(source)] == [IVAN]
    await session.refresh(jane)
    assert (jane.status, jane.failure_reason) == (
        MessageStatus.STOPPED,
        attempts.elsewhere_reason(Stage.DONORS),
    )
    link = await session.scalar(
        select(SalesThreadModel).where(SalesThreadModel.thread_id == jane.thread_id)
    )
    assert link is not None


async def test_a_window_of_withdrawn_letters_is_no_alarm_and_each_is_said_once(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    told: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Каждое письмо окна снято при отправке — это не «отказ каждому письму окна»: тревоги нет и
    через 20 минут, журнал говорит о каждом письме одной строкой словами снятия, а «не ушло» и
    «стоит» не пишет. Письма в очереди больше нет — следующий проход видит пустую очередь."""
    caplog.set_level(logging.INFO, logger="backend.features.sales.autosend_jobs")
    jane, ivan = await _queued(session, world, JANE, IVAN)
    await _other(session, jane.domain_id)
    await _other(session, ivan.domain_id, Stage.ADVERTISERS, MessageStatus.SENT)
    _on_test_base(session, monkeypatch)
    watch = Watch()

    for minutes in (0, 5, 15, 20):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)

    lines = [r.getMessage() for r in caplog.records if r.name.endswith("autosend_jobs")]
    assert told == []
    assert lines == [
        f"продажи: автоотправка — {_withdrawn_words(jane, Stage.DONORS, ACME)}",
        "продажи: автоотправка — "
        + _withdrawn_words(ivan, Stage.ADVERTISERS, "gamma.example.test"),
        "продажи: автоотправка стоит — в очереди продаж писем нет — их собирает сборка очереди",
    ]
    assert (watch.said, watch.refused, watch.told) == (Stop.EMPTY, {}, None)


async def test_a_letter_refused_before_and_withdrawn_now_leaves_the_pass_memory(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Письмо получило отказ по письму (здесь — подстановка без значения), проход его запомнил;
    потом его сняли при отправке — из памяти прохода оно уходит: в очереди его больше нет."""
    [jane] = await _queued(session, world, JANE)
    _on_test_base(session, monkeypatch)
    watch = Watch()
    jane.body = (jane.body or "") + " {{name}}"
    await session.flush()
    await autosend_jobs.tick(now=NOW, watch=watch)
    refused = dict(watch.refused)
    jane.body = (jane.body or "").removesuffix(" {{name}}")
    await _other(session, jane.domain_id)
    await autosend_jobs.tick(now=NOW + MINUTE, watch=watch)

    assert list(refused) == [jane.id]
    assert (watch.refused, watch.bounced) == ({}, {})


# --- лид ждёт, пока сайт в работе у другого направления ----------------------------------------


async def test_while_the_site_is_in_work_elsewhere_the_lead_waits_in_words_without_the_model(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо Jane снято при отправке. Пока письмо доноров живо, лид ждёт: сборка кнопкой письма
    не пишет и модель не зовёт — причина в отчёте; автосборка его не берёт (гипотеза — не
    кандидат), лид без письма — один, а сборка напишет — ноль; на экране лида — почему он ждёт."""
    [jane] = await _queued(session, world, JANE)
    await _other(session, jane.domain_id)
    await autosend.run(session, _transports(), now=NOW)
    rewriter = w.CorridorRewriter()

    report = await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=5)
    found = await autobuild.plan(session, _AutoJobs(), now=NOW)
    state = await queue.state(session, world.hypothesis_id)
    page = await browse.leads(session, browse.LeadFilters())

    words = elsewhere.waiting(Stage.DONORS)
    assert (report.prepared, report.refreshed, dict(report.waiting)) == (0, 0, {words: 1})
    assert rewriter.seen == []
    assert (found.hypothesis_id, found.why) == (None, autobuild.NO_CANDIDATES)
    assert (state.unwritten, state.will_write) == (1, 0)
    assert [row.elsewhere for row in page.rows] == [words]
    await session.refresh(jane)
    assert jane.status is MessageStatus.STOPPED


async def test_once_the_other_letter_bounces_the_lead_is_written_again_in_the_same_place(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо доноров не дошло (отказ доставки) — сайт свободен: экран лида молчит, автосборка
    берёт гипотезу, сборка пишет первое письмо заново на том же месте — тот же номер письма и
    диалог, причина снятия стёрта, — и проход отдаёт его почте."""
    [jane] = await _queued(session, world, JANE)
    donor = await _other(session, jane.domain_id)
    await autosend.run(session, _transports(), now=NOW)
    thread_id = jane.thread_id
    donor.status = MessageStatus.BOUNCED
    await session.flush()
    rewriter = w.CorridorRewriter()

    found = await autobuild.plan(session, _AutoJobs(), now=NOW)
    page = await browse.leads(session, browse.LeadFilters())
    state = await queue.state(session, world.hypothesis_id)
    report = await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=5)
    source = _transports()
    went = await autosend.run(session, source, now=NOW + MINUTE)

    assert found.hypothesis_id == world.hypothesis_id
    assert [row.elsewhere for row in page.rows] == [None]
    assert (state.unwritten, state.will_write) == (1, 1)
    assert (report.prepared, report.refreshed) == (0, 1)
    assert len(rewriter.seen) == 1
    assert went.sent == {jane.id: w.SALES_BOX}
    assert [outgoing.to for outgoing in _seen(source)] == [JANE]
    await session.refresh(jane)
    assert (jane.status, jane.failure_reason, jane.thread_id) == (
        MessageStatus.SENT,
        None,
        thread_id,
    )
    letters = await session.scalar(select(func.count()).select_from(MessageModel))
    assert letters == 2  # письмо продаж и письмо доноров — второго первого письма Jane нет


async def test_a_ready_lead_on_a_site_in_work_elsewhere_gets_no_letter_until_it_is_free(
    session: AsyncSession, world: w.World
) -> None:
    """Лид «готов», письма ещё нет, а сайту ушло письмо рекламодателю: сборка его не пишет (письмо
    отправка сняла бы) — причина в отчёте, модель не звали. Письмо рекламодателю не дошло —
    следующая сборка пишет лиду, как любому."""
    lead = await w.lead(session, world.hypothesis_id, JANE)
    advertiser = await _other(session, lead.domain_id, Stage.ADVERTISERS, MessageStatus.SENT)
    rewriter = w.CorridorRewriter()

    held = await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=5)
    asked = list(rewriter.seen)
    advertiser.status = MessageStatus.BOUNCED
    await session.flush()
    later = await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=5)

    assert (held.prepared, dict(held.waiting)) == (0, {elsewhere.waiting(Stage.ADVERTISERS): 1})
    assert asked == []
    assert later.prepared == 1


async def test_the_leads_screen_names_why_the_lead_waits(
    client: AsyncClient,
    session: AsyncSession,
    world: w.World,
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    """Экран лидов (`GET /api/sales/leads`): у ждущего лида «готов» — слова. Нет слов у соседнего
    «готов» на свободном сайте, у лида того же сайта, чьё письмо уже ушло (разговор идёт), и у
    лида, которого очистка ещё не пропустила: ждёт он не другого направления."""
    [went] = await _queued(session, world, MARK)
    went.status = MessageStatus.SENT
    jane = await w.lead(session, world.hypothesis_id, JANE)
    await w.lead(session, world.hypothesis_id, IVAN)
    await w.lead(session, world.hypothesis_id, OLGA, status=LeadStatus.NEW)
    await _other(session, jane.domain_id, Stage.DONORS, MessageStatus.DELIVERED)
    await make_user(SELLER)

    response = await client.get("/api/sales/leads", headers=bearer(await sign_in(SELLER)))

    assert response.status_code == 200, response.text
    shown = {row["email"]: row["elsewhere"] for row in response.json()["rows"]}
    assert shown == {JANE: elsewhere.waiting(Stage.DONORS), IVAN: None, OLGA: None, MARK: None}
