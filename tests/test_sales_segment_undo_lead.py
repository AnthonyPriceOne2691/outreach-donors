"""Отмена «Принять» после заведения лида, пока письмо ему не ушло, — на настоящей базе.

Тест продаж 21.10: после «Принять» адрес находится и лид заводится почти сразу, а отмена отказывала,
как только лид заведён, — отменить «Принять» на деле не успевали. Проверяется путь целиком:

- лид снят — «отклонён» с причиной `undone`, его письмо в очереди — снято, отправка его не берёт;
  сайт — там же и с теми словами, куда его кладёт судья; числа прогона и воронки — без лида;
- «Принять» снова — тот же лид, без дубля и без «адрес уже у лида»: очистка его пропускает, сборка
  пишет письмо на том же месте;
- письмо ушло, уходит или могло уйти — отказ словами, ни сайт, ни лид, ни письмо не тронуты;
- `undoable` — правдиво; снятый лид отпускает компанию для лида другой гипотезы;
- гонки — на настоящих фиксациях (сессии своего движка): сборка письма посреди отмены, отмена посреди
  записи письма, отправка посреди отмены.

Сайты судит настоящий проход судьи с подделкой модели (`test_sales_segment_undo.judged`), продажи
подключены миром отправки (`test_sales_send_world`), проверяльщик выдуманный, модель письма —
подделка коридора. Ниши и языки разные: каталог курсов (английский), велосипедная мастерская
(русский), языковой портал (японский), ветклиника (польский). Домены и тексты выдуманы.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.core.domain import ContactSource, MessageStatus, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters.sending import NotQueuedError, Sending
from backend.features.sales import (
    funnel,
    hypotheses,
    lead_edit,
    off_queue,
    queue,
    segment_leads,
    segment_report,
    segment_sites,
)
from backend.features.sales import segment_undo as su
from backend.features.sales.hold_words import BACK, HELD
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.queue_report import QueueReport
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests import test_sales_send_world as w
from tests.segment_facts_fakes import facts
from tests.test_sales_segment_undo import (
    CLINIC,
    PORTAL,
    Said,
    _network,
    _paused_after,
    decided,
    judged,
    landed,
    undone,
)
from tests.test_sales_send import _seen, _transports
from tests.test_sales_unconfirmed import Hunter
from tests.test_send_race import MEETING, committed_sessions

__all__ = ["_network"]  # сеть прогона (DNS, платная ступень) — отсюда фикстуру видит pytest

Candidate = SalesSegmentCandidateModel

#: Каталог курсов английского: модель — посредник, в наблюдении «судья отказал бы»; человек принял
#: по ошибке. Слова — английские.
CATALOG = Said(
    "course-finder.example.test",
    "Compare English courses for adults from every school in town",
    facts(
        "no",
        "Compare English courses for adults",
        "a course directory that runs no classes itself",
        kind="intermediary",
    ),
)
#: Велосипедная мастерская: своё ли дело, по тексту не понять — «на проверку». Слова — русские.
REMONT = Said(
    "remont-velo.example.test",
    "Ремонт велосипедов — мастерская и прокат у парка",
    facts("unsure", "Ремонт велосипедов — мастерская", "не видно, своя ли мастерская или витрина"),
)
#: Письмо ушло, уходит или могло уйти (свой `Message-ID` прежней попытки) — отмене не поддаётся.
GONE = {
    "sending": {
        "status": MessageStatus.SENDING,
        "internet_message_id": "<1.try@mail.example.test>",
    },
    "sent": {"status": MessageStatus.SENT, "internet_message_id": "<1.sent@mail.example.test>"},
    "delivered": {
        "status": MessageStatus.DELIVERED,
        "internet_message_id": "<1.d@mail.example.test>",
    },
    "bounced": {"status": MessageStatus.BOUNCED, "internet_message_id": "<1.b@mail.example.test>"},
    "queued-after-a-try": {
        "status": MessageStatus.QUEUED,
        "internet_message_id": "<1.again@mail.example.test>",
    },
}


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    """Продажи подключены: «Отправитель», общая цепочка на английском и русском, ящик продаж."""
    return await w.world(session, monkeypatch)


def _email(said: Said) -> str:
    return f"hello@{said.host}"


async def _accepted(
    session: AsyncSession, said: Said
) -> tuple[SalesSegmentRunModel, Candidate, dict[str, Any]]:
    """Сайт, принятый человеком, и живой адрес на его домене — как после поиска адреса. Третье —
    куда сайт положил судья: туда его вернёт отмена."""
    run, made = await judged(session, said)
    site = made[said.host]
    before = landed(site)
    await decided(session, site, accept=True)
    session.add(
        ContactModel(domain_id=site.domain_id, email=_email(said), source=ContactSource.PAGE)
    )
    await session.commit()
    return run, site, before


async def _lead_of(
    session: AsyncSession, run: SalesSegmentRunModel, site: Candidate
) -> SalesLeadModel:
    """Этап лидов прогона заводит лида принятому сайту. Имени и компании сбор не знает — их вписали
    правкой на вкладке «emails»: цепочка мира просит их в письме."""
    made = await segment_leads.make_leads(session, run, FixtureVerifier())
    await session.commit()
    await session.refresh(site)
    assert (made["created"], site.lead_id is not None) == (1, True), made
    lead = await session.get(SalesLeadModel, site.lead_id)
    assert lead is not None
    assert lead.status is LeadStatus.READY, lead.cleaning_note
    await lead_edit.change(
        session, lead.id, {"name": "Ana Testova", "company": "Test Co"}, author_id=None
    )
    await session.commit()
    return lead


async def _queued(session: AsyncSession, hypothesis_id: int) -> MessageModel:
    """Первое письмо лида — сборкой очереди продаж."""
    report = await queue.build(session, w.CorridorRewriter(), hypothesis_id=hypothesis_id, limit=5)
    letter = await session.scalar(select(MessageModel))
    assert letter is not None, report.totals()
    assert letter.status is MessageStatus.QUEUED
    return letter


async def _count(session: AsyncSession, model: type[Any]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _numbers(session: AsyncSession, run: SalesSegmentRunModel) -> tuple[int, int, int]:
    """Числа прогона (принято, лидов) и воронки гипотезы (в очереди) — так их видит экран."""
    view = await segment_report.run_view(session, run.id)
    assert view is not None
    told = segment_report.summary(view)
    found = (await funnel.counts(session, funnel.Period())).get(run.hypothesis_id)
    return told["accepted"], told["leads"], 0 if found is None else found.queued


async def _went(session: AsyncSession, letter: MessageModel, values: dict[str, Any]) -> None:
    """Письмо — в состоянии `values`, как его оставила отправка."""
    await session.execute(
        update(MessageModel)
        .where(MessageModel.id == letter.id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    await session.refresh(letter)


async def sent_letter(
    session: AsyncSession, lead: SalesLeadModel, *, status: MessageStatus = MessageStatus.SENT
) -> MessageModel:
    """Диалог лида и его первое письмо в состоянии `status` — без сборки и почты: для отказов,
    где мир отправки не нужен. Свой `Message-ID` — у письма, которое отправка уже брала."""
    tried = status is not MessageStatus.QUEUED
    campaign = CampaignModel(name=f"Гипотеза №{lead.hypothesis_id}", stage=Stage.SALES)
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=lead.domain_id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=lead.domain_id,
        step=0,
        status=status,
        internet_message_id=f"<{lead.id}.first@mail.example.test>" if tried else None,
        idempotency_key=f"{Stage.SALES.value}:lead-{lead.id}:{lead.email}:0",
    )
    session.add_all(
        [
            SalesThreadModel(
                thread_id=thread.id, lead_id=lead.id, language="en", chain_version="chain-0"
            ),
            letter,
        ]
    )
    await session.commit()
    return letter


# --- лид снят, письмо не уходит ------------------------------------------------------------------


@pytest.mark.parametrize("said", [CATALOG, REMONT], ids=["en-catalog", "ru-workshop"])
async def test_an_undone_accept_takes_the_lead_and_its_queued_letter_off(
    session: AsyncSession, world: w.World, said: Said
) -> None:
    run, site, before = await _accepted(session, said)
    lead = await _lead_of(session, run, site)
    letter = await _queued(session, run.hypothesis_id)
    assert await _numbers(session, run) == (1, 1, 1)

    found = await undone(session, site)

    assert (found.was, found.repeated) == (Lane.ACCEPT, False)
    for row in (site, lead, letter):
        await session.refresh(row)
    assert landed(site) == before
    # Время — судьи, как до «Принять»: решения человека нет.
    assert (site.lead_id, site.decided_at, site.decided_by_user) == (
        None,
        before["judged_at"],
        None,
    )
    assert (lead.status, lead.rejection_reason, lead.cleaning_note) == (
        LeadStatus.REJECTED,
        "undone",
        su.UNDONE_NOTE,
    )
    assert (letter.status, letter.failure_reason) == (
        MessageStatus.STOPPED,
        off_queue.closed(su.UNDONE_NOTE),
    )
    # Отправка его не берёт: в очереди письма нет, почта не видит ничего.
    transports = _transports()
    with pytest.raises(NotQueuedError):
        await Sending(session, transports, now=w.NOW).send(letter.id)
    assert _seen(transports) == []
    # Числа прогона и воронки — без лида: сайт не принят, лида у прогона нет, в очереди пусто.
    assert await _numbers(session, run) == (0, 0, 0)


async def test_a_lead_without_a_letter_is_taken_off_and_the_assembly_writes_it_none(
    session: AsyncSession, world: w.World
) -> None:
    run, site, _ = await _accepted(session, CATALOG)
    lead = await _lead_of(session, run, site)

    await undone(session, site)
    report = await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=run.hypothesis_id, limit=5
    )

    await session.refresh(lead)
    assert (lead.status, lead.rejection_reason) == (LeadStatus.REJECTED, "undone")
    assert (report.prepared, await _count(session, MessageModel)) == (0, 0)


async def test_a_letter_taken_off_before_is_left_as_it_was(
    session: AsyncSession, world: w.World
) -> None:
    """Первое письмо сняли до отправки другим правилом (сайт в работе у другого направления) и
    нашего `Message-ID` у него нет — оно не уходило: лид снимается, письмо остаётся со своей
    причиной — её снятие лида не переписывает."""
    run, site, _ = await _accepted(session, REMONT)
    lead = await _lead_of(session, run, site)
    letter = await _queued(session, run.hypothesis_id)
    elsewhere = off_queue.ELSEWHERE[0]
    await _went(session, letter, {"status": MessageStatus.STOPPED, "failure_reason": elsewhere})

    await undone(session, site)

    await session.refresh(lead)
    await session.refresh(letter)
    assert (lead.status, lead.rejection_reason) == (LeadStatus.REJECTED, "undone")
    assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, elsewhere)


async def test_a_lead_the_cleaning_rejected_is_taken_off_and_its_reason_stays_in_the_journal(
    session: AsyncSession,
) -> None:
    """Очистка уже отклонила лида сайта (японский портал; адрес — дубль лида раньше): отмена снимает и
    его. Причина у лида теперь «решение отменено», а прежняя остаётся словами в том, что отмена
    отдаёт журналу (`LeadOff.was`), — иначе она пропала бы."""
    _, site, before = await _accepted(session, PORTAL)
    lead = SalesLeadModel(
        hypothesis_id=site.hypothesis_id,
        domain_id=site.domain_id,
        email=_email(PORTAL),
        source=LeadSource.COLLECTED,
        status=LeadStatus.REJECTED,
        rejection_reason=RejectionReason.DUPLICATE.value,
        cleaning_note="дубль: адрес уже у лида №7",
    )
    session.add(lead)
    await session.flush()
    site.lead_id = lead.id
    await session.commit()

    found = await undone(session, site)

    assert found.lead == su.LeadOff(
        lead.id, _email(PORTAL), letter_off=False, was="отклонён: дубль"
    )
    await session.refresh(lead)
    await session.refresh(site)
    assert (lead.status, lead.rejection_reason) == (LeadStatus.REJECTED, "undone")
    assert (landed(site), site.lead_id) == (before, None)


# --- «Принять» снова: тот же лид ---------------------------------------------------------------------


@pytest.mark.parametrize("said", [CATALOG, REMONT], ids=["en-catalog", "ru-workshop"])
async def test_accepting_again_brings_the_same_lead_back_and_its_letter_in_its_place(
    session: AsyncSession, world: w.World, said: Said
) -> None:
    """Новый лид с тем же адресом очистка отсекла бы дублем снятого («адрес уже у лида №…»), а лид
    ушёл бы из сбора насовсем. Этап лидов берёт снятого отменой — он снова «новый», очистка его
    пропускает, и сборка пишет снятое письмо заново на том же месте: тот же диалог, тот же ключ."""
    run, site, _ = await _accepted(session, said)
    lead = await _lead_of(session, run, site)
    letter = await _queued(session, run.hypothesis_id)
    await undone(session, site)

    await decided(session, site, accept=True)
    made = await segment_leads.make_leads(session, run, FixtureVerifier())
    await session.commit()
    rebuilt = await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=run.hypothesis_id, limit=5
    )

    for row in (site, lead, letter):
        await session.refresh(row)
    assert made["created"] == 1, made
    assert (site.lead_id, await _count(session, SalesLeadModel)) == (lead.id, 1)
    assert (lead.status, lead.rejection_reason, lead.email) == (
        LeadStatus.READY,
        None,
        _email(said),
    )
    assert (rebuilt.refreshed, rebuilt.prepared) == (1, 0), rebuilt.totals()
    assert (await _count(session, MessageModel), await _count(session, SalesThreadModel)) == (1, 1)
    assert (letter.status, letter.failure_reason) == (MessageStatus.QUEUED, None)
    assert await _numbers(session, run) == (1, 1, 1)


async def test_a_returned_lead_starts_its_address_check_afresh(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверка адреса ответила «не знаю» (`unknown`), лида сняли отменой и приняли сайт снова.
    Вернувшийся лид — без прежнего вердикта: новый `unknown` для него первый (лид ждёт перепроверки),
    а не второй — прежний отклонил бы его «адрес не подтверждён» (`unconfirmed.judged`). Проверяльщик
    — живой без сети, отвечает `unknown` (`test_sales_unconfirmed.Hunter`)."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "100000")
    run, made = await judged(session, CATALOG)
    site = made[CATALOG.host]
    await decided(session, site, accept=True)
    session.add(
        ContactModel(domain_id=site.domain_id, email=_email(CATALOG), source=ContactSource.PAGE)
    )
    await session.commit()
    await segment_leads.make_leads(session, run, Hunter())
    await session.commit()
    await session.refresh(site)
    lead = await session.get(SalesLeadModel, site.lead_id)
    assert lead is not None
    assert (lead.status, lead.verification_status) == (LeadStatus.NEW, "hunter:unknown")
    await undone(session, site)
    await decided(session, site, accept=True)

    await segment_leads.make_leads(session, run, Hunter())
    await session.commit()

    await session.refresh(lead)
    assert (lead.status, lead.rejection_reason) == (LeadStatus.NEW, None), lead.cleaning_note
    assert lead.verification_status == "hunter:unknown"
    assert "к письмам не идёт" in (lead.cleaning_note or "")


# --- письмо ушло: отказ словами ----------------------------------------------------------------------


@pytest.mark.parametrize("values", list(GONE.values()), ids=list(GONE))
async def test_a_letter_gone_or_going_refuses_the_undo_in_words_and_touches_nothing(
    session: AsyncSession, world: w.World, values: dict[str, Any]
) -> None:
    run, site, _ = await _accepted(session, CATALOG)
    lead = await _lead_of(session, run, site)
    letter = await _queued(session, run.hypothesis_id)
    await _went(session, letter, values)
    kept = (landed(site), site.lead_id, lead.status, lead.rejection_reason, letter.status)

    with pytest.raises(su.UndoRefusedError) as refused:
        await su.undo(session, site.id)

    assert str(refused.value) == su.GONE
    for row in (site, lead, letter):
        await session.refresh(row)
    assert (landed(site), site.lead_id, lead.status, lead.rejection_reason, letter.status) == kept
    assert not (await segment_sites.site_row(session, site)).undoable


async def test_a_letter_that_really_went_refuses_the_undo(
    session: AsyncSession, world: w.World
) -> None:
    """То же — письмом, которое ушло общей отправкой, а не записью состояния."""
    run, site, _ = await _accepted(session, REMONT)
    await _lead_of(session, run, site)
    letter = await _queued(session, run.hypothesis_id)
    transports = _transports()
    await Sending(session, transports, now=w.NOW).send(letter.id)
    assert len(_seen(transports)) == 1

    with pytest.raises(su.UndoRefusedError) as refused:
        await su.undo(session, site.id)

    assert str(refused.value) == su.GONE


# --- `undoable` — правдиво -------------------------------------------------------------------------


async def test_undoable_is_true_while_the_undo_would_go_through(
    session: AsyncSession, world: w.World
) -> None:
    """Принят без лида, с лидом без письма, с письмом в очереди — отмена пройдёт; письмо ушло,
    решил судья — нет. Одно правило у строки списка и у самой отмены."""
    run, made = await judged(session, CATALOG, REMONT, PORTAL, CLINIC)
    for host in (CATALOG.host, REMONT.host, PORTAL.host):
        await decided(session, made[host], accept=True)
    for host in (CATALOG.host, PORTAL.host):  # у мастерской адреса ещё нет — и лида нет
        found = ContactModel(
            domain_id=made[host].domain_id, email=f"hi@{host}", source=ContactSource.PAGE
        )
        session.add(found)
    await session.commit()
    await segment_leads.make_leads(session, run, FixtureVerifier())
    await session.commit()
    leads: dict[str, SalesLeadModel] = {}
    for host in (CATALOG.host, PORTAL.host):
        lead = await session.get(SalesLeadModel, made[host].lead_id)
        assert lead is not None, host
        leads[host] = lead
    await sent_letter(session, leads[PORTAL.host], status=MessageStatus.QUEUED)
    gone = await sent_letter(session, leads[CATALOG.host])

    page = await segment_sites.sites_page(session, segment_sites.SitesAsk(run_id=run.id))

    assert page is not None
    told = {row.host: row.undoable for row in page.rows}
    assert told == {CATALOG.host: False, REMONT.host: True, PORTAL.host: True, CLINIC.host: False}
    assert gone.status is MessageStatus.SENT
    for host, undoable in told.items():
        row = await segment_sites.site_row(session, made[host])
        assert row.undoable is undoable, host


# --- снятый лид отпускает компанию ------------------------------------------------------------------


async def test_the_lead_taken_off_lets_its_company_go(
    session: AsyncSession, world: w.World
) -> None:
    """Лид другой гипотезы на том же домене отклонён потому, что компанию держал наш лид («компания —
    в одной гипотезе»). Снятый отменой лид компанию больше не держит: тот лид возвращается и ждёт
    очистки своей гипотезы — как после стоп-листа продаж."""
    run, site, _ = await _accepted(session, CATALOG)
    lead = await _lead_of(session, run, site)
    owner = await session.get(SalesHypothesisModel, run.hypothesis_id)
    assert owner is not None
    other = await hypotheses.add(session, "Другая выдуманная гипотеза", None)
    early = SalesLeadModel(
        hypothesis_id=other.id,
        domain_id=site.domain_id,
        email=f"office@{CATALOG.host}",
        source=LeadSource.IMPORT,
        status=LeadStatus.REJECTED,
        rejection_reason=RejectionReason.DUPLICATE.value,
        cleaning_note=HELD.say(lead=lead.id, hypothesis=owner.name, host=CATALOG.host),
    )
    session.add(early)
    await session.commit()

    await undone(session, site)

    await session.refresh(early)
    assert (early.status, early.rejection_reason) == (LeadStatus.NEW, None)
    assert early.cleaning_note == BACK.say(lead=lead.id, hypothesis=owner.name, own=other.name)


# --- гонки: на настоящих фиксациях ------------------------------------------------------------------


async def _ready_lead(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, *, letter: bool
) -> tuple[int, int, int | None]:
    """Мир продаж, сайт, принятый человеком, его лид «готов» и, если `letter`, первое письмо в
    очереди — всё зафиксировано. Номера сайта, гипотезы и письма."""
    async with factory() as session:
        await w.world(session, monkeypatch)
        await session.commit()
        run, site, _ = await _accepted(session, CATALOG)
        await _lead_of(session, run, site)
        queued = await _queued(session, run.hypothesis_id) if letter else None
        return site.id, run.hypothesis_id, None if queued is None else queued.id


async def _building(factory: async_sessionmaker[AsyncSession], hypothesis_id: int) -> QueueReport:
    async with factory() as session:
        return await queue.build(
            session, w.CorridorRewriter(), hypothesis_id=hypothesis_id, limit=5
        )


async def _state(factory: async_sessionmaker[AsyncSession]) -> tuple[Any, ...]:
    """Лид, его письма (состояние и причина) и связи диалогов — после гонки."""
    async with factory() as session:
        lead = await session.scalar(select(SalesLeadModel))
        assert lead is not None
        letters = (
            await session.execute(
                select(MessageModel.status, MessageModel.failure_reason).order_by(MessageModel.id)
            )
        ).all()
        links = await _count(session, SalesThreadModel)
        return (lead.status, lead.rejection_reason), [tuple(row) for row in letters], links


async def _edited(factory: async_sessionmaker[AsyncSession]) -> None:
    """Лида поправили после сборки письма: следующая сборка пишет письмо заново на том же месте."""
    async with factory() as session:
        lead = await session.scalar(select(SalesLeadModel))
        assert lead is not None
        await lead_edit.change(session, lead.id, {"name": "Ana Novakova"}, author_id=None)
        await session.commit()


async def _let_go(task: asyncio.Future[Any], release: asyncio.Event) -> None:
    """Отпустить придержанную задачу и дождаться её конца — и когда тест упал раньше: висящая
    задача писала бы в базу, которую уже чистит `committed_sessions`."""
    release.set()
    await asyncio.gather(asyncio.wait_for(task, MEETING), return_exceptions=True)


@pytest.mark.parametrize("rebuilt", [False, True], ids=["new-letter", "letter-rebuilt-in-place"])
async def test_an_assembly_arriving_in_the_middle_of_an_undo_waits_and_writes_no_letter(
    monkeypatch: pytest.MonkeyPatch, rebuilt: bool
) -> None:
    """Сборка прочла лида «готовым» и ждёт модель; отмена приходит сейчас и ещё не зафиксирована.
    Сборка доходит до записи письма — и ждёт строку лида: после отмены лид снят, письма нет, а
    собранное раньше — не воскресает. Без замка строки лида запись письма ждала бы только ссылку
    диалога на лида — и записала бы письмо снятому лиду."""
    async with committed_sessions() as factory:
        site_id, hypothesis_id, letter_id = await _ready_lead(factory, monkeypatch, letter=rebuilt)
        if rebuilt:
            await _edited(factory)
        written, release = await _paused_after(monkeypatch, "build_followups", queue)
        assembly = asyncio.ensure_future(_building(factory, hypothesis_id))
        try:
            await asyncio.wait_for(written.wait(), MEETING)
            async with factory() as undoing:
                found = await su.undo(undoing, site_id)
                assert found is not None
                assert not found.repeated
                release.set()
                await asyncio.sleep(0.3)  # сборке — время упереться в строку лида
                assert not assembly.done()
                await undoing.commit()
        finally:
            await _let_go(assembly, release)
        report = await assembly

        assert (report.prepared, report.refreshed) == (0, 0), report.totals()
        assert report.waiting == {off_queue.WITHDRAWN: 1}
        lead, letters, links = await _state(factory)
        assert lead == (LeadStatus.REJECTED, "undone")
        closed = [(MessageStatus.STOPPED, off_queue.closed(su.UNDONE_NOTE))]
        assert (letters, links) == ((closed, 1) if rebuilt else ([], 0))
        assert (letter_id is not None) is rebuilt


async def test_an_undo_in_the_middle_of_writing_the_letter_waits_for_it_and_takes_it_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сборка записала письмо и держит строку лида до своей фиксации; отмена приходит сейчас — ждёт
    её и снимает уже зафиксированное письмо с очереди. Без замка строки лида отмена не видела бы
    письма, которое вот-вот встанет, и снятый лид остался бы с письмом в очереди."""
    async with committed_sessions() as factory:
        site_id, hypothesis_id, _ = await _ready_lead(factory, monkeypatch, letter=False)
        stored, release = await _paused_after(monkeypatch, "_write", queue)
        assembly = asyncio.ensure_future(_building(factory, hypothesis_id))
        try:
            await asyncio.wait_for(stored.wait(), MEETING)
            async with factory() as undoing:
                task = asyncio.ensure_future(su.undo(undoing, site_id))
                await asyncio.sleep(0.3)  # отмене — время упереться в строку лида
                assert not task.done()
                release.set()
                found = await asyncio.wait_for(task, MEETING)
                await undoing.commit()
        finally:
            await _let_go(assembly, release)
        report = await assembly

        assert found is not None
        assert found.was == Lane.ACCEPT
        assert report.prepared == 1, report.totals()
        lead, letters, links = await _state(factory)
        assert lead == (LeadStatus.REJECTED, "undone")
        assert (letters, links) == ([(MessageStatus.STOPPED, off_queue.closed(su.UNDONE_NOTE))], 1)


