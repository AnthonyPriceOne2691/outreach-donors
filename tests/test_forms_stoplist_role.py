"""«Формы» — только тем, кому пишем: стоп-лист и одна роль на сайт (28.10.2026).

Форма — тоже письмо (`contacts/forms._form_allowed`). Очередь доноров сверяла только «кому не
пишем» ТЗ, очередь рекламодателей — ни того, ни другого: домен, внесённый в стоп-лист уже после
приёма, стоял в очереди с готовым текстом письма, а сайту, которому пишет другое направление,
человек отправил бы форму во второй роли. Теперь у обеих очередей — правило отправки, своим
этапом: запись стоп-листа на домен, держащая этап сейчас (`runs/exclusions.stoplisted`), и живое
письмо другого этапа (`attempts.roles_elsewhere`).

Одним условием судят страница очереди, её число, меню, главная и строка под действие, у доноров —
ещё и карточка: каждый случай сверяется со всеми сразу, на обеих очередях. Бизнес ниши из «кому
не пишем» — тоже не в очереди, и «пишем» человека этого не перебивает: ТЗ, «кому не пишем» — на
всех путях; рекламодатель по ссылке так не судится — его отсеял гейт кандидатов.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
from backend.features.crawl.denylist import MAJOR_MEDIA
from backend.features.crawl.niche import NICHE
from backend.features.letters import attempts
from backend.features.ops.overview import overview, work
from backend.features.outreach.repository import EVERY_STAGE
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.stage2_rows import promoted

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

HOST = "held.example.test"
FREE = "free.example.test"
#: Крупное СМИ — из самого списка (`crawl/denylist.py`), а не вписанное здесь.
MEDIA = sorted(MAJOR_MEDIA)[0]


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


async def _donor_site(session: AsyncSession, host: str) -> int:
    return (await _form_donor(session, host)).domain_id


async def _advertiser_site(session: AsyncSession, host: str) -> int:
    """Рекламодатель по ссылке — ему пишем; на сайте только форма, адреса в базе нет."""
    return (await promoted(session, host, contact_status=ContactStatus.FORM_ONLY)).domain_id


async def _stoplist(
    session: AsyncSession,
    domain_id: int,
    *,
    stage: Stage | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Запись стоп-листа на домен — заведённая руками уже после того, как сайт встал в очередь."""
    session.add(
        SuppressionModel(
            domain_id=domain_id,
            reason=SuppressionReason.MANUAL,
            stage=stage,
            expires_at=expires_at,
            created_by="оператор@team.example.test",
        )
    )
    await session.flush()


async def _letter(
    session: AsyncSession,
    domain_id: int,
    stage: Stage,
    *,
    status: MessageStatus = MessageStatus.SENT,
    reason: str | None = None,
) -> None:
    """Первое письмо сайту в роли этапа `stage` — без адреса, как письмо продаж."""
    campaign = CampaignModel(stage=stage, name=f"Другая роль: {stage.value}", status="running")
    session.add(campaign)
    await session.flush()
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            domain_id=domain_id,
            step=0,
            status=status,
            failure_reason=reason,
            subject="Hello",
            body="Hello",
            sent_at=None if status is MessageStatus.QUEUED else datetime.now(UTC),
            idempotency_key=f"{stage.value}:forms-{domain_id}:{status.value}",
        )
    )
    await session.flush()


async def _donors_waiting(session: AsyncSession) -> list[str]:
    """Кто в очереди доноров — по странице; число очереди, меню и главной — то же."""
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


async def _advertisers_waiting(session: AsyncSession) -> list[str]:
    """Кто в очереди рекламодателей — по странице; её число, меню и главная — то же
    (доноров с формой в этих тестах нет, и меню с главной считают одних рекламодателей)."""
    hosts = [row.host for row in await forms.advertiser_queue(session)]
    view = await overview(session, stages=EVERY_STAGE)
    counted = {
        "очередь": await forms.advertiser_total(session),
        "меню": (await work(session, stages=EVERY_STAGE)).forms,
        "главная": view.waiting.forms,
        "главная, рекламодатели": view.waiting.advertiser_forms,
    }
    assert counted == dict.fromkeys(counted, len(hosts)), "разошлись с очередью"
    return hosts


@dataclass(frozen=True, slots=True)
class Queue:
    """Очередь «Форм» в тесте: её этап, как поставить в неё сайт и как её прочесть."""

    name: str
    stage: Stage
    #: Сайт с одной формой, которому эта очередь пишет, — номер его домена.
    put: Callable[[AsyncSession, str], Awaitable[int]]
    #: Кто в очереди — по странице; её числа сверены с ней внутри.
    waiting: Callable[[AsyncSession], Awaitable[list[str]]]

    @property
    def others(self) -> tuple[Stage, ...]:
        """Этапы других направлений: их запись форму не держит, их живое письмо — держит."""
        return tuple(stage for stage in Stage if stage is not self.stage)


