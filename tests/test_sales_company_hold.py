"""Компания — в одной гипотезе (решение владельца 14.10): очистка и сбор базы.

Держатель компании — её «готовый» лид, а без них — раньше всех заведённый живой. Держит её лид
другой гипотезы: «готовый» — очистка отклоняет лида кодом дубля и словами с именем гипотезы и
номером держателя; ещё не проверенный — лид ждёт его без платной проверки (правка (а) ревью
соседней сессии). Внутри одной гипотезы людей компании бывает несколько; бесплатная почта
судится только по точному адресу. Сбор не ищет адрес и не платит Hunter за домен с живым лидом
любой гипотезы. Пробуждение ждущих и возврат отклонённых — `test_sales_company_wake.py`.

База настоящая, сеть — подделки: DNS — таблицей, проверка адресов — выдуманная (со счётчиком
вопросов), сайты лестницы — `httpx.MockTransport`, платная ступень — подделка со счётчиком.
Домены выдуманы (`*.example.test`), утверждения точные — на статус, код и слова.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from backend.config import sales as sales_cfg
from backend.features.contacts.mx import MailRoute
from backend.features.contacts.provider import Quota
from backend.features.contacts.quality import Candidate
from backend.features.contacts.search import search_contacts
from backend.features.core.domain import ContactSource, ContactStatus, MessageStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.sales import (
    cleaning,
    company_hold,
    hypotheses,
    intake,
    queue,
    segment_leads,
    segment_report,
    segment_sites,
    stoplist_leads,
)
from backend.features.sales.clean_report import CleaningReport
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesStoplistModel,
)
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.segment_queue import SegmentContactQueue
from backend.features.sales.segment_sites import NOT_SEARCHED, Outcome, OutcomeKind, SitesAsk
from backend.features.sales.verifier import FixtureVerifier, HunterVerifier, Verdict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_segment_run as run_test
from tests import test_sales_send_world as send
from tests.test_sales_cleaning import NOW, READY, UNTOUCHED, Row, World, rejected
from tests.test_sales_unconfirmed import Hunter as Answers

ACME = "acme.example.test"
IVAN = f"ivan@{ACME}"
#: Домен адреса без почты: ранний лид на нём отсеивается проверкой MX той же партии.
NO_MAIL = "nomail.example.test"
#: Сайты сбора: у магазина живой лид другой гипотезы, у запчастей — никого.
SHOP = "velo-shop.example.test"
PARTS = "velo-parts.example.test"


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Почту принимают все домены, кроме `NO_MAIL`, — у очистки и у лестницы сбора."""

    async def route(host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.NONE if host == NO_MAIL else MailRoute.MX

    monkeypatch.setattr(cleaning, "mail_route", route)
    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


@pytest.fixture
async def world(session: AsyncSession) -> World:
    hypothesis = SalesHypothesisModel(name="сайты EN")
    session.add(hypothesis)
    await session.flush()
    return World(session, hypothesis)


def waits_for(holder: SalesLeadModel, hypothesis: str) -> Row:
    """Лид ждёт: компанию держит ещё не проверенный `holder` гипотезы `hypothesis`."""
    note = (
        f"ждёт: компанию держит лид №{holder.id} гипотезы «{hypothesis}», его адрес ещё не "
        f"проверен — решит очистка гипотезы «{hypothesis}»"
    )
    return Row(LeadStatus.NEW, note=note)


def held_by(holder: SalesLeadModel, hypothesis: str, host: str = ACME) -> Row:
    """Лид отклонён: компанию держит `holder` гипотезы `hypothesis` — словами очистки."""
    words = (
        f"компания уже в работе гипотезы «{hypothesis}»: лид №{holder.id} на домене {host} — "
        "одной компании пишет одна гипотеза"
    )
    return rejected(RejectionReason.DUPLICATE, words)


class Counting(FixtureVerifier):
    """Выдуманная проверка, которая помнит, о ком её спросили: платная прошла бы так же."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def verify(self, email: str) -> Verdict:
        self.asked.append(email)
        return await super().verify(email)


# --- очистка: компанию держит раньше заведённый живой лид ------------------------------------


async def test_a_ready_holder_rejects_a_lead_of_another_hypothesis_at_once(world: World) -> None:
    """«Готовый» держатель — в том числе с законченной цепочкой: компании уже писали — отклоняет
    лида другой гипотезы сразу, словами с именем гипотезы и номером лида. Адреса разные: правило —
    про домен."""
    other = await world.hypothesis_named("сайты DE")
    first = await world.lead("ivan@acme.example.test", status=LeadStatus.READY, hypothesis=other)
    await world.lead("maria@acme.example.test")

    report = await world.clean()

    assert await world.rows() == [Row(LeadStatus.READY), held_by(first, "сайты DE")]
    assert report == CleaningReport(
        checked=1, rejected=Counter({"duplicate": 1}), verifier="fixture"
    )


async def test_an_unverified_holder_of_another_hypothesis_makes_the_lead_wait_unpaid(
    world: World,
) -> None:
    """Держатель ещё не проверен — лид другой гипотезы ждёт его: статус прежний, Hunter о нём не
    спрошен, слова называют держателя и очистку, которая его решит. В сводке ждущий — своим
    числом, а не «проверка не выполнена»: её никто не запускал."""
    other = await world.hypothesis_named("сайты DE")
    first = await world.lead("ivan@acme.example.test", hypothesis=other)
    await world.lead("maria@acme.example.test")
    verifier = Counting()

    report = await cleaning.clean(
        world.session, verifier, hypothesis_id=world.hypothesis.id, now=NOW
    )

    assert await world.rows() == [UNTOUCHED, waits_for(first, "сайты DE")]
    assert verifier.asked == []
    assert report == CleaningReport(checked=1, awaiting_holder=1, verifier="fixture")


async def test_a_waiting_lead_keeps_its_earlier_verdict(world: World) -> None:
    """Ждущий — как непроверенный: прежний вердикт и день проверки целы (здесь — первый
    `unknown`, день перепроверки которого настал)."""
    other = await world.hypothesis_named("сайты DE")
    first = await world.lead("ivan@acme.example.test", hypothesis=other)
    second = await world.lead("maria@acme.example.test")
    earlier = NOW - timedelta(days=8)
    second.verification_status, second.verification_score = "fixture:unknown", 48
    second.verified_at = earlier
    await world.session.flush()

    await world.clean(hypothesis_id=world.hypothesis.id)

    waiting = waits_for(first, "сайты DE")
    assert await world.rows() == [
        UNTOUCHED,
        Row(LeadStatus.NEW, None, waiting.note, "fixture:unknown", 48, earlier),
    ]


@pytest.mark.parametrize("earlier_in_first_hypothesis", [True, False])
async def test_the_earlier_lead_holds_whichever_hypothesis_it_belongs_to(
    world: World, earlier_in_first_hypothesis: bool
) -> None:
    """Держит раньше заведённый лид, а не гипотеза с меньшим номером: ранний лид — в гипотезе,
    заведённой первой или второй, и поздний ждёт именно его."""
    other = await world.hypothesis_named("сайты DE")
    early, late = (
        (world.hypothesis, other) if earlier_in_first_hypothesis else (other, world.hypothesis)
    )
    first = await world.lead("ivan@acme.example.test", hypothesis=early)
    await world.lead("maria@acme.example.test", hypothesis=late)

    await world.clean(hypothesis_id=late.id)

    assert await world.rows() == [UNTOUCHED, waits_for(first, early.name)]


async def test_people_of_one_company_in_one_hypothesis_are_all_written(world: World) -> None:
    """Внутри одной гипотезы несколько людей компании — можно, как прежде."""
    await world.lead("ivan@acme.example.test", status=LeadStatus.READY)
    await world.lead("maria@acme.example.test")
    await world.lead("olga@acme.example.test")

    report = await world.clean()

    assert await world.rows() == [Row(LeadStatus.READY), READY, READY]
    assert report.rejected == Counter()


async def test_a_rejected_earlier_lead_does_not_hold_the_company(world: World) -> None:
    other = await world.hypothesis_named("сайты DE")
    await world.lead("ivan@acme.example.test", status=LeadStatus.REJECTED, hypothesis=other)
    await world.lead("maria@acme.example.test")

    await world.clean()

    assert await world.rows() == [Row(LeadStatus.REJECTED), READY]


async def test_a_ready_lead_beside_a_holder_closed_by_the_same_batch_keeps_its_letter(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Перепроверка двух «готовых» лидов компании из двух гипотез (заведены до правила), а ранний
    закрыт той же партией — его адрес в стоп-листе: компанию он уже не держит (`gone`), и поздний
    сразу идёт на проверку — собранное ему письмо остаётся в очереди. Считай партия раннего
    держателем, поздний был бы сперва отклонён «компания уже в работе» — с письмом, снятым с
    очереди, — и лишь кругом позже возвращён."""
    world = await send.world(session, monkeypatch)
    german = await hypotheses.add(session, "сайты DE", None)
    early = await send.lead(session, german.id, IVAN, verification_status="fixture:valid")
    late = await send.lead(
        session, world.hypothesis_id, "jane@acme.example.test", verification_status="fixture:valid"
    )
    await queue.build(session, send.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5)
    letter = await session.scalar(select(MessageModel))
    assert letter is not None
    session.add(SalesStoplistModel(email=early.email, created_by="тест"))  # судит сама очистка
    await session.flush()
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "100000")

    await cleaning.clean(session, Answers("valid"), now=send.NOW)

    for row in (early, late, letter):
        await session.refresh(row)
    assert (early.status, early.rejection_reason) == (LeadStatus.REJECTED, "stoplist")
    assert (late.status, late.verification_status) == (LeadStatus.READY, "hunter:valid")
    assert letter.status is MessageStatus.QUEUED


