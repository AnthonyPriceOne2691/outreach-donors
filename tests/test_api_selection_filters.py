"""Фильтры отбора под колонками и страницы по двадцать (26.09.2026).

Четыре флага над таблицей стали фильтрами под своими колонками: пороги,
судья, ответ донора, человек. Проверяется то, ради чего фильтр под
колонкой: он сужает ровно то, что колонка показывает, тем же правилом —
«судья не смотрел» значит «вердикта нет», как у значка в строке, а
«до Ahrefs не дошёл» — «донора нет».

И страница: размер называет сервер, страница за концом — пустая, но
с настоящим числом, по которому экран находит последнюю.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.features.core.domain import DonorStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime.now(UTC)


async def _domain(
    session: AsyncSession,
    host: str,
    *,
    status: DonorStatus | None,
    recommendation: str | None = None,
    layer: str | None = "model",
    human: str | None = None,
    answer: str | None = None,
    dr: int = 40,
) -> DomainModel:
    domain = DomainModel(host=host, human_intent=human, seller_answer=answer)
    if recommendation is not None:
        domain.judge_recommendation = recommendation
        domain.judge_decided_by = layer
        domain.judge_model = "gpt-5-mini"
        domain.judge_reason = "по тексту выдачи"
        domain.judged_at = NOW
    session.add(domain)
    await session.flush()
    if status is not None:
        session.add(DonorModel(domain_id=domain.id, status=status, dr=dr))
    return domain


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def field(session: AsyncSession) -> None:
    """По домену на каждое значение каждого фильтра.

    Вкладки: отклонены — `unsuitable`, `brand`, `cut`, `declined`, `vendor`,
    `asked`; к разбору — `unsure`, `blank`; приняты — `media`, `old`, `sold`,
    `quiet`.
    """
    S, U, C = DonorStatus.SUITABLE, DonorStatus.UNSUITABLE, DonorStatus.UNCHECKED
    await _domain(session, "media.test", status=S, recommendation="accept", layer="arbiter")
    await _domain(session, "old.test", status=S)  # база до судьи
    await _domain(session, "sold.test", status=S, recommendation="accept", answer="sells")
    await _domain(session, "quiet.test", status=S, recommendation="accept", answer="free")
    await _domain(session, "unsure.test", status=S, recommendation="review", layer="arbiter")
    await _domain(session, "blank.test", status=C, recommendation="accept")
    await _domain(session, "unsuitable.test", status=U, recommendation="accept")
    await _domain(
        session,
        "brand.test",
        status=S,
        recommendation="reject",
        layer="rule",
        human="non_commercial",
    )  # человек согласен с правилом
    await _domain(session, "cut.test", status=None, recommendation="reject", layer="rule")
    await _domain(
        session,
        "declined.test",
        status=S,
        recommendation="accept",
        answer="declines",
        human="sells_own",
    )  # человек разошёлся с моделью, донор с ним согласен
    # Вердикт без отметки слоя — судья до трёх слоёв.
    await _domain(session, "vendor.test", status=S, recommendation="reject", layer=None)
    # Судья просил посмотреть, человек посмотрел: «посмотри» — не мнение,
    # и расхождения здесь нет.
    await _domain(session, "asked.test", status=S, recommendation="review", human="sells_own")
    await session.commit()


async def _hosts(client: AsyncClient, token: str, **params: Any) -> list[str]:
    response = await client.get("/api/selection", params=params, headers=bearer(token))
    assert response.status_code == 200, response.text
    return sorted(row["host"] for row in response.json()["rows"])


@pytest.mark.usefixtures("field")
class TestFilterUnderEachColumn:
    async def test_thresholds_verdict_or_no_donor_at_all(
        self, client: AsyncClient, token: str
    ) -> None:
        """«До Ahrefs не дошёл» — донора нет: судья отрезал раньше порогов."""
        assert await _hosts(client, token, tab="rejected", thresholds="none") == ["cut.test"]
        assert await _hosts(client, token, tab="rejected", thresholds="unsuitable") == [
            "unsuitable.test"
        ]
        assert await _hosts(client, token, tab="review", thresholds="unchecked") == ["blank.test"]
        assert await _hosts(client, token, tab="rejected", thresholds="suitable") == [
            "asked.test",
            "brand.test",
            "declined.test",
            "vendor.test",
        ]

    async def test_judge_layer_as_its_badge(self, client: AsyncClient, token: str) -> None:
        """Слой — тот, что стоит значком в строке. Вердикт без отметки слоя
        ни под один слой не попадает: значка слоя у него нет и в строке."""
        assert await _hosts(client, token, tab="rejected", judge="rule") == [
            "brand.test",
            "cut.test",
        ]
        assert await _hosts(client, token, tab="accepted", judge="arbiter") == ["media.test"]
        assert await _hosts(client, token, tab="rejected", judge="model") == [
            "asked.test",
            "declined.test",
            "unsuitable.test",
        ]

    async def test_judge_did_not_look_means_no_verdict(
        self, client: AsyncClient, token: str
    ) -> None:
        assert await _hosts(client, token, tab="accepted", judge="none") == ["old.test"]
        assert await _hosts(client, token, tab="rejected", judge="none") == []

    async def test_donor_answer_any_none_or_which(self, client: AsyncClient, token: str) -> None:
        assert await _hosts(client, token, tab="accepted", answer="answered") == [
            "quiet.test",
            "sold.test",
        ]
        assert await _hosts(client, token, tab="accepted", answer="none") == [
            "media.test",
            "old.test",
        ]
        assert await _hosts(client, token, tab="accepted", answer="free") == ["quiet.test"]
        assert await _hosts(client, token, tab="rejected", answer="declines") == ["declined.test"]

    async def test_human_did_not_look_looked_or_disagreed(
        self, client: AsyncClient, token: str
    ) -> None:
        """«Смотрел» — любое решение; «разошёлся» — только против мнения
        судьи: «посмотри» — просьба, а не мнение."""
        assert await _hosts(client, token, tab="rejected", human="reviewed") == [
            "asked.test",
            "brand.test",
            "declined.test",
        ]
        assert await _hosts(client, token, tab="rejected", human="disagrees") == ["declined.test"]
        assert await _hosts(client, token, tab="rejected", human="unreviewed") == [
            "cut.test",
            "unsuitable.test",
            "vendor.test",
        ]

    async def test_filters_narrow_together(self, client: AsyncClient, token: str) -> None:
        assert await _hosts(
            client, token, tab="rejected", thresholds="suitable", judge="rule", human="reviewed"
        ) == ["brand.test"]
        assert await _hosts(client, token, tab="rejected", search="brand", judge="model") == []

    async def test_summary_is_the_whole_selection_not_the_filter(
        self, client: AsyncClient, token: str
    ) -> None:
        """Сводка отвечает «насколько верить машине», а не «что под фильтром»."""
        response = await client.get(
            "/api/selection",
            params={"tab": "rejected", "judge": "model", "human": "disagrees"},
            headers=bearer(token),
        )
        body = response.json()
        assert body["total"] == 1
        assert body["tabs"] == {"accepted": 4, "review": 2, "rejected": 6}
        assert body["reviewed"] == 3

    async def test_unknown_filter_value_is_refused_not_ignored(
        self, client: AsyncClient, token: str
    ) -> None:
        """Опечатка в фильтре не превращается в «показать всё»: список,
        который выглядит отфильтрованным и не отфильтрован, врёт."""
        response = await client.get(
            "/api/selection", params={"judge": "robot"}, headers=bearer(token)
        )
        assert response.status_code == 422


class TestPages:
    @pytest.fixture
    async def many(self, session: AsyncSession) -> None:
        """Двадцать пять принятых — на две страницы."""
        for number in range(25):
            await _domain(
                session,
                f"site{number:02d}.test",
                status=DonorStatus.SUITABLE,
                recommendation="accept",
                dr=90 - number,
            )
        await session.commit()

    @pytest.mark.usefixtures("many")
    async def test_twenty_on_a_page_and_the_server_names_the_size(
        self, client: AsyncClient, token: str
    ) -> None:
        first = (await client.get("/api/selection", headers=bearer(token))).json()
        second = (
            await client.get("/api/selection", params={"page": 2}, headers=bearer(token))
        ).json()

        assert (first["page"], first["limit"], first["total"]) == (1, 20, 25)
        assert len(first["rows"]) == 20
        assert (second["page"], len(second["rows"])) == (2, 5)
        hosts = [row["host"] for row in first["rows"] + second["rows"]]
        assert len(set(hosts)) == 25, "страницы не пересекаются и ничего не теряют"

    @pytest.mark.usefixtures("many")
    async def test_page_past_the_end_is_empty_with_the_true_total(
        self, client: AsyncClient, token: str
    ) -> None:
        """По настоящему `total` экран переходит на последнюю страницу,
        а не показывает «ничего не нашлось»."""
        body = (
            await client.get("/api/selection", params={"page": 9}, headers=bearer(token))
        ).json()
        assert body["rows"] == []
        assert body["total"] == 25

    async def test_page_number_out_of_range_is_refused(
        self, client: AsyncClient, token: str
    ) -> None:
        for page in (0, 10**12):
            response = await client.get(
                "/api/selection", params={"page": page}, headers=bearer(token)
            )
            assert response.status_code == 422, page