DONORS = Queue("доноры", Stage.DONORS, _donor_site, _donors_waiting)
ADVERTISERS = Queue("рекламодатели", Stage.ADVERTISERS, _advertiser_site, _advertisers_waiting)
QUEUES = [pytest.param(queue, id=queue.name) for queue in (DONORS, ADVERTISERS)]
#: Запись своего этапа и запись без этапа — обе держат.
OWN_RECORDS = [
    pytest.param(queue, stage, id=f"{queue.name}-{stage.value if stage else 'без-этапа'}")
    for queue in (DONORS, ADVERTISERS)
    for stage in (None, queue.stage)
]
#: Очередь и этап другого направления.
WITH_OTHERS = [
    pytest.param(queue, other, id=f"{queue.name}-{other.value}")
    for queue in (DONORS, ADVERTISERS)
    for other in queue.others
]


class TestStopList:
    @pytest.mark.parametrize(("queue", "stage"), OWN_RECORDS)
    async def test_held_domain_is_neither_in_the_queue_nor_in_the_counts(
        self, session: AsyncSession, queue: Queue, stage: Stage | None
    ) -> None:
        held = await queue.put(session, HOST)
        await queue.put(session, FREE)
        await _stoplist(session, held, stage=stage)

        assert await queue.waiting(session) == [FREE]

    @pytest.mark.parametrize(("queue", "other"), WITH_OTHERS)
    async def test_record_of_another_stage_does_not_hold_the_form(
        self, session: AsyncSession, queue: Queue, other: Stage
    ) -> None:
        """Запись с этапом держит письма только своего этапа (`stoplist.holding`)."""
        site = await queue.put(session, HOST)
        await _stoplist(session, site, stage=other)

        assert await queue.waiting(session) == [HOST]

    @pytest.mark.parametrize("queue", QUEUES)
    @pytest.mark.parametrize(
        ("days", "queued"), [(-1, [HOST]), (30, [])], ids=["истекла", "в-сроке"]
    )
    async def test_record_holds_until_its_term(
        self, session: AsyncSession, queue: Queue, days: int, queued: list[str]
    ) -> None:
        """«Размещались за последние 12 месяцев» — окно: истёкшая запись не держит никого."""
        site = await queue.put(session, HOST)
        await _stoplist(session, site, expires_at=datetime.now(UTC) + timedelta(days=days))

        assert await queue.waiting(session) == queued

    @pytest.mark.parametrize("queue", QUEUES)
    async def test_record_of_one_address_does_not_close_the_site(
        self, session: AsyncSession, queue: Queue
    ) -> None:
        """Запись на адрес держит адрес, а не сайт — как у отбора прогона: адреса у сайта
        формы нет, и форма идёт на сайт, а не этому человеку."""
        await queue.put(session, HOST)
        session.add(SuppressionModel(email=f"editor@{HOST}", reason=SuppressionReason.MANUAL))
        await session.flush()

        assert await queue.waiting(session) == [HOST]


class TestOneRolePerSite:
    @pytest.mark.parametrize(("queue", "other"), WITH_OTHERS)
    @pytest.mark.parametrize(
        "status",
        [MessageStatus.QUEUED, MessageStatus.DELIVERED, MessageStatus.STOPPED],
        ids=["в-очереди", "дошло", "остановлено-человеком"],
    )
    async def test_site_in_work_elsewhere_is_not_in_the_queue(
        self, session: AsyncSession, queue: Queue, other: Stage, status: MessageStatus
    ) -> None:
        """Живое — как у отправки (`attempts.dead`): и письмо, остановленное человеком."""
        site = await queue.put(session, HOST)
        await queue.put(session, FREE)
        await _letter(session, site, other, status=status)

        assert await queue.waiting(session) == [FREE]

    @pytest.mark.parametrize(("queue", "other"), WITH_OTHERS)
    @pytest.mark.parametrize(
        "status", [MessageStatus.STOPPED, MessageStatus.BOUNCED], ids=["снято", "не-дошло"]
    )
    async def test_letter_that_never_reached_anyone_does_not_hold(
        self, session: AsyncSession, queue: Queue, other: Stage, status: MessageStatus
    ) -> None:
        """Снятое при отправке — сайт был в работе у этой очереди — «писали» не считается
        (`attempts.withdrawn`), не дошедшее — мёртвое: сайт свободен и снова ждёт рук."""
        site = await queue.put(session, HOST)
        withdrawn = status is MessageStatus.STOPPED
        reason = attempts.elsewhere_reason(queue.stage) if withdrawn else None
        await _letter(session, site, other, status=status, reason=reason)

        assert await queue.waiting(session) == [HOST]