@pytest.mark.parametrize(
    ("email", "why"),
    [
        (
            "ivan@acme.example.test",
            Row(
                LeadStatus.REJECTED,
                RejectionReason.STOPLIST.value,
                "стоп-лист продаж: адрес ivan@acme.example.test",
            ),
        ),
        (
            f"ivan@{NO_MAIL}",
            Row(
                LeadStatus.REJECTED,
                RejectionReason.NO_MAIL.value,
                f"домен не принимает почту (нет ни MX, ни A): {NO_MAIL}",
            ),
        ),
    ],
)
async def test_an_earlier_lead_rejected_by_the_same_pass_does_not_hold_the_company(
    world: World, email: str, why: Row
) -> None:
    """Ранний лид отсеян той же партией — стоп-листом адреса или почтой своего домена: в базе
    он ещё «новый», но компанию уже не держит, и поздний лид другой гипотезы идёт к письмам."""
    other = await world.hypothesis_named("сайты DE")
    await world.stoplist(email="ivan@acme.example.test")
    await world.lead(email, ACME, hypothesis=other)
    await world.lead("maria@acme.example.test")

    await world.clean()

    assert await world.rows() == [why, READY]


async def test_the_earliest_ready_lead_holds_and_its_hypothesis_keeps_writing(world: World) -> None:
    """Держатель у компании один — из «готовых» заведённый раньше, а не любой «готовый» лид
    другой гипотезы: у компании уже есть лиды двух гипотез (заведены до правила), и её
    гипотеза — первого."""
    german = await world.hypothesis_named("сайты DE")
    french = await world.hypothesis_named("сайты FR")
    first = await world.lead("ivan@acme.example.test", status=LeadStatus.READY)
    await world.lead("hans@acme.example.test", status=LeadStatus.READY, hypothesis=german)
    await world.lead("maria@acme.example.test")
    await world.lead("jean@acme.example.test", hypothesis=french)

    await world.clean()

    assert await world.rows() == [
        Row(LeadStatus.READY),
        Row(LeadStatus.READY),
        READY,
        held_by(first, "сайты EN"),
    ]


