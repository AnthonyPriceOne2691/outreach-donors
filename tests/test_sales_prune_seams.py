"""Чистка (`prune --probes`, `prune --test-traces`) и проба продаж — ревью стыков (E2) и
решение владельца.

Передача лида (`sales_handoffs`) несёт номер сделки в Kommo: потеряй строку молча — пробную
сделку в Kommo не найти. Правило владельца общего кода:

- `--probes` держит то же правило, что основная чистка (`runs/prune.py`): липовый домен,
  который держит лид продаж, не удаляется и не роняет чистку (было — `IntegrityError`),
  а называется словами: «домен держит лид продаж №N — его убирает `prune --test-traces`»;
- пробу продаж — лид с адресом своего ящика, диалог, письма, ответы, передачу — целиком
  убирает только `--test-traces`. Номер сделки Kommo — в плане и в журнале; сделку в Kommo
  чистка не трогает: удалить её может только человек, по номеру из плана.
"""

from __future__ import annotations

import pytest
from backend.cli.main import build_parser
from backend.cli.prune import run_prune
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.donors.probe import make_probe
from backend.features.letters.sending import Sending
from backend.features.outreach.own_inboxes import OwnInboxes
from backend.features.runs.prune import apply_prune, plan_prune
from backend.features.sales import queue
from backend.features.sales.domain_hold import kept_words
from backend.features.sales.models import SalesHandoffModel, SalesLeadModel, SalesThreadModel
from backend.features.sales.trials import TrialLead, TrialTrace, remove_trials
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_probe_donor import _thresholds
from tests.test_sales_handoff_rows import answer
from tests.test_sales_send import _transports

#: Свой ящик из предохранителя — адрес пробного лида; номера пробных сделок — выдуманные.
MINE = "checker@ours.example.test"
DEAL = 7351
REAL_DEAL = 7353
FAKE = "acme.invalid"


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    found = await w.world(session, monkeypatch)
    monkeypatch.setattr(outreach_cfg, "ALLOWED_RECIPIENTS", (MINE,))
    return found


async def _trial(
    session: AsyncSession,
    world: w.World,
    email: str,
    *,
    host: str | None = None,
    send: bool = True,
    deal: int | None = DEAL,
) -> SalesHandoffModel:
    """Лид продаж: первое письмо собрано (и ушло), лид ответил и захотел говорить — сделка в Kommo."""
    lead = await w.lead(session, world.hypothesis_id, email, host=host, name="Тест Пробы")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=10)
    letter = await session.scalar(
        select(MessageModel)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .where(SalesThreadModel.lead_id == lead.id)
    )
    assert letter is not None
    if send:
        await Sending(session, _transports(), now=w.NOW).send(letter.id)
    thread = await session.get(ThreadModel, letter.thread_id)
    assert thread is not None
    await answer(session, thread, letter, "Давайте созвонимся.", at=w.NOW, sender=email)
    handoff = SalesHandoffModel(thread_id=thread.id, lead_id=lead.id, kommo_lead_id=deal)
    session.add(handoff)
    await session.flush()
    return handoff


async def _left(session: AsyncSession) -> tuple[int, int, int, list[int | None]]:
    """Сколько осталось диалогов, писем, ответов и какие номера сделок."""
    threads = await session.scalar(select(func.count()).select_from(ThreadModel))
    letters = await session.scalar(select(func.count()).select_from(MessageModel))
    replies = await session.scalar(select(func.count()).select_from(ReplyModel))
    deals = list(
        await session.scalars(
            select(SalesHandoffModel.kommo_lead_id).order_by(SalesHandoffModel.id)
        )
    )
    return int(threads or 0), int(letters or 0), int(replies or 0), deals


def _kept(lead_id: int) -> str:
    return f"домен держит лид продаж №{lead_id} — его убирает `prune --test-traces`"


def _args(*flags: str) -> object:
    return build_parser().parse_args(["prune", *flags])


# --- `--probes`: домен, который держит лид продаж, остаётся и назван ----------------------------


async def test_probe_cleanup_keeps_a_fake_domain_held_by_a_sales_lead(
    session: AsyncSession, world: w.World
) -> None:
    """Лид продаж на липовом домене держит домен, как у основной чистки: домена нет в плане
    удаления, он назван оставленным, чистка идёт без `IntegrityError`, передача цела."""
    handoff = await _trial(session, world, f"jane@{FAKE}", send=False)

    plan = await plan_prune(session, run_ids=[], probes=True)
    await apply_prune(session, plan, author="тест")

    assert plan.probes is not None
    assert plan.probes.domains == []
    assert plan.probes.kept == {FAKE: _kept(handoff.lead_id)}
    assert await _left(session) == (1, 1, 1, [DEAL])
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.DATA_PRUNED)
    )
    assert journal is not None
    assert journal.details is not None
    assert journal.details["оставлено"] == {FAKE: _kept(handoff.lead_id)}


