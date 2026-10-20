"""Адрес не подтверждён — решение владельца 13.10: Hunter `unknown` к письмам не идёт,
`accept_all` идёт, как раньше (ревью стыков, база и проверка адресов, вопрос 2).

Первый `unknown` — лид не решён: статус прежний, вердикт записан, слова называют день
перепроверки, и до него очистка лида не берёт. В свой день — одна платная перепроверка под
месячным потолком; второй `unknown` — отказ с причиной `unconfirmed`, больше проверок нет. Лид
`ready` с `unknown`: первое письмо держат сборка и отправка, а цепочку с ушедшим первым письмом
`unknown` не обрывает. Сети нет: живой проверяльщик — подделка с именем `hunter`, почта домена —
заглушка. Адреса и компании выдуманы.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.config import contacts as contacts_cfg
from backend.config import sales as sales_cfg
from backend.features.contacts.mx import MailRoute
from backend.features.contacts.provider import ProviderError
from backend.features.core.domain import AuditAction, MessageStatus, UsageProvider
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.sending import NotReadyError, Sending
from backend.features.sales import (
    auto_lines,
    autobuild,
    autosend_window,
    clean_jobs,
    cleaning,
    lead_edit,
    mail,
    queue,
    unconfirmed,
    verifier,
)
from backend.features.sales.clean_report import CleaningReport
from backend.features.sales.models import LeadStatus, RejectionReason, SalesLeadModel
from backend.features.sales.verifier import Verdict
from backend.features.sales.verify_cap import reached
from sqlalchemy import Update, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_autobuild import _AutoJobs
from tests.test_sales_send import _transports

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Некруглый выдуманный момент очистки — не дата-обязательство.
NOW = datetime(2026, 10, 4, 9, 30, tzinfo=UTC)
WEEK = timedelta(days=verifier.RECHECK_DAYS)
JANE = "jane@acme.example.test"
OLGA = "olga@beta.example.test"
FIRST = (
    "адрес не подтверждён: почтовый сервер не дал проверке ответа (unknown) — к письмам не идёт; "
    "очистка проверит снова не раньше 11.10.2026"
)


class Hunter:
    """Живой проверяльщик без сети: слово Hunter по адресу (по умолчанию `unknown`), единица —
    платная; помнит, кого спрашивали."""

    name = "hunter"

    def __init__(self, default: str = "unknown", **said: str | Exception) -> None:
        self.default = default
        self.said = said
        self.asked: list[str] = []

    async def verify(self, email: str) -> Verdict:
        self.asked.append(email)
        answer = self.said.get(email.partition("@")[0], self.default)
        if isinstance(answer, Exception):
            raise answer
        return Verdict(answer, 23, units=1)


@pytest.fixture(autouse=True)
def _month_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Месячный потолок задан с запасом; тест потолка ставит свой."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "100000")


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.MX

    monkeypatch.setattr(cleaning, "mail_route", route)


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    """Подключённые продажи, живой проверяльщик в настройке — с ключом: окно очистки его судит."""
    found = await w.world(session, monkeypatch)
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "k-test")
    return found


async def _new(session: AsyncSession, world: w.World, email: str, **fields: Any) -> SalesLeadModel:
    return await w.lead(
        session, world.hypothesis_id, email, **({"status": LeadStatus.NEW} | fields)
    )


async def _clean(
    session: AsyncSession, world: w.World, said: Hunter, moment: datetime
) -> CleaningReport:
    return await cleaning.clean(session, said, hypothesis_id=world.hypothesis_id, now=moment)


def _state(lead: SalesLeadModel) -> tuple[Any, ...]:
    return (
        lead.status,
        lead.rejection_reason,
        lead.cleaning_note,
        lead.verification_status,
        lead.verified_at,
    )


async def _units(session: AsyncSession) -> int:
    found = await session.scalars(
        select(UsageRecordModel.units).where(UsageRecordModel.provider == UsageProvider.HUNTER)
    )
    return sum(units or 0 for units in found)


# --- первый ответ и день перепроверки --------------------------------------------------------


async def test_first_unknown_keeps_the_new_lead_out_of_letters_until_its_recheck_day(
    session: AsyncSession, world: w.World
) -> None:
    lead = await _new(session, world, JANE)
    said = Hunter()

    report = await _clean(session, world, said, NOW)

    await session.refresh(lead)
    assert _state(lead) == (LeadStatus.NEW, None, FIRST, "hunter:unknown", NOW)
    assert report == CleaningReport(
        checked=1,
        unconfirmed=1,
        recheck_on=date(2026, 10, 11),
        verified=1,
        paid_units=1,
        verifier="hunter",
    )
    assert await _units(session) == 1
    # До дня перепроверки очистка лида не берёт и не платит; в окне очистки его нет.
    early = await _clean(session, world, said, NOW + WEEK - timedelta(minutes=1))
    assert (early.checked, said.asked) == (0, [JANE])


async def test_on_its_day_a_second_unknown_rejects_the_lead_for_good(
    session: AsyncSession, world: w.World
) -> None:
    lead = await _new(session, world, JANE)
    said = Hunter()
    await _clean(session, world, said, NOW)

    report = await _clean(session, world, said, NOW + WEEK)

    await session.refresh(lead)
    assert _state(lead) == (
        LeadStatus.REJECTED,
        RejectionReason.UNCONFIRMED.value,
        unconfirmed.TWICE,
        "hunter:unknown",
        NOW + WEEK,
    )
    assert report == CleaningReport(
        checked=1,
        rejected=Counter({"unconfirmed": 1}),
        verified=1,
        paid_units=1,
        verifier="hunter",
    )
    # Больше его не проверяют: лид стоил две проверки, вечного круга нет.
    later = await _clean(session, world, said, NOW + 10 * WEEK)
    assert (later.checked, said.asked, await _units(session)) == (0, [JANE, JANE], 2)


async def test_on_its_day_a_confirmed_address_lets_the_lead_into_letters(
    session: AsyncSession, world: w.World
) -> None:
    lead = await _new(session, world, JANE)
    await _clean(session, world, Hunter(), NOW)

    report = await _clean(session, world, Hunter("valid"), NOW + WEEK)

    await session.refresh(lead)
    assert _state(lead) == (LeadStatus.READY, None, None, "hunter:valid", NOW + WEEK)
    assert (report.ready, report.unconfirmed) == (1, 0)


async def test_accept_all_goes_to_letters_as_before(session: AsyncSession, world: w.World) -> None:
    """Решение владельца 13.10: «сервер принимает любой адрес» — к письмам, как сейчас."""
    lead = await _new(session, world, JANE)

    report = await _clean(session, world, Hunter("accept_all"), NOW)

    await session.refresh(lead)
    assert _state(lead) == (LeadStatus.READY, None, None, "hunter:accept_all", NOW)
    assert (report.ready, report.unconfirmed) == (1, 0)


async def test_a_made_up_unknown_does_not_wait_under_the_live_verifier(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Выдуманный `unknown` при живом проверяльщике не годен — очистка спрашивает Hunter сразу,
    а при выдуманном проверяльщике свой `unknown` ждёт дня, как живой."""
    await _new(session, world, JANE, verification_status="fixture:unknown", verified_at=NOW)
    live = Hunter("valid")
    assert (await _clean(session, world, live, NOW)).checked == 1
    assert live.asked == [JANE]

    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "fixture")
    await _new(session, world, OLGA, verification_status="fixture:unknown", verified_at=NOW)
    made_up = await cleaning.clean(
        session, verifier.FixtureVerifier(), hypothesis_id=world.hypothesis_id, now=NOW
    )
    assert made_up.checked == 0