async def test_a_ready_lead_holds_the_company_even_against_an_earlier_one(world: World) -> None:
    """«Готовый» лид держит компанию и против раньше заведённого «нового»: с ним работа уже идёт
    или пойдёт — цепочка, ответ, передача."""
    other = await world.hypothesis_named("сайты DE")
    await world.lead("ivan@acme.example.test", hypothesis=other)
    ready = await world.lead("maria@acme.example.test", status=LeadStatus.READY)

    report = await world.clean()

    assert await world.rows() == [held_by(ready, "сайты EN"), Row(LeadStatus.READY)]
    assert report.rejected == Counter({"duplicate": 1})


async def test_an_earlier_lead_back_from_the_stoplist_yields_the_company_to_a_ready_one(
    world: World,
) -> None:
    """Ранний лид сняли со стоп-листа (`stoplist_leads.release` — снова «новый»), а компанию за
    это время взял «готовый» лид другой гипотезы: очистка отклоняет вернувшегося — двух писем
    компании не уйдёт."""
    other = await world.hypothesis_named("сайты DE")
    early = await world.lead("ivan@acme.example.test", status=LeadStatus.REJECTED, hypothesis=other)
    early.rejection_reason = RejectionReason.STOPLIST.value
    early.cleaning_note = f"стоп-лист продаж: домен {ACME}"
    ready = await world.lead("maria@acme.example.test", status=LeadStatus.READY)
    await world.session.flush()

    assert await stoplist_leads.release(world.session, now=NOW) == 1
    await world.clean()

    assert await world.rows() == [held_by(ready, "сайты EN"), Row(LeadStatus.READY)]


