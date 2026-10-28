"""«Формы» — только тем, кому пишем: стоп-лист и одна роль на сайт (28.10.2026).

Форма — тоже письмо (`contacts/forms.in_queue`). Очередь сверяла только «кому не пишем» ТЗ:
домен, внесённый в стоп-лист уже после приёма донора, стоял в ней с готовым текстом письма, а
сайту, которому пишут продажи или офферы, человек отправил бы форму во второй роли. Теперь —
правило отправки: запись стоп-листа на домен, держащая этап доноров сейчас
(`runs/exclusions.stoplisted`), и живое письмо другого этапа (`attempts.roles_elsewhere`).

Одним условием судят страница очереди, её число, меню, главная, строка под действие и
карточка донора: каждый случай сверяется со всеми сразу.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.contacts import forms
from backend.features.contacts.forms import UnknownFormError
from backend.features.core.domain import (
    ContactStatus,
    MessageStatus,
    Stage,
    SuppressionReason,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import attempts
from backend.features.ops.overview import overview, work
from backend.features.outreach.repository import EVERY_STAGE
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

HOST = "held.example.test"
FREE = "free.example.test"


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("оператор@team.example.test", role=UserRole.OPERATOR)
    return bearer(await sign_in("оператор@team.example.test"))


async def _form_donor(session: AsyncSession, host: str) -> DonorModel:
    """Принятый донор: на сайте только форма, адреса в базе нет."""
    domain = await make_donor(session, host, dr=40)
    donor = (
        await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id))
    ).scalar_one()
    donor.contact_status = ContactStatus.FORM_ONLY
    await session.flush()
    return donor


async def _stoplist(
    session: AsyncSession,
    donor: DonorModel,
    *,
    stage: Stage | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Запись стоп-листа на домен донора — заведённая руками уже после приёма."""
    session.add(
        SuppressionModel(
            domain_id=donor.domain_id,
            reason=SuppressionReason.MANUAL,
            stage=stage,
            expires_at=expires_at,
            created_by="оператор@team.example.test",
        )
    )
    await session.flush()


async def _letter(
    session: AsyncSession,
    donor: DonorModel,
    stage: Stage,
    *,
    status: MessageStatus = MessageStatus.SENT,
    reason: str | None = None,
) -> None:
    """Первое письмо сайту донора в роли этапа `stage` — без адреса, как письмо продаж."""
    campaign = CampaignModel(stage=stage, name=f"Другая роль: {stage.value}", status="running")
    session.add(campaign)
    await session.flush()
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            domain_id=donor.domain_id,
            step=0,
            status=status,
            failure_reason=reason,
            subject="Hello",
            body="Hello",
            sent_at=None if status is MessageStatus.QUEUED else datetime.now(UTC),
            idempotency_key=f"{stage.value}:forms-{donor.domain_id}:{status.value}",
        )
    )
    await session.flush()


async def _waiting(session: AsyncSession) -> list[str]:
    """Кто в очереди — по странице; число очереди, меню и главной — то же, что у страницы."""
    hosts = [row.host for row in await forms.queue(session)]
    view = await overview(session, stages=EVERY_STAGE)
    counted = {
        "очередь": await forms.total(session),
        "меню": (await work(session, stages=EVERY_STAGE)).forms,
        "главная": view.waiting.forms,
        "воронка доноров": view.donors.form_only,
    }
    assert counted == dict.fromkeys(counted, len(hosts)), "разошлись с очередью"
    return hosts


class TestStopList:
    @pytest.mark.parametrize("stage", [None, Stage.DONORS], ids=["без-этапа", "доноров"])
    async def test_held_domain_is_neither_in_the_queue_nor_in_the_counts(
        self, session: AsyncSession, stage: Stage | None
    ) -> None:
        held = await _form_donor(session, HOST)
        await _form_donor(session, FREE)
        await _stoplist(session, held, stage=stage)

        assert await _waiting(session) == [FREE]

    @pytest.mark.parametrize("stage", [Stage.SALES, Stage.ADVERTISERS])
    async def test_record_of_another_stage_does_not_hold_the_form(
        self, session: AsyncSession, stage: Stage
    ) -> None:
        """Запись с этапом держит письма только своего этапа (`stoplist.holding`)."""
        donor = await _form_donor(session, HOST)
        await _stoplist(session, donor, stage=stage)

        assert await _waiting(session) == [HOST]

    @pytest.mark.parametrize(
        ("days", "queued"), [(-1, [HOST]), (30, [])], ids=["истекла", "в-сроке"]
    )
    async def test_record_holds_until_its_term(
        self, session: AsyncSession, days: int, queued: list[str]
    ) -> None:
        """«Размещались за последние 12 месяцев» — окно: истёкшая запись не держит никого."""
        donor = await _form_donor(session, HOST)
        await _stoplist(session, donor, expires_at=datetime.now(UTC) + timedelta(days=days))

        assert await _waiting(session) == queued

    async def test_record_of_one_address_does_not_close_the_site(
        self, session: AsyncSession
    ) -> None:
        """Запись на адрес держит адрес, а не сайт — как у отбора прогона: у донора формы
        адреса нет, и форма идёт на сайт, а не этому человеку."""
        await _form_donor(session, HOST)
        session.add(SuppressionModel(email=f"editor@{HOST}", reason=SuppressionReason.MANUAL))
        await session.flush()

        assert await _waiting(session) == [HOST]


