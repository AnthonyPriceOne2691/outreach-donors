"""Цена руками по HTTP: «Указать цену» на карточке донора и «Завести донора
вручную» на панели «Обход доноров».

Правила ввода и отказов — у ядра (`donors/manual_price.py`), их проверяет
`test_manual_price.py`. Здесь — то, что доходит до экрана: код ответа и отказ
словами ядра целиком, право `run`, автор цены — вошедший, а не поле запроса,
и ничего не записано, если отказали. И путь, ради которого всё затевалось:
донор с ручной ценой — цель обхода Этапа 2 и на панели, и у сервера запуска.

Домены, которые приводятся к корню, — из зарезервированных (`example.com`,
`example.net`): у `*.test` и `.invalid` в списке суффиксов корня нет, и ядро
отвечает им «не домен».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import pytest
from backend.api.crawls import routes as crawl_routes
from backend.features.core.domain import (
    AuditAction,
    DonorStatus,
    Stage,
    SuppressionReason,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.run import RunCandidateModel
from backend.features.replies.repository import ReplyRepository
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor, make_sender

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

WHO = "anna@ours.example.test"
CARD_HOST = "card.example.test"
#: Как писать цену и валюту — хвост каждого отказа ввода (`manual_price._*_HINT`).
PRICE_HINT = "впишите число больше нуля, например 150 или 150.50."
CURRENCY_HINT = "впишите код (USD, EUR, GBP) или знак ($, €, £)."


@pytest.fixture
async def anna(make_user: MakeUser, sign_in: SignIn) -> str:
    """Оператор с правом `run`: цену знает сам и вписывает её."""
    await make_user(WHO, role=UserRole.OPERATOR)
    return await sign_in(WHO)


@pytest.fixture
async def viewer(make_user: MakeUser, sign_in: SignIn) -> str:
    """Смотреть базу может, писать в неё цену — нет."""
    await make_user("watcher@ours.example.test", permissions={"run": False})
    return await sign_in("watcher@ours.example.test")


@pytest.fixture
async def card(session: AsyncSession) -> DonorModel:
    """Принятый донор без цены — тот, кому цену указывают с карточки."""
    domain = await make_donor(session, CARD_HOST)
    await session.commit()
    return await _donor(session, domain.host)


async def _donor(session: AsyncSession, host: str) -> DonorModel:
    found = await session.scalar(
        select(DonorModel).join(DomainModel).where(DomainModel.host == host)
    )
    assert found is not None
    return found


async def _count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _journal(session: AsyncSession) -> list[AuditLogModel]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.PRICE_REVIEWED)
        .order_by(AuditLogModel.id)
    )
    return list(rows.all())


async def _nothing_written(session: AsyncSession, *, domains: int) -> None:
    """Отказ — до первой записи: ни домена, ни цены, ни строки журнала."""
    assert await _count(session, DomainModel) == domains
    priced = await session.scalar(
        select(func.count()).select_from(DonorModel).where(DonorModel.last_price.is_not(None))
    )
    assert priced == 0
    assert await _journal(session) == []


# --- что записано и что ответили ---------------------------------------------------------------


async def test_a_new_domain_becomes_a_donor_on_behalf_of_who_signed_in(
    client: AsyncClient, anna: str, session: AsyncSession
) -> None:
    response = await client.post(
        "/api/donors",
        json={
            "host": "https://www.Example.com/blog?x=1",
            "price": "150.50",
            "currency": "€",
            "note": "прайс агентства",
            # Поля «кто» у запроса нет: лишнее поле не делает автором другого.
            "by": "someone@else.example.test",
        },
        headers=bearer(anna),
    )

    assert response.status_code == 200, response.text
    donor = await _donor(session, "example.com")
    assert response.json() == {
        "donor_id": donor.id,
        "host": "example.com",
        "created": True,
        "suitable": True,
        "price": "150.50",
        "currency": "EUR",
    }
    card = (await client.get(f"/api/donors/{donor.id}", headers=bearer(anna))).json()
    assert {key: card[key] for key in ("status", "review", "review_by", "entered_by")} == {
        "status": "suitable",
        "review": "accepted",
        "review_by": WHO,
        "entered_by": WHO,
    }
    assert (card["last_price"], card["last_price_currency"], card["last_offers"]) == (
        "150.50",
        "EUR",
        None,
    )
    assert (card["last_price_source"], card["last_price_note"], card["last_price_by"]) == (
        "manual",
        "прайс агентства",
        WHO,
    )
    (row,) = await _journal(session)
    author = await session.scalar(select(UserModel.id).where(UserModel.email == WHO))
    assert (row.user_id, row.target) == (author, f"donor:{donor.id}")
    assert (row.details["действие"], row.details["кто"]) == ("донор заведён вручную, с ценой", WHO)


async def test_the_same_domain_again_only_gets_the_new_price(
    client: AsyncClient, anna: str, session: AsyncSession
) -> None:
    """Повторное заведение — не второй донор, а новая цена прежнему."""
    first = await client.post(
        "/api/donors", json={"host": "example.com", "price": "150"}, headers=bearer(anna)
    )
    again = await client.post(
        "/api/donors", json={"host": "www.example.com", "price": 140}, headers=bearer(anna)
    )

    assert again.status_code == 200, again.text
    assert again.json() == {**first.json(), "created": False, "price": "140.00"}
    assert await _count(session, DonorModel) == 1
    assert [row.details["действие"] for row in await _journal(session)] == [
        "донор заведён вручную, с ценой",
        "цена указана вручную",
    ]


async def test_the_card_gets_the_price_and_answers_with_itself(
    client: AsyncClient, anna: str, card: DonorModel, session: AsyncSession
) -> None:
    # Цена — как её отдаёт база (`DECIMAL(10, 2)`): запрос карточки читает
    # донора заново, а сессия теста одна на тест и помнит записанное.
    await ReplyRepository(session).store_price(
        domain_id=card.domain_id,
        price=Decimal("120.00"),
        currency="EUR",
        offers=[{"product": "guest post", "price": "120"}],
    )
    await session.commit()

    response = await client.post(
        f"/api/donors/{card.id}/price",
        json={"price": 90, "note": "  LinkDetective\n"},
        headers=bearer(anna),
    )

    assert response.status_code == 200, response.text
    answer = response.json()
    assert (answer["id"], answer["host"]) == (card.id, CARD_HOST)
    # Список цен чужого ответа рядом с ручной ценой врал бы — его нет.
    assert (answer["last_price"], answer["last_price_currency"], answer["last_offers"]) == (
        "90.00",
        "USD",
        None,
    )
    assert (answer["last_price_source"], answer["last_price_note"], answer["last_price_by"]) == (
        "manual",
        "LinkDetective",
        WHO,
    )
    # Донора принимали в очереди прогона, а не здесь: решение прежнее.
    assert (answer["review"], answer["entered_by"]) == ("accepted", None)
    (row,) = await _journal(session)
    assert (row.details["действие"], row.details["прежняя цена"]) == (
        "цена указана вручную",
        "120.00 EUR, из ответа",
    )


# --- отказы ввода: 400 словами ядра ------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({"price": ""}, f"Цена не указана: {PRICE_HINT}"),
        ({"price": "сто"}, f"«сто» — не цена: {PRICE_HINT}"),
        ({"price": 0}, f"«0» — не цена: {PRICE_HINT}"),
        ({"price": "-5"}, f"«-5» — не цена: {PRICE_HINT}"),
        ({"price": "1,200"}, f"«1,200» — не цена: {PRICE_HINT}"),
        (
            {"price": "100000"},
            "Цена 100000 — не меньше 100 000: за одно размещение столько не платят, "
            "проверьте число.",
        ),
        ({"price": 150.555}, "Цена 150.555 точнее копеек: хранится два знака после точки."),
        ({"price": "150", "currency": " "}, f"Не указана валюта цены: {CURRENCY_HINT}"),
        (
            {"price": "150", "currency": "UDS"},
            f"Валюта «UDS» не знакома или неоднозначна: {CURRENCY_HINT}",
        ),
        (
            {"price": "150", "note": "x" * 201},
            "«Откуда цена» — не длиннее 200 знаков, а вписано 201.",
        ),
    ],
    ids=["пусто", "словом", "ноль", "минус", "запятая", "потолок", "копейки", "без валюты",
         "опечатка валюты", "длинная заметка"],
)  # fmt: skip
async def test_not_a_price_is_400_in_the_core_words_and_nothing_changes(
    client: AsyncClient,
    anna: str,
    card: DonorModel,
    session: AsyncSession,
    body: dict[str, Any],
    said: str,
) -> None:
    response = await client.post(f"/api/donors/{card.id}/price", json=body, headers=bearer(anna))

    assert (response.status_code, response.json()["detail"]) == (400, said)
    await _nothing_written(session, domains=1)


@pytest.mark.parametrize("raw", ["не-адрес", "probe.invalid", "  "])
async def test_not_a_domain_is_400_in_the_core_words(
    client: AsyncClient, anna: str, session: AsyncSession, raw: str
) -> None:
    response = await client.post(
        "/api/donors", json={"host": raw, "price": "150"}, headers=bearer(anna)
    )

    assert (response.status_code, response.json()["detail"]) == (
        400,
        f"«{raw.strip()}» — не домен: впишите адрес сайта, например example.com.",
    )
    await _nothing_written(session, domains=0)


async def test_a_price_is_never_written_without_an_address_behind_it(
    client: AsyncClient, anna: str, session: AsyncSession
) -> None:
    """Автор цены — почта вошедшего: запись без неё в журнале не назвала бы того,
    кто цену знает. Учётка с испорченной почтой цены не пишет."""
    await session.execute(update(UserModel).where(UserModel.email == WHO).values(email="anna"))
    await session.commit()

    response = await client.post(
        "/api/donors", json={"host": "example.com", "price": "150"}, headers=bearer(anna)
    )

    assert (response.status_code, response.json()["detail"]) == (
        400,
        "«anna» — не почта сотрудника: цену записывают от имени того, кто её знает.",
    )
    await _nothing_written(session, domains=0)


# --- отказы домену: 409 словами ядра -----------------------------------------------------------

Refusal = Callable[[AsyncSession], Awaitable[str]]


async def _sending(session: AsyncSession) -> str:
    await make_sender(session, "outreach@mail.example.net")
    return "example.net — наш домен рассылки: донором он не бывает."


async def _public_zone(_: AsyncSession) -> str:
    return "example.gov — гос. или учебная зона: размещений такие сайты не продают."


async def _platform(_: AsyncSession) -> str:
    return "facebook.com — платформа или соцсеть: разместиться там нельзя."


async def _stoplisted(session: AsyncSession) -> str:
    domain = await make_donor(session, "example.com")
    session.add(
        SuppressionModel(domain_id=domain.id, reason=SuppressionReason.UNSUBSCRIBED, stage=None)
    )
    return (
        "example.com в стоп-листе: ему не пишем, и донором с ценой его не заводим. "
        "Снять запись — на экране «Стоп-лист»."
    )


async def _supplier(session: AsyncSession) -> str:
    session.add(SupplierDonorModel(host="example.com", note="размещались в марте"))
    return (
        "example.com — донор-поставщик агентства: его рекламодателей не трогаем, "
        "Этап 2 по нему не запускается."
    )


async def _rejected(session: AsyncSession) -> str:
    await make_donor(session, "example.com", review="rejected")
    return (
        "example.com отклонён человеком: донором его не заводим, пока решение не снимут "
        "в очереди прогона — она по ссылке в карточке донора."
    )


async def _waiting(session: AsyncSession) -> str:
    runs = RunRepository(session)
    settings = await runs.create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = await runs.create_run(
        stage=Stage.DONORS, settings_id=settings.id, keywords=["garden"], country="us"
    )
    domain = await make_donor(session, "example.com", review=None)
    session.add(RunCandidateModel(run_id=run.id, domain_id=domain.id, status="pending"))
    return (
        f"example.com ждёт решения в очереди прогона №{run.id}: решают там. Примете — "
        "цену укажете на карточке донора."
    )


async def _unsuitable(session: AsyncSession) -> str:
    await make_donor(session, "example.com", review=None)
    donor = await _donor(session, "example.com")
    donor.status, donor.reject_reason = DonorStatus.UNSUITABLE, "dr ниже порога"
    return (
        "example.com не прошёл пороги отбора (dr ниже порога): Этап 2 обходит "
        "только годных доноров."
    )


@pytest.mark.parametrize(
    ("raw", "refused"),
    [
        ("https://example.net/", _sending),
        ("www.example.gov", _public_zone),
        ("facebook.com", _platform),
        ("example.com", _stoplisted),
        ("www.example.com", _supplier),
        ("example.com", _rejected),
        ("example.com", _waiting),
        ("example.com", _unsuitable),
    ],
    ids=["наш домен рассылки", "гос. зона", "платформа", "стоп-лист", "поставщик",
         "отклонён", "ждёт в очереди прогона", "не прошёл пороги"],
)  # fmt: skip
async def test_a_refused_domain_is_409_in_the_core_words_and_nothing_changes(
    client: AsyncClient, anna: str, session: AsyncSession, raw: str, refused: Refusal
) -> None:
    said = await refused(session)
    await session.commit()
    domains = await _count(session, DomainModel)
    reviews = list((await session.scalars(select(DonorModel.review))).all())

    response = await client.post(
        "/api/donors", json={"host": raw, "price": "150"}, headers=bearer(anna)
    )

    assert (response.status_code, response.json()["detail"]) == (409, said)
    await _nothing_written(session, domains=domains)
    assert list((await session.scalars(select(DonorModel.review))).all()) == reviews


async def test_from_the_card_a_rejected_record_is_refused_too(
    client: AsyncClient, anna: str, session: AsyncSession
) -> None:
    """Карточка открывается у любой записи — и у отклонённой; правило то же."""
    domain = await make_donor(session, CARD_HOST, review="rejected")
    donor = await _donor(session, domain.host)
    await session.commit()

    response = await client.post(
        f"/api/donors/{donor.id}/price", json={"price": "150"}, headers=bearer(anna)
    )

    assert response.status_code == 409
    assert response.json()["detail"].startswith(f"{CARD_HOST} отклонён человеком")
    await _nothing_written(session, domains=1)


# --- нет права, нет донора, мусор --------------------------------------------------------------


async def test_without_the_right_nothing_is_written(
    client: AsyncClient, viewer: str, card: DonorModel, session: AsyncSession
) -> None:
    entered = await client.post(
        "/api/donors", json={"host": "example.com", "price": "150"}, headers=bearer(viewer)
    )
    priced = await client.post(
        f"/api/donors/{card.id}/price", json={"price": "150"}, headers=bearer(viewer)
    )

    for response in (entered, priced):
        assert response.status_code == 403
        assert "«run»" in response.json()["detail"]
    await _nothing_written(session, domains=1)


@pytest.mark.parametrize("donor_id", [987654, 2**40])
async def test_price_for_nobody_is_404(
    client: AsyncClient, anna: str, session: AsyncSession, donor_id: int
) -> None:
    response = await client.post(
        f"/api/donors/{donor_id}/price", json={"price": "150"}, headers=bearer(anna)
    )

    assert (response.status_code, response.json()["detail"]) == (404, f"Донора №{donor_id} нет")
    await _nothing_written(session, domains=0)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/donors", {"price": "150"}),
        ("/api/donors", {"host": ["example.com"], "price": "150"}),
        ("/api/donors/{donor}/price", {}),
        ("/api/donors/{donor}/price", {"price": None}),
        ("/api/donors/{donor}/price", {"price": [150]}),
        ("/api/donors/{donor}/price", {"price": "150", "currency": 840}),
        ("/api/donors/{donor}/price", {"price": "150", "note": {"откуда": "прайс"}}),
        ("/api/donors/abc/price", {"price": "150"}),
        ("/api/donors/{donor}/price", "{price: 150"),
    ],
    ids=["без домена", "домен списком", "без цены", "цена null", "цена списком",
         "валюта числом", "заметка объектом", "номер не число", "не JSON"],
)  # fmt: skip
async def test_garbage_is_422_and_nothing_changes(
    client: AsyncClient,
    anna: str,
    card: DonorModel,
    session: AsyncSession,
    path: str,
    body: dict[str, Any] | str,
) -> None:
    target = path.format(donor=card.id)
    response = (
        await client.post(
            target,
            content=body,
            headers={**bearer(anna), "content-type": "application/json"},
        )
        if isinstance(body, str)
        else await client.post(target, json=body, headers=bearer(anna))
    )

    assert response.status_code == 422, response.text
    await _nothing_written(session, domains=1)


# --- до обхода: ручная цена — основание Этапа 2 ------------------------------------------------


async def test_a_hand_priced_donor_is_a_crawl_target_and_the_launch_takes_it(
    client: AsyncClient,
    anna: str,
    card: DonorModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Требование Этапа 2: «по кому запускаем — только доноры с известной ценой (из
    базы или заведённые вручную)». Донор без цены на панели — число «без цены» и
    подсказка указать её; с ручной ценой — строка панели и обход по кнопке."""
    put: list[int] = []

    def enqueue(run_id: int, job_id: str | None = None) -> str:
        put.append(run_id)
        return job_id or "x"

    monkeypatch.setattr(crawl_routes, "enqueue_crawl", enqueue)
    monkeypatch.setattr(crawl_routes, "workers_alive", lambda **_: 4)

    before = (await client.get("/api/crawls/targets", headers=bearer(anna))).json()
    assert (before["donors"], before["no_price"]) == ([], 1)
    assert any("указать вручную на карточке донора" in note for note in before["notes"])

    priced = await client.post(
        f"/api/donors/{card.id}/price",
        json={"price": "120", "currency": "EUR"},
        headers=bearer(anna),
    )
    entered = await client.post(
        "/api/donors",
        json={"host": "example.com", "price": "150", "note": "прайс агентства"},
        headers=bearer(anna),
    )
    assert (priced.status_code, entered.status_code) == (200, 200)

    after = (await client.get("/api/crawls/targets", headers=bearer(anna))).json()
    rows = {row["host"]: (row["price"], row["currency"], row["source"]) for row in after["donors"]}
    assert rows == {CARD_HOST: (120.0, "EUR", "manual"), "example.com": (150.0, "USD", "manual")}
    assert after["no_price"] == 0

    launched = await client.post(
        "/api/crawls", json={"hosts": ["example.com", CARD_HOST]}, headers=bearer(anna)
    )
    body = launched.json()
    assert launched.status_code == 200, body
    assert (sorted(body["queued"]), body["refused"]) == (sorted([CARD_HOST, "example.com"]), {})
    assert sorted(put) == sorted(body["queued"].values())