# --- перепроверка: потолок и отказ сервиса ----------------------------------------------------


async def test_the_month_cap_holds_the_rechecks_too(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "1")
    monkeypatch.setattr(cleaning, "VERIFY_CONCURRENCY", 1)
    old = NOW - WEEK
    jane = await _new(session, world, JANE, verification_status="hunter:unknown", verified_at=old)
    olga = await _new(session, world, OLGA, verification_status="hunter:unknown", verified_at=old)
    said = Hunter()

    report = await _clean(session, world, said, NOW)

    assert said.asked == [JANE]  # вторая перепроверка встала на потолке, Hunter не спрошен
    assert report.stopped == reached(1, 1)
    await session.refresh(jane)
    await session.refresh(olga)
    assert jane.status is LeadStatus.REJECTED
    assert _state(olga) == (
        LeadStatus.NEW,
        None,
        f"{cleaning.NOT_VERIFIED}: {reached(1, 1)}",
        "hunter:unknown",
        old,
    )


async def test_a_failed_recheck_keeps_the_first_unknown_so_the_next_one_is_the_second(
    session: AsyncSession, world: w.World
) -> None:
    """Отказ сервиса на перепроверке не стирает первый `unknown`: иначе следующий `unknown`
    сошёл бы снова за первый, и лид ждал бы ещё неделю — лишняя платная проверка."""
    old = NOW - WEEK
    lead = await _new(session, world, JANE, verification_status="hunter:unknown", verified_at=old)
    down = ProviderError("провайдер не ответил (HTTP 503)")

    await _clean(session, world, Hunter(jane=down), NOW)

    await session.refresh(lead)
    assert _state(lead) == (
        LeadStatus.NEW,
        None,
        f"{cleaning.NOT_VERIFIED}: {down}",
        "hunter:unknown",
        old,
    )
    await _clean(session, world, Hunter(), NOW + timedelta(hours=1))
    await session.refresh(lead)
    assert (lead.status, lead.rejection_reason) == (LeadStatus.REJECTED, "unconfirmed")