async def test_probe_cleanup_prints_the_held_domain_and_its_lead(
    session: AsyncSession, world: w.World, capsys: pytest.CaptureFixture[str]
) -> None:
    handoff = await _trial(session, world, f"jane@{FAKE}", send=False)

    code = await run_prune(session, _args("--probes"))  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert code == 0
    assert "Липовые домены: 0" in out
    assert f"оставлен {FAKE}: {_kept(handoff.lead_id)}" in out


async def test_donor_probe_next_to_a_held_domain_goes_as_before(
    session: AsyncSession, world: w.World
) -> None:
    """Донорская проба — как было: уходит целиком, домен лида продаж рядом остаётся."""
    await _thresholds(session)
    probe = await make_probe(session, host="probe.invalid", email=MINE, author="тест")
    handoff = await _trial(session, world, f"jane@{FAKE}", send=False)

    plan = await plan_prune(session, run_ids=[], probes=True)
    await apply_prune(session, plan, author="тест")

    assert plan.probes is not None
    assert plan.probes.domains == [probe.domain_id]
    assert plan.probes.runs == [probe.run_id]
    assert plan.probes.kept == {FAKE: _kept(handoff.lead_id)}
    hosts = list(
        await session.scalars(select(DomainModel.host).where(DomainModel.host.like("%.invalid")))
    )
    assert hosts == [FAKE]


async def test_lead_that_came_after_the_plan_keeps_its_fake_domain(
    session: AsyncSession, world: w.World
) -> None:
    """Условие повторено в запросе удаления: лид, заведённый между показом и записью,
    держит домен — чистка его не трогает и не падает."""
    late = DomainModel(host="late.invalid")
    session.add(late)
    await session.flush()
    plan = await plan_prune(session, run_ids=[], probes=True)
    assert plan.probes is not None
    assert plan.probes.domains == [late.id]
    await w.lead(session, world.hypothesis_id, "late@late.invalid")

    await apply_prune(session, plan, author="тест")

    assert await session.get(DomainModel, late.id) is not None


# --- `--test-traces`: проба продаж целиком, номер сделки — в плане и журнале ------------------


async def test_trace_cleanup_takes_the_sales_trial_and_names_its_kommo_deal(
    session: AsyncSession, world: w.World
) -> None:
    handoff = await _trial(session, world, MINE)

    plan = await plan_prune(session, run_ids=[], test_traces=True)
    await apply_prune(session, plan, author="тест")

    trials = plan.sales_trials
    assert trials is not None
    [lead] = trials.leads
    assert (lead.lead_id, lead.email, lead.deals) == (handoff.lead_id, MINE, [DEAL])
    assert (lead.threads, lead.letters, lead.replies) == (1, 1, 1)
    assert (trials.threads, trials.handoffs, trials.deals) == (
        [handoff.thread_id],
        [handoff.id],
        [DEAL],
    )
    assert len(trials.campaigns) == 1, "рассылка, где была только проба, уходит"
    assert await session.get(CampaignModel, trials.campaigns[0]) is None
    assert await _left(session) == (0, 0, 0, [])
    assert await session.get(SalesLeadModel, handoff.lead_id) is None
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.DATA_PRUNED)
    )
    assert journal is not None
    assert journal.details is not None
    assert journal.details["проба продаж"]["сделки Kommo — остаются в Kommo"] == [DEAL]
    assert journal.details["проба продаж"]["лиды"] == [handoff.lead_id]


async def test_trace_cleanup_prints_the_trial_and_the_deal_left_in_kommo(
    session: AsyncSession, world: w.World, capsys: pytest.CaptureFixture[str]
) -> None:
    handoff = await _trial(session, world, MINE)

    shown = await run_prune(session, _args("--test-traces"))  # type: ignore[arg-type]
    plan_out = capsys.readouterr().out
    done = await run_prune(session, _args("--test-traces", "--yes"))  # type: ignore[arg-type]
    done_out = capsys.readouterr().out

    assert (shown, done) == (0, 0)
    for out in (plan_out, done_out):
        assert f"лид №{handoff.lead_id} {MINE}" in out
        assert f"сделка Kommo №{DEAL}" in out
        assert "удалить её может только человек" in out
    assert "Уйдёт: лидов 1, диалогов 1, писем 1, ответов 1, передач 1" in plan_out
    assert "Удалено: лидов 1, диалогов 1, писем 1, ответов 1, передач 1" in done_out


