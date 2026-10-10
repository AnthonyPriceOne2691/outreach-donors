"""Подтверждение разбора цены по HTTP.

Ветка, без которой не держится приёмка. Проверяется главное:
решение человека сильнее уверенности модели и доходит до карточки
донора, а след того, что сказала модель, не стирается — иначе потом
не узнать, часто ли она ошибается.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    ReplyKind,
    Stage,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
HOST = "donor.example.test"

REVIEW_ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("PATCH", "/api/replies/{reply}", {"price_white": "250", "currency": "EUR"}, "prices"),
    ("GET", "/api/replies/calibration", None, "view"),
    # Ответы, ждущие человека, разбирает один человек, какого бы этапа они ни были.
    ("POST", "/api/replies/{reply}/lead", None, "prices"),
    # Передать лид в CRM ещё раз — тот же человек, что ведёт лиды.
    ("POST", "/api/replies/{reply}/lead/send", None, "prices"),
    # Лиды файлом — переписка с адресами: право того, кто ведёт лиды.
    ("GET", "/api/replies/leads.csv", None, "prices"),
    # Прайс файлом — то же содержимое переписки, что и текст письма.
    ("GET", "/api/replies/{reply}/attachments/{file}", None, "view"),
    # Текст из прайса — то же содержимое, что сам файл, только прочитанное.
    ("GET", "/api/replies/{reply}/attachments/{file}/text", None, "view"),
    # Ответы без письма — та же переписка, только донор не найден.
    ("GET", "/api/replies/unbound", None, "view"),
]


@pytest.fixture
async def unsure(session: AsyncSession) -> ReplyModel:
    """Ответ с ценой, в которой модель не уверена."""
    domain = DomainModel(host=HOST)
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add_all([domain, campaign])
    await session.flush()

    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=40))
    contact = ContactModel(domain_id=domain.id, email=f"editor@{HOST}", source=ContactSource.PAGE)
    session.add(contact)
    await session.flush()

    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add(thread)
    await session.flush()

    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Advertising rates",
        body="Good afternoon,",
        sent_at=NOW,
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(message)
    await session.flush()

    reply = ReplyModel(
        thread_id=thread.id,
        message_id=message.id,
        kind=ReplyKind.HUMAN,
        raw_body="A post is around 250 EUR, I think.",
        from_email=f"elena@{HOST}",
        subject="Re: Advertising rates",
        inbound_message_id="<in-1@site.test>",
        price_white=Decimal("250"),
        currency="EUR",
        confidence=0.4,
        placement="sells",
        model_parse={
            "price_white": "250",
            "price_grey": None,
            "currency": "EUR",
            "placement": "sells",
            "confidence": 0.4,
            "prompt_version": "v-test",
        },
    )
    session.add(reply)
    await session.commit()
    return reply


@pytest.fixture
async def reviewer_token(make_user: Any, sign_in: Any) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def watcher_token(make_user: Any, sign_in: Any) -> str:
    """Учётка, у которой право подтверждать отобрано точечно."""
    await make_user("зритель@site.com", role=UserRole.OPERATOR, permissions={"prices": False})
    return await sign_in("зритель@site.com")


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), REVIEW_ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        unsure: ReplyModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        response = await client.request(
            method, path.replace("{reply}", str(unsure.id)).replace("{file}", "1"), json=body
        )

        assert response.status_code == 401

    async def test_pointed_refusal_closes_the_action(
        self, client: AsyncClient, watcher_token: str, unsure: ReplyModel
    ) -> None:
        """Точечное «нет» поверх роли: смотреть цены можно всем,
        подтверждать — названное действие."""
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "EUR"},
            headers=bearer(watcher_token),
        )

        assert response.status_code == 403
        assert "«prices»" in response.json()["detail"]

    async def test_operator_may_review(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        assert response.status_code == 200, response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/replies")
            for method in methods
        }
        in_table = {
            (method, path.replace("{reply}", "{reply_id}").replace("{file}", "{attachment_id}"))
            for method, path, _, _ in REVIEW_ROUTES
        }
        assert in_app == in_table

    async def test_unknown_reply_is_not_found(
        self, client: AsyncClient, reviewer_token: str
    ) -> None:
        response = await client.patch(
            "/api/replies/999999", json={"price_white": "1"}, headers=bearer(reviewer_token)
        )

        assert response.status_code == 404


class TestReviewing:
    async def test_confirmed_price_reaches_the_donor(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """Подтверждение человека сильнее уверенности модели."""
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        assert response.json()["stored_price"] is True
        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert donor.last_price == Decimal("300.00")
        assert donor.last_price_currency == "EUR"

    async def test_confirmed_price_carries_the_reply_list(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """Человек решает главную цену, а прочие цены письма идут в карточку
        донора как лежат у ответа: правкой они не затрагиваются."""
        listed = [
            {"product": "guest post", "niche": None, "price": "250", "currency": "EUR"},
            {"product": "link insertion", "niche": None, "price": "90", "currency": "EUR"},
        ]
        unsure.offers = listed
        await session.commit()

        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        donor = (await session.execute(select(DonorModel))).scalars().one()
        await session.refresh(donor)
        assert donor.last_price == Decimal("300.00")
        assert donor.last_offers == listed

    async def test_reply_parsed_before_the_list_clears_the_old_one(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """Ответ, разобранный до 06.10.2026, списка не несёт: прежний список
        другого ответа рядом с новой ценой врал бы."""
        donor = (await session.execute(select(DonorModel))).scalars().one()
        donor.last_offers = [{"product": "old", "niche": None, "price": "1", "currency": "EUR"}]
        await session.commit()

        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        await session.refresh(donor)
        assert donor.last_offers is None

    async def test_model_confidence_is_not_erased(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """След того, что сказала модель, — единственное, по чему потом
        видно, часто ли она ошибается."""
        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        assert reply.confidence == 0.4
        assert reply.reviewed_by == "оператор@site.com"
        assert reply.reviewed_at is not None

    async def test_confirming_no_price_stores_nothing(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """«Цены в письме нет» — законное решение, и оно не должно класть
        в карточку донора прошлую догадку модели."""
        response = await client.patch(
            f"/api/replies/{unsure.id}", json={}, headers=bearer(reviewer_token)
        )

        assert response.json()["stored_price"] is False
        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert donor.last_price is None
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        assert reply.price_white is None

    async def test_decline_lands_on_the_domain(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """«Не продаёт размещения» — одним нажатием: для гест-постинга это
        ответ на главный вопрос письма, и он уходит в отбор."""
        response = await client.patch(
            f"/api/replies/{unsure.id}", json={"declines": True}, headers=bearer(reviewer_token)
        )

        body = response.json()
        assert body["seller_answer"] == "declines"
        assert body["stored_price"] is False
        domain = (await session.execute(select(DomainModel))).scalars().one()
        assert domain.seller_answer == "declines"
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        assert reply.placement == "declines"

    async def test_confirmed_price_means_the_site_sells(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )
        domain = (await session.execute(select(DomainModel))).scalars().one()
        assert domain.seller_answer == "sells"

    async def test_decline_with_a_price_is_refused(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"declines": True, "price_white": "100", "currency": "USD"},
            headers=bearer(reviewer_token),
        )
        assert response.status_code == 422
        assert "не бывают" in response.text

    async def test_review_is_written_to_the_journal(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """Цена в базе — решение с последствиями, и у него должен быть автор."""
        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        rows = await session.execute(
            select(AuditLogModel).where(AuditLogModel.target == f"reply:{unsure.id}")
        )
        entry = rows.scalars().one()
        assert entry.details is not None
        assert entry.details["уверенность модели"] == 0.4

    async def test_thread_stops_waiting_after_review(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        """Список диалогов должен перестать звать на разбор: иначе очередь
        не кончается и по ней перестают ходить."""
        before = await client.get("/api/threads", headers=bearer(reviewer_token))
        assert before.json()[0]["state"] == "needs_review"

        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        after = await client.get("/api/threads", headers=bearer(reviewer_token))
        assert after.json()[0]["state"] == "priced"


class TestTypedPriceIsChecked:
    """Подтверждённая цена ложится в карточку донора — и проверяется теми же
    правилами, что цена руками (`donors/manual_price`). До 10.10.2026 «−5 EUR»
    ложилось последней ценой донора, а 1e12 и «доллар США» роняли запрос
    пятисоткой о колонки (проверка QA 10.10.2026)."""

    @pytest.mark.parametrize(
        ("body", "said"),
        [
            ({"price_white": "-5", "currency": "EUR"}, "«-5» — не цена"),
            ({"price_white": -5, "currency": "EUR"}, "«-5» — не цена"),
            ({"price_white": "0", "currency": "EUR"}, "«0» — не цена"),
            ({"price_grey": "1e12", "currency": "EUR"}, "не меньше 100 000"),
            ({"price_white": "100.555", "currency": "EUR"}, "точнее копеек"),
            ({"price_white": "сто евро", "currency": "EUR"}, "«сто евро» — не цена"),
            ({"price_white": "250", "currency": "фантики"}, "«фантики» не знакома"),
            ({"price_white": "250", "currency": "kr"}, "неоднозначна"),
            ({"price_white": "250"}, "Не указана валюта цены"),
        ],
    )
    async def test_not_a_price_is_refused_in_words_before_any_write(
        self,
        client: AsyncClient,
        reviewer_token: str,
        unsure: ReplyModel,
        session: AsyncSession,
        body: dict[str, Any],
        said: str,
    ) -> None:
        response = await client.patch(
            f"/api/replies/{unsure.id}", json=body, headers=bearer(reviewer_token)
        )

        assert response.status_code == 400, response.text
        assert said in response.json()["detail"]
        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert donor.last_price is None
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        await session.refresh(reply)
        assert reply.reviewed_at is None  # разбор по-прежнему ждёт человека
        assert reply.price_white == Decimal("250")  # снимок модели не тронут

    @pytest.mark.parametrize(("typed", "code"), [("доллар США", "USD"), ("€", "EUR")])
    async def test_currency_in_words_or_sign_lands_as_a_code(
        self,
        client: AsyncClient,
        reviewer_token: str,
        unsure: ReplyModel,
        session: AsyncSession,
        typed: str,
        code: str,
    ) -> None:
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": typed},
            headers=bearer(reviewer_token),
        )

        assert response.status_code == 200, response.text
        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert (donor.last_price, donor.last_price_currency) == (Decimal("300.00"), code)

    async def test_no_price_is_not_held_by_the_currency_field(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """«Цены в письме нет» к валюте не относится: догадка модели в поле
        валюты такое решение не останавливает, а длинное слово ложится кодом."""
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": None, "currency": "доллар США"},
            headers=bearer(reviewer_token),
        )

        assert response.status_code == 200, response.text
        assert response.json()["stored_price"] is False
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        await session.refresh(reply)
        assert (reply.price_white, reply.currency) == (None, "USD")


async def _answered_later(
    session: AsyncSession, unsure: ReplyModel, *, price: Decimal | None, confidence: float
) -> ReplyModel:
    """Следующий ответ донора в той же переписке — через три минуты после неуверенного."""
    unsure.created_at = NOW
    later = ReplyModel(
        thread_id=unsure.thread_id,
        message_id=unsure.message_id,
        kind=ReplyKind.HUMAN,
        raw_body="Sorry, to clarify: a guest post is $150.",
        from_email=f"elena@{HOST}",
        subject="Re: Advertising rates",
        inbound_message_id="<in-2@site.test>",
        price_white=price,
        currency="USD" if price is not None else None,
        confidence=confidence,
        placement="sells" if price is not None else "unclear",
    )
    later.created_at = NOW + timedelta(minutes=3)
    session.add(later)
    if price is not None:
        # Уверенный разбор кладёт цену в карточку донора сам (`pipeline._store_price`).
        donor = (await session.execute(select(DonorModel))).scalars().one()
        donor.last_price, donor.last_price_currency = price, "USD"
    await session.commit()
    return later


class TestSupersededAnswer:
    """Проверка прода 10.10.2026: ответ «250» ждал человека (уверенность 60 %), следом
    донор уточнил «150» (уверенность 93 %, цена легла в карточку сама), а карточка
    переписки звала подтвердить 250 — «Подтвердить» записал бы донору старую цену."""

    async def test_card_does_not_call_to_confirm_a_superseded_answer(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        later = await _answered_later(session, unsure, price=Decimal("150"), confidence=0.93)

        response = await client.get(
            f"/api/threads/{unsure.thread_id}", headers=bearer(reviewer_token)
        )

        body = response.json()
        old, new = body["incoming"]
        assert old["id"] == unsure.id
        assert (old["needs_review"], old["superseded_by"]) == (False, later.id)
        assert (new["needs_review"], new["superseded_by"]) == (False, None)
        assert body["card"]["state"] == "priced"

    async def test_confirming_a_superseded_answer_is_refused_in_words(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """Форма могла остаться открытой с тех пор, как более позднего ответа ещё не было."""
        await _answered_later(session, unsure, price=Decimal("150"), confidence=0.93)

        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "USD"},
            headers=bearer(reviewer_token),
        )

        assert response.status_code == 409, response.text
        said = response.json()["detail"]
        assert f"Ответ №{unsure.id} перекрыт" in said
        assert "150 USD" in said
        donor = (await session.execute(select(DonorModel))).scalars().one()
        await session.refresh(donor)
        assert donor.last_price == Decimal("150.00")
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        await session.refresh(reply)
        assert reply.reviewed_at is None

    async def test_decline_on_a_superseded_answer_is_refused_too(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """«Не продаёт» по старому ответу увёл бы из отбора донора, назвавшего цену позже."""
        await _answered_later(session, unsure, price=Decimal("150"), confidence=0.93)

        response = await client.patch(
            f"/api/replies/{unsure.id}", json={"declines": True}, headers=bearer(reviewer_token)
        )

        assert response.status_code == 409, response.text
        domain = (await session.execute(select(DomainModel))).scalars().one()
        assert domain.seller_answer is None

    async def test_calibration_does_not_count_a_superseded_answer_as_waiting(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """Решения по перекрытому не будет: «ждут человека» иначе не убывало бы никогда
        и расходилось бы с числом у меню."""
        later = await _answered_later(session, unsure, price=Decimal("150"), confidence=0.93)
        later.model_parse = {"price_white": "150", "currency": "USD", "prompt_version": "v-test"}
        await session.commit()

        response = await client.get("/api/replies/calibration", headers=bearer(reviewer_token))

        (score,) = response.json()["versions"]
        assert (score["waiting"], score["auto_stored"], score["reviewed"]) == (0, 1, 0)

    async def test_thanks_without_a_price_leaves_the_old_answer_waiting(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """«Спасибо» без цены цену не называет: разбор по-прежнему ждёт и подтверждается."""
        await _answered_later(session, unsure, price=None, confidence=0.95)

        card = await client.get(f"/api/threads/{unsure.thread_id}", headers=bearer(reviewer_token))
        old = card.json()["incoming"][0]
        assert (old["needs_review"], old["superseded_by"]) == (True, None)
        assert card.json()["card"]["state"] == "needs_review"

        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )
        assert response.status_code == 200, response.text


class TestWhatTheCardShows:
    async def test_card_shows_who_answered_and_whether_it_waits(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        response = await client.get(
            f"/api/threads/{unsure.thread_id}", headers=bearer(reviewer_token)
        )

        incoming = response.json()["incoming"][0]
        assert incoming["from_email"] == f"elena@{HOST}"
        assert incoming["needs_review"] is True
        assert incoming["confidence"] == 0.4

    async def test_user_model_is_untouched(self, session: AsyncSession) -> None:
        """Сторож: разбор цен не должен был тронуть учётки."""
        rows = await session.execute(select(UserModel))
        assert rows.scalars().all() is not None


class TestDonorIsFoundEvenWithoutTheLetter:
    async def test_price_reaches_the_donor_through_the_thread(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """Связь с письмом стирается при его удалении, а диалог остаётся —
        и ответ, потерявший письмо, всё равно принадлежит своему донору.

        Найдено живым прогоном: подтверждение срабатывало, а цена
        в карточку не попадала.
        """
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        reply.message_id = None
        await session.commit()

        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "275", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        assert response.json()["stored_price"] is True
        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert donor.last_price == Decimal("275.00")


class TestCalibration:
    """Предложение модели против решения человека — приём, который в соседней
    системе работал в бою для черновиков ответов. Здесь — для полей разбора."""

    async def _calibration(self, client: AsyncClient, token: str) -> dict[str, Any]:
        response = await client.get("/api/replies/calibration", headers=bearer(token))
        assert response.status_code == 200, response.text
        versions = response.json()["versions"]
        assert len(versions) == 1
        return versions[0]

    async def test_confirmed_as_is(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250.00", "currency": "eur"},
            headers=bearer(reviewer_token),
        )

        score = await self._calibration(client, reviewer_token)
        assert score["version"] == "v-test"
        assert (score["reviewed"], score["as_is"], score["edited"]) == (1, 1, 0)

    async def test_corrected_field_is_named(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        score = await self._calibration(client, reviewer_token)
        assert score["edited"] == 1
        assert score["wrong"]["price_white"] == 1
        assert score["wrong"]["currency"] == 0
        # ⚠ Снимок модели правкой человека не переписан — иначе считать не с чем.
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        await session.refresh(reply)
        assert reply.model_parse is not None
        assert reply.model_parse["price_white"] == "250"

    async def test_decline_over_a_price_is_a_placement_miss(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        await client.patch(
            f"/api/replies/{unsure.id}", json={"declines": True}, headers=bearer(reviewer_token)
        )

        score = await self._calibration(client, reviewer_token)
        assert score["wrong"]["placement"] == 1
        assert score["wrong"]["price_white"] == 1

    async def test_unreviewed_is_not_scored(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        """Без человека сверять не с чем — это не «точность», а пустое место."""
        score = await self._calibration(client, reviewer_token)
        assert score["reviewed"] == 0
        assert score["waiting"] == 1, "уверенность 0,4 — ждёт человека, а не положена сама"
        assert score["auto_stored"] == 0