# --- лид «готов»: первое письмо держат, цепочку не обрывают -----------------------------------


async def test_a_ready_lead_with_an_unknown_address_gets_no_first_letter(
    session: AsyncSession, world: w.World
) -> None:
    """«Готов» с `unknown` — лид, очищенный до решения 13.10, или перепроверка выдуманного
    вердикта. Сборка такого лида не собирает (модель не платит), а собранное до решения письмо
    отправка не пускает — словами, письмо ждёт в очереди."""
    jane = await w.lead(session, world.hypothesis_id, JANE, verification_status="hunter:valid")
    await w.lead(session, world.hypothesis_id, OLGA, verification_status="hunter:unknown")

    report = await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5
    )

    assert (report.prepared, dict(report.waiting)) == (1, {unconfirmed.FIRST_LETTER: 1})
    jane.verification_status = "hunter:unknown"  # перепроверка сказала unknown после сборки
    await session.flush()
    letter = await session.scalar(select(MessageModel.id))
    assert letter is not None
    with pytest.raises(NotReadyError, match=re.escape(unconfirmed.FIRST_LETTER)):
        await Sending(session, _transports(), now=w.NOW).send(letter)


async def _recheck_written(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch, *, sent: bool
) -> SalesLeadModel:
    """Пилот при выдуманном проверяльщике: лиду собрано (и, если `sent`, ушло) первое письмо;
    включили живой — перепроверка ответила `unknown`."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "fixture")
    lead = await w.lead(session, world.hypothesis_id, JANE, verification_status="fixture:valid")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5)
    if sent:
        for letter in await session.scalars(select(MessageModel)):
            letter.status = MessageStatus.SENT
        await session.flush()
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    await _clean(session, world, Hunter(), NOW)
    await session.refresh(lead)
    return lead


async def test_an_unknown_recheck_does_not_break_the_chain_of_a_written_lead(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пилотному лиду уже писали; перепроверка — `unknown`. Лид остаётся `ready`: снятый, он
    потерял бы цепочку навсегда (`LeadStoppedError` прохода добивок) и черновики ответа. Адрес
    письмо уже принял — перепроверки ему нет, в счёт «ждут очистки» он не входит."""
    lead = await _recheck_written(session, world, monkeypatch, sent=True)

    assert (lead.status, lead.verification_status) == (LeadStatus.READY, "hunter:unknown")
    assert lead.cleaning_note == (
        "адрес не подтверждён: почтовый сервер не дал проверке ответа (unknown) — первое письмо "
        "не уходит, очистка проверит снова не раньше 11.10.2026; если оно уже ушло, цепочка идёт "
        "без перепроверки"
    )
    dialog = await mail.lead_dialog(session, lead.id)
    assert dialog is not None
    assert await mail.unwritable(session, dialog) is None  # добивки и ответы идут
    later = Hunter()
    assert (await _clean(session, world, later, NOW + 2 * WEEK)).checked == 0
    assert later.asked == []


