"""Выдуманный вердикт при живом проверяльщике не годен (сверка модуля продаж с ТЗ, п. 4).

По умолчанию проверяльщик — `fixture`, и пилот идёт на выдуманных вердиктах. Переключили на
живой — лид, чей адрес проверял `fixture`, в очередь не идёт, его письмо не уходит, причина
словами «очистите базу заново», а кнопка очистки берёт его снова и проверяет адрес
по-настоящему. При `fixture` — всё как было. Сети нет: живой проверяльщик — подделка с именем
`hunter`, почта домена — заглушка. Адреса выдуманы.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from backend.config import sales as sales_cfg
from backend.features.contacts.mx import MailRoute
from backend.features.contacts.provider import (
    ProviderBlockedError,
    ProviderError,
    ProviderRateLimitError,
)
from backend.features.core.domain import MessageStatus
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.sending import NotReadyError, Sending
from backend.features.sales import clean_jobs, cleaning, mail, queue, verifier
from backend.features.sales.models import LeadStatus, SalesLeadModel
from backend.features.sales.verifier import TOO_OFTEN, Verdict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_send import _transports

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

JANE = "jane@acme.example.test"
OLGA = "olga@beta.example.test"


class LiveVerifier:
    """Живой проверяльщик без сети: имя — как у Hunter, вердикт — «адрес есть», единица — платная."""

    name = "hunter"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def verify(self, email: str) -> Verdict:
        self.asked.append(email)
        return Verdict("valid", 88, units=1)


@pytest.fixture(autouse=True)
def _month_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Месячный потолок проверок у живого проверяльщика задан с запасом: здесь не о нём
    (`tests/test_sales_verify_cap.py`)."""
    monkeypatch.setattr("backend.config.sales.VERIFIER_MONTH_CAP", "100000")


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.MX

    monkeypatch.setattr(cleaning, "mail_route", route)


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


def _provider(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", name)


async def _build(session: AsyncSession, world: w.World, rewriter: Any) -> queue.QueueReport:
    return await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=5)


# --- очередь и отправка --------------------------------------------------------------------


