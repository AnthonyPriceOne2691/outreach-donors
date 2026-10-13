"""Экран сторожа (`GET /api/watchdog`): провайдеры — после транзакции запроса и только «Обзору».

С #355 маршрут спрашивает и полоса тревог над вкладками «Продаж» — при открытии раздела и каждые
5 минут на каждой вкладке, — а опрос провайдеров идёт десятки секунд (таймауты Ahrefs 30 с,
выдачи 60 с). Ревью стыков R7, 7.4:

- тревоги базы считаются и фиксируются до опроса: соединение не висит «idle in transaction» на
  время чужих ответов;
- полоса этапа (`?stage=`) — тревоги своего этапа и без опроса: «провайдер не отвечает» —
  тревога общая, её место на «Обзоре»;
- ответ провайдеров годен минуту (`PROBE_TTL_SEC`): вкладок много, а провайдер один.
"""

from __future__ import annotations

import pytest
from backend.api.watchdog import routes
from backend.features.core.domain import MessageStatus, Stage, UserRole
from backend.features.ops.alarms import Alarm
from backend.features.ops.silence import PROVIDERS_ALARM
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_silence_stages import MakeUser, SignIn, _late, _letters, connected

__all__ = ["connected"]  # продажи подключены: тишина их доставки — тревога

DOWN = Alarm(code=PROVIDERS_ALARM, title="Провайдер не отвечает", detail="Не отвечает: Ahrefs")


@pytest.fixture
async def seller(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Вошедший с правом «Продажи» (оно у оператора в роли)."""
    await make_user("seller@watch-screen.example.test", role=UserRole.OPERATOR)
    return bearer(await sign_in("seller@watch-screen.example.test"))


async def _silent(session: AsyncSession) -> None:
    """Письма доноров и продаж ушли восемь часов назад, а платформа о них молчит: у каждого
    этапа — своя тревога «Платформа молчит о доставке»."""
    for stage in (Stage.DONORS, Stage.SALES):
        await _letters(
            session,
            stage,
            status=MessageStatus.SENT,
            sent_at=_late(8),
            provider_message_id=f"sg-{stage.value}",
        )
    await session.commit()


async def test_the_providers_are_asked_after_the_alarms_are_committed(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    client: AsyncClient,
    seller: dict[str, str],
) -> None:
    events: list[str] = []
    commit = session.commit

    async def committed() -> None:
        events.append("тревоги зафиксированы")
        await commit()

    async def probe() -> Alarm | None:
        events.append(f"провайдеры, транзакция открыта: {session.in_transaction()}")
        return None

    monkeypatch.setattr(session, "commit", committed)
    monkeypatch.setattr(routes, "probe_providers", probe)

    response = await client.get("/api/watchdog", headers=seller)

    assert response.status_code == 200, response.text
    assert events == ["тревоги зафиксированы", "провайдеры, транзакция открыта: False"]


@pytest.mark.usefixtures("connected")
async def test_the_stage_strip_shows_its_own_alarms_without_asking_the_providers(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    client: AsyncClient,
    seller: dict[str, str],
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    asked: list[str] = []

    async def probe() -> Alarm | None:
        asked.append("провайдеры")
        return DOWN

    monkeypatch.setattr(routes, "probe_providers", probe)
    await _silent(session)
    await make_user(
        "outsider@watch-screen.example.test", role=UserRole.OPERATOR, permissions={"sales": False}
    )
    outsider = bearer(await sign_in("outsider@watch-screen.example.test"))

    strip = (await client.get("/api/watchdog?stage=sales", headers=seller)).json()["alarms"]
    hidden = (await client.get("/api/watchdog?stage=sales", headers=outsider)).json()["alarms"]

    assert asked == [], "полоса этапа спросила провайдеров"
    assert [(one["code"], one["stage"]) for one in strip] == [("delivery-silence:sales", "sales")]
    assert hidden == [], "тревога продаж — только с правом «Продажи»"
    overview = (await client.get("/api/watchdog", headers=seller)).json()["alarms"]
    assert asked == ["провайдеры"]
    assert [one["code"] for one in overview] == [
        PROVIDERS_ALARM,
        "delivery-silence:donors",
        "delivery-silence:sales",
    ]


async def test_the_providers_answer_serves_the_screen_for_a_minute(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient, seller: dict[str, str]
) -> None:
    clock = [0.0]
    asked: list[float] = []

    async def probe() -> Alarm | None:
        asked.append(clock[0])
        return DOWN

    monkeypatch.setattr(routes, "probe_providers", probe)
    monkeypatch.setattr(routes, "PROBES", routes.Probes(clock=lambda: clock[0]))
    seen = []
    for moment in (0.0, routes.PROBE_TTL_SEC - 1, routes.PROBE_TTL_SEC):
        clock[0] = moment
        alarms = (await client.get("/api/watchdog", headers=seller)).json()["alarms"]
        seen.append([one["code"] for one in alarms])

    assert asked == [0.0, routes.PROBE_TTL_SEC], "провайдеров спросили чаще раза в минуту"
    assert seen == [[PROVIDERS_ALARM]] * 3, "прежний ответ экрану — тот же, что свежий"
