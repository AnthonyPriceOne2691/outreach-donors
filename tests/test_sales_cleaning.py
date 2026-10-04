"""Очистка лидов продаж — срез 1.4: дубли, стоп-листы, годность, почта домена,
проверяльщик и расход (A1–A7 на уровне прохода).

Лиды и компании выдуманы, домены — `*.example.test`. База настоящая: правила
читают общие `suppressions`, `threads`, `domains` и свои `sales_leads`,
`sales_stoplist`, а исходы и расход пишутся в `sales_leads` и `usage_records`.
Сети нет: домен адреса принимает почту заглушкой `mail_route`, проверяльщик —
`fixture`, сценарный или Hunter за `httpx.MockTransport`.

Утверждения точные — на статус, код причины и слова: в репозитории мутационный
гейт, и каждое правило обязано ронять свой тест.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from backend.features.contacts.mx import MailRoute
from backend.features.contacts.provider import (
    ProviderBlockedError,
    ProviderError,
    ProviderQuotaError,
)
from backend.features.core.domain import Stage, SuppressionReason, ThreadStatus, UsageProvider
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel, UsageRecordModel
from backend.features.core.models.outreach import CampaignModel, ThreadModel
from backend.features.replies.repository import ReplyRepository
from backend.features.sales import cleaning
from backend.features.sales.cleaning import CleaningReport
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesStoplistModel,
)
from backend.features.sales.verifier import FixtureVerifier, HunterVerifier, Verdict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 10, 4, 9, 30, tzinfo=UTC)
ACME = "acme.example.test"
IVAN = "ivan@acme.example.test"


@dataclass(frozen=True, slots=True)
class Row:
    """Лид, как его видит база после очистки."""

    status: LeadStatus
    reason: str | None = None
    note: str | None = None
    verification: str | None = None
    score: int | None = None
    verified_at: datetime | None = None


#: Прошёл очистку с выдуманным вердиктом: источник вердикта виден в статусе.
READY = Row(LeadStatus.READY, verification="fixture:valid", score=93, verified_at=NOW)
UNTOUCHED = Row(LeadStatus.NEW)


def rejected(reason: RejectionReason, note: str) -> Row:
    return Row(LeadStatus.REJECTED, reason.value, note)


class World:
    """Лиды, компании и чужие сущности одной сессии — без повторов в тестах."""

    def __init__(self, session: AsyncSession, hypothesis: SalesHypothesisModel) -> None:
        self.session = session
        self.hypothesis = hypothesis
        self._domains: dict[str, DomainModel] = {}

    async def domain(self, host: str) -> DomainModel:
        if host not in self._domains:
            domain = DomainModel(host=host)
            self.session.add(domain)
            await self.session.flush()
            self._domains[host] = domain
        return self._domains[host]

    async def lead(
        self,
        email: str,
        host: str | None = None,
        *,
        status: LeadStatus = LeadStatus.NEW,
        hypothesis: SalesHypothesisModel | None = None,
    ) -> SalesLeadModel:
        """Лид `new`; домен компании — домен адреса, если не сказано иное."""
        domain = await self.domain(host or email.rpartition("@")[2])
        lead = SalesLeadModel(
            hypothesis_id=(hypothesis or self.hypothesis).id,
            domain_id=domain.id,
            email=email,
            source=LeadSource.IMPORT,
            status=status,
        )
        self.session.add(lead)
        await self.session.flush()
        return lead

    async def hypothesis_named(self, name: str) -> SalesHypothesisModel:
        other = SalesHypothesisModel(name=name)
        self.session.add(other)
        await self.session.flush()
        return other

    async def thread(self, host: str, status: ThreadStatus, stage: Stage) -> None:
        """Диалог другого направления на домене: доноров или рекламодателей."""
        campaign = CampaignModel(stage=stage, name="Проверка", status="running")
        self.session.add(campaign)
        await self.session.flush()
        domain = await self.domain(host)
        self.session.add(ThreadModel(domain_id=domain.id, campaign_id=campaign.id, status=status))
        await self.session.flush()

    async def suppression(
        self,
        *,
        email: str | None = None,
        host: str | None = None,
        reason: SuppressionReason = SuppressionReason.UNSUBSCRIBED,
        stage: Stage | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        domain_id = None if host is None else (await self.domain(host)).id
        self.session.add(
            SuppressionModel(
                email=email, domain_id=domain_id, reason=reason, stage=stage, expires_at=expires_at
            )
        )
        await self.session.flush()

    async def stoplist(self, *, host: str | None = None, email: str | None = None) -> None:
        self.session.add(SalesStoplistModel(host=host, email=email, created_by="тест"))
        await self.session.flush()

    async def clean(self, **kwargs: object) -> CleaningReport:
        return await cleaning.clean(self.session, FixtureVerifier(), now=NOW, **kwargs)  # type: ignore[arg-type]

    async def rows(self) -> list[Row]:
        """Лиды прямо из базы, по порядку заведения."""
        found = await self.session.execute(
            select(
                SalesLeadModel.status,
                SalesLeadModel.rejection_reason,
                SalesLeadModel.cleaning_note,
                SalesLeadModel.verification_status,
                SalesLeadModel.verification_score,
                SalesLeadModel.verified_at,
            ).order_by(SalesLeadModel.id)
        )
        return [Row(*row) for row in found.tuples()]


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Домен адреса принимает почту: ступень MX проверяется своими тестами."""

    async def route(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.MX

    monkeypatch.setattr(cleaning, "mail_route", route)


@pytest.fixture
async def world(session: AsyncSession) -> World:
    hypothesis = SalesHypothesisModel(name="сайты EN")
    session.add(hypothesis)
    await session.flush()
    return World(session, hypothesis)


def test_every_reason_has_its_words_for_the_report() -> None:
    assert set(cleaning.REASON_LABELS) == set(RejectionReason)
    assert cleaning.REASON_LABELS[RejectionReason.UNDELIVERABLE] == "адрес не существует"


# --- A1: дубли -------------------------------------------------------------------


async def test_a1_second_lead_with_the_same_address_is_a_duplicate_of_the_first(
    world: World,
) -> None:  # A1
    # A1 — пример спеки
    first = await world.lead(IVAN)
    await world.lead(IVAN)
    await world.lead("maria@acme.example.test")

    report = await world.clean()

    assert report == CleaningReport(
        checked=3, ready=2, rejected=Counter({"duplicate": 1}), verified=2, verifier="fixture"
    )
    assert await world.rows() == [
        READY,
        rejected(RejectionReason.DUPLICATE, f"дубль: адрес уже у лида №{first.id}"),
        READY,
    ]


@pytest.mark.parametrize("earlier", [LeadStatus.READY, LeadStatus.REJECTED])
async def test_a1_address_of_an_earlier_lead_is_a_duplicate_whatever_its_hypothesis_and_fate(
    world: World, earlier: LeadStatus
) -> None:  # A1
    # A1 — пример спеки
    """Оригинал — наименьший id с этим адресом, в любой гипотезе и с любой судьбой:
    у него своя причина, и повторять её за ним незачем."""
    other = await world.hypothesis_named("сайты DE")
    original = await world.lead(IVAN, status=earlier, hypothesis=other)
    await world.lead(IVAN)

    report = await world.clean()

    assert report == CleaningReport(
        checked=1, rejected=Counter({"duplicate": 1}), verifier="fixture"
    )
    assert await world.rows() == [
        Row(earlier),
        rejected(RejectionReason.DUPLICATE, f"дубль: адрес уже у лида №{original.id}"),
    ]


# --- A2: домен в работе у другого направления -------------------------------------


@pytest.mark.parametrize(
    ("status", "stage", "busy"),
    [
        (ThreadStatus.OPEN, Stage.DONORS, True),
        (ThreadStatus.REPLIED, Stage.ADVERTISERS, True),
        (ThreadStatus.CLOSED, Stage.DONORS, False),
        (ThreadStatus.UNSUBSCRIBED, Stage.ADVERTISERS, False),
    ],
)
async def test_a2_domain_in_a_running_dialog_of_another_direction_is_busy(
    world: World, status: ThreadStatus, stage: Stage, busy: bool
) -> None:  # A2
    # A2 — пример спеки
    await world.thread(ACME, status, stage)
    await world.lead(IVAN)

    report = await world.clean()

    note = f"домен в работе у другого направления: {ACME}"
    expected = rejected(RejectionReason.OTHER_DIRECTION, note) if busy else READY
    assert await world.rows() == [expected]
    assert (report.ready, report.rejected) == (
        (0, Counter({"other_direction": 1})) if busy else (1, Counter())
    )


# --- A3: отписки и общий стоп-лист -------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "stage", "expires_at", "closed"),
    [
        (SuppressionReason.UNSUBSCRIBED, None, None, True),  # A3 буквально: stage NULL
        (SuppressionReason.UNSUBSCRIBED, Stage.DONORS, None, True),  # отписка через ответ
        (SuppressionReason.COMPLAINED, Stage.ADVERTISERS, None, True),  # жалоба любого этапа
        (SuppressionReason.MANUAL, None, None, True),  # без этапа — любая причина
        (SuppressionReason.MANUAL, Stage.DONORS, None, False),  # правило чужого направления
        (SuppressionReason.SUPPLIER, Stage.ADVERTISERS, None, False),
        (SuppressionReason.UNSUBSCRIBED, None, NOW - timedelta(days=1), False),  # срок вышел
        (SuppressionReason.UNSUBSCRIBED, None, NOW + timedelta(days=1), True),  # ещё держит
    ],
)
async def test_a3_unsubscribed_or_complained_anywhere_closes_the_address(
    world: World,
    reason: SuppressionReason,
    stage: Stage | None,
    expires_at: datetime | None,
    closed: bool,
) -> None:  # A3
    # A3 — пример спеки
    await world.suppression(email=IVAN, reason=reason, stage=stage, expires_at=expires_at)
    await world.lead(IVAN)

    await world.clean()

    note = f"отписка: {IVAN} просил не писать"
    assert await world.rows() == [rejected(RejectionReason.UNSUBSCRIBED, note) if closed else READY]


