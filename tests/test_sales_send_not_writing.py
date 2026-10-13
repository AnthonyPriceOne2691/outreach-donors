"""«Кому не пишем» на письмах продаж — решение владельца 13.10 (ревью стыков отправки).

Список «кому не пишем» ТЗ (гос. и учебные зоны, платформы и соцсети, крупные СМИ, справочники)
действует и на письма продаж — одним правилом с доноров и сбора базы (`runs/exclusions.by_name`):
- сборка: лид, чей сайт компании или домен адреса в списке, в очередь не встаёт, модель на него
  не тратится, причина — в итоге сборки;
- письмо, собранное до того, как домен попал в список, не уходит ни проходом автоотправки, ни
  пачкой, ни кнопкой — отказ словами; добивка такого лида останавливает цепочку;
- автосборка не считает такие письма очередью и не ставит сборку гипотезы, у которой без
  письма остались только такие лиды; строка экрана говорит, почему не уйдёт ни одно письмо окна;
- ответ лиду в переписке правило не держит: лид написал сам.

База — настоящая, почта и модель — подставные. Домены из списков — те, что стоят в списках по
умолчанию; адреса и тексты выдуманы.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import timedelta

import pytest
from backend.config import sales as sales_cfg
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import followups
from backend.features.letters.batch import send_queue
from backend.features.letters.sending import NotWritingError, Sending
from backend.features.runs import exclusions
from backend.features.sales import auto_lines, autobuild, autosend, autosend_jobs, not_writing
from backend.features.sales import queue as sales_queue
from backend.features.sales.autosend import Stop
from backend.features.sales.autosend_jobs import Watch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_autobuild import _AutoJobs
from tests.test_sales_send import FIRST_DUE, SECOND_DUE, _first_sent, _seen, _transports
from tests.test_sales_thread_answer import Answered, answered

__all__ = ["answered"]  # фикстура — её видит pytest

JANE = "jane@acme.example.test"
OLGA = "olga@beta.example.test"
#: Сайт компании Джейн — его «добавят в список» после сборки письма.
ACME = "acme.example.test"
MEDIA = "крупным СМИ не пишем"


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


def _listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Список крупных СМИ пополнили сайтом компании Джейн — после того как её письмо собрано
    (`ADVERTISERS_DENY_MEDIA` читают прогон, сбор базы и отправка из одного места)."""
    monkeypatch.setattr(exclusions, "MAJOR_MEDIA", exclusions.MAJOR_MEDIA | {ACME})


async def _built(session: AsyncSession, world: w.World, *emails: str) -> list[MessageModel]:
    """Первые письма лидов — сборкой продаж, по порядку адресов."""
    for email in emails:
        await w.lead(session, world.hypothesis_id, email, name=email.split("@")[0].title())
    await sales_queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=len(emails)
    )
    rows = await session.scalars(select(MessageModel).order_by(MessageModel.id))
    return list(rows)


# --- сборка: лид из списка в очередь не встаёт -------------------------------------------------


@pytest.mark.parametrize(
    ("email", "host", "words"),
    [
        ("desk@forbes.com", None, "крупным СМИ не пишем"),
        ("desk@vk.com", None, "платформам и соцсетям не пишем"),
        ("desk@britannica.com", None, "справочникам и исследованиям не пишем"),
        ("desk@bureau.gov.zz", None, "гос. и учебным сайтам не пишем"),
        # Сайт компании свой, а адрес — на домене из списка: судится и домен адреса, корнем.
        ("jane@desk.forbes.com", ACME, "крупным СМИ не пишем"),
    ],
    ids=["media", "social", "reference", "public-zone", "mailbox-domain"],
)
async def test_a_lead_from_the_list_does_not_enter_the_queue_and_costs_no_model(
    session: AsyncSession, world: w.World, email: str, host: str | None, words: str
) -> None:
    """Мутанты «сборка не спрашивает список» и «судится только сайт компании»: лид из списка
    ждёт со словами общего правила, модель на него не зовётся, письмо соседа собирается."""
    await w.lead(session, world.hypothesis_id, email, host=host)
    await w.lead(session, world.hypothesis_id, OLGA)
    model = w.CorridorRewriter()

    report = await sales_queue.build(session, model, hypothesis_id=world.hypothesis_id, limit=5)

    assert report.waiting == Counter({f"кому не пишем: {words}": 1})
    assert report.prepared == 1
    assert {about.host for about in model.seen} == {"beta.example.test"}