async def test_a_sending_in_the_middle_of_an_undo_finds_the_letter_taken_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отмена сняла письмо и ещё не зафиксирована; отправка (кнопка, пачка, автоотправка — путь
    один) приходит сейчас и упирается в строку письма: после отмены письма в очереди нет — почта не
    видит ничего, а не «снятый лид с ушедшим письмом»."""
    async with committed_sessions() as factory:
        site_id, _, letter_id = await _ready_lead(factory, monkeypatch, letter=True)
        assert letter_id is not None
        transports = _transports()

        async def sending() -> object:
            async with factory() as session:
                return await Sending(session, transports, now=w.NOW).send(letter_id)

        async with factory() as undoing:
            found = await su.undo(undoing, site_id)
            assert found is not None
            task = asyncio.ensure_future(sending())
            try:
                await asyncio.sleep(0.3)  # отправке — время упереться в строку письма
                assert not task.done()
                await undoing.commit()
            finally:
                [outcome] = await asyncio.gather(
                    asyncio.wait_for(task, MEETING), return_exceptions=True
                )

        assert isinstance(outcome, NotQueuedError), outcome
        assert _seen(transports) == []
        lead, letters, _ = await _state(factory)
        assert lead == (LeadStatus.REJECTED, "undone")
        assert letters == [(MessageStatus.STOPPED, off_queue.closed(su.UNDONE_NOTE))]


async def test_an_undo_after_the_sending_took_the_letter_is_refused_and_the_letter_goes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Обратный порядок: отправка взяла письмо («отправляется», зафиксировано) — отмена отказывает
    словами, лид и письмо целы; письмо уходит."""
    async with committed_sessions() as factory:
        site_id, _, letter_id = await _ready_lead(factory, monkeypatch, letter=True)
        assert letter_id is not None
        transports = _transports()
        handed, release = await _paused_after(monkeypatch, "_claim", Sending)

        async def sending() -> object:
            async with factory() as session:
                return await Sending(session, transports, now=w.NOW).send(letter_id)

        task = asyncio.ensure_future(sending())
        try:
            await asyncio.wait_for(handed.wait(), MEETING)
            async with factory() as undoing:
                outcome = await asyncio.gather(su.undo(undoing, site_id), return_exceptions=True)
        finally:
            await _let_go(task, release)

        [refused] = outcome
        assert isinstance(refused, su.UndoRefusedError), refused
        assert str(refused) == su.GONE
        assert len(_seen(transports)) == 1
        lead, letters, _ = await _state(factory)
        assert lead == (LeadStatus.READY, None)
        assert letters == [(MessageStatus.SENT, None)]


