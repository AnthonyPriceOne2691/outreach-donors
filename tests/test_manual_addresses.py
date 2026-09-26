"""Адреса донора, вписанные руками с его карточки: сколько угодно, с удалением.

Замечание 26.09.2026: «проверь, чтобы в карточке донора была возможность
заполнить контакты, и не один ящик, а несколько». Правило одно с очередью
форм (`contacts/manual.py`). Проверяется на настоящей базе, по сочетаниям:

* исход поиска и правило «кому искать» остаются правдой — общий поиск
  не тратит лестницу на донора с вписанным адресом (там платная ступень),
  фильтр «с адресом» и значок «адрес найден» не врут, карточка не пишет
  «искали», если не искали, а удалённый последний адрес даёт честный исход;
* адрес, по которому шла переписка, не удаляется — и почему, сказано
  до нажатия;
* сборка писем по прогону не запирается навсегда донором, которому
  искать больше нечего.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.config import contacts as contacts_cfg
from backend.features.contacts import forms, manual
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.repository import (
    MANUAL_REFUSAL,
    ContactRepository,
    refusal_of,
    search_refusal,
)
from backend.features.core.domain import (
    AuditAction,
    ContactSource,
    ContactStatus,
    DonorStatus,
    MessageStatus,
    Stage,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.core.models.run import RunCandidateModel
from backend.features.letters.repository import LetterRepository
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime.now(UTC)
LONG_AGO = NOW - timedelta(days=contacts_cfg.CONTACT_TTL_DAYS + 20)


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def viewer_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("зритель@site.com", role=UserRole.OPERATOR, permissions={"run": False})
    return await sign_in("зритель@site.com")


async def _donor(
    session: AsyncSession,
    host: str,
    *,
    review: str | None = "accepted",
    contact_status: ContactStatus | None = None,
    attempted_at: datetime | None = None,
    emails: tuple[str, ...] = (),
) -> DonorModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=DonorStatus.SUITABLE,
        dr=40,
        review=review,
        contact_status=contact_status,
        contact_attempted_at=attempted_at,
    )
    session.add(donor)
    for email in emails:
        session.add(ContactModel(domain_id=domain.id, email=email, source=ContactSource.PAGE))
    await session.commit()
    return donor


async def _add(client: AsyncClient, token: str, donor: DonorModel, email: str) -> Any:
    return await client.post(
        f"/api/contacts/donors/{donor.id}/addresses", json={"email": email}, headers=bearer(token)
    )


async def _remove(client: AsyncClient, token: str, donor: DonorModel, contact_id: int) -> Any:
    return await client.delete(
        f"/api/contacts/donors/{donor.id}/addresses/{contact_id}", headers=bearer(token)
    )


async def _contacts(session: AsyncSession, donor: DonorModel) -> list[ContactModel]:
    rows = await session.execute(
        select(ContactModel)
        .where(ContactModel.domain_id == donor.domain_id)
        .order_by(ContactModel.id)
    )
    return list(rows.scalars().all())


async def _waits(session: AsyncSession, donor: DonorModel) -> bool:
    return bool(await ContactRepository(session, donor_id=donor.id).pending_count())


class TestAdding:
    async def test_several_addresses_one_by_one(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "several.example.test")

        first = await _add(client, token, donor, "  Editor@Several.example.test ")
        second = await _add(client, token, donor, "ads@several.example.test")

        assert first.status_code == second.status_code == 200, second.text
        emails = [contact["email"] for contact in second.json()["contacts"]]
        assert sorted(emails) == ["ads@several.example.test", "editor@several.example.test"]
        stored = await _contacts(session, donor)
        assert {contact.source for contact in stored} == {ContactSource.MANUAL}
        assert {contact.verification_score for contact in stored} == {manual.MANUAL_SCORE}

    async def test_address_found_but_search_not_invented(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Вписанный адрес — «адрес найден», но отметки поиска нет: карточка
        не напишет «Искали <дата>», если лестница по донору не ходила."""
        donor = await _donor(session, "fresh.example.test")

        card = (await _add(client, token, donor, "editor@fresh.example.test")).json()

        assert card["contact_status"] == "found"
        assert card["contact_attempted_at"] is None
        await session.refresh(donor)
        assert donor.contact_status is ContactStatus.FOUND
        assert donor.contact_attempted_at is None

    async def test_list_filter_and_badge_see_the_address(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "listed.example.test")
        await _add(client, token, donor, "editor@listed.example.test")

        listed = (await client.get("/api/donors?has_contact=true", headers=bearer(token))).json()

        assert [
            (row["host"], row["contacts"], row["contact_status"]) for row in listed["rows"]
        ] == [("listed.example.test", 1, "found")]

    @pytest.mark.parametrize(
        ("typed", "said"),
        [
            ("", "Впишите адрес почты."),
            ("не адрес", "«не адрес» не похож на адрес почты."),
            ("you@yourcompany.com", "Такой адрес не записываем — домен-заглушка"),
            ("privacy@site.example.test", "Такой адрес не записываем — чужой отдел"),
            ("x" * 250 + "@site.example.test", "Адрес длиннее 255 знаков"),
        ],
    )
    async def test_not_an_address_is_refused_with_words(
        self, client: AsyncClient, token: str, session: AsyncSession, typed: str, said: str
    ) -> None:
        """Проверка — та же, что у сборки писем: адрес, который сборка потом
        молча пропустит, не записывается."""
        donor = await _donor(session, "strict.example.test")

        response = await _add(client, token, donor, typed)

        assert response.status_code == 400
        assert response.json()["detail"].startswith(said)
        assert await _contacts(session, donor) == []

    async def test_duplicate_is_refused_with_words(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "twice.example.test", emails=("ads@twice.example.test",))

        response = await _add(client, token, donor, "ADS@twice.example.test")

        assert response.status_code == 409
        assert response.json()["detail"] == "Адрес ads@twice.example.test у донора уже есть."

    async def test_the_author_is_written_down(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "logged.example.test")

        await _add(client, token, donor, "editor@logged.example.test")

        entry = (
            await session.execute(
                select(AuditLogModel).where(AuditLogModel.action == AuditAction.CONTACT_ADDED)
            )
        ).scalar_one()
        assert entry.user_id is not None
        assert entry.target == f"donor:{donor.id}"
        assert entry.details == {
            "донор": "logged.example.test",
            "адрес": "editor@logged.example.test",
            "откуда": "карточка донора, вписан руками",
        }

    async def test_candidate_may_get_an_address_too(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Кандидату адрес вписать можно: письма всё равно уходят только
        донорам, и карточка это говорит."""
        candidate = await _donor(session, "candidate.example.test", review=None)

        card = (await _add(client, token, candidate, "editor@candidate.example.test")).json()

        assert card["review"] is None
        assert card["letter_contact_id"] is None
        assert card["letter_blocked"] == "письма уходят только донорам, принятым человеком"

    async def test_without_the_right_nothing_is_written(
        self, client: AsyncClient, viewer_token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "guarded.example.test")

        response = await _add(client, viewer_token, donor, "editor@guarded.example.test")

        assert response.status_code == 403
        assert await _contacts(session, donor) == []

    @pytest.mark.parametrize("donor_id", [999_999, 2**40])
    async def test_unknown_donor_is_not_found(
        self, client: AsyncClient, token: str, donor_id: int
    ) -> None:
        response = await client.post(
            f"/api/contacts/donors/{donor_id}/addresses",
            json={"email": "editor@none.example.test"},
            headers=bearer(token),
        )

        assert response.status_code == 404
        assert response.json()["detail"] == f"Донора №{donor_id} нет"


class TestSearchRuleStaysTrue:
    @pytest.mark.parametrize(
        ("contact_status", "attempted_at"),
        [
            (None, None),  # не искали
            (ContactStatus.NOT_FOUND, LONG_AGO),  # искали давно — пора снова
            (ContactStatus.NO_QUOTA, NOW - timedelta(days=1)),  # не спросили — повтор
        ],
    )
    async def test_general_search_skips_a_donor_with_a_written_address(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        contact_status: ContactStatus | None,
        attempted_at: datetime | None,
    ) -> None:
        """До адреса донор ждёт поиска, после — нет: лестница кончается
        платной ступенью, а лучше вписанного она не найдёт."""
        donor = await _donor(
            session, "waits.example.test", contact_status=contact_status, attempted_at=attempted_at
        )
        assert await _waits(session, donor)

        await _add(client, token, donor, "editor@waits.example.test")

        assert not await _waits(session, donor)
        assert "waits.example.test" not in await ContactRepository(session).pending_hosts()
        assert await search_refusal(session, donor) == MANUAL_REFUSAL

    async def test_one_donor_search_is_refused_with_the_reason(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "asked.example.test")
        await _add(client, token, donor, "editor@asked.example.test")

        response = await client.post(f"/api/contacts/donors/{donor.id}", headers=bearer(token))

        assert response.status_code == 409
        assert response.json()["detail"] == MANUAL_REFUSAL

    async def test_a_pass_that_was_already_walking_does_not_overwrite(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Общий поиск взял донора до того, как человек вписал адрес, и кончился
        «адреса нет»: исход не перезаписывается — рядом с адресом он был бы ложью."""
        donor = await _donor(session, "raced.example.test")
        await _add(client, token, donor, "editor@raced.example.test")

        await ContactRepository(session).save(
            [LadderResult(host="raced.example.test", status=ContactStatus.NOT_FOUND)]
        )
        await session.commit()

        await session.refresh(donor)
        assert donor.contact_status is ContactStatus.FOUND
        assert donor.contact_attempted_at is None

    async def test_form_queue_lets_go_of_a_donor_with_an_address(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(
            session,
            "form.example.test",
            contact_status=ContactStatus.FORM_ONLY,
            attempted_at=NOW - timedelta(days=2),
        )
        assert [row.host for row in await forms.queue(session)] == ["form.example.test"]

        await _add(client, token, donor, "editor@form.example.test")

        assert await forms.queue(session) == []

    async def test_words_agree_with_the_query_with_a_written_address(
        self, session: AsyncSession
    ) -> None:
        """Все состояния поиска × вписанный адрес: решает запрос, называет
        `refusal_of` — и они не расходятся (та же проверка, что без адреса,
        в `test_api_contacts.py`)."""
        disagreements = []
        for index, (status, attempted) in enumerate(
            [
                (outcome, when)
                for outcome in (None, *ContactStatus)
                for when in (None, NOW - timedelta(days=5), LONG_AGO)
            ]
        ):
            donor = await _donor(
                session,
                f"state-{index}.example.test",
                contact_status=status,
                attempted_at=attempted,
            )
            session.add(
                ContactModel(
                    domain_id=donor.domain_id,
                    email=f"editor@state-{index}.example.test",
                    source=ContactSource.MANUAL,
                )
            )
            await session.flush()
            waits = await _waits(session, donor)
            said = refusal_of(donor, now=NOW, manual=True)
            if waits or said != MANUAL_REFUSAL:
                disagreements.append(f"{status}/{attempted}: запрос {waits}, слова {said!r}")

        assert disagreements == []


class TestRemoving:
    async def test_last_address_of_a_never_searched_donor(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Не искали, вписали, удалили — снова «не искали», и донор снова ждёт
        поиска: адреса нет, а поиска не было."""
        donor = await _donor(session, "undo.example.test")
        card = (await _add(client, token, donor, "editor@undo.example.test")).json()

        after = await _remove(client, token, donor, card["contacts"][0]["id"])

        assert after.status_code == 200, after.text
        assert after.json()["contacts"] == []
        assert after.json()["contact_status"] is None
        assert await _waits(session, donor)

    async def test_last_address_after_a_search(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Лестница находила адрес, человек его удалил — «адреса нет», искали
        тогда-то; повтор — по сроку, а не сразу: платная ступень та же."""
        attempted = NOW - timedelta(days=3)
        donor = await _donor(
            session,
            "wrong.example.test",
            contact_status=ContactStatus.FOUND,
            attempted_at=attempted,
            emails=("wrong@wrong.example.test",),
        )
        (contact,) = await _contacts(session, donor)

        after = (await _remove(client, token, donor, contact.id)).json()

        assert after["contact_status"] == "not_found"
        assert after["contact_attempted_at"] is not None
        assert not await _waits(session, donor)
        listed = (await client.get("/api/donors?has_contact=false", headers=bearer(token))).json()
        assert [row["host"] for row in listed["rows"]] == ["wrong.example.test"]

    async def test_not_the_last_address_keeps_the_outcome(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(
            session,
            "two.example.test",
            contact_status=ContactStatus.FOUND,
            attempted_at=NOW - timedelta(days=3),
            emails=("one@two.example.test", "two@two.example.test"),
        )
        first, _ = await _contacts(session, donor)

        after = (await _remove(client, token, donor, first.id)).json()

        assert [contact["email"] for contact in after["contacts"]] == ["two@two.example.test"]
        assert after["contact_status"] == "found"

    async def test_removing_the_written_address_brings_the_search_rule_back(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Вписанный удалили, найденные остались: правило поиска — прежнее,
        по сроку найденного, а не «никогда»."""
        donor = await _donor(
            session,
            "back.example.test",
            contact_status=ContactStatus.FOUND,
            attempted_at=LONG_AGO,
            emails=("page@back.example.test",),
        )
        card = (await _add(client, token, donor, "editor@back.example.test")).json()
        assert not await _waits(session, donor)
        written = next(c for c in card["contacts"] if c["email"] == "editor@back.example.test")

        await _remove(client, token, donor, written["id"])

        assert await _waits(session, donor)

    async def test_the_author_of_the_removal_is_written_down(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "gone.example.test", emails=("gone@gone.example.test",))
        (contact,) = await _contacts(session, donor)

        await _remove(client, token, donor, contact.id)

        entry = (
            await session.execute(
                select(AuditLogModel).where(AuditLogModel.action == AuditAction.CONTACT_REMOVED)
            )
        ).scalar_one()
        assert entry.user_id is not None
        assert entry.details is not None
        assert entry.details["адрес"] == "gone@gone.example.test"

    async def test_address_of_another_donor_is_not_found(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        mine = await _donor(session, "mine.example.test")
        theirs = await _donor(session, "theirs.example.test", emails=("ads@theirs.example.test",))
        (contact,) = await _contacts(session, theirs)

        response = await _remove(client, token, mine, contact.id)

        assert response.status_code == 404
        assert len(await _contacts(session, theirs)) == 1


async def _campaign(session: AsyncSession) -> CampaignModel:
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка адресов", status="running")
    session.add(campaign)
    await session.flush()
    return campaign


class TestAddressWithLettersStays:
    @pytest.mark.parametrize("status", [MessageStatus.QUEUED, MessageStatus.DELIVERED])
    async def test_letter_to_the_address_blocks_removal(
        self, client: AsyncClient, token: str, session: AsyncSession, status: MessageStatus
    ) -> None:
        """Письмо в очереди — тоже переписка: обнулённая ссылка оставила бы
        его без адресата. Почему нельзя — в карточке до нажатия."""
        donor = await _donor(session, "written.example.test", emails=("ads@written.example.test",))
        (contact,) = await _contacts(session, donor)
        campaign = await _campaign(session)
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                domain_id=donor.domain_id,
                contact_id=contact.id,
                status=status,
                idempotency_key=f"test:written:{status.value}",
            )
        )
        await session.commit()

        card = (await client.get(f"/api/donors/{donor.id}", headers=bearer(token))).json()
        response = await _remove(client, token, donor, contact.id)

        assert card["contacts"][0]["removal_refusal"] == manual.HAS_LETTERS
        assert response.status_code == 409
        assert response.json()["detail"] == manual.HAS_LETTERS
        assert len(await _contacts(session, donor)) == 1

    async def test_thread_on_the_address_blocks_removal(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "talked.example.test", emails=("ads@talked.example.test",))
        (contact,) = await _contacts(session, donor)
        campaign = await _campaign(session)
        session.add(
            ThreadModel(domain_id=donor.domain_id, campaign_id=campaign.id, contact_id=contact.id)
        )
        await session.commit()

        response = await _remove(client, token, donor, contact.id)

        assert response.status_code == 409

    async def test_a_replied_address_stays(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(
            session, "answered.example.test", emails=("ads@answered.example.test",)
        )
        (contact,) = await _contacts(session, donor)
        contact.last_replied_at = NOW
        await session.commit()

        response = await _remove(client, token, donor, contact.id)

        assert response.status_code == 409

    async def test_untouched_address_says_nothing(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "clean.example.test", emails=("ads@clean.example.test",))

        card = (await client.get(f"/api/donors/{donor.id}", headers=bearer(token))).json()

        assert card["contacts"][0]["removal_refusal"] is None


class TestLetterBuildIsNotLockedForever:
    async def test_run_scope_does_not_wait_for_a_donor_with_a_written_address(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Сборка по прогону ждёт, пока принятым поищут адрес. Донора с вписанным
        адресом общий поиск не возьмёт никогда — ждать его значило бы запереть
        сборку по прогону навсегда."""
        repository = RunRepository(session)
        settings = await repository.create_settings(
            defaults(),
            geo_top_n=5,
            geo_min_share=0.2,
            metrics_ttl_days=90,
            price_ttl_days=150,
            units_cap=100_000,
        )
        run = await repository.create_run(
            stage=Stage.DONORS, settings_id=settings.id, keywords=["garden"], country="us"
        )
        donor = await _donor(session, "scoped.example.test")
        session.add(RunCandidateModel(run_id=run.id, domain_id=donor.domain_id, status="accepted"))
        await session.commit()
        assert await LetterRepository(session).contacts_pending([run.id]) == 1

        await _add(client, token, donor, "editor@scoped.example.test")

        assert await LetterRepository(session).contacts_pending([run.id]) == 0