async def test_an_unwritten_ready_lead_is_decided_on_its_day(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Письмо в очереди ещё не ушло: оно ждёт, а в свой день очистка решает лида снова."""
    lead = await _recheck_written(session, world, monkeypatch, sent=False)
    assert (lead.status, lead.verification_status) == (LeadStatus.READY, "hunter:unknown")

    assert (await _clean(session, world, Hunter(), NOW + WEEK - timedelta(minutes=1))).checked == 0
    await _clean(session, world, Hunter(), NOW + WEEK)

    await session.refresh(lead)
    assert (lead.status, lead.rejection_reason) == (LeadStatus.REJECTED, "unconfirmed")


@pytest.mark.parametrize(
    ("provider", "status", "held"),
    [
        ("live", None, None),
        ("live", "hunter:valid", None),
        ("live", "hunter:accept_all", None),
        ("live", "hunter:unknown", unconfirmed.FIRST_LETTER),
        ("live", "fixture:valid", verifier.MADE_UP),  # выдуманный при живом — прежнее правило
        ("fixture", "fixture:valid", None),
        ("fixture", "fixture:unknown", unconfirmed.FIRST_LETTER),
    ],
)
def test_first_letter_is_held_by_a_made_up_or_an_unknown_verdict_only(
    monkeypatch: pytest.MonkeyPatch, provider: str, status: str | None, held: str | None
) -> None:
    """Одно правило адреса у сборки и отправки первого письма: выдуманный вердикт при живом
    проверяльщике (`verifier.unproven`) и `unknown` держат его, `accept_all` — нет."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", provider)
    lead = SalesLeadModel(email=JANE, verification_status=status)

    assert unconfirmed.first_letter(lead) == held


# --- экран и окно очистки ---------------------------------------------------------------------


async def test_the_clean_button_counts_only_leads_whose_day_has_come(
    client: httpx.AsyncClient,
    session: AsyncSession,
    world: w.World,
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    """«Новые» — оба лида, а очистка возьмёт одного: у второго первый `unknown` и день
    перепроверки не настал. Число у кнопки — то же условие, что у прохода; слова — у лида."""
    moment = datetime.now(UTC)
    await _new(session, world, JANE)
    await _new(session, world, OLGA)
    await _clean(session, world, Hunter(jane="valid"), moment)  # Ольга — первый unknown
    await _new(session, world, "kim@gamma.example.test")
    await session.commit()
    await make_user("seller@ours.example.test")
    headers = bearer(await sign_in("seller@ours.example.test"))

    cards = (await client.get("/api/sales/hypotheses", headers=headers)).json()
    asked = await client.get(f"/api/sales/clean?hypothesis={world.hypothesis_id}", headers=headers)
    leads = (await client.get("/api/sales/leads", headers=headers)).json()

    [card] = cards["rows"]
    assert (card["leads"]["new"], card["waiting"]) == (2, 1)
    assert asked.json()["waiting"] == 1
    notes = {row["email"]: row["cleaning_note"] for row in leads["rows"]}
    day = f"{unconfirmed.recheck_on(moment):%d.%m.%Y}"
    assert notes[OLGA] == FIRST.replace("11.10.2026", day)
    assert (await clean_jobs.waiting(session, world.hypothesis_id)).count == 1


def test_the_job_report_names_the_unconfirmed_and_their_day() -> None:
    found = CleaningReport(checked=2, ready=1, unconfirmed=1, recheck_on=date(2026, 10, 11))

    said = clean_jobs.report_of(found)

    assert (said["unconfirmed"], said["recheck_on"]) == (1, "2026-10-11")
    assert clean_jobs.report_of(CleaningReport())["recheck_on"] is None


# --- автосборка и автоотправка — то же правило адреса, что у сборки и отправки -----------------


async def _letters(
    session: AsyncSession, world: w.World, *emails: str
) -> dict[str, tuple[SalesLeadModel, MessageModel]]:
    """Лиды «готов» с проверенным адресом и их первые письма в очереди — по адресу."""
    leads = [
        await w.lead(session, world.hypothesis_id, email, verification_status="hunter:valid")
        for email in emails
    ]
    await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=len(emails)
    )
    found: dict[str, tuple[SalesLeadModel, MessageModel]] = {}
    for lead in leads:
        dialog = await mail.lead_dialog(session, lead.id)
        assert dialog is not None
        letter = await session.scalar(
            select(MessageModel).where(
                MessageModel.thread_id == dialog.link.thread_id, MessageModel.step == FIRST_STEP
            )
        )
        assert letter is not None
        found[lead.email] = (lead, letter)
    return found