async def _other_held_lead(factory: async_sessionmaker[AsyncSession], site_id: int) -> int:
    """Лид другой гипотезы на домене сайта, отклонённый потому, что компанию держит лид сайта: снятие
    лида сайта вернёт его (`company_hold.freed`). Номер этого лида."""
    async with factory() as session:
        site = await session.get(Candidate, site_id)
        assert site is not None
        assert site.lead_id is not None
        owner = await session.get(SalesHypothesisModel, site.hypothesis_id)
        assert owner is not None
        other = await hypotheses.add(session, "Другая выдуманная гипотеза", None)
        early = SalesLeadModel(
            hypothesis_id=other.id,
            domain_id=site.domain_id,
            email=f"office@{CATALOG.host}",
            source=LeadSource.IMPORT,
            status=LeadStatus.REJECTED,
            rejection_reason=RejectionReason.DUPLICATE.value,
            cleaning_note=HELD.say(lead=site.lead_id, hypothesis=owner.name, host=CATALOG.host),
        )
        session.add(early)
        await session.commit()
        return early.id


@pytest.mark.parametrize("held", ["lead", "other"], ids=["its-lead", "other-hypothesis-lead"])
async def test_a_row_held_longer_than_the_limit_refuses_in_words_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, held: str
) -> None:
    """Строку лида сайта (сборка пишет ему письмо) или лида другой гипотезы, которого отпустит снятый
    держатель компании, держат дольше предела: отмена не висит и не падает пятисоткой, а отказывает
    словами «занято»; транзакция вызывающего жива, после отката ничего не тронуто."""
    monkeypatch.setattr(su, "LOCK_WAIT", "SET LOCAL lock_timeout = '200ms'")
    async with committed_sessions() as factory:
        site_id, _, _ = await _ready_lead(factory, monkeypatch, letter=False)
        async with factory() as session:
            site = await session.get(Candidate, site_id)
            assert site is not None
            lead_id = site.lead_id
        other_id = await _other_held_lead(factory, site_id)
        target = lead_id if held == "lead" else other_id
        async with factory() as holding:
            await holding.execute(
                select(SalesLeadModel.id).where(SalesLeadModel.id == target).with_for_update()
            )
            async with factory() as session:
                with pytest.raises(su.UndoBusyError) as refused:
                    await asyncio.wait_for(su.undo(session, site_id), MEETING)
                assert await _count(session, SalesLeadModel) == 2  # транзакция жива
                await session.rollback()
            await holding.rollback()

        assert str(refused.value) == su.BUSY
        async with factory() as session:
            site = await session.get(Candidate, site_id)
            assert site is not None
            assert (site.lane, site.lead_id) == (Lane.ACCEPT, lead_id)
            statuses = {
                lead.id: lead.status for lead in (await session.scalars(select(SalesLeadModel)))
            }
            assert statuses == {lead_id: LeadStatus.READY, other_id: LeadStatus.REJECTED}