async def test_a3_an_unsubscribe_written_by_the_reply_pipeline_counts(world: World) -> None:  # A3
    # A3 — пример спеки
    """Приём ответов пишет отписку без этапа (`replies/repository.py`, с #154; до него —
    с этапом кампании, такие строки закрывает тест выше): человек отписался от нас,
    а не от одной рассылки — продажи ему не пишут."""
    await ReplyRepository(world.session).suppress(IVAN)
    await world.lead(IVAN)
    await world.lead("maria@acme.example.test")

    await world.clean()

    assert await world.rows() == [
        rejected(RejectionReason.UNSUBSCRIBED, f"отписка: {IVAN} просил не писать"),
        READY,
    ]


async def test_a3_company_domain_in_the_shared_stoplist_without_a_stage_closes_the_lead(
    world: World,
) -> None:  # A3
    # A3 — пример спеки
    await world.suppression(host=ACME, reason=SuppressionReason.MANUAL)
    await world.suppression(
        host="beta.example.test", reason=SuppressionReason.SUPPLIER, stage=Stage.DONORS
    )
    await world.lead(IVAN)
    await world.lead("olga@beta.example.test")

    await world.clean()

    assert await world.rows() == [
        rejected(RejectionReason.UNSUBSCRIBED, f"отписка: домен {ACME} в общем стоп-листе"),
        READY,
    ]