async def test_two_ready_leads_rechecked_together_keep_one_holder(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Живой проверяльщик перепроверяет выдуманные вердикты — и берёт обоих «готовых» лидов
    компании из двух гипотез (заведены до правила): держатель один, заведённый раньше, — и
    отклонён только второй, а не оба."""
    monkeypatch.setattr("backend.config.sales.VERIFIER_MONTH_CAP", "100000")
    other = await world.hypothesis_named("сайты DE")
    first = await world.lead("ivan@acme.example.test", status=LeadStatus.READY)
    second = await world.lead("hans@acme.example.test", status=LeadStatus.READY, hypothesis=other)
    for lead in (first, second):
        lead.verification_status, lead.verified_at = "fixture:valid", NOW
    await world.session.flush()
    valid = httpx.Response(200, json={"data": {"status": "valid", "score": 97}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: valid)) as http:
        await cleaning.clean(world.session, HunterVerifier(http, api_key="k"), now=NOW)

    assert await world.rows() == [
        Row(LeadStatus.READY, verification="hunter:valid", score=97, verified_at=NOW),
        held_by(first, "сайты EN"),
    ]


@pytest.mark.parametrize("holder_status", [LeadStatus.READY, LeadStatus.NEW])
async def test_a_held_lead_is_rejected_before_the_paid_check(
    world: World, holder_status: LeadStatus
) -> None:
    """Шаг компании — до проверки адреса: платить за лида, которому письмо не уйдёт или пока не
    может уйти, незачем. «Готовый» держатель — отказ, непроверенный — ожидание; Hunter не спрошен
    ни там, ни там."""
    other = await world.hypothesis_named("сайты DE")
    await world.lead("ivan@acme.example.test", status=holder_status, hypothesis=other)
    await world.lead("maria@acme.example.test")
    verifier = Counting()

    report = await cleaning.clean(
        world.session, verifier, hypothesis_id=world.hypothesis.id, now=NOW
    )

    assert verifier.asked == []
    held = holder_status is LeadStatus.READY
    assert (report.verified, report.rejected, report.awaiting_holder) == (
        0,
        Counter({"duplicate": 1}) if held else Counter(),
        0 if held else 1,
    )


# --- бесплатная почта: по адресу, не по домену ----------------------------------------------


async def test_free_mail_is_not_a_company_people_there_are_written_from_any_hypothesis(
    world: World,
) -> None:
    """Лиды, у которых домен — сама бесплатная почта (сбор, «пишите другому»): `gmail.com` —
    не компания, и двух разных людей на нём правило не связывает."""
    other = await world.hypothesis_named("сайты DE")
    await world.lead("ivan@gmail.com", hypothesis=other)
    await world.lead("olga@gmail.com")

    await world.clean()

    assert await world.rows() == [READY, READY]


@pytest.mark.parametrize(
    ("second_site", "second_held"),
    [("beta.example.test", False), (ACME, True)],
)
async def test_free_mail_addresses_belong_to_the_company_site(
    world: World, second_site: str, second_held: bool
) -> None:
    """Адрес на бесплатной почте — компания по сайту: разные сайты — разные компании, общий
    сайт — одна, хоть ящики и на `gmail.com` (держатель «готов» — лида другой гипотезы на том же
    сайте очистка отклоняет)."""
    other = await world.hypothesis_named("сайты DE")
    first = await world.lead("ivan@gmail.com", ACME, status=LeadStatus.READY, hypothesis=other)
    await world.lead("olga@gmail.com", second_site)

    await world.clean()

    second = held_by(first, "сайты DE") if second_held else READY
    assert await world.rows() == [Row(LeadStatus.READY), second]


async def test_the_same_free_mail_address_in_another_hypothesis_is_the_old_duplicate(
    world: World,
) -> None:
    other = await world.hypothesis_named("сайты DE")
    first = await world.lead("ivan@gmail.com", hypothesis=other, status=LeadStatus.READY)
    await world.lead("ivan@gmail.com")

    await world.clean()

    assert await world.rows() == [
        Row(LeadStatus.READY),
        rejected(RejectionReason.DUPLICATE, f"дубль: адрес уже у лида №{first.id}"),
    ]


# --- домен компании: из сайта и из адреса, регистр и www. --------------------------------------


async def _loaded(session: AsyncSession, hypothesis: SalesHypothesisModel, text: str) -> None:
    found = intake.preview(intake.read_bytes(text.encode(), "база.csv"))
    assert await intake.load(session, found, hypothesis.id, author_id=None) == 1


async def test_company_from_the_site_and_from_the_address_is_one_whatever_the_case_and_www(
    world: World,
) -> None:
    """Путь загрузки базы: у первого лида компания — сайт `WWW.Acme…` (ящик бесплатный), у
    второго — домен рабочего адреса в другом регистре. Ключ один (`intake.host_key`), и
    компания одна: первый лид прошёл очистку своей гипотезы — второй отклонён его словами."""
    other = await world.hypothesis_named("сайты DE")
    await _loaded(
        world.session, other, "email,website\nivan@gmail.com,https://WWW.Acme.Example.Test/about\n"
    )
    await _loaded(world.session, world.hypothesis, "email\nMaria@ACME.example.test\n")
    first, second = (
        await world.session.scalars(select(SalesLeadModel).order_by(SalesLeadModel.id))
    ).all()
    assert first.domain_id == second.domain_id

    await world.clean(hypothesis_id=other.id)
    await world.clean(hypothesis_id=world.hypothesis.id)

    assert await world.rows() == [READY, held_by(first, "сайты DE")]


# --- сбор: домен с живым лидом не ищется -----------------------------------------------------


@dataclass
class Paid:
    """Платная ступень поиска адреса — подделка: помнит, о ком её спросили."""

    name: str = "hunter"
    asked: list[str] = field(default_factory=list)

    async def quota(self) -> Quota:
        return Quota(used=13, available=977)

    async def find_emails(self, host: str) -> list[Candidate]:
        self.asked.append(host)
        return []


@dataclass
class Sites:
    """Сайты лестницы: главная без адреса, остальное — 404; каждый запрос — в список."""

    hits: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hits.append(str(request.url))
        if request.url.path in ("", "/"):
            return httpx.Response(200, html="<p>Каталог</p>", request=request)
        return httpx.Response(404, text="нет", request=request)


@pytest.fixture
def ladder(monkeypatch: pytest.MonkeyPatch) -> tuple[Paid, Sites]:
    paid, sites = Paid(), Sites()

    async def paid_step(_http: httpx.AsyncClient, _report: object) -> Paid:
        return paid

    monkeypatch.setattr("backend.features.contacts.search.paid_step", paid_step)
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(sites)),
    )
    return paid, sites


async def collected(world: World, owner: SalesHypothesisModel) -> SalesSegmentRunModel:
    """Прогон гипотезы `owner`: магазин и запчасти приняты судьёй, лида из них нет."""
    run = SalesSegmentRunModel(
        hypothesis_id=owner.id,
        keywords=["купить велосипед"],
        country="us",
        language="en",
        depth_pages=1,
        mode="enforce",
        estimate={"hunter_searches": 2},
        report={},
        created_by="seller@ours.example.test",
    )
    world.session.add(run)
    await world.session.flush()
    for host in (SHOP, PARTS):
        domain = await world.domain(host)
        world.session.add(
            SalesSegmentCandidateModel(
                run_id=run.id, hypothesis_id=owner.id, domain_id=domain.id, lane=Lane.ACCEPT
            )
        )
    await world.session.flush()
    return run


@pytest.mark.parametrize("same_hypothesis", [False, True])
async def test_collection_does_not_search_or_pay_for_a_domain_with_a_live_lead(
    world: World, ladder: tuple[Paid, Sites], same_hypothesis: bool
) -> None:
    """Лид из файла строки `contacts` не заводит — прежнее условие «на домене есть живой адрес»
    его не видело, и Hunter платился за компанию, которой уже пишут. Теперь домен с живым лидом
    любой гипотезы не идёт ни одной ступенью, а отчёт прогона называет почему."""
    paid, sites = ladder
    owner = world.hypothesis if same_hypothesis else await world.hypothesis_named("сбор DE")
    await world.lead(f"ivan@{SHOP}", status=LeadStatus.READY)
    run = await collected(world, owner)
    queue = SegmentContactQueue(world.session, run.id)

    assert await queue.pending_hosts() == [PARTS]
    assert await queue.pending_count() == 1
    await search_contacts(world.session, limit=10, queue=queue, no_paid=False)

    assert paid.asked == [PARTS]
    assert [hit for hit in sites.hits if SHOP in hit] == []
    assert [hit for hit in sites.hits if PARTS in hit] != []  # подделка сайтов работает
    units = await world.session.scalars(
        select(UsageRecordModel.units).where(UsageRecordModel.operation == "contacts_search")
    )
    assert units.all() == [1]
    said = "компания уже в работе гипотезы «сайты EN» — адрес не ищем, лида не заводим"
    assert await company_hold.held_in_run(world.session, run.id) == Counter({said: 1})


@pytest.mark.parametrize("same_hypothesis", [False, True])
async def test_collection_makes_no_lead_from_a_known_address_of_a_company_in_work(
    world: World, same_hypothesis: bool
) -> None:
    """Адрес домена уже лежит в `contacts` (нашли доноры), а у компании есть живой лид: сбор лида
    не заводит и проверку адреса не оплачивает — и своей гипотезе, где очистка его пропустила
    бы. Свободный домен с адресом — лид, как прежде."""
    owner = world.hypothesis if same_hypothesis else await world.hypothesis_named("сбор DE")
    holder = await world.lead(f"ivan@{SHOP}", status=LeadStatus.READY)
    run = await collected(world, owner)
    for host in (SHOP, PARTS):
        domain = await world.domain(host)
        world.session.add(
            ContactModel(domain_id=domain.id, email=f"info@{host}", source=ContactSource.PAGE)
        )
    await world.session.flush()
    verifier = Counting()

    report = await segment_leads.make_leads(world.session, run, verifier)

    assert verifier.asked == [f"info@{PARTS}"]
    emails = await world.session.scalars(select(SalesLeadModel.email).order_by(SalesLeadModel.id))
    assert emails.all() == [holder.email, f"info@{PARTS}"]
    assert report["created"] == 1
    said = "компания уже в работе гипотезы «сайты EN» — адрес не ищем, лида не заводим"
    assert await company_hold.held_in_run(world.session, run.id) == Counter({said: 1})


async def test_collection_searches_a_domain_whose_lead_was_rejected(
    world: World, ladder: tuple[Paid, Sites]
) -> None:
    paid, _ = ladder
    await world.lead(f"ivan@{SHOP}", status=LeadStatus.REJECTED)
    run = await collected(world, await world.hypothesis_named("сбор DE"))

    await search_contacts(
        world.session, limit=10, queue=SegmentContactQueue(world.session, run.id), no_paid=False
    )

    assert sorted(paid.asked) == sorted([SHOP, PARTS])
    assert await company_hold.held_in_run(world.session, run.id) == Counter()


async def test_a_whole_run_neither_searches_nor_pays_for_a_company_already_in_work(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Путь прогона целиком (`segment_run.execute`), платная ступень сбора включена и в смете:
    магазин принят судьёй, но его компанию держит лид другой гипотезы — адрес не ищется ни
    одной ступенью и не оплачивается, лида не заводится; запчасти — ищутся, как прежде."""
    paid, pages = Paid(), run_test.sites()

    async def paid_step(_http: httpx.AsyncClient, _report: object) -> Paid:
        return paid

    monkeypatch.setattr("backend.features.contacts.search.paid_step", paid_step)
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(pages)),
    )
    monkeypatch.setattr("backend.config.sales.SEGMENT_PAID_CONTACTS", True)
    owner = await run_test.hypothesis(session)
    other = SalesHypothesisModel(name="сайты DE")
    shop = DomainModel(host=run_test.SHOP)
    session.add_all([other, shop])
    await session.flush()
    earlier = SalesLeadModel(
        hypothesis_id=other.id,
        domain_id=shop.id,
        email=f"owner@{run_test.SHOP}",
        source=LeadSource.IMPORT,
        status=LeadStatus.READY,
    )
    session.add(earlier)
    run = await run_test.make_run(session, owner)
    run.estimate = {**run.estimate, "hunter_searches": 2}
    await session.commit()

    found = run_test.serp(run_test.SHOP, run_test.PARTS)
    await run_test.go(session, run, found, run_test.Model(run_test.ANSWERS))

    assert await run_test.lanes(session, run) == {
        run_test.SHOP: Lane.ACCEPT,
        run_test.PARTS: Lane.ACCEPT,
    }
    assert paid.asked == [run_test.PARTS]
    assert [hit for hit in pages.hits if hit.startswith(run_test.SHOP)] == []
    assert [lead.id for lead in await run_test.leads(session)] == [earlier.id]
    said = "компания уже в работе гипотезы «сайты DE» — адрес не ищем, лида не заводим"
    assert await company_hold.held_in_run(session, run.id) == Counter({said: 1})


