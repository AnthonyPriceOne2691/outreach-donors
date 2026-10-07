"""Чистка прогонов и пробных ответов на настоящей базе: что уходит, что остаётся и почему.

Домены выдуманные (`*.example.test`). Каждое правило оставления проверяется
отдельным доменом: испорченное условие удаляет домен, который должен был
остаться, и тест краснеет на нём поимённо.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.cli.main import build_parser
from backend.cli.prune import EXIT_OK, EXIT_REFUSED, run_prune
from backend.features.core.domain import (
    AuditAction,
    ContactSource,
    CrawlOutcome,
    ReplyKind,
    RunStatus,
    Stage,
    StopReason,
    SuppressionReason,
    UsageProvider,
)
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel, UsageRecordModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel, ThreadModel
from backend.features.core.models.run import RunCandidateModel, RunModel, RunSettingsModel
from backend.features.runs.prune import (
    DETACHED_ADVERTISERS,
    DETACHED_CAMPAIGNS,
    DETACHED_USAGE,
    KEPT_DECISION,
    KEPT_HISTORY,
    KEPT_OTHER_RUN,
    KEPT_SALES,
    KEPT_STOPLIST,
    PruneRefusedError,
    apply_prune,
    plan_prune,
)
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
)
from sqlalchemy import Connection, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "backend/migrations/versions/e4b1c27a9d53_data_pruned_audit_action.py"


async def _settings(session: AsyncSession) -> RunSettingsModel:
    return await RunRepository(session).create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )


async def _run(
    session: AsyncSession,
    hosts: list[str],
    *,
    queued: Sequence[str] = (),
    status: RunStatus = RunStatus.DONE,
    settings: RunSettingsModel | None = None,
) -> RunModel:
    """Прогон с выдачей `hosts`; `queued` — кто из них стоит в очереди разбора."""
    chosen = settings or await _settings(session)
    run = await RunRepository(session).create_run(
        stage=Stage.DONORS,
        settings_id=chosen.id,
        keywords=["guest post example"],
        country="us",
        status=status,
    )
    run.candidates = {"hosts": hosts}
    for host in queued:
        domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
        assert domain is not None, host
        session.add(RunCandidateModel(run_id=run.id, domain_id=domain.id, status="pending"))
    await session.flush()
    return run


async def _donor(session: AsyncSession, host: str, *, email: str | None = None) -> DomainModel:
    return await make_donor(session, host, email=email, review=None)


async def _hosts(session: AsyncSession) -> set[str]:
    rows = await session.scalars(select(DomainModel.host).execution_options(populate_existing=True))
    return set(rows.all())


async def _count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


# --- что принесли только эти прогоны ---------------------------------------------------------


async def _two_runs(session: AsyncSession) -> tuple[RunModel, RunModel]:
    """Старый прогон с a, b и общим доменом; новый — с общим и своим."""
    await _donor(session, "a.example.test", email="editor@a.example.test")
    await _donor(session, "b.example.test")
    await _donor(session, "shared.example.test")
    await _donor(session, "fresh.example.test")
    old = await _run(
        session,
        ["a.example.test", "b.example.test", "shared.example.test", "gov.example.test"],
        queued=["a.example.test", "b.example.test", "shared.example.test"],
    )
    new = await _run(
        session,
        ["shared.example.test", "fresh.example.test"],
        queued=["fresh.example.test"],
    )
    return old, new


async def test_plan_takes_what_only_the_pruned_run_brought_and_changes_nothing(
    session: AsyncSession,
) -> None:
    old, new = await _two_runs(session)

    plan = await plan_prune(session, run_ids=[old.id])

    assert plan.runs == [old.id]
    assert plan.candidates == 3
    gone = await session.scalars(select(DomainModel.host).where(DomainModel.id.in_(plan.domains)))
    hosts = set(gone.all())
    assert hosts == {"a.example.test", "b.example.test"}
    assert plan.contacts == 1
    assert plan.kept == {KEPT_OTHER_RUN: 1}
    # Показ ничего не трогает.
    assert await _hosts(session) == {
        "a.example.test",
        "b.example.test",
        "shared.example.test",
        "fresh.example.test",
    }
    assert await session.get(RunModel, new.id) is not None


async def test_apply_removes_run_queue_domains_and_addresses_and_keeps_the_spend(
    session: AsyncSession,
) -> None:
    old, new = await _two_runs(session)
    session.add(
        UsageRecordModel(
            system="outreach",
            provider=UsageProvider.AHREFS,
            run_id=old.id,
            operation="batch_metrics",
            units=120,
        )
    )
    await session.flush()

    plan = await plan_prune(session, run_ids=[old.id])
    await apply_prune(session, plan, author="тест")
    await session.flush()

    assert await _hosts(session) == {"shared.example.test", "fresh.example.test"}
    assert await session.scalar(select(RunModel.id).where(RunModel.id == old.id)) is None
    assert await session.scalar(select(RunModel.id).where(RunModel.id == new.id)) == new.id
    queue = await session.scalars(select(RunCandidateModel.run_id))
    assert set(queue.all()) == {new.id}
    addresses = await session.scalars(select(ContactModel.email))
    assert "editor@a.example.test" not in set(addresses.all())
    spend = (
        await session.execute(
            select(UsageRecordModel.run_id, UsageRecordModel.units).execution_options(
                populate_existing=True
            )
        )
    ).all()
    assert [tuple(row) for row in spend] == [(None, 120)]

    journal = (
        await session.scalars(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.DATA_PRUNED)
        )
    ).all()
    assert len(journal) == 1
    assert journal[0].target == f"run:{old.id}"
    details = journal[0].details or {}
    assert (details["доменов"], details["адресов"], details["прогоны"]) == (2, 1, [old.id])
    assert details["без номера прогона"] == {DETACHED_USAGE: 1}
    assert details["кто"] == "тест"


async def test_what_the_run_built_or_found_stays_without_its_number(
    session: AsyncSession,
) -> None:
    """Рассылка, собранная по прогону, рекламодатель, найденный им, и расход остаются:
    отправленное отправлено, потраченное потрачено. Теряется только номер прогона —
    и план называет это до удаления."""
    run = await _run(session, [])
    found = await _donor(session, "advertiser.example.test")
    session.add_all(
        [
            CampaignModel(stage=Stage.DONORS, name="Пилот", status="running", run_id=run.id),
            AdvertiserModel(domain_id=found.id, found_run_id=run.id),
            UsageRecordModel(
                system="outreach",
                provider=UsageProvider.AHREFS,
                run_id=run.id,
                operation="batch_metrics",
                units=120,
            ),
        ]
    )
    await session.flush()

    plan = await plan_prune(session, run_ids=[run.id])
    assert plan.detached == {DETACHED_USAGE: 1, DETACHED_CAMPAIGNS: 1, DETACHED_ADVERTISERS: 1}
    await apply_prune(session, plan, author="тест")
    await session.flush()

    campaigns = (await session.scalars(select(CampaignModel.run_id))).all()
    advertisers = (await session.scalars(select(AdvertiserModel.found_run_id))).all()
    spend = (await session.scalars(select(UsageRecordModel.run_id))).all()
    assert (list(campaigns), list(advertisers), list(spend)) == ([None], [None], [None])


async def test_many_runs_fit_the_journal_and_keep_exact_numbers_in_details(
    session: AsyncSession,
) -> None:
    """Живой прогон 06.10 на копии базы: пятнадцать прогонов не влезли в поле цели
    журнала (64 знака), запись упала — и с ней вся чистка. Номера — в `details`."""
    runs = [await _run(session, []) for _ in range(15)]

    plan = await plan_prune(session, run_ids=[run.id for run in runs])
    await apply_prune(session, plan, author="тест")
    await session.flush()

    entry = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.DATA_PRUNED)
    )
    assert entry is not None
    assert entry.target == "runs:15 replies:0 (номера — в details)"
    assert (entry.details or {})["прогоны"] == sorted(run.id for run in runs)


# --- что держит домен ------------------------------------------------------------------------


async def _decided(session: AsyncSession, domain: DomainModel) -> None:
    domain.human_intent = "sells_placement"


async def _reviewed(session: AsyncSession, domain: DomainModel) -> None:
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    donor.review = "rejected"


async def _priced(session: AsyncSession, domain: DomainModel) -> None:
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    donor.last_price = Decimal("150.00")


async def _seller_answered(session: AsyncSession, domain: DomainModel) -> None:
    domain.seller_answer = "sells"


async def _manual_address(session: AsyncSession, domain: DomainModel) -> None:
    session.add(
        ContactModel(
            domain_id=domain.id, email="owner@kept.example.test", source=ContactSource.MANUAL
        )
    )


async def _stoplisted(session: AsyncSession, domain: DomainModel) -> None:
    session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.UNSUBSCRIBED))


async def _thread(session: AsyncSession, domain: DomainModel) -> None:
    campaign = CampaignModel(stage=Stage.DONORS, name="Переписка", status="running")
    session.add(campaign)
    await session.flush()
    session.add(ThreadModel(domain_id=domain.id, campaign_id=campaign.id))


async def _advertiser(session: AsyncSession, domain: DomainModel) -> None:
    session.add(AdvertiserModel(domain_id=domain.id))


async def _crawled(session: AsyncSession, domain: DomainModel) -> None:
    session.add(
        CrawlRunModel(
            host=domain.host,
            outcome=CrawlOutcome.OK,
            stop_reason=StopReason.EXHAUSTED,
            pages_opened=10,
            articles=8,
        )
    )


async def _sales_lead(session: AsyncSession, domain: DomainModel) -> None:
    """Ревью продаж 06.10: лид ссылается на общую строку домена без каскада, а чистка
    о нём не знала — обещала домен удалить и падала на внешнем ключе целиком."""
    hypothesis = SalesHypothesisModel(name="Гипотеза")
    session.add(hypothesis)
    await session.flush()
    session.add(
        SalesLeadModel(
            hypothesis_id=hypothesis.id,
            domain_id=domain.id,
            email="ceo@kept.example.test",
            source=LeadSource.IMPORT,
            status=LeadStatus.NEW,
        )
    )


Holder = Callable[[AsyncSession, DomainModel], Awaitable[None]]


@pytest.mark.parametrize(
    ("hold", "reason"),
    [
        (_decided, KEPT_DECISION),
        (_reviewed, KEPT_DECISION),
        (_priced, KEPT_DECISION),
        (_seller_answered, KEPT_DECISION),
        (_manual_address, KEPT_DECISION),
        (_stoplisted, KEPT_STOPLIST),
        (_thread, KEPT_HISTORY),
        (_advertiser, KEPT_HISTORY),
        (_crawled, KEPT_HISTORY),
        (_sales_lead, KEPT_SALES),
    ],
    ids=lambda value: getattr(value, "__name__", value),
)
async def test_a_domain_held_by_anything_but_the_run_stays_and_says_why(
    session: AsyncSession, hold: Holder, reason: str
) -> None:
    kept = await _donor(session, "kept.example.test")
    await _donor(session, "gone.example.test")
    await hold(session, kept)
    await session.flush()
    run = await _run(
        session,
        ["kept.example.test", "gone.example.test"],
        queued=["kept.example.test", "gone.example.test"],
    )

    plan = await plan_prune(session, run_ids=[run.id])
    assert plan.kept == {reason: 1}
    await apply_prune(session, plan, author="тест")
    await session.flush()

    assert await _hosts(session) == {"kept.example.test"}


async def test_what_changed_between_the_plan_and_the_delete_is_checked_again(
    session: AsyncSession,
) -> None:
    """План показан, человек думает — а домен тем временем попал в новый прогон или
    в стоп-лист. Удаление проверяет те же условия своим запросом и его не трогает."""
    old, _ = await _two_runs(session)
    plan = await plan_prune(session, run_ids=[old.id])

    await _run(session, ["b.example.test"])
    a = await session.scalar(select(DomainModel).where(DomainModel.host == "a.example.test"))
    assert a is not None
    await _stoplisted(session, a)
    await session.flush()
    await apply_prune(session, plan, author="тест")
    await session.flush()

    assert {"a.example.test", "b.example.test"} <= await _hosts(session)


# --- отказы ----------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.ESTIMATING, RunStatus.RUNNING])
async def test_a_run_still_going_is_refused_by_number(
    session: AsyncSession, status: RunStatus
) -> None:
    run = await _run(session, [], status=status)

    with pytest.raises(PruneRefusedError, match=f"ещё идут: №{run.id}"):
        await plan_prune(session, run_ids=[run.id])


async def test_unknown_numbers_and_an_empty_request_are_refused(session: AsyncSession) -> None:
    with pytest.raises(PruneRefusedError, match="Прогонов нет: №999999"):
        await plan_prune(session, run_ids=[999_999])
    with pytest.raises(PruneRefusedError, match="Ответов нет: №999999"):
        await plan_prune(session, run_ids=[], reply_ids=[999_999])
    with pytest.raises(PruneRefusedError, match="Нечего чистить"):
        await plan_prune(session, run_ids=[])


# --- версии порогов --------------------------------------------------------------------------


async def test_settings_of_the_pruned_run_go_but_shared_and_current_stay(
    session: AsyncSession,
) -> None:
    only_old = await _settings(session)
    shared = await _settings(session)
    current = await _settings(session)
    first = await _run(session, [], settings=only_old)
    second = await _run(session, [], settings=shared)
    await _run(session, [], settings=shared)
    third = await _run(session, [], settings=current)

    plan = await plan_prune(session, run_ids=[first.id, second.id, third.id])
    assert plan.settings == [only_old.id]
    await apply_prune(session, plan, author="тест")
    await session.flush()

    left = await session.scalars(select(RunSettingsModel.id).order_by(RunSettingsModel.id))
    assert list(left.all()) == [shared.id, current.id]


# --- пробные ответы --------------------------------------------------------------------------


def _reply(**fields: object) -> ReplyModel:
    return ReplyModel(
        kind=ReplyKind.HUMAN,
        raw_body="Reply intake loopback check",
        subject="Reply intake loopback check",
        unbound_reason="no_label",
        **fields,
    )


async def test_an_unbound_test_reply_goes(session: AsyncSession) -> None:
    reply = _reply()
    session.add(reply)
    await session.flush()

    plan = await plan_prune(session, run_ids=[], reply_ids=[reply.id])
    assert plan.replies == {reply.id: "Reply intake loopback check"}
    await apply_prune(session, plan, author="тест")
    await session.flush()

    assert await _count(session, ReplyModel) == 0
    journal = await session.scalar(
        select(AuditLogModel.target).where(AuditLogModel.action == AuditAction.DATA_PRUNED)
    )
    assert journal == f"reply:{reply.id}"


async def test_a_reply_seen_after_the_plan_is_not_deleted(session: AsyncSession) -> None:
    reply = _reply()
    session.add(reply)
    await session.flush()
    plan = await plan_prune(session, run_ids=[], reply_ids=[reply.id])

    reply.reviewed_by = "человек"
    reply.reviewed_at = func.now()
    await session.flush()
    await apply_prune(session, plan, author="тест")
    await session.flush()

    assert await _count(session, ReplyModel) == 1


async def test_a_reply_in_a_thread_or_seen_by_a_person_is_refused(session: AsyncSession) -> None:
    domain = await _donor(session, "talks.example.test")
    campaign = CampaignModel(stage=Stage.DONORS, name="Переписка", status="running")
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    in_thread = _reply(thread_id=thread.id)
    seen = _reply(reviewed_by="человек", reviewed_at=func.now())
    session.add_all([in_thread, seen])
    await session.flush()

    with pytest.raises(PruneRefusedError, match=f"№{in_thread.id}, №{seen.id}"):
        await plan_prune(session, run_ids=[], reply_ids=[in_thread.id, seen.id])
    assert await _count(session, ReplyModel) == 2


# --- консоль: показ и запись -----------------------------------------------------------------


async def test_console_shows_without_yes_and_deletes_with_it(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    old, _ = await _two_runs(session)

    shown = await run_prune(session, build_parser().parse_args(["prune", "--runs", str(old.id)]))
    out = capsys.readouterr().out
    assert shown == EXIT_OK
    assert "Уйдёт доменов, которые принесли только эти прогоны: 2" in out
    assert "в базе ничего не изменилось" in out
    assert "a.example.test" in await _hosts(session)

    done = await run_prune(
        session, build_parser().parse_args(["prune", "--runs", str(old.id), "--yes"])
    )
    out = capsys.readouterr().out
    assert done == EXIT_OK
    assert "Удалено доменов, которые принесли только эти прогоны: 2" in out
    assert await _hosts(session) == {"shared.example.test", "fresh.example.test"}


async def test_console_refusal_is_a_sentence_and_a_code(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await run_prune(session, build_parser().parse_args(["prune", "--runs", "999999"]))

    assert code == EXIT_REFUSED
    assert "Чистка не выполнена: Прогонов нет: №999999" in capsys.readouterr().out


def test_numbers_are_parsed_or_refused_by_the_parser() -> None:
    parsed = build_parser().parse_args(["prune", "--runs", "18, 19,20", "--replies", "1"])
    assert (parsed.runs, parsed.replies, parsed.yes) == ([18, 19, 20], [1], False)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["prune", "--runs", "18-24"])


# --- схема -----------------------------------------------------------------------------------


def _migration(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"prune_migration_{path.stem}", path)
    assert spec is not None, path
    assert spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _journal_values(connection: Connection) -> list[str]:
    """Ревизия журнала ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие
    его не видит. `ADD VALUE IF NOT EXISTS` делает повтор безвредным."""
    migration = _migration(JOURNAL)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()
    values = connection.execute(text("SELECT unnest(enum_range(NULL::auditaction))::text"))
    return list(values.scalars())


async def test_journal_values_are_there_once_and_survive_a_rerun(session: AsyncSession) -> None:
    connection = await session.connection()
    values = await connection.run_sync(_journal_values)
    assert (values.count("data_pruned"), values.count("probe_created")) == (1, 1)


#: Откуда чистка удаляет сама. Каскад добавит то, что уходит вместе с ними.
_PRUNED = frozenset({"domains", "runs", "run_settings", "replies", "messages", "campaigns"})

#: Ссылка на удаляемое → что с ней при чистке. «Держит» — правило в `runs/prune.py`
#: (каждое проверено тестом выше); «уходит» — каскадом вместе с удаляемым; «теряет
#: номер» — запись остаётся, план называет их число.
REVIEWED = {
    "advertisers.domain_id → domains CASCADE": "держит: история",
    "contacts.domain_id → domains CASCADE": "уходит с доменом; вписанный руками — держит",
    "donors.domain_id → domains CASCADE": "уходит с доменом; решение и цена — держат",
    "messages.domain_id → domains NO ACTION": "держит: история",
    "run_candidates.domain_id → domains CASCADE": "очередь оставшегося прогона держит",
    "sales_leads.domain_id → domains RESTRICT": "держит: лид продаж",
    "suppressions.domain_id → domains CASCADE": "держит: стоп-лист",
    "threads.domain_id → domains CASCADE": "держит: история",
    "run_candidates.run_id → runs CASCADE": "очередь уходит с прогоном",
    "usage_records.run_id → runs SET NULL": "теряет номер",
    "campaigns.run_id → runs SET NULL": "теряет номер",
    "advertisers.found_run_id → runs SET NULL": "теряет номер",
    "runs.settings_id → run_settings NO ACTION": "версия уходит, только если не ссылаются",
    "messages.answers_reply_id → replies SET NULL": "ответ, на который ответили, не уходит",
    "reply_attachments.reply_id → replies CASCADE": "вложения пробного ответа уходят с ним",
    "agent_drafts.reply_id → replies CASCADE": "черновик пробного ответа уходит с ним",
    "replies.message_id → messages SET NULL": "уходят только липовые письма — с ответами",
    "agent_drafts.sent_message_id → messages SET NULL": "уходят только липовые письма — с ответами",
    "messages.thread_id → threads CASCADE": "уходит только липовая переписка",
    "replies.thread_id → threads CASCADE": "уходит только липовая переписка",
    "messages.campaign_id → campaigns CASCADE": "рассылка уходит только пустой",
    "threads.campaign_id → campaigns CASCADE": "рассылка уходит только пустой",
    # Адрес письма и переписки — всегда адрес их домена (letters/recipients.py,
    # letters/answers.py), а домен держит история.
    "messages.contact_id → contacts SET NULL": "держит: история",
    "threads.contact_id → contacts SET NULL": "держит: история",
    "sales_leads.contact_id → contacts SET NULL": "лид знает адрес по email (sales/models.py)",
}


def _references() -> set[str]:
    """Ссылки на таблицы, из которых чистка удаляет, — сама или каскадом."""
    links = [
        (
            table.name,
            fk.parent.name,
            fk.target_fullname.split(".")[0],
            (fk.ondelete or "NO ACTION").upper(),
        )
        for table in DomainModel.metadata.tables.values()
        for fk in table.foreign_keys
    ]
    pruned = set(_PRUNED)
    grown = True
    while grown:  # каскад уносит и то, что ссылается на удаляемое
        cascaded = {
            source for source, _, target, rule in links if target in pruned and rule == "CASCADE"
        }
        grown = not cascaded <= pruned
        pruned |= cascaded
    return {
        f"{source}.{column} → {target} {rule}"
        for source, column, target, rule in links
        if target in pruned
    }


def test_every_reference_to_what_prune_deletes_is_decided() -> None:
    """Ревью продаж 06.10: лид ссылался на домен без каскада, а чистка о нём не знала.
    Новая ссылка на домены, прогоны, адреса, ответы — вопрос к чистке: держит ли она
    домен, уходит с ним или теряет номер. Тест красный, пока ответ не вписан сюда,
    а «держит» — правилом в `runs/prune.py`."""
    found = _references()
    assert found == set(REVIEWED), (
        f"не разобраны: {sorted(found - set(REVIEWED))}; "
        f"больше нет: {sorted(set(REVIEWED) - found)}"
    )
