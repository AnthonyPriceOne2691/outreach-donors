"""Перезапрос цены у донора, чья цена старше срока свежести (`letters/price_recheck.py`).

ТЗ, Этап 2: «свежесть цены 150 дней; старше — сначала перезапрос цены у донора». До
12.10.2026 перезапросить было нечем. Проверяется весь путь — протухшая цена, письмо
в переписку, ответ с новой ценой, цена свежая, обход открыт — и то, что по зелёному
прогону не видно: ящик и ветка переписки, новое первое письмо без переписки, отказы
словами до заведения письма и не чаще раза в `PRICE_RECHECK_DAYS` дней.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    MessageStatus,
    ReplyKind,
    Stage,
    SuppressionReason,
    ThreadStatus,
)
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.crawl.targets import choose
from backend.features.letters import price_recheck
from backend.features.letters.chain import RECHECK_STEP
from backend.features.letters.followups import send_due
from backend.features.letters.price_recheck import RecheckRefusedError, Way
from backend.features.letters.recheck_rules import CAMPAIGN_TEMPLATE
from backend.features.letters.sending import SendError, Sending
from backend.features.letters.transport import MaybeSentError, Outgoing, TransportError
from backend.features.letters.unknown_outcome import Outcome, resolve
from backend.features.ops.overview import overview
from backend.features.outreach.repository import EVERY_STAGE, OutreachRepository
from backend.features.outreach.threads import ThreadState
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_sender
from tests.recheck_world import (
    ANSWERED_FROM,
    BOX,
    HOST,
    Recording,
    World,
    answer,
    manual_stale,
    parse,
    stale_conversation,
)

pytestmark = pytest.mark.usefixtures("filled_legal")


@pytest.fixture
def moment() -> datetime:
    """Минута нажатия — настоящая: время ответов в базе ставит сама база."""
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
async def world(session: AsyncSession, moment: datetime) -> World:
    return await stale_conversation(session, moment)


async def _ask(
    session: AsyncSession, donor_id: int, moment: datetime, transport: object | None = None
) -> price_recheck.Asked:
    sending = Sending(session, transport or Recording(), now=moment)  # type: ignore[arg-type]
    return await price_recheck.ask(session, sending, donor_id, author_id=None, now=moment)


async def _letters(session: AsyncSession, domain_id: int) -> int:
    total = await session.scalar(
        select(func.count(MessageModel.id)).where(MessageModel.domain_id == domain_id)
    )
    return int(total or 0)


class TestWholePath:
    async def test_stale_price_is_asked_in_the_thread_and_the_answer_opens_the_crawl(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Протухшая цена → перезапрос в переписке → ответ с новой ценой → цена свежая →
        обход разрешён."""
        assert HOST in (await choose(session, now=moment)).stale_price
        transport = Recording()

        asked = await _ask(session, world.donor.id, moment, transport)

        [out] = transport.seen
        assert out.from_email == BOX  # ящик переписки
        assert out.to == ANSWERED_FROM  # кто ответил, а не куда писали
        assert out.in_reply_to == world.reply.inbound_message_id  # ветка к его письму
        assert out.subject == world.reply.subject  # «Re:» его письма, без второго «Re:»
        assert "what is your current price per article" in out.body
        assert "Anna Ro" in out.body  # подпись — именем отправителя
        assert asked.way is Way.THREAD
        assert asked.thread_id == world.thread.id
        letter = await session.get(MessageModel, asked.message_id)
        assert letter is not None
        assert (letter.step, letter.status) == (RECHECK_STEP, MessageStatus.SENT)
        assert letter.next_action_at is None  # вне цепочки: добивок нет

        detail = await OutreachRepository(session).thread(world.thread.id)
        assert detail.row.summary.state is ThreadState.RECHECK  # снова ждёт ответа

        later = moment + timedelta(hours=2)
        reply = await answer(session, letter, "Still fine, now $120 per article.", at=later)
        assert await parse(session, reply, price="120")

        await session.refresh(world.donor)
        assert world.donor.last_price == Decimal("120")
        assert world.donor.last_price_at == later  # слово донора — временем ответа
        assert HOST in (await choose(session, now=later)).hosts  # обход открыт
        # Карточка переписки — новым запросом, как у экрана: прочитанные ответы сессии
        # иначе остались бы прежними.
        thread_id = world.thread.id
        session.expire_all()
        detail = await OutreachRepository(session).thread(thread_id)
        assert detail.row.summary.state is ThreadState.PRICED

    async def test_the_list_and_the_menu_count_the_asked_thread_alike(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Числа меню и «Обзора» берут состояние из базы без писем — оно то же, что у списка,
        и ответивший донор остаётся ответившим."""
        await _ask(session, world.donor.id, moment)

        marks = await OutreachRepository(session).states(stages=EVERY_STAGE)
        rows = await OutreachRepository(session).threads(stages=EVERY_STAGE)

        assert [mark.state for mark in marks] == [ThreadState.RECHECK]
        assert [row.summary.state for row in rows] == [ThreadState.RECHECK]
        assert (await overview(session, now=moment, stages=EVERY_STAGE)).donors.replied == 1

    async def test_without_a_conversation_it_is_a_new_first_letter(
        self, session: AsyncSession, moment: datetime
    ) -> None:
        """Цену вписали руками, писем донору не было — перезапрос новым первым письмом,
        с любого свободного ящика этапа, в своей рассылке."""
        donor = await manual_stale(session, moment, host="manual.example.test")
        await make_sender(session, BOX)
        transport = Recording()

        asked = await _ask(session, donor.id, moment, transport)

        [out] = transport.seen
        assert out.to == "editor@manual.example.test"
        assert out.in_reply_to is None
        assert out.subject == "Guest article on manual.example.test: current rate?"
        assert asked.way is Way.FIRST
        letter = await session.get(MessageModel, asked.message_id)
        assert letter is not None
        assert letter.step == 0
        campaign = await session.get(CampaignModel, letter.campaign_id)
        assert campaign is not None
        assert (campaign.template_id, campaign.stage) == (CAMPAIGN_TEMPLATE, Stage.DONORS)
        # Одиночный, как и перезапрос в переписке (решение 12.10.2026): срока добивки нет,
        # и проход добивок через месяц его не трогает.
        assert letter.next_action_at is None
        passed = await send_due(
            session, transport=Recording(), limit=10, now=moment + timedelta(days=30)
        )
        assert passed.sent == 0
        same_thread = select(func.count(MessageModel.id)).where(
            MessageModel.thread_id == letter.thread_id
        )
        assert await session.scalar(same_thread) == 1

        reply = await answer(
            session,
            letter,
            "It is $140 now.",
            at=moment + timedelta(hours=1),
            sender="editor@manual.example.test",
            message_id="<in-1@manual.example.test>",
        )
        assert await parse(session, reply, price="140")
        assert "manual.example.test" in (await choose(session, now=moment)).hosts


class TestWhere:
    async def test_the_latest_answered_conversation_is_taken(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Донор отвечал в двух переписках — перезапрос в ту, где он ответил последним."""
        campaign = CampaignModel(name="Вторая рассылка", stage=Stage.DONORS, status="draft")
        session.add(campaign)
        await session.flush()
        later = ThreadModel(
            domain_id=world.donor.domain_id,
            campaign_id=campaign.id,
            contact_id=world.thread.contact_id,
            status=ThreadStatus.REPLIED,
        )
        session.add(later)
        await session.flush()
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                thread_id=later.id,
                domain_id=world.donor.domain_id,
                contact_id=world.thread.contact_id,
                sender_id=world.box.id,
                step=0,
                status=MessageStatus.DELIVERED,
                sent_at=moment - timedelta(days=180),
                idempotency_key=f"donors:{HOST}:0:a2",
            )
        )
        session.add(
            ReplyModel(
                thread_id=later.id,
                kind=ReplyKind.HUMAN,
                raw_body="Thanks, we'll keep you posted.",
                from_email=ANSWERED_FROM,
                subject="Re: second",
                created_at=moment - timedelta(days=170),
                # Разобран уверенно: цены в ответе нет, ждать человека нечего.
                confidence=0.9,
                placement="unclear",
                offers=[],
            )
        )
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.plan is not None
        assert found.plan.thread_id == later.id

    async def test_a_silent_donor_is_not_written_a_fourth_time(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Писали, а ответа нет: ещё одно письмо молчавшему — путь к жалобе на спам."""
        await session.delete(world.reply)
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.plan is None
        assert found.refusal is not None
        assert "не ответил на наши письма" in found.refusal

    async def test_unsubscribed_donor_is_refused(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        world.thread.status = ThreadStatus.UNSUBSCRIBED
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert (
            found.refusal
            == f"{HOST} отписался в переписке №{world.thread.id}: писать ему больше нельзя."
        )

    async def test_an_answer_waiting_for_review_is_read_first(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Ответ, который ждёт разбора, может уже нести цену: спрашивать её снова рано."""
        world.reply.confidence = 0.3
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.refusal is not None
        assert "ждёт разбора" in found.refusal

    async def test_letter_written_to_the_site_as_an_advertiser_blocks_a_new_letter(
        self, session: AsyncSession, moment: datetime
    ) -> None:
        donor = await manual_stale(session, moment, host="both.example.test")
        await make_sender(session, BOX)
        offers = CampaignModel(name="Офферы", stage=Stage.ADVERTISERS, status="draft")
        session.add(offers)
        await session.flush()
        session.add(
            MessageModel(
                campaign_id=offers.id,
                domain_id=donor.domain_id,
                step=0,
                status=MessageStatus.SENT,
                sent_at=moment - timedelta(days=3),
                idempotency_key="advertisers:both.example.test:0",
            )
        )
        await session.commit()

        found = await price_recheck.look(session, donor.id, now=moment)

        assert found.refusal is not None
        assert "уже писали как рекламодателю" in found.refusal


class TestGuards:
    @pytest.mark.parametrize(
        ("days", "said"),
        [(10, "свежая: названа"), (None, "цены нет")],
    )
    async def test_only_a_stale_price_is_asked_again(
        self, session: AsyncSession, world: World, moment: datetime, days: int | None, said: str
    ) -> None:
        world.donor.last_price_at = None if days is None else moment - timedelta(days=days)
        if days is None:
            world.donor.last_price = None
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.refusal is not None
        assert said in found.refusal
        with pytest.raises(RecheckRefusedError, match=said):
            await _ask(session, world.donor.id, moment)

    async def test_not_a_donor_is_refused(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        world.donor.review = "rejected"
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert (
            found.refusal
            == f"{HOST} — не донор: цену перезапрашивают только у донора, принятого человеком."
        )

    async def test_supplier_is_refused(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        session.add(SupplierDonorModel(host=HOST))
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.refusal is not None
        assert "донор-поставщик" in found.refusal

    async def test_stop_list_is_checked_before_the_letter(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        session.add(
            SuppressionModel(domain_id=world.donor.domain_id, reason=SuppressionReason.MANUAL)
        )
        await session.commit()
        before = await _letters(session, world.donor.domain_id)

        with pytest.raises(RecheckRefusedError, match="в стоп-листе"):
            await _ask(session, world.donor.id, moment)
        assert await _letters(session, world.donor.domain_id) == before

    async def test_the_address_that_answered_in_the_stop_list_is_refused(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        session.add(SuppressionModel(email=ANSWERED_FROM, reason=SuppressionReason.UNSUBSCRIBED))
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.refusal is not None
        assert f"Адрес {ANSWERED_FROM} в стоп-листе" in found.refusal

    async def test_one_letter_at_a_time(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        session.add(
            MessageModel(
                campaign_id=world.thread.campaign_id,
                thread_id=world.thread.id,
                domain_id=world.donor.domain_id,
                step=1,
                status=MessageStatus.SENDING,
                idempotency_key=f"donors:{HOST}:1",
            )
        )
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.refusal is not None
        assert "отправляется, и исход его неизвестен" in found.refusal

    async def test_not_more_often_than_the_setting_says(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        await _ask(session, world.donor.id, moment)
        soon = moment + timedelta(days=outreach_cfg.PRICE_RECHECK_DAYS - 1)
        again = moment + timedelta(days=outreach_cfg.PRICE_RECHECK_DAYS)

        with pytest.raises(RecheckRefusedError) as refused:
            await _ask(session, world.donor.id, soon)
        assert str(refused.value) == (
            f"Цену {HOST} уже перезапросили {moment:%d.%m.%Y} — повторно не раньше "
            f"{again:%d.%m.%Y}: перезапрос — не чаще раза в 14 дней."
        )

    async def test_no_second_recheck_until_the_donor_answers_the_first(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Донор ответил однажды, а на перезапрос молчит: срок повтора прошёл, но второй
        перезапрос не уходит — ждём ответа на первый (проверка 12.10.2026)."""
        await _ask(session, world.donor.id, moment)
        later = moment + timedelta(days=outreach_cfg.PRICE_RECHECK_DAYS + 10)

        found = await price_recheck.look(session, world.donor.id, now=later)

        assert found.refusal == (
            f"Ждём ответа {HOST} на перезапрос от {moment:%d.%m.%Y} (переписка "
            f"№{world.thread.id}): второй до его ответа не уходит. Ответит — цена обновится "
            "из ответа."
        )

    async def test_a_silent_thread_box_refuses_before_the_letter(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Ящик переписки выключен — с другого перезапрос не уходит, и в очереди его нет."""
        world.box.enabled = False
        await session.commit()
        before = await _letters(session, world.donor.domain_id)

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.refusal is not None
        assert f"Ящик {BOX} сейчас не пишет" in found.refusal
        assert await _letters(session, world.donor.domain_id) == before

    async def test_unset_signature_refuses_before_the_letter(
        self, session: AsyncSession, world: World, moment: datetime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "SENDER_NAME", "")

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.refusal is not None
        assert "не задано имя отправителя" in found.refusal

    async def test_refused_by_the_mail_it_leaves_with_the_next_click(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Почта сказала «нет» — письмо ждёт, и следующее нажатие отправляет его же, а не
        второе."""

        class Refusing:
            name = "sendgrid"
            real = True

            async def send(self, outgoing: Outgoing) -> str:
                raise TransportError(f"Платформа отказала (503) на письмо №{outgoing.message_id}")

        with pytest.raises(SendError, match="503"):
            await _ask(session, world.donor.id, moment, Refusing())
        waiting = await session.scalar(
            select(MessageModel).where(MessageModel.step == RECHECK_STEP)
        )
        assert waiting is not None
        assert waiting.status is MessageStatus.QUEUED

        asked = await _ask(session, world.donor.id, moment)

        assert asked.message_id == waiting.id
        recheck = select(func.count(MessageModel.id)).where(MessageModel.step == RECHECK_STEP)
        assert await session.scalar(recheck) == 1


class Unanswered:
    """Почта не ответила, когда запрос мог уже дойти: письмо остаётся «отправляется»."""

    name = "sendgrid"
    real = True

    async def send(self, outgoing: Outgoing) -> str:
        raise MaybeSentError(f"Платформа не ответила на письмо №{outgoing.message_id}")


async def _stuck(session: AsyncSession, domain_id: int, moment: datetime) -> MessageModel:
    """Письмо донора, зависшее в «отправляется», — передано почте десять минут назад."""
    stuck = await session.scalar(
        select(MessageModel).where(
            MessageModel.domain_id == domain_id, MessageModel.status == MessageStatus.SENDING
        )
    )
    assert stuck is not None
    await session.execute(
        update(MessageModel)
        .where(MessageModel.id == stuck.id)
        .values(updated_at=moment - timedelta(minutes=10))
    )
    await session.commit()
    return stuck


class TestSingle:
    async def test_found_in_the_platform_log_it_still_gets_no_followup(
        self, session: AsyncSession, moment: datetime
    ) -> None:
        """«Ушло» пишется одним путём на все случаи (`settle.record_sent`): и у перезапроса
        новым письмом, исход которого решил человек по журналу платформы, срока добивки нет."""
        donor = await manual_stale(session, moment, host="lost.example.test")
        await make_sender(session, BOX)
        with pytest.raises(MaybeSentError):
            await _ask(session, donor.id, moment, Unanswered())
        stuck = await _stuck(session, donor.domain_id, moment)
        assert stuck.step == 0

        resolved = await resolve(
            session, stuck.id, Outcome.SENT, author_id=None, now=moment + timedelta(minutes=1)
        )
        await session.commit()

        assert resolved.status is MessageStatus.SENT
        await session.refresh(stuck)
        assert stuck.next_action_at is None

    async def test_a_letter_of_an_ordinary_campaign_keeps_its_chain(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Одиночный — только перезапрос: у первого письма обычной рассылки добивка в срок."""
        first = MessageModel(
            campaign_id=world.thread.campaign_id,
            thread_id=world.thread.id,
            domain_id=world.donor.domain_id,
            contact_id=world.thread.contact_id,
            step=0,
            status=MessageStatus.QUEUED,
            subject="Rates",
            body="Hi,\n\nAnna Ro",
            idempotency_key=f"donors:{HOST}:0:a2",
        )
        session.add(first)
        await session.commit()

        await Sending(session, Recording(), now=moment).send(first.id)

        await session.refresh(first)
        assert first.next_action_at is not None


class TestUnknownOutcome:
    async def test_returned_to_the_queue_it_leaves_with_the_next_click(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Почта не ответила — перезапрос «отправляется»; человек не нашёл его в журнале
        платформы и вернул в очередь — следующее нажатие отправляет то же письмо."""

        with pytest.raises(MaybeSentError):
            await _ask(session, world.donor.id, moment, Unanswered())
        stuck = await session.scalar(select(MessageModel).where(MessageModel.step == RECHECK_STEP))
        assert stuck is not None
        assert stuck.status is MessageStatus.SENDING
        refused = await price_recheck.look(session, world.donor.id, now=moment)
        assert refused.refusal is not None
        assert "отправляется, и исход его неизвестен" in refused.refusal

        await session.execute(
            update(MessageModel)
            .where(MessageModel.id == stuck.id)
            .values(updated_at=moment - timedelta(minutes=10))
        )
        await session.commit()
        resolved = await resolve(
            session, stuck.id, Outcome.QUEUED, author_id=None, now=moment + timedelta(minutes=1)
        )
        await session.commit()
        assert resolved.status is MessageStatus.QUEUED
        assert resolved.said.startswith("перезапрос цены не ушёл и ждёт")

        asked = await _ask(session, world.donor.id, moment + timedelta(minutes=2))

        assert asked.message_id == stuck.id


class TestBatch:
    async def test_each_donor_answers_for_itself(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        fresh = await stale_conversation(session, moment, host="fresh.example.test")
        fresh.donor.last_price_at = moment - timedelta(days=3)
        await session.commit()
        sending = Sending(session, Recording(), now=moment)

        done = await price_recheck.ask_many(
            session,
            sending,
            [world.donor.id, fresh.donor.id, 999_999, world.donor.id],
            author_id=None,
            now=moment,
        )

        asked = [one for one in done if isinstance(one, price_recheck.Asked)]
        refused = {one.donor_id: one for one in done if isinstance(one, price_recheck.Refused)}
        assert [one.donor_id for one in asked] == [world.donor.id]
        assert refused[fresh.donor.id].host == "fresh.example.test"
        assert "свежая" in refused[fresh.donor.id].reason
        assert refused[999_999].reason == "Донора №999999 нет"
        assert len(done) == 3  # повтор номера — один перезапрос


class TestStaleList:
    async def test_lists_stale_donors_with_their_way_or_refusal(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        manual = await manual_stale(session, moment, host="manual.example.test")
        silent = await stale_conversation(session, moment, host="silent.example.test")
        await session.delete(silent.reply)
        await session.commit()

        found = await price_recheck.stale(session, limit=10, now=moment)

        by_host = {row.host: row for row in found.rows}
        assert found.total == 3
        assert by_host[HOST].plan is not None
        assert by_host[HOST].plan.way is Way.THREAD
        assert by_host["manual.example.test"].donor_id == manual.id
        assert by_host["manual.example.test"].plan is not None
        assert by_host["manual.example.test"].plan.way is Way.FIRST
        assert by_host["silent.example.test"].refusal is not None
