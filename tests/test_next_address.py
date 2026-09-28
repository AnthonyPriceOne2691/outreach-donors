"""Следующий адрес донора: кому снова можно первое письмо и на какой адрес.

Правило (`letters/attempts.py`): письмо, не дошедшее ни до кого, «писали»
не считается. Донор снова открыт, когда мёртвые все его письма — отказ
доставки или письмо на похороненный адрес, — никто не ответил и потолок
в три адреса не выбран. Мёртвый адрес и адрес, куда письмо уже уходило,
первым письмом не берутся.

Всё проверяется настоящей сборкой очереди на настоящей базе: до 28.09.2026
правило стояло в документах, а три барьера в коде держали его закрытым,
и увидеть это можно было только тем, что легло (или не легло) в таблицы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.features.contacts import manual
from backend.features.contacts.preference import DEAD
from backend.features.core.domain import (
    ContactSource,
    MessageStatus,
    ReplyKind,
    Stage,
    SuppressionReason,
    ThreadStatus,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters import attempts
from backend.features.letters.building import (
    BuildReport,
    BuildRequest,
    QueueBuilder,
    attempt_of,
    idempotency_key,
)
from backend.features.letters.recipients import Recipients
from backend.features.letters.rewrite import RewriteResult
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
HOST = "donor.example.test"


class TemplateOnly:
    """Модель, которая ничего не переписывает: адрес письма от неё не зависит."""

    async def rewrite(self, rendered: object, about: object) -> RewriteResult:
        return RewriteResult(notes=["модель в тесте не участвует"])


async def _donor(
    session: AsyncSession, *emails: str, host: str = HOST
) -> tuple[DomainModel, list[ContactModel]]:
    """Принятый донор и его адреса — в том порядке, в каком записаны."""
    domain = await make_donor(session, host)
    contacts = []
    for email in emails:
        contact = ContactModel(domain_id=domain.id, email=email, source=ContactSource.PAGE)
        session.add(contact)
        await session.flush()
        contacts.append(contact)
    return domain, contacts


def _bury(contact: ContactModel) -> None:
    """Так адрес хоронят событие отказа и автоответ мёртвого ящика."""
    contact.verification_status = DEAD
    contact.verification_score = 0


async def _letter(
    session: AsyncSession,
    domain: DomainModel,
    contact: ContactModel | None,
    *,
    status: MessageStatus,
    step: int = 0,
    stage: Stage = Stage.DONORS,
    thread: ThreadModel | None = None,
    attempt: int = 1,
) -> MessageModel:
    """Письмо домену в нужном состоянии — со своей рассылкой и диалогом."""
    if thread is None:
        campaign = CampaignModel(name=f"Прошлая {stage.value} {attempt}", stage=stage)
        session.add(campaign)
        await session.flush()
        thread = ThreadModel(
            domain_id=domain.id,
            campaign_id=campaign.id,
            contact_id=contact.id if contact else None,
        )
        session.add(thread)
        await session.flush()
    letter = MessageModel(
        campaign_id=thread.campaign_id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id if contact else None,
        step=step,
        status=status,
        subject="Guest article",
        body="Hi",
        sent_at=None if status in attempts.PENDING else NOW,
        idempotency_key=idempotency_key(stage=stage, host=domain.host, step=step, attempt=attempt),
    )
    session.add(letter)
    await session.flush()
    return letter


async def _build(session: AsyncSession, *, stage: Stage = Stage.DONORS) -> BuildReport:
    return await QueueBuilder(session, TemplateOnly()).build(  # type: ignore[arg-type]
        BuildRequest(campaign_name=f"Следующая {stage.value}", stage=stage)
    )


async def _new_letters(session: AsyncSession, domain: DomainModel) -> list[MessageModel]:
    """Письма, которые положила в очередь последняя сборка."""
    rows = await session.execute(
        select(MessageModel)
        .where(MessageModel.domain_id == domain.id, MessageModel.status == MessageStatus.QUEUED)
        .order_by(MessageModel.id)
    )
    return list(rows.scalars().all())


class TestKey:
    def test_first_attempt_keeps_the_old_key(self) -> None:
        """Письма, собранные до попыток, остаются своими: повтор их сборки
        упирается в тот же ключ, а не заводит второе письмо."""
        assert idempotency_key(stage=Stage.DONORS, host=HOST, step=0) == f"donors:{HOST}:0"
        assert idempotency_key(stage=Stage.DONORS, host=HOST, step=0, attempt=1) == (
            f"donors:{HOST}:0"
        )

    def test_next_attempts_carry_their_number(self) -> None:
        assert idempotency_key(stage=Stage.DONORS, host=HOST, step=0, attempt=2) == (
            f"donors:{HOST}:0:a2"
        )
        assert idempotency_key(stage=Stage.ADVERTISERS, host=HOST, step=2, attempt=3) == (
            f"advertisers:{HOST}:2:a3"
        )

    @pytest.mark.parametrize(
        ("key", "attempt"),
        [
            (f"donors:{HOST}:0", 1),
            (f"donors:{HOST}:1:a2", 2),
            ("advertisers:a2.example.test:0:a3", 3),
            # Домен сам похож на номер попытки — номером он не становится.
            ("donors:a2.example.test:0", 1),
            ("donors:x.a7:1", 1),
            # Чужой формат (строки тестов и старых рук) — первая попытка.
            ("test:one:earlier", 1),
            ("", 1),
        ],
    )
    def test_attempt_is_read_back_from_the_key(self, key: str, attempt: int) -> None:
        assert attempt_of(key) == attempt


class TestDeadChainsOpenTheDonor:
    """Письмо не дошло ни до кого — донор снова открыт, и письмо уходит
    на следующий адрес со своим ключом и в своём диалоге."""

    async def test_hard_bounce_opens_the_next_address(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        domain, (info, editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        first = await _letter(session, domain, info, status=MessageStatus.BOUNCED)
        _bury(info)
        await session.flush()

        report = await _build(session)

        assert report.prepared == 1
        (letter,) = await _new_letters(session, domain)
        assert letter.contact_id == editor.id
        assert letter.idempotency_key == f"donors:{HOST}:0:a2"
        # Новая попытка — новый диалог: адрес другой, и переписка для донора новая.
        assert letter.thread_id != first.thread_id
        thread = await session.get(ThreadModel, letter.thread_id)
        assert thread is not None
        assert thread.contact_id == editor.id

    async def test_letter_to_a_buried_address_counts_as_dead_even_if_delivered(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Автоответ «ящик больше не читается» хоронит адрес, а само письмо
        платформа честно отметила доставленным."""
        domain, (info, editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        await _letter(session, domain, info, status=MessageStatus.DELIVERED)
        _bury(info)
        await session.flush()

        await _build(session)

        (letter,) = await _new_letters(session, domain)
        assert letter.contact_id == editor.id
        assert letter.idempotency_key == f"donors:{HOST}:0:a2"

    @pytest.mark.parametrize(
        "followup", [MessageStatus.BOUNCED, MessageStatus.STOPPED, MessageStatus.DELIVERED]
    )
    async def test_whole_chain_to_a_buried_address_is_dead(
        self, session: AsyncSession, filled_legal: None, followup: MessageStatus
    ) -> None:
        """Ящик умер между первым письмом и добивкой: добивка не дошла или
        остановлена приёмом ответа — цепочка вся мёртвая."""
        domain, (info, editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        first = await _letter(session, domain, info, status=MessageStatus.DELIVERED)
        thread = await session.get(ThreadModel, first.thread_id)
        await _letter(session, domain, info, status=followup, step=1, thread=thread)
        _bury(info)
        await session.flush()

        await _build(session)

        (letter,) = await _new_letters(session, domain)
        assert letter.contact_id == editor.id

    async def test_soft_bounce_moves_on_without_burying_the_address(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Мягкий отказ адрес не хоронит — но и второго «первого письма»
        тому же ящику не будет: письмо уходит на следующий адрес."""
        domain, (info, editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        await _letter(session, domain, info, status=MessageStatus.BOUNCED)

        await _build(session)

        (letter,) = await _new_letters(session, domain)
        assert letter.contact_id == editor.id
        await session.refresh(info)
        assert info.verification_status != DEAD

    async def test_third_attempt_counts_both_earlier_ones(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        domain, (one, two, three) = await _donor(session, f"a@{HOST}", f"b@{HOST}", f"c@{HOST}")
        await _letter(session, domain, one, status=MessageStatus.BOUNCED)
        await _letter(session, domain, two, status=MessageStatus.BOUNCED, attempt=2)
        _bury(one)
        _bury(two)
        await session.flush()

        await _build(session)

        (letter,) = await _new_letters(session, domain)
        assert letter.contact_id == three.id
        assert letter.idempotency_key == f"donors:{HOST}:0:a3"

    async def test_second_build_does_not_write_the_next_address_twice(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Собранное на следующий адрес письмо живое — оно в очереди."""
        domain, (info, _editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        await _letter(session, domain, info, status=MessageStatus.BOUNCED)
        _bury(info)
        await session.flush()

        await _build(session)
        second = await _build(session)

        assert second.prepared == 0
        assert len(await _new_letters(session, domain)) == 1


class TestNothingOpens:
    """Письмо живое, донор ответил, адреса или попытки кончились — нового
    письма нет, и карточка говорит почему."""

    @pytest.mark.parametrize(
        ("status", "said"),
        [
            (MessageStatus.QUEUED, attempts.PENDING_LETTER),
            (MessageStatus.SENDING, attempts.PENDING_LETTER),
            (MessageStatus.SENT, attempts.WRITTEN),
            (MessageStatus.DELIVERED, attempts.WRITTEN),
            # «Не писать» — решение по донору, а не по письму.
            (MessageStatus.STOPPED, attempts.WRITTEN),
        ],
    )
    async def test_live_letter_holds_the_donor(
        self, session: AsyncSession, filled_legal: None, status: MessageStatus, said: str
    ) -> None:
        domain, (info, _editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        await _letter(session, domain, info, status=status)

        report = await _build(session)
        address = await Recipients(session).letter_address(domain.id)

        assert report.prepared == 0
        assert (address.contact_id, address.blocked) == (None, said)

    async def test_letter_in_the_queue_holds_even_a_buried_address(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Пока письмо в очереди, второго не собираем — какой бы ни был адрес:
        иначе человек отправил бы оба."""
        domain, (info, _editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        await _letter(session, domain, info, status=MessageStatus.QUEUED)
        _bury(info)
        await session.flush()

        report = await _build(session)

        assert report.prepared == 0

    @pytest.mark.parametrize("kind", [ReplyKind.HUMAN, ReplyKind.UNSUBSCRIBE])
    async def test_answer_closes_the_donor_even_after_the_address_died(
        self, session: AsyncSession, filled_legal: None, kind: ReplyKind
    ) -> None:
        """Ответ получен — следующий адрес никогда: разговор идёт там, где
        начался. Даже если ящик, с которого ответили, потом умер."""
        domain, (info, _editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        first = await _letter(session, domain, info, status=MessageStatus.BOUNCED)
        session.add(
            ReplyModel(thread_id=first.thread_id, message_id=first.id, kind=kind, raw_body="Hi")
        )
        _bury(info)
        await session.flush()

        report = await _build(session)
        address = await Recipients(session).letter_address(domain.id)

        assert report.prepared == 0
        assert address.blocked == attempts.ANSWERED

    async def test_answer_found_by_the_letter_when_the_thread_is_gone(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        domain, (info, _editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        first = await _letter(session, domain, info, status=MessageStatus.BOUNCED)
        session.add(ReplyModel(message_id=first.id, kind=ReplyKind.HUMAN, raw_body="Hi"))
        _bury(info)
        await session.flush()

        assert (await _build(session)).prepared == 0

    async def test_unsubscribed_by_the_button_is_an_answer(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Кнопка отписки метит диалог; снятая потом запись стоп-листа
        не открывает донору следующий адрес."""
        domain, (info, _editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        first = await _letter(session, domain, info, status=MessageStatus.BOUNCED)
        thread = await session.get(ThreadModel, first.thread_id)
        assert thread is not None
        thread.status = ThreadStatus.UNSUBSCRIBED
        _bury(info)
        await session.flush()

        assert (await _build(session)).prepared == 0

    async def test_automatic_reply_is_not_an_answer(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        domain, (info, editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        first = await _letter(session, domain, info, status=MessageStatus.BOUNCED)
        session.add(
            ReplyModel(thread_id=first.thread_id, kind=ReplyKind.AUTO_REPLY, raw_body="Away")
        )
        _bury(info)
        await session.flush()

        await _build(session)

        (letter,) = await _new_letters(session, domain)
        assert letter.contact_id == editor.id

    async def test_all_addresses_dead(self, session: AsyncSession, filled_legal: None) -> None:
        domain, (info, editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        await _letter(session, domain, info, status=MessageStatus.BOUNCED)
        await _letter(session, domain, editor, status=MessageStatus.BOUNCED, attempt=2)
        _bury(info)
        _bury(editor)
        await session.flush()

        report = await _build(session)
        address = await Recipients(session).letter_address(domain.id)

        assert report.prepared == 0
        assert (address.contact_id, address.blocked) == (None, attempts.EXHAUSTED)
        assert report.funnel["ещё не писали"] == 0
        assert report.funnel["адреса кончились"] == 1

    async def test_cap_stops_before_the_last_address(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Три адреса не дошли — четвёртому не пишем: письма на все найденные
        ящики сайта для получателя и есть рассылка по всем ящикам."""
        domain, contacts = await _donor(session, f"a@{HOST}", f"b@{HOST}", f"c@{HOST}", f"d@{HOST}")
        for number, contact in enumerate(contacts[:3], start=1):
            await _letter(session, domain, contact, status=MessageStatus.BOUNCED, attempt=number)
            _bury(contact)
        await session.flush()

        report = await _build(session)
        address = await Recipients(session).letter_address(domain.id)

        assert report.prepared == 0
        assert address.blocked == attempts.CAPPED
        assert str(attempts.MAX_ADDRESSES) in attempts.CAPPED
        assert report.funnel["адреса кончились"] == 1

    async def test_cap_counts_both_stages(self, session: AsyncSession, filled_legal: None) -> None:
        """Для сайта неважно, в какой роли ему писали: потолок один на оба этапа."""
        domain, contacts = await _donor(session, f"a@{HOST}", f"b@{HOST}", f"c@{HOST}", f"d@{HOST}")
        await _letter(session, domain, contacts[0], status=MessageStatus.BOUNCED)
        await _letter(
            session, domain, contacts[1], status=MessageStatus.BOUNCED, stage=Stage.ADVERTISERS
        )
        await _letter(session, domain, contacts[2], status=MessageStatus.BOUNCED, attempt=2)
        for contact in contacts[:3]:
            _bury(contact)
        await session.flush()

        assert (await _build(session)).prepared == 0

    async def test_live_letter_of_the_other_stage_holds(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Одно письмо на сайт — на оба этапа сразу."""
        domain, (info, editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        await _letter(session, domain, info, status=MessageStatus.BOUNCED)
        await _letter(
            session, domain, editor, status=MessageStatus.DELIVERED, stage=Stage.ADVERTISERS
        )
        _bury(info)
        await session.flush()

        assert (await _build(session)).prepared == 0


class TestTheCard:
    """Карточка донора отмечает тот же адрес, что берёт сборка, и говорит,
    почему не первый."""

    @pytest.fixture
    async def token(self, make_user: MakeUser, sign_in: SignIn) -> str:
        await make_user("оператор@site.com", role=UserRole.OPERATOR)
        return await sign_in("оператор@site.com")

    async def _card(
        self, client: AsyncClient, token: str, session: AsyncSession, domain: DomainModel
    ) -> dict[str, Any]:
        """Карточка тем же путём, каким её получает экран."""
        await session.commit()
        donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
        assert donor is not None
        response = await client.get(f"/api/donors/{donor.id}", headers=bearer(token))
        assert response.status_code == 200, response.text
        card: dict[str, Any] = response.json()
        return card

    async def test_card_marks_the_next_address(
        self, client: AsyncClient, token: str, session: AsyncSession, filled_legal: None
    ) -> None:
        # Вписанный руками адрес стоял бы первым (оценка 100) — пока не умер.
        domain, (page,) = await _donor(session, f"page@{HOST}")
        manual_contact = ContactModel(
            domain_id=domain.id,
            email=f"editor@{HOST}",
            source=ContactSource.MANUAL,
            verification_score=manual.MANUAL_SCORE,
        )
        session.add(manual_contact)
        await session.flush()
        await _letter(session, domain, manual_contact, status=MessageStatus.BOUNCED)
        _bury(manual_contact)

        card = await self._card(client, token, session, domain)
        await _build(session)
        (letter,) = await _new_letters(session, domain)

        assert card["letter_contact_id"] == page.id == letter.contact_id
        assert card["letter_note"] == "прошлый адрес не дошёл — письмо уйдёт на следующий"
        assert card["letter_blocked"] is None
        # Мёртвый — в конце списка и с пометкой, хоть у него и была оценка 100.
        assert [(c["email"], c["bounced"]) for c in card["contacts"]] == [
            (f"page@{HOST}", False),
            (f"editor@{HOST}", True),
        ]

    async def test_never_written_donor_has_no_note(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        domain, (page,) = await _donor(session, f"page@{HOST}")

        card = await self._card(client, token, session, domain)

        assert card["letter_contact_id"] == page.id
        assert card["letter_note"] is None
        assert card["contacts"][0]["bounced"] is False

    async def test_dead_address_goes_last_even_if_it_answered_once(
        self, session: AsyncSession
    ) -> None:
        """Ответивший адрес стоит первым — пока он жив."""
        domain, (first, second) = await _donor(session, f"one@{HOST}", f"two@{HOST}")
        first.last_replied_at = NOW
        _bury(first)
        await session.flush()

        address = await Recipients(session).letter_address(domain.id)

        assert address.contact_id == second.id


class TestTheFunnel:
    async def test_funnel_says_who_gets_the_next_address(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """«Ещё не писали» — и те, кому прежнее письмо не дошло: донор его
        не видел. Сколько таких — отдельной строкой."""
        await _donor(session, f"info@new.{HOST}", host=f"new.{HOST}")
        domain, (info, _editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        await _letter(session, domain, info, status=MessageStatus.BOUNCED)
        _bury(info)
        await session.flush()

        report = await _build(session)

        assert report.funnel == {
            "подходящих": 2,
            "принятых": 2,
            "с адресом": 2,
            "вне стоп-листа": 2,
            "ещё не писали": 2,
            "из них на следующий адрес": 1,
        }
        assert report.prepared == 2

    async def test_no_earlier_letters_no_extra_lines(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _donor(session, f"info@{HOST}")

        report = await _build(session)

        assert set(report.funnel) == {
            "подходящих",
            "принятых",
            "с адресом",
            "вне стоп-листа",
            "ещё не писали",
        }

    async def test_one_unsubscribed_address_does_not_drop_the_donor(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Отписка — про адрес: донор с другим адресом остаётся и в сборке,
        и в воронке. До 28.09.2026 воронка выбрасывала его целиком, и её
        «ещё не писали» расходилось с тем, что собиралось."""
        domain, (info, editor) = await _donor(session, f"info@{HOST}", f"editor@{HOST}")
        session.add(SuppressionModel(email=info.email, reason=SuppressionReason.UNSUBSCRIBED))
        await session.flush()

        report = await _build(session)

        assert report.funnel["вне стоп-листа"] == 1
        assert report.funnel["ещё не писали"] == 1
        (letter,) = await _new_letters(session, domain)
        assert letter.contact_id == editor.id
