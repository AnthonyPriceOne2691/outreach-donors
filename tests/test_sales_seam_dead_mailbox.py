"""Ревью стыков, круг 2 (поручение координатора): автоответ «Jane ушла, пишите olga@…» на письмо
продаж.

Приём узнаёт такой автоответ правилом «мёртвый ящик» (`replies/classify.DEAD_MAILBOX`) и даёт ему
вид «отказ доставки»: цепочка встаёт. Адрес из него общий приём клал в контакты домена
(`replies/redirect.follow`) — у продаж контактов нет, есть лиды, и соседняя сессия для продаж
`follow` больше не зовёт: адрес остаётся модулю продаж.

- **Модуль продаж (правка здесь):** задача ответа продаж, получив такой ответ, берёт адрес из
  написанного рукой и заводит лида той же компании тем же путём, что «пишите другому»
  (`referral.refer`): та же гипотеза и домен, источник `referral`, очистка, исходный диалог
  закрыт, письмо — сборкой для него одного. Адрес на чужом домене — человеку словами (частный ящик
  бывшего сотрудника — не адрес компании); отказ доставки почтовой службы (адреса в нём — кому не
  доставлено, postmaster) и автоответ без адреса лида не заводят.
- **Общий код (`xfail(strict=True)`):** приём не отдаёт такой ответ очереди продаж — вид «отказ
  доставки» туда не ходит (`outcome.to_sales_queue`). Правка — в разделе «Для PR «общее»» отчёта
  ревью: продажам — `sales_pending` по правилу «мёртвый ящик» вместо `follow`.

Мир — подключённые продажи (`tests/test_sales_send_world.py`): первое письмо ушло лиду Jane;
модели нет — вид таких ответов модель не называет.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import ReplyKind, ThreadStatus
from backend.features.core.models.outreach import MessageModel, ReplyModel, ThreadModel
from backend.features.letters import reply_to
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Accepted, Inbox
from backend.features.sales.models import LeadSource, SalesLeadModel
from backend.features.sales.replies import SalesReplies
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_referral import mail_is_accepted
from tests.test_sales_reply_routing import FakeClassifier
from tests.test_sales_send import JANE, _first_sent

__all__ = ["mail_is_accepted"]  # домен адреса принимает почту — фикстура очистки

OLGA = "olga@acme.example.test"
#: Автоответ мёртвого ящика — заголовок автомата и «ушла из компании».
AUTO = {"Auto-Submitted": "auto-replied"}
LEFT = f"Jane has left the company. Please contact {OLGA} for all matters."


async def _sent(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> MessageModel:
    world = await w.world(session, monkeypatch)
    _, [first] = await _first_sent(session, world)
    return first


async def _accepted(
    session: AsyncSession,
    first: MessageModel,
    text: str,
    *,
    sender: str = JANE,
    subject: str = "Automatic reply: A made-up question",
    headers: dict[str, str] | None = None,
) -> tuple[Accepted, ReplyModel]:
    got = await Inbox(session, now=w.NOW + timedelta(hours=2)).accept(
        Incoming(
            message_id=f"<auto-{len(text)}@acme.example.test>",
            to=(
                reply_to.address_for(
                    first.id, sender_email=w.SALES_BOX, secret=outreach_cfg.INBOUND_SECRET
                ),
            ),
            from_email=sender,
            subject=subject,
            text=text,
            headers=AUTO if headers is None else headers,
        )
    )
    assert got.reply_id is not None
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    return got, reply


def _sales(session: AsyncSession) -> tuple[SalesReplies, FakeClassifier, list[tuple[int, int]]]:
    model = FakeClassifier()
    built: list[tuple[int, int]] = []
    sales = SalesReplies(
        session,
        model,
        verifier=FixtureVerifier,
        build_for=lambda hypothesis_id, lead_id: built.append((hypothesis_id, lead_id)),
        now=w.NOW + timedelta(hours=2),
    )
    return sales, model, built


async def _leads(session: AsyncSession) -> list[SalesLeadModel]:
    found = await session.scalars(select(SalesLeadModel).order_by(SalesLeadModel.id))
    return list(found)


# --- модуль продаж: адрес из автоответа мёртвого ящика — лид той же компании --------------------


async def test_a_colleague_named_by_the_dead_mailbox_becomes_a_lead_of_the_same_company(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await _sent(session, monkeypatch)
    got, reply = await _accepted(session, first, LEFT)
    assert got.kind is ReplyKind.BOUNCE
    sales, model, built = _sales(session)

    handled = await sales.handle(reply.id)
    await sales.write_to_referred(handled)

    jane, olga = await _leads(session)
    assert (olga.email, olga.source, olga.referred_from_thread_id) == (
        OLGA,
        LeadSource.REFERRAL,
        first.thread_id,
    )
    assert (olga.hypothesis_id, olga.domain_id, olga.company) == (
        jane.hypothesis_id,
        jane.domain_id,
        jane.company,
    )
    assert built == [(olga.hypothesis_id, olga.id)], "письмо новому лиду — сборкой для него"
    assert model.calls == 0, "вид такого ответа модель не называет"
    assert (handled.route, handled.waits) == ("referral", False)
    assert handled.reason is not None
    assert OLGA in handled.reason
    thread = await session.get(ThreadModel, first.thread_id)
    assert thread is not None
    assert thread.status is ThreadStatus.CLOSED
    await session.refresh(reply)
    assert (reply.model_parse or {}).get("reason") == handled.reason


async def test_the_same_answer_taken_again_makes_no_second_lead(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Задача живёт дольше кода: повтор задачи о том же ответе лида не задваивает."""
    first = await _sent(session, monkeypatch)
    _, reply = await _accepted(session, first, LEFT)
    sales, _, _ = _sales(session)
    await sales.handle(reply.id)

    again = await sales.handle(reply.id)

    assert again.skipped == "уже разобран"
    assert [lead.email for lead in await _leads(session)] == [JANE, OLGA]


