"""«Продаёт размещение» — один раз в строке, в колонке своего источника.

Замечание аудита 25.09.2026: строка очереди печатала это трижды — значком
у домена, пояснением «судья: продаёт размещение у себя» рядом и ярлыком
судьи. Сервер теперь называет, чей голос сказал «продаёт», и экран кладёт
признак туда, где этот голос и так виден. Здесь проверяется, что источник
назван верно для каждой ветки порядка силы — ответ сайта, человек, судья,
дверь — и что источник не расходится с пояснением.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.review.candidates import Decision, RunReview
from backend.features.review.ordering import SellsBy
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_review import _judged, _run

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


async def _page(session: AsyncSession, *hosts: str) -> dict[str, tuple[str | None, SellsBy | None]]:
    run = await _run(session)
    review = RunReview(session)
    await review.queue_run(run.id, list(hosts))
    page = await review.page(run.id, status=Decision.PENDING, show_doubtful=True)
    return {row.domain.host: (row.sells, row.sells_by) for row in page.rows}


class TestWhoSaidItSells:
    async def test_each_voice_is_named(self, session: AsyncSession) -> None:
        answered = await _judged(session, "answered.test", "accept")
        answered.seller_answer = "sells"
        free = await _judged(session, "free.test", "accept")
        free.seller_answer = "free"
        human = await _judged(session, "human.test", "accept")
        human.human_intent = "sells_placement"
        await _judged(session, "judged.test", "accept", intent="sells_placement")
        door = await _judged(session, "door.test", "accept")
        door.site_door = "страница «write-for-us»"
        await _judged(session, "plain.test", "accept")
        await session.flush()

        rows = await _page(
            session,
            "answered.test",
            "free.test",
            "human.test",
            "judged.test",
            "door.test",
            "plain.test",
        )

        assert rows["answered.test"] == ("сам сказал: продаёт размещение", SellsBy.ANSWER)
        assert rows["free.test"] == ("сам сказал: берёт статьи бесплатно", SellsBy.ANSWER)
        assert rows["human.test"] == ("человек: продаёт размещение у себя", SellsBy.HUMAN)
        assert rows["judged.test"] == ("судья: продаёт размещение у себя", SellsBy.JUDGE)
        assert rows["door.test"] == ("страница «write-for-us»", SellsBy.DOOR)
        assert rows["plain.test"] == (None, None)

    async def test_no_from_the_site_silences_both(self, session: AsyncSession) -> None:
        """Ответ «не продаём» гасит и пояснение, и источник — вместе."""
        refused = await _judged(session, "refused.test", "accept", intent="sells_placement")
        refused.site_door = "меню главной: «Advertise»"
        refused.seller_answer = "declines"
        await session.flush()

        assert (await _page(session, "refused.test"))["refused.test"] == (None, None)

    async def test_screen_gets_the_source(
        self,
        client: AsyncClient,
        session: AsyncSession,
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        await make_user("оператор@site.com", role=UserRole.OPERATOR)
        token = await sign_in("оператор@site.com")
        await _judged(session, "judged.test", "accept", intent="sells_placement")
        run = await _run(session)
        await RunReview(session).queue_run(run.id, ["judged.test"])
        await session.commit()

        response = await client.get(f"/api/review/runs/{run.id}", headers=bearer(token))

        assert response.status_code == 200, response.text
        row = response.json()["rows"][0]
        assert row["sells_by"] == "judge"
