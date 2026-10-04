"""Передача лида дальше: подписанный вебхук и выгрузка CSV.

До 04.10.2026 лид — ответ человека на оффер рекламодателю — жил только в
диалогах. Решение Anthony: выгрузка файлом с экрана и вебхук на настраиваемый
адрес CRM. Проверяется: что считается лидом, подпись, которую получатель
может проверить, и что очередь повторяет, а что — нет.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies import lead_handoff
from backend.features.replies.pipeline import Inbox
from backend.features.runs.failures import is_permanent
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_letter_draft import FakeQueue
from tests.test_letters_advertisers import NOW, SECRET, answer_to, sent_offer
from tests.test_replies_inbox import inbound_secret, reply_from, sent

__all__ = ["inbound_secret", "sent"]  # фикстуры приёма — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
HOOK = "https://crm.example.test/hooks/leads"
HOOK_SECRET = "s3cr3t-for-tests"  # pragma: allowlist secret


@pytest.fixture
def hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "LEAD_WEBHOOK_URL", HOOK)
    monkeypatch.setattr(outreach_cfg, "LEAD_WEBHOOK_SECRET", HOOK_SECRET)


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.replies.routes.runs_queue", lambda: fake)
    return fake


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


async def _lead(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    letter = await sent_offer(session)
    got = await Inbox(session, now=NOW).accept(answer_to(letter, "We might be interested."))
    await session.commit()
    assert got.reply_id is not None
    return got.reply_id


def _card(**changes: str) -> lead_handoff.LeadCard:
    fields: dict[str, Any] = dict.fromkeys(lead_handoff.LeadCard.__dataclass_fields__, "")
    fields |= {"lead_id": 7, "advertiser": "brand.test", "text": "We might be interested."}
    return lead_handoff.LeadCard(**(fields | changes))


class TestWhatIsALead:
    @pytest.mark.usefixtures("filled_legal")
    async def test_human_answer_to_an_offer_is_a_lead_with_its_link(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reply_id = await _lead(session, monkeypatch)

        card = await lead_handoff.lead_card(session, reply_id)

        assert card is not None
        assert card.text == "We might be interested."
        assert card.donor_host
        assert card.page_url
        assert card.anchor
        assert card.taken_by == ""

    async def test_donor_answer_is_not_a_lead(
        self, session: AsyncSession, sent: Any, inbound_secret: None
    ) -> None:
        got = await Inbox(session, now=NOW).accept(reply_from(sent, "Our price is $90."))
        assert got.reply_id is not None

        assert await lead_handoff.lead_card(session, got.reply_id) is None
        assert await lead_handoff.leads(session) == []

    @pytest.mark.usefixtures("filled_legal")
    async def test_export_filters_by_taken(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reply_id = await _lead(session, monkeypatch)
        assert [c.lead_id for c in await lead_handoff.leads(session, taken=False)] == [reply_id]
        assert await lead_handoff.leads(session, taken=True) == []

        reply = await session.get(ReplyModel, reply_id)
        assert reply is not None
        reply.reviewed_by, reply.reviewed_at = "anna@ours.test", NOW
        await session.flush()
        assert [c.lead_id for c in await lead_handoff.leads(session, taken=True)] == [reply_id]


class TestFile:
    def test_csv_header_is_the_webhook_fields(self) -> None:
        rows = list(csv.DictReader(io.StringIO(lead_handoff.to_csv([_card()]))))
        assert list(rows[0]) == list(lead_handoff.LeadCard.__dataclass_fields__)
        assert rows[0]["advertiser"] == "brand.test"


class TestWebhook:
    def test_signature_is_checkable_by_the_receiver(self) -> None:
        body = lead_handoff.body_of(_card(), event_id="lead-7")
        header = lead_handoff.signature(body, secret=HOOK_SECRET, timestamp=1_700_000_000)

        stamp, given = (part.split("=", 1)[1] for part in header.split(","))
        expected = hmac.new(HOOK_SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256)
        assert hmac.compare_digest(given, expected.hexdigest())
        assert json.loads(body)["event_id"] == "lead-7"

    @pytest.mark.usefixtures("hook")
    async def test_delivered_with_signature(self) -> None:
        seen: list[httpx.Request] = []

        def crm(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(202)

        async with httpx.AsyncClient(transport=httpx.MockTransport(crm)) as http:
            code = await lead_handoff.deliver(_card(), http, event_id="lead-7", now=1_700_000_000)

        assert code == 202
        assert str(seen[0].url) == HOOK
        assert seen[0].headers[lead_handoff.SIGNATURE_HEADER].startswith("t=1700000000,v1=")
        assert json.loads(seen[0].content)["lead"]["lead_id"] == 7

    @pytest.mark.usefixtures("hook")
    @pytest.mark.parametrize("code", [400, 401, 404, 422])
    async def test_refusal_is_final_and_not_retried(self, code: int) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(code, request=r))
        ) as http:
            with pytest.raises(lead_handoff.LeadWebhookRefusedError) as refused:
                await lead_handoff.deliver(_card(), http, event_id="lead-7")
        assert is_permanent(refused.value)

    @pytest.mark.usefixtures("hook")
    @pytest.mark.parametrize("code", [408, 429, 500, 503])
    async def test_server_trouble_is_retried(self, code: int) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(code, request=r))
        ) as http:
            with pytest.raises(httpx.HTTPStatusError) as failed:
                await lead_handoff.deliver(_card(), http, event_id="lead-7")
        assert not is_permanent(failed.value)

    async def test_no_address_is_final(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(outreach_cfg, "LEAD_WEBHOOK_URL", "")
        async with httpx.AsyncClient() as http:
            with pytest.raises(lead_handoff.LeadWebhookOffError) as off:
                await lead_handoff.deliver(_card(), http, event_id="lead-7")
        assert is_permanent(off.value)


class TestScreen:
    async def test_taking_a_lead_queues_the_handoff(
        self,
        client: AsyncClient,
        admin_token: str,
        session: AsyncSession,
        filled_legal: None,
        hook: None,
        queue: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reply_id = await _lead(session, monkeypatch)

        taken = await client.post(f"/api/replies/{reply_id}/lead", headers=bearer(admin_token))

        assert taken.status_code == 200, taken.text
        assert taken.json()["handoff"] == "queued"
        assert queue.kwargs[0]["job_id"] == f"lead-{reply_id}"

    async def test_without_an_address_the_lead_stays_here(
        self,
        client: AsyncClient,
        admin_token: str,
        session: AsyncSession,
        filled_legal: None,
        queue: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "LEAD_WEBHOOK_URL", "")
        reply_id = await _lead(session, monkeypatch)

        taken = await client.post(f"/api/replies/{reply_id}/lead", headers=bearer(admin_token))
        resend = await client.post(
            f"/api/replies/{reply_id}/lead/send", headers=bearer(admin_token)
        )

        assert taken.json()["handoff"] == "off"
        assert queue.kwargs == []
        assert resend.status_code == 409
        assert "OUTREACH_LEAD_WEBHOOK_URL" in resend.json()["detail"]

    async def test_resend_needs_a_taken_lead(
        self,
        client: AsyncClient,
        admin_token: str,
        session: AsyncSession,
        filled_legal: None,
        hook: None,
        queue: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reply_id = await _lead(session, monkeypatch)
        early = await client.post(f"/api/replies/{reply_id}/lead/send", headers=bearer(admin_token))
        await client.post(f"/api/replies/{reply_id}/lead", headers=bearer(admin_token))
        later = await client.post(f"/api/replies/{reply_id}/lead/send", headers=bearer(admin_token))

        assert early.status_code == 409
        assert later.status_code == 200, later.text
        assert queue.kwargs[-1]["job_id"].startswith(f"lead-{reply_id}-")  # новый номер события
        assert len(queue.kwargs) == 2  # «взять» и ручной повтор

    async def test_leads_file(
        self,
        client: AsyncClient,
        admin_token: str,
        session: AsyncSession,
        filled_legal: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reply_id = await _lead(session, monkeypatch)

        got = await client.get("/api/replies/leads.csv", headers=bearer(admin_token))

        assert got.status_code == 200, got.text
        assert got.headers["content-type"].startswith("text/csv")
        assert got.headers["x-export-rows"] == "1"
        rows = list(csv.DictReader(io.StringIO(got.text)))
        assert rows[0]["lead_id"] == str(reply_id)
