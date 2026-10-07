"""Липовый донор на настоящей базе: заводится в зоне .invalid, сборка видит только его,
чистка `--probes` уносит его целиком — с письмами, перепиской и ответами.

Адреса выдуманные (`*.example.test`), предохранитель подменяется в каждом тесте, где он
важен: тест не должен зависеть от того, что лежит в `.env` машины.
"""

from __future__ import annotations

import pytest
from backend.cli.main import build_parser
from backend.cli.probe_donor import EXIT_OK, EXIT_REFUSED, run_probe_donor
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    AuditAction,
    DonorStatus,
    MessageStatus,
    ReplyKind,
    RunStatus,
    Stage,
)
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.donors.probe import (
    ProbeError,
    make_probe,
    probe_trace,
    remove_probes,
)
from backend.features.letters.repository import LetterRepository
from backend.features.runs.prune import apply_prune, plan_prune
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

MINE = "checker@ours.example.test"


@pytest.fixture(autouse=True)
def _allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Предохранитель как на проде в первые дни: только свои ящики."""
    monkeypatch.setattr(outreach_cfg, "ALLOWED_RECIPIENTS", (MINE, "@team.example.test"))


async def _thresholds(session: AsyncSession) -> None:
    await RunRepository(session).create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )


async def _count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


# --- заведение -------------------------------------------------------------------------------


async def test_probe_is_an_accepted_donor_with_own_address_and_a_stopped_run(
    session: AsyncSession,
) -> None:
    await _thresholds(session)

    probe = await make_probe(session, host="probe.invalid", email=MINE, author="тест")

    domain = await session.get(DomainModel, probe.domain_id)
    donor = await session.get(DonorModel, probe.donor_id)
    run = await session.get(RunModel, probe.run_id)
    assert domain is not None
    assert domain.host == "probe.invalid"
    assert donor is not None
    assert (donor.status, donor.review) == (DonorStatus.SUITABLE, "accepted")
    assert run is not None
    # Остановлен, а не завершён: смета берёт историю только завершённых.
    assert run.status is RunStatus.STOPPED
    assert run.candidates == {
        "hosts": ["probe.invalid"],
        "found_by": {"probe.invalid": ["проверка цепочки"]},
    }
    queue = await session.scalars(
        select(RunCandidateModel.status).where(RunCandidateModel.run_id == run.id)
    )
    assert list(queue.all()) == ["accepted"]
    addresses = await session.scalars(
        select(ContactModel.email).where(ContactModel.domain_id == probe.domain_id)
    )
    assert list(addresses.all()) == [MINE]
    journal = await session.scalar(
        select(AuditLogModel.details).where(AuditLogModel.action == AuditAction.PROBE_CREATED)
    )
    assert journal == {"домен": "probe.invalid", "адрес": MINE, "прогон": run.id, "кто": "тест"}


async def test_the_build_by_the_probe_run_sees_only_the_probe(session: AsyncSession) -> None:
    """Принятый настоящий донор с адресом в рассылку по проверочному прогону не попадает:
    письмо уходит только на ящик проверяющего."""
    await _thresholds(session)
    await make_donor(session, "real.example.test", email="editor@real.example.test")
    probe = await make_probe(session, host="probe.invalid", email=MINE, author="тест")

    picked = await LetterRepository(session).candidates(
        Stage.DONORS, limit=10, run_ids=[probe.run_id]
    )

    assert [(c.host, c.email) for c in picked] == [("probe.invalid", MINE)]


async def test_a_second_probe_call_doubles_nothing(session: AsyncSession) -> None:
    await _thresholds(session)
    first = await make_probe(session, host="probe.invalid", email=MINE, author="тест")
    again = await make_probe(
        session, host="Probe.Invalid ", email=f" {MINE.upper()}", author="тест"
    )

    assert (first.created, again.created) == (True, False)
    assert (again.run_id, again.domain_id) == (first.run_id, first.domain_id)
    assert await _count(session, RunModel) == 1
    assert await _count(session, ContactModel) == 1


async def test_a_whole_allowed_domain_is_enough(session: AsyncSession) -> None:
    await _thresholds(session)
    probe = await make_probe(
        session, host="probe.invalid", email="anyone@team.example.test", author="тест"
    )
    assert probe.email == "anyone@team.example.test"


@pytest.mark.parametrize(
    ("host", "email", "says"),
    [
        ("donor.example.test", MINE, "не липовый домен"),
        ("invalid", MINE, "не липовый домен"),
        ("probe.invalid", "not-an-address", "не адрес почты"),
        ("probe.invalid", "stranger@elsewhere.example.test", "нет в списке разрешённых"),
    ],
)
async def test_refusals_name_the_reason(
    session: AsyncSession, host: str, email: str, says: str
) -> None:
    await _thresholds(session)
    with pytest.raises(ProbeError, match=says):
        await make_probe(session, host=host, email=email, author="тест")
    assert await _count(session, DomainModel) == 0


async def test_no_thresholds_no_probe(session: AsyncSession) -> None:
    with pytest.raises(ProbeError, match="Порогов ещё нет"):
        await make_probe(session, host="probe.invalid", email=MINE, author="тест")


async def test_console_tells_the_next_steps_and_refuses_in_words(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _thresholds(session)
    code = await run_probe_donor(
        session, build_parser().parse_args(["probe-donor", "--email", MINE])
    )
    out = capsys.readouterr().out
    run_id = await session.scalar(select(RunModel.id))
    assert code == EXIT_OK
    assert f"--runs {run_id} --limit 1" in out
    assert "outreach prune --probes" in out

    code = await run_probe_donor(
        session, build_parser().parse_args(["probe-donor", "--email", MINE, "--host", "x.com"])
    )
    assert code == EXIT_REFUSED
    assert "Липовый донор не заведён" in capsys.readouterr().out


# --- чистка ----------------------------------------------------------------------------------


async def _letter(
    session: AsyncSession, campaign: CampaignModel, domain_id: int, key: str
) -> MessageModel:
    letter = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain_id,
        step=0,
        status=MessageStatus.SENT,
        subject="Guest article",
        body="Hello",
        idempotency_key=key,
    )
    session.add(letter)
    await session.flush()
    return letter


async def _talked_to(session: AsyncSession) -> tuple[int, int, int, int]:
    """Липовый донор после проверки: письмо, переписка, ответ с ценой; рассылка только
    с ним и рассылка вперемешку с настоящим донором. → (липовый домен, настоящий домен,
    липовая рассылка, смешанная рассылка)."""
    await _thresholds(session)
    probe = await make_probe(session, host="probe.invalid", email=MINE, author="тест")
    real = await make_donor(session, "real.example.test", email="editor@real.example.test")
    only = CampaignModel(stage=Stage.DONORS, name="Проверка цепочки", status="running")
    mixed = CampaignModel(stage=Stage.DONORS, name="Пилот", status="running")
    session.add_all([only, mixed])
    await session.flush()
    letter = await _letter(session, only, probe.domain_id, "probe:1")
    await _letter(session, mixed, probe.domain_id, "probe:2")
    await _letter(session, mixed, real.id, "real:1")
    thread = ThreadModel(domain_id=probe.domain_id, campaign_id=only.id)
    session.add(thread)
    await session.flush()
    letter.thread_id = thread.id
    session.add(
        ReplyModel(
            thread_id=thread.id,
            message_id=letter.id,
            kind=ReplyKind.HUMAN,
            raw_body="Guest post — 150 USD.",
            price_white=150,
            currency="USD",
        )
    )
    await session.flush()
    return probe.domain_id, real.id, only.id, mixed.id


async def test_prune_probes_takes_the_probe_whole_and_leaves_the_real_donor(
    session: AsyncSession,
) -> None:
    probe_id, real_id, only_id, mixed_id = await _talked_to(session)

    plan = await plan_prune(session, run_ids=[], probes=True)
    trace = plan.probes
    assert trace is not None
    assert (trace.domains, trace.letters, trace.threads, trace.replies) == ([probe_id], 2, 1, 1)
    assert trace.campaigns == [only_id]
    assert plan.runs == trace.runs
    assert len(plan.runs) == 1
    await apply_prune(session, plan, author="тест")
    await session.flush()

    assert await session.scalar(select(DomainModel.id).where(DomainModel.id == probe_id)) is None
    assert await session.scalar(select(DomainModel.id).where(DomainModel.id == real_id)) == real_id
    assert await _count(session, ReplyModel) == 0
    assert await _count(session, ThreadModel) == 0
    letters = await session.scalars(select(MessageModel.idempotency_key))
    assert list(letters.all()) == ["real:1"]
    campaigns = await session.scalars(select(CampaignModel.id))
    assert list(campaigns.all()) == [mixed_id]
    assert await _count(session, RunModel) == 0
    target = await session.scalar(
        select(AuditLogModel.target).where(AuditLogModel.action == AuditAction.DATA_PRUNED)
    )
    assert target == f"run:{plan.runs[0]}, probes:1"


@pytest.mark.parametrize(
    ("before_plan", "listed"), [(True, False), (False, True)], ids=["до плана", "после плана"]
)
async def test_a_campaign_with_a_real_thread_stays(
    session: AsyncSession, before_plan: bool, listed: bool
) -> None:
    """Рассылка уходит, только если пуста: переписка с настоящим доменом ушла бы
    с ней каскадом — вместе с ответами. Проверяют и план, и само удаление."""
    _, real_id, only_id, _ = await _talked_to(session)
    real = ThreadModel(domain_id=real_id, campaign_id=only_id)

    if before_plan:
        session.add(real)
        await session.flush()
    plan = await plan_prune(session, run_ids=[], probes=True)
    assert plan.probes is not None
    assert (only_id in plan.probes.campaigns) is listed
    if not before_plan:
        session.add(real)
        await session.flush()
    await apply_prune(session, plan, author="тест")
    await session.flush()

    campaign = await session.scalar(select(CampaignModel.id).where(CampaignModel.id == only_id))
    assert campaign == only_id
    threads = await session.scalars(select(ThreadModel.domain_id))
    assert list(threads.all()) == [real_id]


async def test_without_probes_the_probe_domain_is_not_touched(session: AsyncSession) -> None:
    probe_id, _, _, _ = await _talked_to(session)
    run_id = await session.scalar(select(RunModel.id))
    assert run_id is not None

    plan = await plan_prune(session, run_ids=[run_id])
    assert (plan.domains, plan.kept, plan.probes) == ([], {}, None)
    await apply_prune(session, plan, author="тест")
    await session.flush()

    assert (
        await session.scalar(select(DomainModel.id).where(DomainModel.id == probe_id)) == probe_id
    )


async def test_a_real_domain_smuggled_into_the_trace_survives(session: AsyncSession) -> None:
    """Зона проверяется в самом запросе удаления: что бы ни лежало в плане,
    настоящий домен этим путём не уходит."""
    _, real_id, _, _ = await _talked_to(session)
    trace = await probe_trace(session)
    trace.domains.append(real_id)

    await remove_probes(session, trace)
    await session.flush()

    assert await session.scalar(select(DomainModel.id).where(DomainModel.id == real_id)) == real_id
    letters = await session.scalars(select(MessageModel.idempotency_key))
    assert list(letters.all()) == ["real:1"]