# --- стоп-лист продаж ----------------------------------------------------------------


async def test_stoplist_closes_the_address_the_company_domain_and_the_mailbox_domain(
    world: World,
) -> None:
    # A8 — пример спеки
    await world.stoplist(email="boss@beta.example.test")
    await world.stoplist(host=ACME)
    await world.stoplist(host="gamma.example.test")
    await world.lead("boss@beta.example.test")
    await world.lead("olga@beta.example.test")  # адрес в списке — не домен
    await world.lead(IVAN)  # домен компании
    await world.lead("ivan@gamma.example.test", "other.example.test")  # домен самого адреса

    report = await world.clean()

    assert await world.rows() == [
        rejected(RejectionReason.STOPLIST, "стоп-лист продаж: адрес boss@beta.example.test"),
        READY,
        rejected(RejectionReason.STOPLIST, f"стоп-лист продаж: домен {ACME}"),
        rejected(RejectionReason.STOPLIST, "стоп-лист продаж: домен gamma.example.test"),
    ]
    assert report.rejected == Counter({"stoplist": 3})


async def test_stoplist_root_of_the_mailbox_domain_counts(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A8 — пример спеки
    """Стоп-лист записан корнем (`host_key`), адрес — на поддомене. В зоне `.test`
    корня по списку публичных суффиксов нет, поэтому сведение здесь подменяется;
    само сведение держат тесты `donors/host`."""
    monkeypatch.setattr(cleaning, "host_key", lambda host: host.removeprefix("mail."))
    await world.stoplist(host=ACME)
    await world.lead("ivan@mail.acme.example.test", "other.example.test")

    await world.clean()

    assert await world.rows() == [
        rejected(RejectionReason.STOPLIST, f"стоп-лист продаж: домен {ACME}")
    ]


# --- годность адреса ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "note"),
    [
        ("privacy@acme.example.test", "чужой отдел: цену за размещение там не называют: privacy@acme.example.test"),
        ("datenschutz@acme.example.test", "чужой отдел: цену за размещение там не называют: datenschutz@acme.example.test"),
        ("noreply@acme.example.test", "ящик не принимает ответов: noreply@acme.example.test"),
        ("max.mustermann@acme.example.test", "заглушка вместо адреса: max.mustermann@acme.example.test"),
        ("info@gmail.com", "ролевой ящик на бесплатной почте — чужой личный, а не роль компании: info@gmail.com"),
        ("hello@yahoo.com", "ролевой ящик на бесплатной почте — чужой личный, а не роль компании: hello@yahoo.com"),
        ("ivan@gmail.com", None),  # личный ящик на бесплатной почте — годен
        ("info@acme.example.test", None),  # ролевой на домене компании — роль компании
    ],
)  # fmt: skip
async def test_unusable_addresses_are_named_by_the_shared_rules_and_the_free_mail_role_rule(
    world: World, email: str, note: str | None
) -> None:
    # A9 — пример спеки
    await world.lead(email, ACME)

    await world.clean()

    assert await world.rows() == [
        READY if note is None else rejected(RejectionReason.UNUSABLE, note)
    ]


