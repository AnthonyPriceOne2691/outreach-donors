"""Компания — в одной гипотезе: ждущий не висит, освободившаяся компания возвращает раннего.

Ревью соседней сессии 15.10, правка (а): непроверенный держатель не отклоняет — лид другой
гипотезы его ждёт. Проход очистки, решив лида, будит ждущих на его домене тем же проходом
(`company_hold.after`), а компания, у которой не осталось живого лида, возвращает раннего,
отклонённого именно её держателем (`company_hold.freed`, решение владельца 15.10): очистка —
кругами и в начале прохода, стоп-лист продаж — в своей транзакции. Очистка гипотезы платит
только за свои лиды: разбуженным и вернувшимся чужих гипотез — бесплатные исходы, их проверит
очистка их гипотезы; окно «до N запросов» считает своих возвращаемых. Слова правила — данные:
шаблон и разбор в одном месте (`hold_words`), петля «построили → разобрали» — и в базе.

База настоящая, сеть — подделки: DNS — таблицей, проверка адресов — выдуманная (со счётчиком
вопросов) или Hunter за `httpx.MockTransport` с ответами по адресу. Домены выдуманы
(`*.example.test`), утверждения точные — на статус, код, слова и вердикт.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import timedelta
from typing import Any

import httpx
import pytest
from backend.features.contacts.mx import MailRoute
from backend.features.contacts.provider import ProviderError
from backend.features.sales import (
    clean_jobs,
    clean_report,
    cleaning,
    company_hold,
    hunter_codes,
    stoplist_leads,
)
from backend.features.sales.clean_report import CleaningReport, Outcome
from backend.features.sales.company_hold import Holder
from backend.features.sales.hold_words import BACK, FREED, HELD, WAITS
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
)
from backend.features.sales.unconfirmed import recheck_on
from backend.features.sales.verifier import FixtureVerifier, HunterVerifier, Verdict
from backend.shared.net import retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_cleaning import NOW, READY, UNTOUCHED, Row, World, rejected
from tests.test_sales_company_hold import ACME, Counting, held_by, waits_for

#: Адреса нет — выдуманный проверяльщик отвечает `invalid`.
DEAD = f"bounce@{ACME}"
MARIA = f"maria@{ACME}"
OLGA = f"olga@{ACME}"
IVAN = f"ivan@{ACME}"

VALID = httpx.Response(200, json={"data": {"status": "valid", "score": 97}})
INVALID = httpx.Response(200, json={"data": {"status": "invalid", "score": 12}})
NOT_KNOWN = httpx.Response(200, json={"data": {"status": "unknown", "score": 40}})
PENDING = httpx.Response(202, json={"data": {"status": "pending"}})
#: Ключ не принят — платная часть прохода встаёт (`verifier.STOPS`).
NO_KEY = httpx.Response(
    401, json={"errors": [{"id": "authentication_failed", "code": 401, "details": "no key"}]}
)


@pytest.fixture(autouse=True)
def _network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Почту принимают все домены; повторы Hunter не спят, но отдают ход; месячный потолок
    проверок — с запасом: здесь не о нём."""

    async def route(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.MX

    async def pause(_seconds: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(cleaning, "mail_route", route)
    monkeypatch.setattr(retry, "_sleep", pause)
    monkeypatch.setattr("backend.config.sales.VERIFIER_MONTH_CAP", "100000")


@pytest.fixture
async def world(session: AsyncSession) -> World:
    hypothesis = SalesHypothesisModel(name="сайты EN")
    session.add(hypothesis)
    await session.flush()
    return World(session, hypothesis)


def dead(email: str = DEAD) -> Row:
    """Отклонён проверкой выдуманного проверяльщика: ящика нет."""
    note = f"адрес не существует: {email}"
    return Row(
        LeadStatus.REJECTED, RejectionReason.UNDELIVERABLE.value, note, "fixture:invalid", 7, NOW
    )


def back(holder: SalesLeadModel, hypothesis: str, own: str) -> Row:
    """Вернулся: компания свободна — её держатель `holder` гипотезы `hypothesis` отклонён; адрес
    проверит очистка гипотезы `own` — самого лида."""
    note = (
        f"вернулся: компания освободилась — лид №{holder.id} гипотезы «{hypothesis}» отклонён — "
        f"адрес проверит очистка гипотезы «{own}»"
    )
    return Row(LeadStatus.NEW, note=note)


def free_for(holder: SalesLeadModel, hypothesis: str, own: str) -> Row:
    """Разбуженный лид чужой гипотезы: компания для него свободна, адрес проверит очистка `own`."""
    note = (
        f"компания свободна: лид №{holder.id} гипотезы «{hypothesis}» отклонён — "
        f"адрес проверит очистка гипотезы «{own}»"
    )
    return Row(LeadStatus.NEW, note=note)


def hunter_ready(score: int = 97) -> Row:
    return Row(LeadStatus.READY, verification="hunter:valid", score=score, verified_at=NOW)


class Hunter:
    """Hunter по адресу: ответы по очереди, последний повторяется; `None` — обрыв связи.
    Каждый вопрос — в список."""

    def __init__(self, answers: dict[str, list[httpx.Response | None]]) -> None:
        self.answers = {email: list(said) for email, said in answers.items()}
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        email = request.url.params["email"]
        self.asked.append(email)
        said = self.answers[email]
        answer = said.pop(0) if len(said) > 1 else said[0]
        if answer is None:
            raise httpx.ConnectError("соединение сброшено", request=request)
        return answer

    async def clean(self, world: World, **kwargs: Any) -> CleaningReport:
        async with httpx.AsyncClient(transport=httpx.MockTransport(self)) as http:
            verifier = HunterVerifier(http, api_key="k")
            return await cleaning.clean(world.session, verifier, now=NOW, **kwargs)


class Failing(FixtureVerifier):
    """Выдуманная проверка, которой сервис отказал по адресам `down` — по разу; вопросы — в
    список."""

    def __init__(self, *down: str) -> None:
        self.down = set(down)
        self.asked: list[str] = []

    async def verify(self, email: str) -> Verdict:
        self.asked.append(email)
        if email in self.down:
            self.down.discard(email)
            raise ProviderError("сервис проверки не ответил")
        return await super().verify(email)


# --- слова правила — данные: шаблон и разбор в одном месте (п. 2г) ---------------------------

#: Держатель со всем, что может сбить разбор: кавычки, «№» и сами слова шаблона в имени.
TRICKY = Holder(4021, 7, "DE»: лид №9 на домене x — «сайты №2", "www.acme-shop.example.test", False)
PLAIN = Holder(17, 3, "сайты DE", ACME, True)


@pytest.mark.parametrize("holder", [PLAIN, TRICKY])
def test_rule_words_parse_back_to_the_holder_number(holder: Holder) -> None:
    """Петля: слова отказа и ожидания, построенные держателем, разбор возвращает в его номер; одни
    слова за другие не сходят, дубль адреса — не слова держания."""
    assert HELD.lead(holder.words) == holder.lead_id
    assert WAITS.lead(holder.waits) == holder.lead_id
    assert (HELD.lead(holder.waits), WAITS.lead(holder.words)) == (None, None)
    assert HELD.lead(f"дубль: адрес уже у лида №{holder.lead_id}") is None
    for words in (BACK, FREED):
        said = words.say(lead=holder.lead_id, hypothesis=holder.hypothesis, own=holder.hypothesis)
        assert words.lead(said) == holder.lead_id
        assert said.endswith(f"— адрес проверит очистка гипотезы «{holder.hypothesis}»")


@pytest.mark.parametrize("holder", [PLAIN, TRICKY])
async def test_rule_words_parse_back_in_the_database_too(world: World, holder: Holder) -> None:
    """Та же петля в настоящей базе: регулярка SQL — из того же шаблона, номер — тот же."""
    notes = [holder.words, holder.waits, f"дубль: адрес уже у лида №{holder.lead_id}"]
    for index, note in enumerate(notes):
        lead = await world.lead(f"p{index}@{ACME}", status=LeadStatus.REJECTED)
        lead.cleaning_note = note
    await world.session.flush()

    note = SalesLeadModel.cleaning_note
    found = await world.session.execute(
        select(
            HELD.lead_of(note), HELD.match(note), WAITS.lead_of(note), WAITS.match(note)
        ).order_by(SalesLeadModel.id)
    )

    number = holder.lead_id
    assert [tuple(row) for row in found.all()] == [
        (number, True, None, False),
        (None, False, number, True),
        (None, False, None, False),
    ]


@pytest.mark.parametrize(
    ("outcome", "decided"),
    [
        (Outcome(1, LeadStatus.REJECTED, RejectionReason.DUPLICATE, "дубль"), True),
        (Outcome(1, LeadStatus.READY, verdict=Verdict("valid", 93)), True),
        # первый `unknown` перепроверки и «проверка не выполнена» — не решение
        (Outcome(1, LeadStatus.READY, verdict=Verdict("unknown", 48)), False),
        (Outcome(1, LeadStatus.NEW, verdict=Verdict("unknown", 48)), False),
        (Outcome(1, LeadStatus.READY, note="проверка не выполнена: сеть", kept=True), False),
        (Outcome(1, LeadStatus.READY, note="проверка не выполнена: сеть"), False),  # без вердикта
        (Outcome(1, LeadStatus.NEW, note="ждёт", kept=True, waits=True), False),
    ],
)
def test_only_a_rejected_or_freshly_ready_lead_is_decided(outcome: Outcome, decided: bool) -> None:
    assert outcome.decided is decided


# --- пробуждение: решённый держатель будит ждущего тем же проходом (п. 2) ------------------


async def test_one_cleaning_rejects_the_holder_and_checks_the_lead_it_kept_waiting(
    world: World,
) -> None:
    """Проба соседней сессии: держатель «сайты DE» заведён раньше, адреса у него нет. Одна
    очистка: держатель отклонён проверкой, ждавший его лид разбужен тем же проходом, держит
    компанию сам и проверен — а не отклонён «компания уже в работе», как до правки."""
    other = await world.hypothesis_named("сайты DE")
    await world.lead(DEAD, hypothesis=other)
    await world.lead(MARIA)

    report = await world.clean()

    assert await world.rows() == [dead(), READY]
    assert report == CleaningReport(
        checked=2,
        ready=1,
        rejected=Counter({"undeliverable": 1}),
        verified=2,
        verifier="fixture",
    )


async def test_a_holder_without_a_verdict_keeps_the_lead_waiting_until_its_check_decides(
    world: World,
) -> None:
    """Держатель без вердикта от сервиса: 202 и после переспросов, затем обрыв связи. Обе очистки
    лид другой гипотезы ждёт — словами, с прежним вердиктом, Hunter о нём не спрошен. Третья:
    держатель «готов» — ждущий отклонён тем же проходом, тоже без платной проверки."""
    other = await world.hypothesis_named("сайты DE")
    holder = await world.lead(IVAN, hypothesis=other)
    waiting = await world.lead(MARIA)
    earlier = NOW - timedelta(days=8)
    waiting.verification_status, waiting.verification_score = "hunter:unknown", 40
    waiting.verified_at = earlier
    await world.session.flush()
    hunter = Hunter({IVAN: [PENDING, PENDING, PENDING, None, None, None, VALID]})
    kept = Row(
        LeadStatus.NEW, None, waits_for(holder, "сайты DE").note, "hunter:unknown", 40, earlier
    )

    first = await hunter.clean(world)

    still = f"проверка не выполнена: {hunter_codes.STILL_CHECKING}"
    assert await world.rows() == [Row(LeadStatus.NEW, note=still), kept]
    assert (first.unverified, first.awaiting_holder) == (1, 1)

    await hunter.clean(world)

    unreached, waited = await world.rows()
    assert (unreached.status, waited) == (LeadStatus.NEW, kept)
    assert (unreached.note or "").startswith("проверка не выполнена: сеть до провайдера не дошла")

    third = await hunter.clean(world)

    assert await world.rows() == [hunter_ready(), held_by(holder, "сайты DE")]
    assert set(hunter.asked) == {IVAN}
    assert (third.checked, third.ready, third.rejected, third.awaiting_holder) == (
        2,
        1,
        Counter({"duplicate": 1}),
        0,
    )


@pytest.mark.parametrize("holder_email", [IVAN, DEAD])
async def test_cleaning_the_holders_hypothesis_decides_the_lead_waiting_in_another_for_free(
    world: World, holder_email: str
) -> None:
    """Держатель — в другой гипотезе. Очистка гипотезы ждущего: он ждёт, держатель не тронут и не
    оплачен. Очистка гипотезы держателя решает ждущего тем же проходом, но только бесплатно — она
    платит за свои лиды: держатель «готов» — ждущий отклонён; держатель отклонён — ждущий `new`
    словами «компания свободна», Hunter о нём не спрошен, и проверяет его очистка его гипотезы."""
    other = await world.hypothesis_named("сайты DE")
    holder = await world.lead(holder_email, hypothesis=other)
    await world.lead(MARIA)
    verifier = Counting()

    await cleaning.clean(world.session, verifier, hypothesis_id=world.hypothesis.id, now=NOW)

    assert await world.rows() == [UNTOUCHED, waits_for(holder, "сайты DE")]
    assert verifier.asked == []

    report = await cleaning.clean(world.session, verifier, hypothesis_id=other.id, now=NOW)

    assert verifier.asked == [holder_email]
    assert (report.checked, report.rejected_total + report.ready) == (1, 1)  # только свой
    if holder_email == IVAN:
        assert await world.rows() == [READY, held_by(holder, "сайты DE")]
        return
    assert await world.rows() == [dead(), free_for(holder, "сайты DE", "сайты EN")]

    await cleaning.clean(world.session, verifier, hypothesis_id=world.hypothesis.id, now=NOW)

    assert await world.rows() == [dead(), READY]
    assert verifier.asked == [DEAD, MARIA]


@pytest.mark.parametrize("holder_email", [IVAN, DEAD])
async def test_cleaning_named_leads_wakes_the_lead_waiting_for_one_of_them(
    world: World, holder_email: str
) -> None:
    """`clean_leads` (сбор, «пишите другому») — тот же проход с кругами, своя область — гипотеза
    названных: решив держателя, он будит ждущего другой гипотезы и решает его бесплатно."""
    other = await world.hypothesis_named("сайты DE")
    holder = await world.lead(holder_email, hypothesis=other)
    await world.lead(MARIA)
    await world.clean(hypothesis_id=world.hypothesis.id)
    verifier = Counting()

    report = await cleaning.clean_leads(world.session, [holder], verifier, now=NOW)

    expected = (
        [READY, held_by(holder, "сайты DE")]
        if holder_email == IVAN
        else [dead(), free_for(holder, "сайты DE", "сайты EN")]
    )
    assert await world.rows() == expected
    assert verifier.asked == [holder_email]
    assert report.checked == 1


@pytest.mark.parametrize("newcomer_first", [False, True])
async def test_a_new_lead_never_offered_to_cleaning_is_neither_woken_nor_paid_for(
    world: World, newcomer_first: bool
) -> None:
    """Новый лид третьей гипотезы на том же домене — без слов ожидания: в очистку его ещё не
    отдавали, пробуждение его не трогает и Hunter за него не платится. Заведён раньше ждущего —
    он новый держатель компании, и ждущий ждёт уже его: в словах — его номер и гипотеза; позже —
    компания для ждущего свободна, и проверит его очистка его гипотезы."""
    german = await world.hypothesis_named("сайты DE")
    french = await world.hypothesis_named("сайты FR")
    holder = await world.lead(DEAD, hypothesis=german)
    early = await world.lead(OLGA, hypothesis=french) if newcomer_first else None
    await world.lead(MARIA)
    await world.clean(hypothesis_id=world.hypothesis.id)
    late = None if newcomer_first else await world.lead(OLGA, hypothesis=french)
    verifier = Counting()

    await cleaning.clean(world.session, verifier, hypothesis_id=german.id, now=NOW)

    if early is not None:
        assert await world.rows() == [dead(), UNTOUCHED, waits_for(early, "сайты FR")]
    else:
        assert late is not None
        assert await world.rows() == [dead(), free_for(holder, "сайты DE", "сайты EN"), UNTOUCHED]
    assert verifier.asked == [DEAD]


async def test_three_hypotheses_the_earliest_waiting_holds_and_the_next_round_decides_the_third(
    world: World,
) -> None:
    """Три гипотезы на одном домене, одна очистка: держатель отклонён проверкой — компанию держит
    ранний из ждущих и проверен; третий ждал уже его и, когда тот «готов», разбужен следующим
    кругом и отклонён его словами."""
    german = await world.hypothesis_named("сайты DE")
    french = await world.hypothesis_named("сайты FR")
    await world.lead(DEAD, hypothesis=german)
    second = await world.lead(MARIA)
    await world.lead(OLGA, hypothesis=french)
    verifier = Counting()

    report = await cleaning.clean(world.session, verifier, now=NOW)

    assert await world.rows() == [dead(), READY, held_by(second, "сайты EN")]
    assert verifier.asked == [DEAD, MARIA]
    assert report == CleaningReport(
        checked=3,
        ready=1,
        rejected=Counter({"undeliverable": 1, "duplicate": 1}),
        verified=2,
        verifier="fixture",
    )


@pytest.mark.parametrize(
    "order", [("all",), ("DE", "EN", "FR"), ("FR", "EN", "DE")], ids=["all", "forward", "reverse"]
)
async def test_two_waiting_leads_of_two_hypotheses_one_company_only_one_becomes_ready(
    world: World, order: tuple[str, ...]
) -> None:
    """Двое ждущих из разных гипотез на одну компанию, держатель отклонён проверкой. «Готов»
    становится только один — ранний, первым проверенный, второго отклоняют его словами — и в
    проходе без гипотезы, и в проходах каждой гипотезы по очереди, и в обратном порядке (очистки
    сериализует один воркер). Hunter спрошен по разу о держателе и о раннем, о втором — ни разу."""
    hypotheses = {
        "EN": world.hypothesis,
        "DE": await world.hypothesis_named("сайты DE"),
        "FR": await world.hypothesis_named("сайты FR"),
    }
    await world.lead(DEAD, hypothesis=hypotheses["DE"])
    first = await world.lead(MARIA)
    await world.lead(OLGA, hypothesis=hypotheses["FR"])
    verifier = Counting()

    for _ in range(2):
        for name in order:
            chosen = hypotheses.get(name)
            scope = None if chosen is None else chosen.id
            await cleaning.clean(world.session, verifier, hypothesis_id=scope, now=NOW)

    assert await world.rows() == [dead(), READY, held_by(first, "сайты EN")]
    assert verifier.asked == [DEAD, MARIA]


async def test_the_third_lead_names_the_new_holder_while_its_check_is_pending(
    world: World,
) -> None:
    """Проверка нового держателя не выполнена — третий ждёт его, и слова называют его номер и
    гипотезу. Следующая очистка проверяет держателя — и третий решён тем же проходом."""
    german = await world.hypothesis_named("сайты DE")
    french = await world.hypothesis_named("сайты FR")
    await world.lead(DEAD, hypothesis=german)
    second = await world.lead(MARIA)
    await world.lead(OLGA, hypothesis=french)
    verifier = Failing(MARIA)

    await cleaning.clean(world.session, verifier, now=NOW)

    unreached = Row(LeadStatus.NEW, note="проверка не выполнена: сервис проверки не ответил")
    assert await world.rows() == [dead(), unreached, waits_for(second, "сайты EN")]

    await cleaning.clean(world.session, verifier, now=NOW)

    assert await world.rows() == [dead(), READY, held_by(second, "сайты EN")]
    assert verifier.asked == [DEAD, MARIA, MARIA]


async def test_rounds_that_never_settle_stop_at_the_limit_and_say_so(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Страховка кругов: если правило однажды ошибётся и каждый круг будет будить снова (здесь —
    подделка `company_hold.after`), проход не крутится вечно. После `ROUNDS` кругов он встаёт,
    пишет ошибку с доменами последнего круга и отдаёт сводку; лиды того круга остаются как есть —
    их решит следующий проход."""
    lead = await world.lead(MARIA)
    calls: list[int] = []

    async def endless(*_args: object) -> list[SalesLeadModel]:
        calls.append(len(calls))
        if len(calls) > cleaning.ROUNDS + 3:
            raise AssertionError("круги не остановились")
        return [lead]

    monkeypatch.setattr(company_hold, "after", endless)

    with caplog.at_level(logging.ERROR, logger=cleaning.__name__):
        report = await world.clean()

    assert len(calls) == cleaning.ROUNDS
    assert (report.checked, report.ready) == (1, 1)
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert [record.getMessage() for record in errors] == [cleaning.UNSETTLED]
    assert errors[0].__dict__["domains"] == [lead.domain_id]


async def test_a_holder_that_is_not_decided_wakes_nobody(world: World) -> None:
    """Первый `unknown` у держателя — не решение: он «новый», и ждущий ждёт дальше."""
    other = await world.hypothesis_named("сайты DE")
    holder = await world.lead(f"unknown.ivan@{ACME}", hypothesis=other)  # выдуманный: «не знаю»
    await world.lead(MARIA)

    await world.clean()

    first, waiting = await world.rows()
    assert (first.status, first.verification) == (LeadStatus.NEW, "fixture:unknown")
    assert waiting == waits_for(holder, "сайты DE")


async def test_a_stopped_paid_part_stops_the_rounds_too(world: World) -> None:
    """Проход без гипотезы: держатель отклонён проверкой, а ключ на соседнем адресе не принят —
    платная часть встала. Разбуженный кругом ждущий своей области не проверяется: остаётся `new`
    со словами остановки, Hunter о нём не спрошен."""
    other = await world.hypothesis_named("сайты DE")
    await world.lead(IVAN, hypothesis=other)
    lone = await world.lead("petr@beta.example.test", hypothesis=other)
    await world.lead(MARIA)
    hunter = Hunter({IVAN: [INVALID], lone.email: [NO_KEY]})

    report = await hunter.clean(world)

    stopped = f"проверка не выполнена: {report.stopped}"
    assert (report.stopped or "").startswith("ключ проверки адресов не принят")
    assert (await world.rows())[1:] == [Row(LeadStatus.NEW, note=stopped)] * 2
    assert sorted(hunter.asked) == sorted([IVAN, lone.email])


async def test_the_summary_counts_the_waiting_apart_from_the_unverified(world: World) -> None:
    """Сводка: ждущий держателя — своим числом (`awaiting_holder`), а не «проверка не выполнена»; лид,
    чью проверку сорвал сервис, — «не проверено». Консоль и строка задачи — тем же числом."""
    other = await world.hypothesis_named("сайты DE")
    await world.lead(IVAN, hypothesis=other)
    await world.lead(MARIA)
    await world.lead("petr@beta.example.test")

    report = await cleaning.clean(
        world.session, Failing("petr@beta.example.test"), hypothesis_id=world.hypothesis.id, now=NOW
    )

    assert report == CleaningReport(checked=2, unverified=1, awaiting_holder=1, verifier="fixture")
    assert clean_report.lines(report) == [
        "Очистка продаж: проверено 2; готово 0; отклонено 0; не подтверждено 0; не проверено 1.",
        "  ждут: компанию держит непроверенный лид другой гипотезы — 1: Hunter за них не "
        "платился, их решит очистка гипотезы держателя — она названа в словах лида",
        "\nНепроверенные лиды остались в прежнем статусе: следующая очистка повторит их.",
    ]
    assert clean_jobs.report_of(report)["awaiting_holder"] == 1


# --- компания освободилась: возвращается ранний, отклонённый её держателем (п. 2б–2в) ------


async def held_off(
    world: World, *people: tuple[str, SalesHypothesisModel]
) -> tuple[SalesLeadModel, list[SalesLeadModel]]:
    """Держатель «сайты DE» «готов» (выдуманный вердикт), а лиды `people` (адрес, гипотеза)
    очистка отклонила из-за него — «компания уже в работе»."""
    german = await world.hypothesis_named("сайты DE")
    holder = await world.lead(IVAN, status=LeadStatus.READY, hypothesis=german)
    holder.verification_status, holder.verification_score, holder.verified_at = (
        "fixture:valid",
        93,
        NOW,
    )
    leads = [await world.lead(email, hypothesis=hypothesis) for email, hypothesis in people]
    await world.clean()
    assert (await world.rows())[1:] == [held_by(holder, "сайты DE")] * len(leads)
    return holder, leads


async def test_a_holder_rejected_at_recheck_returns_the_earliest_lead_it_held_off(
    world: World,
) -> None:
    """Держателя отклонила перепроверка (проверяльщик живой, ящика нет): компания свободна —
    ранний из отклонённых им вернулся и проверен той же очисткой, поздний остался отклонённым:
    компанию держит вернувшийся."""
    french = await world.hypothesis_named("сайты FR")
    holder, (early, _) = await held_off(world, (MARIA, world.hypothesis), (OLGA, french))
    hunter = Hunter({IVAN: [INVALID], MARIA: [VALID]})

    report = await hunter.clean(world)

    gone = Row(
        LeadStatus.REJECTED,
        RejectionReason.UNDELIVERABLE.value,
        f"адрес не существует: {IVAN}",
        "hunter:invalid",
        12,
        NOW,
    )
    assert await world.rows() == [gone, hunter_ready(), held_by(holder, "сайты DE")]
    assert hunter.asked == [IVAN, early.email]
    assert report == CleaningReport(
        checked=2,
        ready=1,
        rejected=Counter({"undeliverable": 1}),
        verified=2,
        paid_units=2,
        verifier="hunter",
    )


async def test_a_holder_closed_by_the_stoplist_returns_the_earliest_lead_to_wait_for_cleaning(
    world: World,
) -> None:
    """Держателя закрыл стоп-лист продаж (запись — его адрес): компания свободна. Ранний из
    отклонённых им — снова `new` словами «вернулся» без вердикта и ждёт очистки; Hunter не
    спрошен, поздний остался отклонённым."""
    french = await world.hypothesis_named("сайты FR")
    holder, _ = await held_off(world, (MARIA, world.hypothesis), (OLGA, french))
    await world.stoplist(email=IVAN)

    assert await stoplist_leads.close(world.session, now=NOW) == 1

    stopped = Row(
        LeadStatus.REJECTED,
        RejectionReason.STOPLIST.value,
        f"стоп-лист продаж: адрес {IVAN}",
        "fixture:valid",
        93,
        NOW,
    )
    returned = back(holder, "сайты DE", "сайты EN")
    assert await world.rows() == [stopped, returned, held_by(holder, "сайты DE")]


async def test_a_company_stoplisted_whole_returns_nobody_to_work(world: World) -> None:
    """Стоп-лист закрыл домен компании: вернувшегося закрывает он же, и следующего — тоже. Слова
    «вернулся, компания свободна» у компании из стоп-листа были бы неправдой."""
    french = await world.hypothesis_named("сайты FR")
    await held_off(world, (MARIA, world.hypothesis), (OLGA, french))
    await world.stoplist(host=ACME)

    assert await stoplist_leads.close(world.session, now=NOW) == 1

    stop = f"стоп-лист продаж: домен {ACME}"
    closed = rejected(RejectionReason.STOPLIST, stop)
    holder = Row(
        LeadStatus.REJECTED, RejectionReason.STOPLIST.value, stop, "fixture:valid", 93, NOW
    )
    assert await world.rows() == [holder, closed, closed]


async def test_another_live_lead_of_the_company_keeps_the_held_off_rejected(world: World) -> None:
    """Держатель отклонён, но у компании есть ещё живой лид (второй человек его гипотезы):
    компания не свободна — никто не возвращается, ни стоп-листом, ни очисткой."""
    holder, _ = await held_off(world, (MARIA, world.hypothesis))
    german = await world.session.get(SalesHypothesisModel, holder.hypothesis_id)
    await world.lead(f"petr@{ACME}", status=LeadStatus.READY, hypothesis=german)
    await world.stoplist(email=IVAN)
    await stoplist_leads.close(world.session, now=NOW)

    report = await world.clean()

    rows = await world.rows()
    assert rows[1:] == [held_by(holder, "сайты DE"), Row(LeadStatus.READY)]
    assert report.checked == 0


async def test_only_a_lead_held_off_by_a_holder_of_this_company_returns(world: World) -> None:
    """Возвращается только лид, отклонённый держателем этой компании: код дубля и слова с номером
    её отклонённого держателя. Дубль адреса, слова держания с другим кодом и слова, где держатель —
    лид другого домена, заведены раньше, но назад не идут (п. 2в). Две последние строки — прямо
    в базе: их не пишет ни один путь."""
    german = await world.hypothesis_named("сайты DE")
    holder = await world.lead(IVAN, status=LeadStatus.READY, hypothesis=german)
    holder.verification_status, holder.verification_score, holder.verified_at = (
        "fixture:valid",
        93,
        NOW,
    )
    await world.lead(IVAN)  # тот же адрес — дубль адреса
    stranger = await world.lead("petr@beta.example.test", status=LeadStatus.REJECTED)
    foreign = await world.lead(OLGA, status=LeadStatus.REJECTED)
    foreign.rejection_reason = RejectionReason.DUPLICATE.value
    foreign.cleaning_note = Holder(
        stranger.id, german.id, "сайты DE", "beta.example.test", True
    ).words
    coded = await world.lead(f"anna@{ACME}", status=LeadStatus.REJECTED)
    coded.rejection_reason = RejectionReason.UNUSABLE.value
    coded.cleaning_note = Holder(holder.id, german.id, "сайты DE", ACME, True).words
    await world.lead(MARIA)
    await world.clean()
    before = await world.rows()
    holder.status = LeadStatus.REJECTED  # держателя отклонили не очисткой
    await world.session.flush()

    report = await world.clean()

    rows = await world.rows()
    assert rows[1:5] == before[1:5]
    assert rows[5] == READY
    assert report == CleaningReport(checked=1, ready=1, verified=1, verifier="fixture")


async def test_a_returned_lead_rejected_by_its_check_hands_the_company_to_the_next(
    world: World,
) -> None:
    """Цепочка: вернувшегося отклонила проверка (ящика нет) — компания снова свободна, держатель
    по-прежнему отклонён, и следующий круг того же прохода возвращает следующего раннего,
    отклонённого тем же держателем. Каждый раз — один."""
    french = await world.hypothesis_named("сайты FR")
    italian = await world.hypothesis_named("сайты IT")
    holder, _ = await held_off(
        world, (DEAD, world.hypothesis), (OLGA, french), (f"anna@{ACME}", italian)
    )
    holder.status = LeadStatus.REJECTED
    await world.session.flush()

    report = await world.clean()

    assert (await world.rows())[1:] == [dead(), READY, held_by(holder, "сайты DE")]
    assert (report.checked, report.ready, report.rejected) == (
        2,
        1,
        Counter({"undeliverable": 1}),
    )


async def test_a_holder_rejected_by_hand_frees_the_company_at_any_next_cleaning(
    world: World,
) -> None:
    """Держателя отклонили не очисткой (статус — прямо в базе). Очистка любой гипотезы — здесь
    гипотезы без лидов — в начале прохода находит свободную компанию по всей базе и возвращает
    раннего бесплатно: за чужого лида она не платит, адрес проверит очистка его гипотезы. Двух
    лидов одной гипотезы возврат не поднимает; повторный проход ничего не возвращает."""
    holder, _ = await held_off(world, (MARIA, world.hypothesis), (OLGA, world.hypothesis))
    holder.status = LeadStatus.REJECTED
    await world.session.flush()
    idle = await world.hypothesis_named("сайты IT")
    verifier = Counting()

    report = await cleaning.clean(world.session, verifier, hypothesis_id=idle.id, now=NOW)

    manual = Row(LeadStatus.REJECTED, None, None, "fixture:valid", 93, NOW)
    returned = back(holder, "сайты DE", "сайты EN")
    assert await world.rows() == [manual, returned, held_by(holder, "сайты DE")]
    assert (report, verifier.asked) == (CleaningReport(verifier="fixture"), [])

    again = await cleaning.clean(world.session, verifier, hypothesis_id=idle.id, now=NOW)
    assert await world.rows() == [manual, returned, held_by(holder, "сайты DE")]
    own = await cleaning.clean(world.session, verifier, hypothesis_id=world.hypothesis.id, now=NOW)

    assert await world.rows() == [manual, READY, held_by(holder, "сайты DE")]
    assert (again.checked, own.checked, verifier.asked) == (0, 1, [MARIA])


async def test_a_returned_lead_starts_without_its_old_verdict(world: World) -> None:
    """Вернувшийся — без прежнего вердикта: очистка проверяет его целиком, и новый `unknown` —
    первый, а не второй (иначе лида отклонило бы «не подтверждён и при повторной проверке»)."""
    holder, (early,) = await held_off(world, (MARIA, world.hypothesis))
    early.verification_status, early.verification_score = "hunter:unknown", 40
    early.verified_at = NOW - timedelta(days=9)
    holder.status = LeadStatus.REJECTED
    await world.session.flush()

    await Hunter({MARIA: [NOT_KNOWN]}).clean(world)

    note = (
        "адрес не подтверждён: почтовый сервер не дал проверке ответа (unknown) — к письмам не "
        f"идёт; очистка проверит снова не раньше {recheck_on(NOW):%d.%m.%Y}"
    )
    assert (await world.rows())[1] == Row(LeadStatus.NEW, None, note, "hunter:unknown", 40, NOW)


async def test_the_cleaning_window_counts_own_returned_leads_and_not_foreign_ones(
    world: World,
) -> None:
    """Окно перед очисткой («до N запросов») — честная верхняя граница: в N входят и свои лиды,
    которых вернёт поиск свободных компаний в начале прохода, а чужие — нет. Здесь у двух свободных
    компаний ранний отклонённый — лид «сайты EN» на одной и «сайты FR» на другой; проход «сайты EN»
    возвращает обоих, а платит ровно за N — за своего."""
    french = await world.hypothesis_named("сайты FR")
    acme, (own, late_foreign) = await held_off(world, (MARIA, world.hypothesis), (OLGA, french))
    german = await world.session.get(SalesHypothesisModel, acme.hypothesis_id)
    assert german is not None
    beta = await world.lead("ivan@beta.example.test", status=LeadStatus.READY, hypothesis=german)
    foreign = await world.lead("olga@beta.example.test", hypothesis=french)
    late_own = await world.lead("anna@beta.example.test")
    await world.clean()
    for holder in (acme, beta):
        holder.status = LeadStatus.REJECTED  # держателей отклонили не очисткой
    await world.session.flush()

    windows = [await clean_jobs.waiting(world.session, h.id) for h in (world.hypothesis, french)]
    assert [window.count for window in windows] == [1, 1]
    assert (await clean_jobs.waiting(world.session, german.id)).count == 0

    verifier = Counting()
    report = await cleaning.clean(
        world.session, verifier, hypothesis_id=world.hypothesis.id, now=NOW
    )

    assert verifier.asked == [own.email]
    assert report.checked == windows[0].count
    rows = await world.rows()
    assert rows[own.id - acme.id] == READY
    assert rows[foreign.id - acme.id] == back(beta, "сайты DE", "сайты FR")
    assert rows[late_foreign.id - acme.id] == held_by(acme, "сайты DE")
    assert rows[late_own.id - acme.id] == held_by(beta, "сайты DE", "beta.example.test")


async def test_free_mail_is_never_a_company_to_free(world: World) -> None:
    """Бесплатной почты правило не касается: отклонённый «из-за держателя» на `gmail.com` (так
    его не отклонит ни один путь — строки прямо в базе) назад не идёт."""
    german = await world.hypothesis_named("сайты DE")
    holder = await world.lead("ivan@gmail.com", status=LeadStatus.REJECTED, hypothesis=german)
    held = await world.lead("olga@gmail.com", status=LeadStatus.REJECTED)
    held.rejection_reason = RejectionReason.DUPLICATE.value
    held.cleaning_note = Holder(holder.id, german.id, "сайты DE", "gmail.com", True).words
    await world.session.flush()
    before = await world.rows()

    report = await world.clean()

    assert await world.rows() == before
    assert report.checked == 0