class TestTheRowForAnAction:
    async def test_held_donor_is_refused_like_a_donor_not_in_the_queue(
        self, session: AsyncSession
    ) -> None:
        donor = await _form_donor(session, HOST)
        await _stoplist(session, donor.domain_id)

        with pytest.raises(UnknownFormError, match="нет в ручной очереди"):
            await forms.form_row(session, donor.id)
        with pytest.raises(UnknownFormError, match="нет в ручной очереди"):
            await forms.gave_up(session, donor.id)
        await session.refresh(donor)
        assert donor.contact_status is ContactStatus.FORM_ONLY

    async def test_held_advertiser_is_refused_like_one_not_in_the_queue(
        self, session: AsyncSession
    ) -> None:
        advertiser = await promoted(session, HOST, contact_status=ContactStatus.FORM_ONLY)
        await _stoplist(session, advertiser.domain_id, stage=Stage.ADVERTISERS)

        with pytest.raises(UnknownFormError, match="нет в ручной очереди"):
            await forms.advertiser_filled(session, advertiser.id, email=f"ads@{HOST}")
        with pytest.raises(UnknownFormError, match="нет в ручной очереди"):
            await forms.advertiser_gave_up(session, advertiser.id)
        await session.refresh(advertiser)
        assert advertiser.contact_status is ContactStatus.FORM_ONLY

    async def test_address_is_not_written_and_the_refusal_says_why(
        self, client: AsyncClient, session: AsyncSession, operator: dict[str, str]
    ) -> None:
        donor = await _form_donor(session, HOST)
        await _stoplist(session, donor.domain_id)
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

    async def test_advertiser_refusal_says_why(
        self, client: AsyncClient, session: AsyncSession, operator: dict[str, str]
    ) -> None:
        """Сайту, которому пишут продажи, оффер не уходит — и адрес с формы не вписывается."""
        advertiser = await promoted(session, HOST, contact_status=ContactStatus.FORM_ONLY)
        await _letter(session, advertiser.domain_id, Stage.SALES)

        answer = await client.post(
            f"/api/advertisers/forms/{advertiser.id}/filled",
            json={"email": f"ads@{HOST}"},
            headers=operator,
        )

        assert answer.status_code == 409
        assert answer.json()["detail"] == (
            f"Рекламодателя №{advertiser.id} нет в ручной очереди: либо адрес у него уже есть, "
            "либо ему не пишем: сайт в стоп-листе или в работе у другого направления, бизнес "
            "ниши — из «кому не пишем» или без «пишем» человека, — либо очередь успел разобрать "
            "кто-то другой"
        )
        written = await session.scalar(
            select(func.count(ContactModel.id)).where(
                ContactModel.domain_id == advertiser.domain_id
            )
        )
        assert written == 0

    async def test_no_letter_text_for_a_site_in_work_elsewhere(
        self, client: AsyncClient, session: AsyncSession, operator: dict[str, str]
    ) -> None:
        """Текст для формы — вызов модели: сайту, которому пишут продажи, он не собирается."""
        donor = await _form_donor(session, HOST)
        await _letter(session, donor.domain_id, Stage.SALES)

        answer = await client.post(
            f"/api/contacts/forms/{donor.id}/letter", json={}, headers=operator
        )

        assert answer.status_code == 409
        assert "нет в ручной очереди" in answer.json()["detail"]


async def _niche_business(session: AsyncSession, host: str) -> int:
    """Бизнес ниши из выдачи с «пишем» человека — на сайте только форма; номер рекламодателя."""
    business = await promoted(
        session,
        host,
        source=NICHE,
        confirmed=True,
        decided=True,
        contact_status=ContactStatus.FORM_ONLY,
    )
    return business.id


async def _not_writing_site(session: AsyncSession, how: str) -> str:
    """Сайт из «кому не пишем»: по имени — из списка крупных СМИ, по DR — замер прогона выше
    порога (строка донора без решения человека). Возвращает его хост."""
    if how == "по-имени":
        return MEDIA
    await make_donor(session, HOST, dr=88, review=None)
    return HOST


class TestNicheBusinessWeDoNotWriteTo:
    @pytest.mark.parametrize("how", ["по-имени", "по-DR"])
    async def test_is_neither_in_the_queue_nor_in_the_counts(
        self, session: AsyncSession, how: str
    ) -> None:
        """Письмо такому бизнесу отправка остановит (`site_not_writing`) — и форма не нужна."""
        host = await _not_writing_site(session, how)
        held = await _niche_business(session, host)
        await _niche_business(session, FREE)

        assert await _advertisers_waiting(session) == [FREE]
        with pytest.raises(UnknownFormError, match="нет в ручной очереди"):
            await forms.advertiser_filled(session, held, email=f"ads@{host}")

    @pytest.mark.parametrize("how", ["по-имени", "по-DR"])
    async def test_advertiser_by_a_link_is_not_judged_so(
        self, session: AsyncSession, how: str
    ) -> None:
        """По ссылке — как раньше: при отправке его сайт так не судится."""
        host = await _not_writing_site(session, how)
        await promoted(session, host, contact_status=ContactStatus.FORM_ONLY)

        assert await _advertisers_waiting(session) == [host]


async def test_card_says_whether_the_donor_waits_by_the_queue_condition(
    client: AsyncClient, session: AsyncSession, operator: dict[str, str]
) -> None:
    """Карточка звала в «Формы» всякого принятого; теперь — условием очереди."""
    held = await _form_donor(session, HOST)
    free = await _form_donor(session, FREE)
    await _stoplist(session, held.domain_id)

    said = {
        host: (await client.get(f"/api/donors/{donor.id}", headers=operator)).json()["in_forms"]
        for host, donor in ((HOST, held), (FREE, free))
    }

    assert said == {HOST: False, FREE: True}