# --- порядок правил и границы прохода ---------------------------------------------------


async def test_rules_go_from_the_cheapest_and_the_first_one_names_the_lead(world: World) -> None:
    """Дубль → стоп-лист → отписка → чужой диалог → годность: лид, попавший под
    два правила, назван первым из них."""
    await world.stoplist(host=ACME)
    await world.suppression(email="privacy@acme.example.test")
    await world.suppression(email="legal@beta.example.test")
    await world.thread("beta.example.test", ThreadStatus.OPEN, Stage.DONORS)
    await world.thread("gamma.example.test", ThreadStatus.OPEN, Stage.DONORS)
    first = await world.lead("privacy@acme.example.test")  # стоп-лист и отписка и годность
    await world.lead("privacy@acme.example.test")  # и ещё дубль
    await world.lead("legal@beta.example.test")  # отписка и чужой диалог и годность
    await world.lead("legal@gamma.example.test")  # чужой диалог и годность

    await world.clean()

    assert [(row.reason, row.note) for row in await world.rows()] == [
        ("stoplist", f"стоп-лист продаж: домен {ACME}"),
        ("duplicate", f"дубль: адрес уже у лида №{first.id}"),
        ("unsubscribed", "отписка: legal@beta.example.test просил не писать"),
        ("other_direction", "домен в работе у другого направления: gamma.example.test"),
    ]


