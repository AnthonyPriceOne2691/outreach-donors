"""«Кому не пишем» — на всех путях, где пишем (решение владельца 12.10.2026).

ТЗ: список «кому не пишем» Этапа 2 распространяется на Этап 1. Кроме очереди рассмотрения
и сборки писем (`test_not_writing_list.py`) пишем ещё формой, платным поиском адреса
готовим письмо, отправка уносит собранное раньше письмо и добивку, а цена руками заводит
донора. Крупный сайт (DR > 80) и домен из списков не проходит ни одним из этих путей —
даже принятый до правила: вердикт тогда не переписывали.

Домены списков берутся из самих списков (`crawl/denylist.py`), а не пишутся здесь.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.features.contacts import forms
from backend.features.contacts.repository import ContactRepository, search_refusal
from backend.features.core.domain import ContactStatus, MessageStatus, Stage
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import MessageModel
from backend.features.crawl.denylist import MAJOR_MEDIA
from backend.features.donors.price_refusal import refusal_for
from backend.features.donors.verdict import big_site_reason
from backend.features.letters import batch
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.donor_standing import Refusal, donor_refusal
from backend.features.letters.sending import NotWritingError, SuppressedError
from backend.features.letters.transport import NullTransport
from backend.features.runs.exclusions import NOT_WRITING_WORDS, ExclusionReason
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender
from tests.test_letters_queue import _build

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
MEDIA = sorted(MAJOR_MEDIA)[0]


async def _donor(session: AsyncSession, host: str, *, dr: int | None) -> DonorModel:
    domain = await make_donor(session, host, dr=30)
    donor = (
        await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id))
    ).scalar_one()
    donor.dr = dr
    await session.flush()
    return donor


class TestForms:
    async def test_form_is_a_letter_too(self, session: AsyncSession) -> None:
        for host, dr in (("big.example.test", 88), (MEDIA, None), ("blog.example.test", 45)):
            donor = await _donor(session, host, dr=dr)
            donor.contact_status = ContactStatus.FORM_ONLY
        await session.flush()

        rows = await forms.queue(session)

        assert [row.host for row in rows] == ["blog.example.test"]
        assert await forms.total(session) == 1


class TestPaidSearch:
    async def test_no_money_on_an_address_nobody_writes_to(self, session: AsyncSession) -> None:
        big = await _donor(session, "big.example.test", dr=88)
        await _donor(session, "blog.example.test", dr=45)

        hosts = await ContactRepository(session).pending_hosts(now=NOW)

        assert hosts == ["blog.example.test"]
        said = await search_refusal(session, big, now=NOW)
        assert said == "Адрес не ищут: DR > 80 — крупным сайтам не пишем."


async def _queued_letter(session: AsyncSession, host: str) -> MessageModel:
    """Письмо, собранное, пока донор был годен: DR > 80 он стал после сборки."""
    await make_donor(session, host, email=f"info@{host}")
    await make_sender(session, "outreach1@mail.example.test")
    await _build(session)
    await session.commit()
    return (await session.execute(select(MessageModel))).scalar_one()


class TestSending:
    async def test_letter_built_before_does_not_go(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letter = await _queued_letter(session, "big.example.test")
        await session.execute(
            update(DonorModel).where(DonorModel.domain_id == letter.domain_id).values(dr=88)
        )
        await session.commit()

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)
        await session.refresh(letter)

        assert (report.sent, dict(report.refused)) == (0, {"кому не пишем": 1})
        assert letter.status is MessageStatus.STOPPED, "в очереди оно ждало бы того, что не будет"
        assert letter.failure_reason == (
            f"Письмо №{letter.id} остановлено: {big_site_reason(80)} (big.example.test). "
            "Добивки не уйдут"
        )

    async def test_the_chain_ends_like_on_the_stop_list(self) -> None:
        """Добивки идут той же отправкой (`followups.py`), а `SuppressedError` кончает цепочку."""
        assert issubclass(NotWritingError, SuppressedError)
        assert batch.why(NotWritingError("…")) == "кому не пишем"

    async def test_followup_is_refused_and_an_answer_is_not(self, session: AsyncSession) -> None:
        """Добивка — рассылка, ответ в переписке — нет: донор написал сам."""
        donor = await _donor(session, MEDIA, dr=None)
        followup = MessageModel(domain_id=donor.domain_id, step=1)
        answer = MessageModel(domain_id=donor.domain_id, step=ANSWER_STEP)

        refused = await donor_refusal(session, followup, host=MEDIA)

        assert refused is not None
        words = NOT_WRITING_WORDS[ExclusionReason.MAJOR_MEDIA]
        assert (refused.reason, words in refused.words) == (Refusal.NOT_WRITING, True)
        assert await donor_refusal(session, answer, host=MEDIA) is None


class TestManualPrice:
    async def test_big_site_and_lists_get_no_price_by_hand(self, session: AsyncSession) -> None:
        await _donor(session, "big.example.test", dr=88)

        assert await refusal_for(session, "big.example.test") == (
            f"У big.example.test {big_site_reason(80)}: донором с ценой его не заводим."
        )
        assert await refusal_for(session, MEDIA) == (
            f"{MEDIA} — крупное СМИ: им не пишем, донором с ценой не заводим."
        )
        assert await refusal_for(session, "blog.example.test") is None