def test_the_rule_is_the_shared_whom_we_do_not_write_rule() -> None:
    """Своей копии классов у продаж нет: слова и домены — общего правила (`by_name`); бесплатная
    почта в списках не стоит, и лид с ней не отсекается."""
    for email, host in [("desk@forbes.com", "forbes.com"), ("jane@gmail.com", ACME)]:
        hosts = not_writing.hosts_of(email, host)
        shared = exclusions.by_name(hosts)
        found = not_writing.excluded(email, host)
        expected = {exclusions.NOT_WRITING_WORDS[why] for why in shared.values()}
        assert ({found.words} if found is not None else set()) == expected


# --- письмо, собранное до списка, не уходит -----------------------------------------------------


async def test_the_button_refuses_a_letter_built_before_the_list_in_words(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мутант «отправка не спрашивает список»: кнопка одного письма — отказ «кому не пишем»
    словами, письмо ждёт в очереди, почта его не видела."""
    [letter] = await _built(session, world, JANE)
    _listed(monkeypatch)
    source = _transports()

    with pytest.raises(NotWritingError) as refused:
        await Sending(session, source, now=w.NOW).send(letter.id)

    assert str(refused.value) == (
        f"Письмо №{letter.id}: кому не пишем: {MEDIA} ({ACME}) — письма продаж этому лиду не уходят"
    )
    assert _seen(source) == []
    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED


async def test_the_batch_counts_a_letter_of_a_listed_lead_and_goes_on(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    letters = await _built(session, world, JANE, OLGA)
    _listed(monkeypatch)
    source = _transports()

    report = await send_queue(session, source, stage=Stage.SALES)

    assert (report.sent, dict(report.refused)) == (1, {"кому не пишем": 1})
    assert [outgoing.to for outgoing in _seen(source)] == [OLGA]
    assert len(letters) == 2


async def test_the_autosend_pass_refuses_it_and_says_why_once(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Проход автоотправки: письмо Джейн — отказ «кому не пишем», письмо Ольги ушло; журнал
    говорит причину целиком один раз, а не на каждом проходе."""
    caplog.set_level(logging.INFO, logger="backend.features.sales.autosend_jobs")
    monkeypatch.setattr(sales_cfg, "AUTOSEND_GAP_MIN", 1)
    letters = await _built(session, world, JANE, OLGA)
    _listed(monkeypatch)
    source = _transports()
    watch = Watch()

    first = await autosend.run(session, source, now=w.NOW)
    await watch.see(first, w.NOW)
    again = await autosend.run(session, source, now=w.NOW + 2 * autosend.gap())
    await watch.see(again, w.NOW + 2 * autosend.gap())

    assert first.refused == {letters[0].id: "кому не пишем"}
    assert first.sent == {letters[1].id: w.SALES_BOX}
    assert again.refused == {letters[0].id: "кому не пишем"}
    jobs = [r.getMessage() for r in caplog.records if r.name == autosend_jobs.__name__]
    journal = [line for line in jobs if line.startswith("продажи: автоотправка — письмо №")]
    assert journal == [
        f"продажи: автоотправка — письмо №{letters[0].id} не ушло: кому не пишем — Письмо "
        f"№{letters[0].id}: кому не пишем: {MEDIA} ({ACME}) — письма продаж этому лиду не уходят"
    ]


async def test_a_followup_of_a_listed_lead_stops_the_chain(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Первое письмо ушло до списка; домен попал в список — добивка не уходит, цепочка
    кончается, как на стоп-листе, и вторая добивка не придёт."""
    source, [first] = await _first_sent(session, world)
    _listed(monkeypatch)

    report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)
    later = await followups.send_due(session, transport=source, limit=5, now=SECOND_DUE)

    assert (report.sent, report.stopped, report.postponed) == (0, 1, 0)
    assert (later.sent, later.stopped) == (0, 0)
    assert len(_seen(source)) == 1
    await session.refresh(first)
    assert first.next_action_at is None


async def test_an_answer_to_a_listed_lead_is_not_held(
    session: AsyncSession, answered: Answered, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мутант «список держит и ответ»: лид написал сам — ответ ему уходит, как у доноров."""
    _listed(monkeypatch)

    outcome = await answered.answer(session)

    assert outcome.sender_email == w.SALES_BOX
    assert len(_seen(answered.source)) == 2


# --- автосборка и строка экрана --------------------------------------------------------------------


async def test_autobuild_does_not_count_a_listed_letter_and_builds_for_the_shortage(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мутант «автосборка считает письмо из списка»: письмо Джейн не уйдёт — в счёт очереди
    оно не идёт, и не хватает на него; пересобирать его сборка тоже не берётся."""
    world.sales_box.daily_cap = 2
    await _built(session, world, JANE, OLGA)
    await w.lead(session, world.hypothesis_id, "ivan@gamma.example.test")
    _listed(monkeypatch)

    found = await autobuild.plan(session, _AutoJobs(), now=w.NOW)
    queued = await autobuild.queued(session)

    assert (found.need, found.have, found.short) == (2, 1, 1)
    assert found.hypothesis_id == world.hypothesis_id
    assert queued.redo == frozenset()


async def test_a_hypothesis_with_only_listed_leads_left_is_no_candidate(
    session: AsyncSession, world: w.World
) -> None:
    """Мутант «кандидат — любой лид без письма»: без письма остались только лиды из списка —
    сборка не возьмёт ни одного, и проход не ставит её впустую. Появился лид, которому пишем, —
    гипотеза снова кандидат."""
    await w.lead(session, world.hypothesis_id, "desk@forbes.com")
    await w.lead(session, world.hypothesis_id, "desk@vk.com")
    jobs = _AutoJobs()

    idle = await autobuild.run(session, jobs, now=w.NOW)
    await w.lead(session, world.hypothesis_id, JANE)
    busy = await autobuild.run(session, jobs, now=w.NOW)

    assert (idle.hypothesis_id, idle.why) == (None, autobuild.NO_CANDIDATES)
    assert busy.hypothesis_id == world.hypothesis_id
    assert len(jobs.enqueued) == 1


async def test_the_screen_says_no_letter_of_the_window_goes_when_their_leads_are_listed(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    letters = await _built(session, world, JANE)
    _listed(monkeypatch)

    line = await auto_lines.autosend_line(session, w.NOW)
    found = await autosend.run(session, _transports(), now=w.NOW)

    assert line.line.endswith(f"Стоит: {auto_lines.CLOSED}.")
    assert "«кому не пишем»" in auto_lines.CLOSED
    assert (found.stop, list(found.refused)) == (Stop.REFUSED_ALL, [letters[0].id])


async def test_an_alarm_when_only_listed_letters_are_in_the_window_names_the_rule(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В окне только письма лидов из списка — проход стоит не по делу, через 15 минут тревога
    словами «кому не пишем» (`Stop.REFUSED_ALL`)."""
    told: list[str] = []

    async def alert(text: str) -> bool:
        told.append(text)
        return True

    monkeypatch.setattr(autosend_jobs, "send_alert", alert)
    await _built(session, world, JANE)
    _listed(monkeypatch)
    watch = Watch()

    for minutes in (0, 16):
        found = await autosend.run(session, _transports(), now=w.NOW)
        await watch.see(found, w.NOW + timedelta(minutes=minutes))

    [alarm] = told
    assert "кому не пишем: 1" in alarm