async def test_a_held_site_does_not_wait_for_an_address_in_the_run_report(world: World) -> None:
    """Отчёт прогона: принятый сайт, чью компанию держит живой лид, поиска адреса не ждёт —
    `waiting_address` его не считает, и кнопка «Найти адреса принятым» не горит, когда других
    ждущих нет; «Без адреса — почему» называет его словами компании."""
    await world.lead(f"ivan@{SHOP}", status=LeadStatus.READY)
    run = await collected(world, await world.hypothesis_named("сбор DE"))
    said = "компания уже в работе гипотезы «сайты EN» — адрес не ищем, лида не заводим"

    view = await segment_report.run_view(world.session, run.id)

    assert view is not None
    assert (view.waiting_address, view.no_address) == (1, [(said, 1)])  # ждут — только запчасти

    parts = await world.session.scalar(
        select(SalesSegmentCandidateModel).where(
            SalesSegmentCandidateModel.run_id == run.id,
            SalesSegmentCandidateModel.domain_id == (await world.domain(PARTS)).id,
        )
    )
    assert parts is not None
    # Запчастям поиск ответил «адреса нет» — исход и время попытки, как их пишет поиск
    # (`attempts.values`): «ждут адреса» считает условием прохода, а оно смотрит и на время.
    parts.contact_status, parts.contact_attempted_at = ContactStatus.NOT_FOUND, datetime.now(UTC)
    await world.session.flush()

    view = await segment_report.run_view(world.session, run.id)

    assert view is not None
    assert view.waiting_address == 0  # кнопка не горит: искать некому
    assert sorted(view.no_address) == sorted([(said, 1), ("адреса на сайте нет", 1)])