async def test_trace_cleanup_leaves_a_real_sales_lead_and_its_deal(
    session: AsyncSession, world: w.World
) -> None:
    real = await _trial(session, world, "jane@real.example.test", deal=REAL_DEAL)
    trial = await _trial(session, world, MINE)

    plan = await plan_prune(session, run_ids=[], test_traces=True)
    await apply_prune(session, plan, author="тест")

    assert plan.sales_trials is not None
    assert [lead.lead_id for lead in plan.sales_trials.leads] == [trial.lead_id]
    assert await _left(session) == (1, 1, 1, [REAL_DEAL])
    assert await session.get(SalesLeadModel, real.lead_id) is not None


async def test_trial_on_a_fake_domain_is_freed_for_the_probe_cleanup(
    session: AsyncSession, world: w.World
) -> None:
    """Слова плана `--probes` верны: после `--test-traces` лида нет, и `--probes` уносит домен."""
    handoff = await _trial(session, world, MINE, host=FAKE)
    first = await plan_prune(session, run_ids=[], probes=True)
    assert first.probes is not None
    assert first.probes.kept == {FAKE: _kept(handoff.lead_id)}

    await apply_prune(
        session, await plan_prune(session, run_ids=[], test_traces=True), author="тест"
    )
    again = await plan_prune(session, run_ids=[], probes=True)
    await apply_prune(session, again, author="тест")

    assert again.probes is not None
    assert again.probes.kept == {}
    assert await session.scalar(select(DomainModel.id).where(DomainModel.host == FAKE)) is None


async def test_what_is_not_the_trial_survives_a_smuggled_plan(
    session: AsyncSession, world: w.World
) -> None:
    """Что бы ни лежало в плане, уходит только проба: лид со своим ящиком и его диалоги."""
    real = await _trial(session, world, "jane@real.example.test", deal=REAL_DEAL)
    letters = list(await session.scalars(select(MessageModel.id)))
    replies = list(await session.scalars(select(ReplyModel.id)))
    trace = TrialTrace(
        inboxes=OwnInboxes(addresses=(MINE,)),
        leads=[TrialLead(real.lead_id, "jane@real.example.test", "real.example.test")],
        threads=[real.thread_id],
        letters=letters,
        replies=replies,
        handoffs=[real.id],
    )

    await remove_trials(session, trace)
    await session.flush()

    assert await _left(session) == (1, 1, 1, [REAL_DEAL])
    assert await session.get(SalesLeadModel, real.lead_id) is not None


async def test_trial_lead_handed_over_after_the_plan_stays_with_its_new_dialog(
    session: AsyncSession, world: w.World
) -> None:
    """Между показом и записью у лида пробы появилась передача в диалоге вне плана: лид
    остаётся (его держит то, чего в плане нет), чистка не падает, новый диалог цел."""
    handoff = await _trial(session, world, MINE)
    plan = await plan_prune(session, run_ids=[], test_traces=True)
    lead = await session.get(SalesLeadModel, handoff.lead_id)
    trial = await session.get(ThreadModel, handoff.thread_id)
    assert lead is not None
    assert trial is not None
    other = ThreadModel(domain_id=lead.domain_id, campaign_id=trial.campaign_id, contact_id=None)
    session.add(other)
    await session.flush()
    session.add(SalesHandoffModel(thread_id=other.id, lead_id=lead.id, kommo_lead_id=REAL_DEAL))
    await session.flush()

    await apply_prune(session, plan, author="тест")

    assert await session.get(SalesLeadModel, handoff.lead_id) is not None
    assert await _left(session) == (1, 0, 0, [REAL_DEAL])


async def test_trial_without_a_deal_names_no_deal(
    session: AsyncSession, world: w.World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kommo не подключён — сделки нет: проба уходит, а номеров сделок в плане нет."""
    await _trial(session, world, MINE, deal=None)

    code = await run_prune(session, _args("--test-traces", "--yes"))  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert code == 0
    assert "Удалено: лидов 1" in out
    assert "сделка Kommo" not in out
    assert await _left(session) == (0, 0, 0, [])


def test_words_name_every_lead_that_holds_the_domain() -> None:
    assert kept_words([3]) == "домен держит лид продаж №3 — его убирает `prune --test-traces`"
    assert kept_words([3, 7]) == (
        "домен держат лиды продаж №3, №7 — их убирает `prune --test-traces`"
    )