async def test_a_queued_letter_of_an_unknown_lead_is_not_counted_as_going(
    session: AsyncSession, world: w.World
) -> None:
    """Лид очищен Hunter до решения 13.10 — «готов» с `unknown`, письмо в очереди. Отправка ему
    откажет, поэтому в счёт очереди на сутки оно не идёт, а гипотеза из-за него к сборке не
    встаёт: сборка его всё равно пропустит."""
    world.sales_box.daily_cap = 2
    found = await _letters(session, world, JANE, OLGA)
    jane, held = found[JANE]
    jane.verification_status = "hunter:unknown"
    await session.flush()

    counted = await autobuild.queued(session)
    plan = await autobuild.plan(session, _AutoJobs(), now=w.NOW)

    assert (counted.going, counted.held, counted.redo) == (1, frozenset({held.id}), frozenset())
    assert (plan.have, plan.short) == (1, 1)
    assert (plan.hypothesis_id, plan.why) == (None, autobuild.NO_CANDIDATES)


async def test_an_unknown_lead_changed_after_its_letter_starts_no_empty_build_and_says_why(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Переход на живой проверяльщик: очистка — своей транзакцией, и время правки лида позже
    письма. Адрес судят раньше правки: письмо не «к пересборке», и автосборка не ставит пустую
    сборку каждые десять минут; отправка отказывает словами «не подтверждён», а не «соберите
    очередь заново»; строка автоотправки говорит то же."""
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    world.sales_box.daily_cap = 2
    found = await _letters(session, world, JANE, OLGA)
    for lead, letter in found.values():
        lead.verification_status = "hunter:unknown"
        lead.updated_at = letter.updated_at + timedelta(minutes=1)  # очистка — позже письма
    await session.flush()
    jobs = _AutoJobs()
    _, letter = found[JANE]

    plan = await autobuild.run(session, jobs, now=w.NOW)
    line = await auto_lines.autosend_line(session, w.NOW)
    with pytest.raises(NotReadyError) as refused:
        await Sending(session, _transports(), now=w.NOW).send(letter.id)

    assert (plan.hypothesis_id, plan.why, jobs.enqueued) == (None, autobuild.NO_CANDIDATES, [])
    builds = select(func.count()).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    assert await session.scalar(builds) == 0
    assert str(refused.value) == f"Письмо №{letter.id} не уходит: {unconfirmed.FIRST_LETTER}"
    assert line.line.endswith(f"Стоит: {autosend_window.HELD.format(2)}.")


async def test_ready_leads_without_a_letter_held_by_their_address_are_no_reason_to_build(
    session: AsyncSession, world: w.World
) -> None:
    """«Готов» без письма с `unknown` сборка всё равно пропустит: гипотеза ради него к сборке не
    встаёт — иначе пустая сборка каждые десять минут до его перепроверки. Лид с подтверждённым
    адресом — встаёт."""
    await w.lead(session, world.hypothesis_id, JANE, verification_status="hunter:unknown")
    jobs = _AutoJobs()

    held_only = await autobuild.plan(session, jobs, now=w.NOW)
    await w.lead(session, world.hypothesis_id, OLGA, verification_status="hunter:valid")
    one_writable = await autobuild.plan(session, jobs, now=w.NOW)

    assert (held_only.hypothesis_id, held_only.why) == (None, autobuild.NO_CANDIDATES)
    assert one_writable.hypothesis_id == world.hypothesis_id


async def test_the_sql_rule_holds_the_same_leads_as_the_first_letter_rule(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Счёт автосборки (`unconfirmed.holds`, условием SQL) и сборка с отправкой
    (`unconfirmed.first_letter`) держат одних и тех же лидов — при живом проверяльщике и при
    выдуманном."""
    statuses = (None, "hunter:valid", "hunter:accept_all", "hunter:unknown")
    leads = [
        await w.lead(
            session, world.hypothesis_id, f"lead{n}@firm{n}.example.test", verification_status=said
        )
        for n, said in enumerate((*statuses, "fixture:valid", "fixture:unknown"))
    ]
    for provider in ("live", "fixture"):
        monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", provider)

        by_sql = set(await session.scalars(select(SalesLeadModel.id).where(unconfirmed.holds())))

        by_rule = {lead.id for lead in leads if unconfirmed.first_letter(lead) is not None}
        assert (provider, by_sql) == (provider, by_rule)


async def test_the_cleaning_does_not_make_a_lead_edited_so_its_letter_is_not_rebuilt(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Перепроверка выдуманного вердикта при живом ответила `valid`: имя и компания те же, и
    письмо из очереди уйдёт как есть. Очистка не двигает время правки лида: иначе письмо сошло бы
    за «собранное до правки», и сборка переписала бы его моделью зря."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "fixture")
    lead = await w.lead(session, world.hypothesis_id, JANE, verification_status="fixture:valid")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=1)
    letter = await session.scalar(select(MessageModel).where(MessageModel.step == FIRST_STEP))
    assert letter is not None
    earlier = letter.updated_at - timedelta(hours=1)
    lead.updated_at = letter.updated_at = earlier  # письмо и лид — из прошлых транзакций
    await session.flush()
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")

    await cleaning.clean(session, Hunter("valid"), hypothesis_id=world.hypothesis_id, now=NOW)

    await session.refresh(lead)
    await session.refresh(letter)
    assert (lead.verification_status, lead.updated_at) == ("hunter:valid", earlier)
    assert lead_edit.edited(lead, letter) is False
    assert (await autobuild.queued(session)).ready == frozenset({letter.id})


# --- запись партии очистки: правка человека и удалённый лид -----------------------------------


async def test_a_human_edit_landing_just_before_the_batch_write_keeps_its_time(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правка лида делает flush раньше коммита. Очистка, прочитав время правки в этом окне,
    записала бы прежнее поверх нового — `edited()` правки не увидел бы, и письмо со старыми
    именем или компанией ушло бы. Время правки пишется выражением колонки: правка, легшая перед
    самой записью партии, остаётся. И `onupdate` колонки явное значение не перебивает."""
    lead = await _new(session, world, JANE)
    edit_time = datetime.now(UTC) + timedelta(hours=1)
    execute = session.execute
    written: list[str] = []

    async def edit_first(statement: Any, params: Any = None, **kwargs: Any) -> Any:
        if isinstance(statement, Update) and isinstance(params, list) and not written:
            written.append(str(statement.compile()))
            edit = update(SalesLeadModel).where(SalesLeadModel.id == lead.id)
            await execute(edit.values(updated_at=edit_time))  # правка — между чтением и записью
        return await execute(statement, params, **kwargs)

    monkeypatch.setattr(session, "execute", edit_first)
    await _clean(session, world, Hunter("valid"), NOW)
    monkeypatch.undo()

    await session.refresh(lead)
    assert (lead.status, lead.verification_status, lead.updated_at) == (
        LeadStatus.READY,
        "hunter:valid",
        edit_time,
    )
    [sql] = written
    assert "updated_at=sales_leads.updated_at" in sql


async def test_a_lead_deleted_mid_pass_is_skipped_and_the_batch_keeps_the_rest_and_the_spend(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Лида удалили между выборкой прохода и записью партии (чистка следов проверки): его строка
    просто не обновится. Вердикты остальных и строка расхода — за обе проверки, Hunter списал
    обе, — записаны: откат партии стёр бы оплаченное, и повтор задачи проверил бы адреса заново."""
    monkeypatch.setattr(cleaning, "VERIFY_CONCURRENCY", 1)
    jane = await _new(session, world, JANE)
    olga = await _new(session, world, OLGA)
    gone = olga.id

    class Deleting(Hunter):
        async def verify(self, email: str) -> Verdict:
            said = await super().verify(email)
            if email == OLGA:  # удалили, пока проход спрашивал Hunter
                await session.execute(delete(SalesLeadModel).where(SalesLeadModel.id == gone))
            return said

    report = await _clean(session, world, Deleting("valid"), NOW)

    await session.refresh(jane)
    assert (jane.status, jane.verification_status) == (LeadStatus.READY, "hunter:valid")
    assert await session.scalar(select(func.count()).where(SalesLeadModel.id == gone)) == 0
    assert (report.verified, report.paid_units, await _units(session)) == (2, 2, 2)
