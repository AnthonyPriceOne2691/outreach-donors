"""«Пишите другому» в треде продаж — срез 2.3a: новый лид той же компании.

Всё на настоящей базе дерева: лид, очистка, стоп-листы, диалог, расход.
Модель подставная (её работа — срез 2.2); DNS — заглушкой, как в тестах
очистки; проверяльщик адресов — выдуманный (`FixtureVerifier`).
"""

from __future__ import annotations

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.config import contacts as contacts_cfg
from backend.config import sales as sales_cfg
from backend.features.contacts.mx import MailRoute
from backend.features.core.domain import Stage, SuppressionReason, ThreadStatus
from backend.features.core.models.ops import SuppressionModel, UsageRecordModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.outreach.threads import review_of
from backend.features.replies.pipeline import Inbox
from backend.features.sales import cleaning
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesStoplistModel,
)
from backend.features.sales.replies import SalesReplies
from backend.features.sales.reply_kind import KindFound, SalesKind
from backend.features.sales.verifier import FixtureVerifier
from backend.workers import sales_jobs
from sqlalchemy import Connection, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_model import ROOT, _migration
from tests.test_sales_reply_kind import _job_body_on_test_base
from tests.test_sales_reply_routing import (
    HOST,
    NOW,
    FakeClassifier,
    _incoming,
    sales_letter,
    secret,
)

__all__ = ["secret"]  # подпись адреса ответа — фикстура 2.1

#: Адрес коллеги проходит правила годности: зона `.example` — «под примеры», а `.example.test` нет
#: (как в тестах очистки).
COLLEAGUE = f"marketing@{HOST}.test"
MIGRATION = ROOT / "backend/migrations/versions/739817077a57_sales_lead_referred_from_thread.py"
REFERRAL_TEXT = f"Это не ко мне, пишите коллеге из маркетинга: {COLLEAGUE}"