async def test_only_new_leads_of_the_chosen_hypothesis_are_cleaned_and_nobody_is_checked_twice(
    world: World,
) -> None:
    other = await world.hypothesis_named("сайты DE")
    await world.lead(IVAN)
    await world.lead("maria@acme.example.test", status=LeadStatus.READY)
    await world.lead("olga@beta.example.test", status=LeadStatus.REJECTED)
    await world.lead("hans@delta.example.test", hypothesis=other)

    chosen = await world.clean(hypothesis_id=world.hypothesis.id)
    assert chosen == CleaningReport(checked=1, ready=1, verified=1, verifier="fixture")
    assert await world.rows() == [READY, Row(LeadStatus.READY), Row(LeadStatus.REJECTED), UNTOUCHED]

    everyone = await world.clean()
    assert everyone == CleaningReport(checked=1, ready=1, verified=1, verifier="fixture")
    assert await world.rows() == [READY, Row(LeadStatus.READY), Row(LeadStatus.REJECTED), READY]

    assert await world.clean() == CleaningReport(verifier="fixture")


async def test_each_batch_is_written_and_committed_on_its_own(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Партия — транзакция: обрыв прохода не теряет уже решённых лидов. Дубль
    через границу партий виден по базе, а не по памяти прохода."""
    monkeypatch.setattr(cleaning, "BATCH", 2)
    commits = 0
    commit = world.session.commit

    async def counting() -> None:
        nonlocal commits
        commits += 1
        await commit()

    monkeypatch.setattr(world.session, "commit", counting)
    first = await world.lead(IVAN)
    for n in range(3):
        await world.lead(f"lead{n}@firm{n}.example.test")
    await world.lead(IVAN)

    report = await world.clean()

    assert commits == 3
    assert report == CleaningReport(
        checked=5, ready=4, rejected=Counter({"duplicate": 1}), verified=4, verifier="fixture"
    )
    assert (await world.rows())[-1] == rejected(
        RejectionReason.DUPLICATE, f"дубль: адрес уже у лида №{first.id}"
    )


# --- A4: почта домена самого адреса --------------------------------------------------


class Scripted:
    """Проверяльщик по сценарию: адрес → вердикт или отказ сервиса; помнит, кого спрашивали."""

    name = "hunter"

    def __init__(self, script: dict[str, Verdict | Exception] | None = None) -> None:
        self.script = script or {}
        self.asked: list[str] = []

    async def verify(self, email: str) -> Verdict:
        self.asked.append(email)
        answer = self.script.get(email, Verdict("valid", 97, units=1))
        if isinstance(answer, Exception):
            raise answer
        return answer


PAID_READY = Row(LeadStatus.READY, verification="hunter:valid", score=97, verified_at=NOW)


async def _usage(session: AsyncSession) -> list[tuple[str, UsageProvider, int | None, str]]:
    rows = await session.execute(
        select(
            UsageRecordModel.operation,
            UsageRecordModel.provider,
            UsageRecordModel.units,
            UsageRecordModel.system,
        ).order_by(UsageRecordModel.id)
    )
    return [tuple(row) for row in rows.tuples()]  # type: ignore[misc]


async def test_a4_domain_of_the_address_decides_and_dead_ones_never_reach_the_verifier(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:  # A4
    # A4 — пример спеки
    routes = {
        "none.example.test": MailRoute.NONE,
        "nullmx.example.test": MailRoute.NULL_MX,
        "silent.example.test": MailRoute.UNKNOWN,
        "a-only.example.test": MailRoute.IMPLICIT,
        "mail.other.example.test": MailRoute.MX,
    }
    asked: list[str] = []

    async def route(host: str, **_kwargs: object) -> MailRoute:
        asked.append(host)
        return routes[host]

    monkeypatch.setattr(cleaning, "mail_route", route)
    verifier = Scripted()
    await world.lead("a@none.example.test")
    await world.lead("b@nullmx.example.test")
    await world.lead("c@silent.example.test")
    await world.lead("d@a-only.example.test")
    await world.lead("e@mail.other.example.test", ACME)  # домен компании — не домен адреса
    await world.lead("f@mail.other.example.test", ACME)  # тот же домен адреса — один запрос DNS

    report = await cleaning.clean(world.session, verifier, now=NOW)

    assert sorted(asked) == sorted(routes)  # каждый домен адреса — один раз, домен компании — нет
    assert sorted(verifier.asked) == [
        "c@silent.example.test",
        "d@a-only.example.test",
        "e@mail.other.example.test",
        "f@mail.other.example.test",
    ]
    no_mail = "домен не принимает почту"
    assert await world.rows() == [
        rejected(RejectionReason.NO_MAIL, f"{no_mail} (нет ни MX, ни A): none.example.test"),
        rejected(RejectionReason.NO_MAIL, f"{no_mail} (нулевой MX): nullmx.example.test"),
        PAID_READY,
        PAID_READY,
        PAID_READY,
        PAID_READY,
    ]
    assert report == CleaningReport(
        checked=6,
        ready=4,
        rejected=Counter({"no_mail": 2}),
        mx_unknown=1,
        verified=4,
        paid_units=4,
        verifier="hunter",
    )


# --- A5–A6: вердикты и отказы проверяльщика на уровне прохода ---------------------------


async def test_a5_fixture_verdicts_reach_the_lead_and_cost_nothing(world: World) -> None:  # A5
    # A5 — пример спеки
    for local in ("bounce", "disposable", "unknown.box", "catchall"):
        await world.lead(f"{local}@acme.example.test")

    report = await world.clean()

    dead = LeadStatus.REJECTED, "undeliverable"
    assert await world.rows() == [
        Row(*dead, "адрес не существует: bounce@acme.example.test", "fixture:invalid", 7, NOW),
        Row(*dead, "одноразовый ящик: disposable@acme.example.test", "fixture:disposable", 11, NOW),
        # «Провайдер не знает» — вердикт, лид готов; порог по нему решает отправка.
        Row(LeadStatus.READY, verification="fixture:unknown", score=48, verified_at=NOW),
        Row(LeadStatus.READY, verification="fixture:accept_all", score=61, verified_at=NOW),
    ]
    assert report == CleaningReport(
        checked=4, ready=2, rejected=Counter({"undeliverable": 2}), verified=4, verifier="fixture"
    )
    assert await _usage(world.session) == []


async def test_a6_service_failure_leaves_the_lead_new_with_the_reason_and_the_next_pass_retries(
    world: World,
) -> None:  # A6
    # A6 — пример спеки
    why = "провайдер не ответил (HTTP 503) — повторим следующей очисткой"
    failing = Scripted({IVAN: ProviderError(why)})
    await world.lead(IVAN)
    await world.lead("maria@acme.example.test")

    report = await cleaning.clean(world.session, failing, now=NOW)

    assert await world.rows() == [
        Row(LeadStatus.NEW, note=f"проверка не выполнена: {why}"),
        PAID_READY,
    ]
    assert report == CleaningReport(
        checked=2, ready=1, unverified=1, verified=1, paid_units=1, verifier="hunter"
    )
    assert await _usage(world.session) == [
        ("sales_verify", UsageProvider.HUNTER, 1, "outreach-donors")
    ]

    healed = Scripted()
    again = await cleaning.clean(world.session, healed, now=NOW)

    assert healed.asked == [IVAN]
    assert again == CleaningReport(checked=1, ready=1, verified=1, paid_units=1, verifier="hunter")
    assert await world.rows() == [PAID_READY, PAID_READY]


@pytest.mark.parametrize(
    "refusal",
    [
        ProviderQuotaError("квота исчерпана: monthly"),
        ProviderBlockedError("учётка закрыта провайдером: x — квота тут ни при чём"),
    ],
)
async def test_a6_quota_or_closed_account_stops_the_paid_part_and_the_rest_wait_new(
    world: World, monkeypatch: pytest.MonkeyPatch, refusal: ProviderError
) -> None:  # A6
    # A6 — пример спеки
    """Повторять квоту и закрытую учётку бессмысленно: платная часть прохода
    останавливается на первом отказе, остальные лиды — всех партий — остаются
    `new` с той же причиной, а бесплатные правила им всё равно применены."""
    monkeypatch.setattr(cleaning, "VERIFY_CONCURRENCY", 1)
    monkeypatch.setattr(cleaning, "BATCH", 2)
    stopped = Scripted({IVAN: refusal})
    await world.lead("privacy@acme.example.test")  # решён правилом — платная часть ни при чём
    await world.lead(IVAN)
    await world.lead("maria@acme.example.test")  # та же партия, после остановки
    await world.lead("olga@beta.example.test")  # следующая партия: провайдера не спрашивают

    report = await cleaning.clean(world.session, stopped, now=NOW)

    assert stopped.asked == [IVAN]
    waiting = Row(LeadStatus.NEW, note=f"проверка не выполнена: {refusal}")
    assert await world.rows() == [
        rejected(
            RejectionReason.UNUSABLE,
            "чужой отдел: цену за размещение там не называют: privacy@acme.example.test",
        ),
        waiting,
        waiting,
        waiting,
    ]
    assert report == CleaningReport(
        checked=4,
        rejected=Counter({"unusable": 1}),
        unverified=3,
        verifier="hunter",
        stopped=str(refusal),
    )
    assert await _usage(world.session) == []


async def test_hunter_verdicts_are_written_with_their_source_and_paid_once_per_batch(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:  # A5, A6
    # A5 — пример спеки
    monkeypatch.setattr(cleaning, "BATCH", 2)
    answers = {
        IVAN: {"data": {"status": "valid", "score": 97}},
        "dead@acme.example.test": {"data": {"status": "invalid", "score": 3}},
        "slow@acme.example.test": None,  # 429 без тела
        "olga@beta.example.test": {"data": {"status": "accept_all", "score": 64}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = answers[request.url.params["email"]]
        return httpx.Response(429) if payload is None else httpx.Response(200, json=payload)

    first = await world.lead(IVAN)
    for email in ("dead@acme.example.test", "slow@acme.example.test", "olga@beta.example.test"):
        await world.lead(email)
    await world.lead(IVAN)  # третья партия — дубль, за него не платят

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await cleaning.clean(world.session, HunterVerifier(http, api_key="k"), now=NOW)

    assert await world.rows() == [
        PAID_READY,
        Row(LeadStatus.REJECTED, "undeliverable", "адрес не существует: dead@acme.example.test", "hunter:invalid", 3, NOW),
        Row(LeadStatus.NEW, note="проверка не выполнена: провайдер не ответил (HTTP 429) — повторим следующей очисткой"),
        Row(LeadStatus.READY, verification="hunter:accept_all", score=64, verified_at=NOW),
        rejected(RejectionReason.DUPLICATE, f"дубль: адрес уже у лида №{first.id}"),
    ]  # fmt: skip
    assert report == CleaningReport(
        checked=5,
        ready=2,
        rejected=Counter({"undeliverable": 1, "duplicate": 1}),
        unverified=1,
        verified=3,
        paid_units=3,
        verifier="hunter",
    )
    paid = ("sales_verify", UsageProvider.HUNTER)
    assert await _usage(world.session) == [
        (*paid, 2, "outreach-donors"),
        (*paid, 1, "outreach-donors"),
    ]