async def test_a_held_site_says_so_in_the_list_of_run_sites(world: World) -> None:
    """Список сайтов прогона: итог удержанного сайта — те же слова компании, что в отчёте
    (`company_hold.held_sites`), а не «адрес ещё не искали»; свободный — как прежде."""
    await world.lead(f"ivan@{SHOP}", status=LeadStatus.READY)
    run = await collected(world, await world.hypothesis_named("сбор DE"))
    said = "компания уже в работе гипотезы «сайты EN» — адрес не ищем, лида не заводим"

    page = await segment_sites.sites_page(world.session, SitesAsk(run_id=run.id))

    assert page is not None
    assert [row.outcome for row in page.rows] == [
        Outcome(OutcomeKind.OTHER, said),
        Outcome(OutcomeKind.NOT_SEARCHED, NOT_SEARCHED),
    ]
    assert await company_hold.held_in_run(world.session, run.id) == Counter({said: 1})


async def test_a_free_mail_domain_is_never_held(world: World) -> None:
    """Домен бесплатной почты компанию не держит и у сбора: одно правило с очисткой."""
    gmail = await world.domain("gmail.com")
    await world.lead("ivan@gmail.com", status=LeadStatus.READY)
    run = await collected(world, await world.hypothesis_named("сбор DE"))
    world.session.add(
        SalesSegmentCandidateModel(
            run_id=run.id, hypothesis_id=run.hypothesis_id, domain_id=gmail.id, lane=Lane.ACCEPT
        )
    )
    await world.session.flush()

    hosts = await SegmentContactQueue(world.session, run.id).pending_hosts()

    assert sorted(hosts) == sorted([SHOP, PARTS, "gmail.com"])
    assert await company_hold.holders(world.session, [gmail.id]) == {}