@pytest.fixture(autouse=True)
def mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Домен адреса принимает почту: ступень MX проверяется тестами очистки."""

    async def route(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.MX

    monkeypatch.setattr(cleaning, "mail_route", route)


async def _origin(session: AsyncSession, letter: MessageModel) -> SalesLeadModel:
    """Лид исходного диалога: тот адрес, которому писали, на домене диалога."""
    hypothesis = SalesHypothesisModel(name="гипотеза для ответов")
    session.add(hypothesis)
    await session.flush()
    lead = SalesLeadModel(
        hypothesis_id=hypothesis.id,
        domain_id=letter.domain_id,
        email=f"ceo@{HOST}",
        company="Компания-пример",
        country="de",
        language="en",
        source=LeadSource.IMPORT,
        status=LeadStatus.READY,
    )
    session.add(lead)
    await session.flush()
    return lead


async def _answer(session: AsyncSession, letter: MessageModel, text: str) -> ReplyModel:
    got = await Inbox(session, now=NOW).accept(_incoming(letter, text))
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    return reply


def _sales(
    session: AsyncSession, found: KindFound | None = None
) -> tuple[SalesReplies, FakeClassifier]:
    model = FakeClassifier(found)
    return SalesReplies(session, model, verifier=FixtureVerifier, threshold=0.8, now=NOW), model


async def _leads(session: AsyncSession) -> list[SalesLeadModel]:
    return list((await session.scalars(select(SalesLeadModel).order_by(SalesLeadModel.id))).all())


def _referral(contact: str | None = COLLEAGUE, *, tokens: int = 0) -> KindFound:
    return KindFound(
        SalesKind.REFERRAL,
        0.9,
        quote="пишите коллеге из маркетинга",
        contact=contact,
        tokens=tokens,
    )


# --- A1: «пишите другому» с адресом ---------------------------------------------------------


async def test_a1_referral_makes_a_ready_lead_and_closes_the_thread(
    session: AsyncSession,
) -> None:
    letter = await sales_letter(session)
    origin = await _origin(session, letter)
    reply = await _answer(session, letter, REFERRAL_TEXT)
    sales, _ = _sales(session, _referral())

    handled = await sales.handle(reply.id)

    [_, lead] = await _leads(session)
    assert (lead.source, lead.status, lead.email) == (
        LeadSource.REFERRAL,
        LeadStatus.READY,
        COLLEAGUE,
    )
    assert (lead.domain_id, lead.hypothesis_id) == (origin.domain_id, origin.hypothesis_id)
    assert lead.referred_from_thread_id == letter.thread_id
    assert (lead.company, lead.country, lead.language) == ("Компания-пример", "de", "en")
    assert lead.verification_status == "fixture:valid", "очистка — как у любого лида"
    thread = await session.get(ThreadModel, letter.thread_id)
    assert thread is not None
    assert thread.status is ThreadStatus.CLOSED
    assert (handled.route, handled.waits) == ("referral", False)
    assert handled.reason == (
        f"назвал другого человека: новый лид №{lead.id} ({COLLEAGUE}) прошёл очистку "
        "и ждёт письма; диалог закрыт"
    )
    review = review_of(reply, Stage.SALES)
    assert (review.waiting, review.reason) == (False, handled.reason)


async def test_a1_referral_without_an_address_waits_for_a_human(session: AsyncSession) -> None:
    letter = await sales_letter(session)
    await _origin(session, letter)
    reply = await _answer(session, letter, "Это не ко мне, этим занимается отдел маркетинга.")
    sales, _ = _sales(session, _referral(contact=None))

    handled = await sales.handle(reply.id)

    assert len(await _leads(session)) == 1, "лида без адреса не выдумываем"
    assert (handled.route, handled.waits) == ("referral", True)
    assert "адреса в ответе нет" in str(handled.reason)
    assert review_of(reply, Stage.SALES).waiting is True


async def test_a1_referral_without_the_origin_lead_waits_for_a_human(
    session: AsyncSession,
) -> None:
    letter = await sales_letter(session)
    reply = await _answer(session, letter, REFERRAL_TEXT)
    sales, _ = _sales(session, _referral())

    handled = await sales.handle(reply.id)

    assert await _leads(session) == []
    assert handled.waits is True
    assert f"лид исходного диалога не найден — завести лида {COLLEAGUE} руками" in str(
        handled.reason
    )


async def test_a1_misconfigured_verifier_costs_one_model_call_and_no_retry(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    """Проверка адресов не настроена (`live` без ключа) — `ConfigError` встаёт уже
    после вызова модели. Задача не падает: лида заводит человек, расход за вызов
    записан, а повтор задачи (очередь, вторая доставка) модель не зовёт. Раньше задача
    падала, откат стирал расход, и каждый повтор очереди звал модель заново."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "")
    letter = await sales_letter(session)
    await _origin(session, letter)
    reply = await _answer(session, letter, REFERRAL_TEXT)
    model = FakeClassifier(_referral(tokens=41))
    _job_body_on_test_base(monkeypatch, session, model)

    first = await sales_jobs.handle(reply.id)
    again = await sales_jobs.handle(reply.id)

    assert model.calls == 1, "повтор задачи модель не зовёт"
    assert (first["route"], first["waits"]) == ("referral", True)
    assert first["reason"] == (
        "назвал другого человека: проверка адресов не настроена — завести лида руками"
    )
    assert again == {"reply": reply.id, "skipped": "уже разобран"}
    spent = (await session.execute(select(UsageRecordModel))).scalars().one()
    assert (spent.operation, spent.units) == ("sales_reply_kind", 41), "расход не стёрт"
    assert len(await _leads(session)) == 1, "лида без проверки адреса не заводим"


# --- A2: «пишите другому», а адрес в стоп-листе ---------------------------------------------