async def test_an_address_elsewhere_is_left_to_a_human_in_words(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Частный ящик бывшего сотрудника — не адрес компании: лида нет, адрес — человеку словами."""
    first = await _sent(session, monkeypatch)
    _, reply = await _accepted(
        session, first, "Jane has left the company. Her new address is jane.doe@gmail.com."
    )
    sales, _, built = _sales(session)

    handled = await sales.handle(reply.id)

    assert [lead.email for lead in await _leads(session)] == [JANE]
    assert built == []
    assert (handled.route, handled.waits) == ("manual", True)
    assert handled.reason is not None
    assert "jane.doe@gmail.com" in handled.reason


@pytest.mark.parametrize(
    ("text", "sender", "subject"),
    [
        (
            f"Delivery to the following recipient failed permanently: {JANE}. "
            "The account jane.smith@acme.example.test does not exist either. "
            "Contact postmaster@acme.example.test for help.",
            "mailer-daemon@acme.example.test",
            "Delivery Status Notification (Failure)",
        ),
        ("Jane has left the company.", JANE, "Automatic reply: A made-up question"),
    ],
    ids=["mail-service-bounce", "dead-mailbox-without-an-address"],
)
async def test_a_bounce_without_a_named_colleague_makes_no_lead(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    sender: str,
    subject: str,
) -> None:
    """Отказ почтовой службы называет адреса (кому не доставлено, другой ящик компании,
    postmaster) — лида из него нет: фразы мёртвого ящика в нём нет; мёртвый ящик без адреса —
    тоже."""
    first = await _sent(session, monkeypatch)
    _, reply = await _accepted(session, first, text, sender=sender, subject=subject)
    assert reply.kind is ReplyKind.BOUNCE
    sales, _, built = _sales(session)

    handled = await sales.handle(reply.id)

    assert [lead.email for lead in await _leads(session)] == [JANE]
    assert built == []
    assert handled.route == "manual"


# --- общий код: приём не отдаёт такой ответ продажам ---------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код (replies/pipeline._settle и _redirect): автоответ мёртвого ящика в треде "
        "продаж — вид «отказ доставки», и очереди продаж его не отдают (outcome.to_sales_queue): "
        "названный адрес остаётся только в тексте ответа. Правка: при этапе продаж вместо "
        "redirect.follow — sales_pending по правилу DEAD_MAILBOX"
    ),
)
async def test_the_intake_hands_a_sales_dead_mailbox_answer_to_the_sales_queue(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await _sent(session, monkeypatch)

    got, reply = await _accepted(session, first, LEFT)

    assert got.kind is ReplyKind.BOUNCE
    assert got.to_sales == reply.id