async def test_live_lead_checked_by_the_fixture_waits_with_the_reason(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мутант «fixture при живом идёт в очередь»: письмо встало бы, модель бы позвали."""
    _provider(monkeypatch, "live")
    await w.lead(session, world.hypothesis_id, JANE, verification_status="fixture:valid")
    await w.lead(session, world.hypothesis_id, OLGA, verification_status="hunter:valid")

    report = await _build(session, world, w.CorridorRewriter())

    assert report.prepared == 1  # Ольга — проверена по-настоящему
    assert report.waiting == Counter({verifier.MADE_UP: 1})
    assert verifier.MADE_UP == "адрес проверен тестовым проверяльщиком — очистите базу заново"


async def test_with_the_fixture_verifier_everything_is_as_before(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _provider(monkeypatch, "fixture")
    await w.lead(session, world.hypothesis_id, JANE, verification_status="fixture:valid")

    report = await _build(session, world, w.CorridorRewriter())

    assert (report.prepared, dict(report.waiting)) == (1, {})


async def test_queued_letter_of_a_fixture_lead_does_not_go_after_switching_to_live(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Письмо собрано на пилоте при `fixture`; переключили на живой — пачка его не отправит."""
    _provider(monkeypatch, "fixture")
    await w.lead(session, world.hypothesis_id, JANE, verification_status="fixture:valid")
    await _build(session, world, w.CorridorRewriter())
    letter = await session.scalar(select(MessageModel.id))
    assert letter is not None
    _provider(monkeypatch, "live")

    with pytest.raises(NotReadyError, match=verifier.MADE_UP):
        await Sending(session, _transports(), now=w.NOW).send(letter)


# --- очистка берёт его снова ----------------------------------------------------------------


async def test_cleaning_with_the_live_verifier_checks_the_fixture_lead_again(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _provider(monkeypatch, "live")
    made_up = await w.lead(session, world.hypothesis_id, JANE, verification_status="fixture:valid")
    await w.lead(session, world.hypothesis_id, OLGA, verification_status="hunter:valid")
    live = LiveVerifier()

    report = await cleaning.clean(session, live, hypothesis_id=world.hypothesis_id)

    assert live.asked == [JANE]  # проверенную по-настоящему Ольгу второй раз не платим
    assert (report.checked, report.ready, report.paid_units) == (1, 1, 1)
    await session.refresh(made_up)
    assert (made_up.status, made_up.verification_status) == (LeadStatus.READY, "hunter:valid")
    assert verifier.unproven(made_up) is None
    again = await _build(session, world, w.CorridorRewriter())
    assert again.prepared == 2


class Refusing:
    """Живой проверяльщик, который отказывает: сеть, ключ, частота."""

    name = "hunter"

    def __init__(self, refusal: ProviderError) -> None:
        self.refusal = refusal
        self.asked: list[str] = []

    async def verify(self, email: str) -> Verdict:
        self.asked.append(email)
        raise self.refusal


@pytest.mark.parametrize(
    "refusal",
    [
        ProviderError("провайдер не ответил (HTTP 503) — повторим следующей очисткой"),
        ProviderBlockedError("ключ проверки адресов не принят провайдером (HTTP 401)"),
        ProviderRateLimitError(TOO_OFTEN),
    ],
)
async def test_failed_recheck_keeps_a_written_lead_ready_in_its_dialog_and_still_unproven(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch, refusal: ProviderError
) -> None:
    """Пилотным лидам с выдуманным вердиктом уже писали; включили живой, а Hunter отказал (ключ,
    сеть, частота). Лид остаётся `ready` с прежним вердиктом и причиной: `new` снял бы его с
    цепочки — проход добивок закрыл бы её навсегда (`LeadStoppedError`), а черновик ответа ему
    не писался бы. Первое письмо по-прежнему держит `unproven`, следующая очистка возьмёт снова.
    Второй лид — тот, кого проход не спросил после остановки платной части."""
    monkeypatch.setattr(cleaning, "VERIFY_CONCURRENCY", 1)
    _provider(monkeypatch, "fixture")
    jane = await w.lead(session, world.hypothesis_id, JANE, verification_status="fixture:valid")
    olga = await w.lead(session, world.hypothesis_id, OLGA, verification_status="fixture:valid")
    await _build(session, world, w.CorridorRewriter())
    for letter in await session.scalars(select(MessageModel)):
        letter.status = MessageStatus.SENT  # первые письма ушли на пилоте
    await session.flush()
    _provider(monkeypatch, "live")

    report = await cleaning.clean(session, Refusing(refusal), hypothesis_id=world.hypothesis_id)

    halted = isinstance(refusal, verifier.STOPS)
    assert (report.checked, report.ready, report.unverified, report.verified) == (2, 0, 2, 0)
    for lead in (jane, olga):
        await session.refresh(lead)
        assert (lead.status, lead.verification_status, lead.verification_score) == (
            LeadStatus.READY,
            "fixture:valid",
            None,
        )
        assert lead.cleaning_note == f"проверка не выполнена: {refusal}"
        dialog = await mail.lead_dialog(session, lead.id)
        assert dialog is not None
        assert await mail.unwritable(session, dialog) is None  # добивки и ответы идут
        assert verifier.unproven(lead) == verifier.MADE_UP
    assert report.stopped == (str(refusal) if halted else None)
    assert (await clean_jobs.waiting(session, world.hypothesis_id)).count == 2


async def test_cleaning_with_the_fixture_verifier_does_not_take_ready_leads(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _provider(monkeypatch, "fixture")
    await w.lead(session, world.hypothesis_id, JANE, verification_status="fixture:valid")

    report = await cleaning.clean(
        session, verifier.FixtureVerifier(), hypothesis_id=world.hypothesis_id
    )

    assert report.checked == 0


@pytest.mark.parametrize(("provider", "waiting"), [("live", 2), ("fixture", 1)])
async def test_waiting_counts_the_fixture_leads_only_with_the_live_verifier(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    waiting: int,
) -> None:
    _provider(monkeypatch, provider)
    await w.lead(session, world.hypothesis_id, JANE, verification_status="fixture:valid")
    await w.lead(session, world.hypothesis_id, OLGA, status=LeadStatus.NEW)
    await w.lead(
        session, world.hypothesis_id, "kim@gamma.example.test", verification_status="hunter:valid"
    )

    found = await clean_jobs.waiting(session, world.hypothesis_id)

    assert found.count == waiting


# --- экран -----------------------------------------------------------------------------------


async def test_screen_names_the_unproven_lead_and_counts_it_for_the_clean_button(
    client: httpx.AsyncClient,
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    _provider(monkeypatch, "live")
    await w.lead(session, world.hypothesis_id, JANE, verification_status="fixture:valid")
    await w.lead(session, world.hypothesis_id, OLGA, verification_status="hunter:valid")
    await session.commit()
    await make_user("seller@ours.example.test")
    headers = bearer(await sign_in("seller@ours.example.test"))

    leads = (await client.get("/api/sales/leads", headers=headers)).json()
    cards = (await client.get("/api/sales/hypotheses", headers=headers)).json()

    unproven = {row["email"]: row["unproven"] for row in leads["rows"]}
    assert unproven == {JANE: verifier.MADE_UP, OLGA: None}
    [card] = cards["rows"]
    assert card["recheck"] == 1
    _provider(monkeypatch, "fixture")
    calm = (await client.get("/api/sales/hypotheses", headers=headers)).json()
    assert calm["rows"][0]["recheck"] == 0


def test_without_a_verdict_or_with_a_live_one_the_lead_is_not_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _provider(monkeypatch, " LIVE ")
    assert verifier.unproven(SalesLeadModel(email=JANE, verification_status=None)) is None
    assert verifier.unproven(SalesLeadModel(email=JANE, verification_status="hunter:valid")) is None
    made_up = SalesLeadModel(email=JANE, verification_status="fixture:unknown")
    assert verifier.unproven(made_up) == verifier.MADE_UP