async def test_a2_referral_to_a_stoplisted_address_is_rejected_and_the_thread_closed(
    session: AsyncSession,
) -> None:
    letter = await sales_letter(session)
    await _origin(session, letter)
    session.add(SalesStoplistModel(email=COLLEAGUE, note="партнёр"))
    reply = await _answer(session, letter, REFERRAL_TEXT)
    sales, _ = _sales(session, _referral())

    handled = await sales.handle(reply.id)

    [_, lead] = await _leads(session)
    assert (lead.status, lead.rejection_reason) == (LeadStatus.REJECTED, RejectionReason.STOPLIST)
    assert lead.cleaning_note == f"стоп-лист продаж: адрес {COLLEAGUE}"
    thread = await session.get(ThreadModel, letter.thread_id)
    assert thread is not None
    assert thread.status is ThreadStatus.CLOSED
    assert handled.waits is False
    assert "отсеян очисткой — стоп-лист продаж" in str(handled.reason)


async def test_a2_referral_to_an_unsubscribed_address_is_rejected(session: AsyncSession) -> None:
    letter = await sales_letter(session)
    await _origin(session, letter)
    session.add(SuppressionModel(email=COLLEAGUE, reason=SuppressionReason.UNSUBSCRIBED))
    reply = await _answer(session, letter, REFERRAL_TEXT)
    sales, _ = _sales(session, _referral())

    await sales.handle(reply.id)

    [_, lead] = await _leads(session)
    assert (lead.status, lead.rejection_reason) == (
        LeadStatus.REJECTED,
        RejectionReason.UNSUBSCRIBED,
    )


async def test_a2_an_open_sales_thread_of_the_company_is_not_another_direction(
    session: AsyncSession,
) -> None:
    """Очистка не считает диалог продаж «другим направлением»: у компании бывает
    несколько лидов. Идущий диалог доноров на домене — другое направление."""
    letter = await sales_letter(session)
    origin = await _origin(session, letter)
    colleague = SalesLeadModel(
        hypothesis_id=origin.hypothesis_id,
        domain_id=letter.domain_id,
        email=COLLEAGUE,
        source=LeadSource.IMPORT,
        status=LeadStatus.NEW,
    )
    session.add(colleague)
    await session.flush()

    await cleaning.clean_leads(session, [colleague], FixtureVerifier(), now=NOW)
    await session.refresh(colleague)
    assert colleague.status is LeadStatus.READY

    donors = CampaignModel(stage=Stage.DONORS, name="Доноры", status="running")
    session.add(donors)
    await session.flush()
    session.add(ThreadModel(domain_id=letter.domain_id, campaign_id=donors.id, contact_id=None))
    colleague.status = LeadStatus.NEW
    await session.flush()
    await cleaning.clean_leads(session, [colleague], FixtureVerifier(), now=NOW)
    await session.refresh(colleague)
    assert colleague.rejection_reason == RejectionReason.OTHER_DIRECTION


# --- миграция ссылки на исходный диалог -------------------------------------------------------


def _referral_link(connection: Connection) -> tuple[bool, bool, str | None]:
    """Колонка, индекс и правило удаления ссылки лида на диалог — в базе сейчас."""
    column = connection.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'sales_leads' AND column_name = 'referred_from_thread_id'"
        )
    ).first()
    index = connection.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_sales_leads_referred_from_thread'")
    ).first()
    rule = connection.execute(
        text(
            "SELECT rc.delete_rule FROM information_schema.referential_constraints rc "
            "JOIN information_schema.key_column_usage k ON k.constraint_name = rc.constraint_name "
            "WHERE k.table_name = 'sales_leads' AND k.column_name = 'referred_from_thread_id'"
        )
    ).scalar()
    return column is not None, index is not None, rule


def _cycle(connection: Connection) -> list[tuple[bool, bool, str | None]]:
    """Ревизия ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие его
    не видит. DDL в Postgres транзакционен — внешняя транзакция теста всё откатит."""
    migration = _migration(MIGRATION)
    states = [_referral_link(connection)]
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        states.append(_referral_link(connection))
        migration.upgrade()
    states.append(_referral_link(connection))
    return states


async def test_migration_adds_the_link_and_takes_it_back(session: AsyncSession) -> None:
    connection = await session.connection()

    assert await connection.run_sync(_cycle) == [
        (True, True, "SET NULL"),
        (False, False, None),
        (True, True, "SET NULL"),
    ]