class TestOneRolePerSite:
    @pytest.mark.parametrize("stage", [Stage.SALES, Stage.ADVERTISERS])
    @pytest.mark.parametrize(
        "status",
        [MessageStatus.QUEUED, MessageStatus.DELIVERED, MessageStatus.STOPPED],
        ids=["в-очереди", "дошло", "остановлено-человеком"],
    )
    async def test_site_in_work_elsewhere_is_not_in_the_queue(
        self, session: AsyncSession, stage: Stage, status: MessageStatus
    ) -> None:
        """Живое — как у отправки (`attempts.dead`): и письмо, остановленное человеком."""
        donor = await _form_donor(session, HOST)
        await _form_donor(session, FREE)
        await _letter(session, donor, stage, status=status)

        assert await _waiting(session) == [FREE]

    @pytest.mark.parametrize("stage", [Stage.SALES, Stage.ADVERTISERS])
    @pytest.mark.parametrize(
        ("status", "reason"),
        [
            (MessageStatus.STOPPED, attempts.elsewhere_reason(Stage.DONORS)),
            (MessageStatus.BOUNCED, None),
        ],
        ids=["снято-при-отправке", "не-дошло"],
    )
    async def test_letter_that_never_reached_anyone_does_not_hold(
        self, session: AsyncSession, stage: Stage, status: MessageStatus, reason: str | None
    ) -> None:
        """Снятое при отправке «писали» не считается (`attempts.withdrawn`), не дошедшее —
        мёртвое: сайт свободен, и донор снова ждёт рук."""
        donor = await _form_donor(session, HOST)
        await _letter(session, donor, stage, status=status, reason=reason)

        assert await _waiting(session) == [HOST]


class TestTheRowForAnAction:
    async def test_held_donor_is_refused_like_a_donor_not_in_the_queue(
        self, session: AsyncSession
    ) -> None:
        donor = await _form_donor(session, HOST)
        await _stoplist(session, donor)

        with pytest.raises(UnknownFormError, match="нет в ручной очереди"):
            await forms.form_row(session, donor.id)
        with pytest.raises(UnknownFormError, match="нет в ручной очереди"):
            await forms.gave_up(session, donor.id)
        await session.refresh(donor)
        assert donor.contact_status is ContactStatus.FORM_ONLY

    async def test_address_is_not_written_and_the_refusal_says_why(
        self, client: AsyncClient, session: AsyncSession, operator: dict[str, str]
    ) -> None:
        donor = await _form_donor(session, HOST)
        await _stoplist(session, donor)
        before = await forms.monthly_left(session)

        answer = await client.post(
            f"/api/contacts/forms/{donor.id}/filled",
            json={"email": f"editor@{HOST}"},
            headers=operator,
        )

        assert answer.status_code == 409
        assert answer.json()["detail"] == (
            f"Донора №{donor.id} нет в ручной очереди: либо адрес у него уже есть, либо его "
            "не принимал человек — форму заполняют только донору, — либо ему не пишем: сайт "
            "в стоп-листе, в «кому не пишем» или в работе у другого направления, — либо "
            "очередь успел разобрать кто-то другой"
        )
        written = await session.scalar(
            select(func.count(ContactModel.id)).where(ContactModel.domain_id == donor.domain_id)
        )
        assert (written, await forms.monthly_left(session)) == (0, before)

    async def test_no_letter_text_for_a_site_in_work_elsewhere(
        self, client: AsyncClient, session: AsyncSession, operator: dict[str, str]
    ) -> None:
        """Текст для формы — вызов модели: сайту, которому пишут продажи, он не собирается."""
        donor = await _form_donor(session, HOST)
        await _letter(session, donor, Stage.SALES)

        answer = await client.post(
            f"/api/contacts/forms/{donor.id}/letter", json={}, headers=operator
        )

        assert answer.status_code == 409
        assert "нет в ручной очереди" in answer.json()["detail"]


async def test_card_says_whether_the_donor_waits_by_the_queue_condition(
    client: AsyncClient, session: AsyncSession, operator: dict[str, str]
) -> None:
    """Карточка звала в «Формы» всякого принятого; теперь — условием очереди."""
    held = await _form_donor(session, HOST)
    free = await _form_donor(session, FREE)
    await _stoplist(session, held)

    said = {
        host: (await client.get(f"/api/donors/{donor.id}", headers=operator)).json()["in_forms"]
        for host, donor in ((HOST, held), (FREE, free))
    }

    assert said == {HOST: False, FREE: True}
