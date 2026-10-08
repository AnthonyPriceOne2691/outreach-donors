"""Ревью стыков (A6, A8): сторож почты 4.5b, лента тревог и проход сторожа в процессе разбора.

Сторож — общий код (`ops/mail_watch.py`, `ops/alarm_feed.py`, `workers/reaper.py`). Что держится —
зелёные тесты с мутантом; что не так — `xfail(strict=True)` с причиной и правкой для PR
«общее» (сами правки здесь не делаются):

- `_of_stage`: ошибка базы роняет весь проход сторожа и `GET /api/watchdog` (500);
- `Feed.told` живёт в памяти процесса: «прошло» о тревоге, кончившейся за перезапуском, не
  приходит никогда;
- дребезг: тревога, которая то есть, то нет, — сообщение на каждом проходе;
- проход сторожа говорит с провайдерами и Telegram при открытой транзакции («idle in
  transaction» на время сетевых вызовов);
- `_able`: строка домена за другим направлением — «писать некому», а тревоги нет.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from backend.config import storage
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage, UserRole
from backend.features.core.models.outreach import SenderModel, SendingDomainModel
from backend.features.core.stages import MailPolicy
from backend.features.ops import alarm_feed, mail_watch, silence
from backend.features.ops.alarms import Alarm
from backend.workers import reaper
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN, bearer
from tests.test_mail_watch import NOW, _letter
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import sender
from tests.test_send_race import committed_sessions

QUIET = Alarm(code="quiet-box:x", title="Ящик x молчит", detail="ждут его")
BOX = "hello@mail-sales.example.test"


@pytest.fixture(autouse=True)
def watched(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Продажи со сторожем в политике — подставным модулем, как в `test_mail_watch.py`."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(watch=True))
    stages.register_sales(lambda: found)
    return found


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Что лента сказала в Telegram — по порядку; Telegram принимает всё."""
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(alarm_feed, "send_alert", send)
    return said


async def _queued_with_box(session: AsyncSession) -> SenderModel:
    """Пишущий ящик продаж и первое письмо продаж в очереди."""
    box = await sender(session, BOX, Stage.SALES)
    await _letter(session, Stage.SALES, None, status=MessageStatus.QUEUED)
    return box


# --- _able: выдержка держится, чужая строка домена — нет ---------------------------------------


async def test_a_young_domain_leaves_nobody_to_send(session: AsyncSession) -> None:
    """Домен ящика на выдержке — писать нечем: тревога «отправить некому» (мутант «без
    выдержки» в `_able` убит; до ревью его держала только пауза домена)."""
    box = await _queued_with_box(session)
    session.add(
        SendingDomainModel(
            domain=box.domain, stage=Stage.SALES, daily_limit=5, young_until=NOW + timedelta(days=2)
        )
    )

    codes = [alarm.code for alarm in await mail_watch.alarms(session, NOW)]

    assert codes == ["nobody-to-send:sales"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код ops/mail_watch._able: строка `sending_domains` за другим направлением "
        "(`limits.DOMAIN_ELSEWHERE`) отсеивает ящик при отправке, а сторож считает его "
        "пишущим; правка — PR «общее» (ревью стыков R1, A8)"
    ),
)
async def test_a_domain_of_another_direction_leaves_nobody_to_send(session: AsyncSession) -> None:
    box = await _queued_with_box(session)
    session.add(SendingDomainModel(domain=box.domain, stage=Stage.DONORS, daily_limit=5))

    codes = [alarm.code for alarm in await mail_watch.alarms(session, NOW)]

    assert codes == ["nobody-to-send:sales"]


# --- _of_stage: ошибка базы — тревога, а не падение ------------------------------------------


def _break_the_quiet_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Запрос проверки «ящик молчит» падает в базе — как упавший запрос сторожа."""

    async def broken(session: AsyncSession, _box: SenderModel, _now: object) -> Alarm | None:
        await session.execute(text("SELECT 1 FROM made_up_table_of_the_watch"))
        return None

    monkeypatch.setattr(mail_watch, "_quiet", broken)


_WATCH_FALLS = (
    "общий код ops/mail_watch.alarms: ошибка базы в `_of_stage` не поймана и без точки "
    "сохранения — падает весь проход сторожа (Telegram молчит обо всём) и GET /api/watchdog "
    "отвечает 500; правка — PR «общее» (ревью стыков R1, A6)"
)


@pytest.mark.xfail(strict=True, reason=_WATCH_FALLS)
async def test_a_base_error_of_one_stage_is_an_alarm_and_the_watch_goes_on(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _queued_with_box(session)
    _break_the_quiet_check(monkeypatch)

    found = await mail_watch.alarms(session, NOW)

    assert [alarm.code.rpartition(":")[2] for alarm in found] == ["sales"]
    assert "made_up_table_of_the_watch" in found[0].detail
    assert await session.scalar(select(1)) == 1  # транзакция сторожа цела


@pytest.mark.xfail(strict=True, reason=_WATCH_FALLS)
async def test_the_watchdog_screen_survives_a_base_error_of_the_mail_watch(
    session: AsyncSession,
    client: AsyncClient,
    admin_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _queued_with_box(session)
    await session.commit()
    _break_the_quiet_check(monkeypatch)

    async def nobody_down() -> Alarm | None:
        return None

    monkeypatch.setattr("backend.api.watchdog.routes.probe_providers", nobody_down)

    response = await client.get("/api/watchdog", headers=bearer(admin_token))

    assert response.status_code == 200, response.text
    assert any("made_up_table_of_the_watch" in a["detail"] for a in response.json()["alarms"])


@pytest.fixture
async def admin_token(
    make_user: Callable[..., Awaitable[Any]], sign_in: Callable[..., Awaitable[str]]
) -> str:
    await make_user("admin@watch-seams.example.test", role=UserRole.ADMIN)
    return await sign_in("admin@watch-seams.example.test")


# --- лента тревог: перезапуск и дребезг ---------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код ops/alarm_feed.Feed: сказанное помнит процесс — тревога, кончившаяся за "
        "перезапуском reaper, остаётся в Telegram без «прошло»; правка — PR «общее» (ревью "
        "стыков R1, A6)"
    ),
)
async def test_the_end_of_an_alarm_told_before_a_restart_is_still_told(
    telegram: list[str],
) -> None:
    """Выкатка между тревогой и её концом: новый процесс знает, что о тревоге сказано
    (хранилище сказанного — выбор правки; внешнее — тест получит его подделку)."""
    await alarm_feed.Feed().tell([QUIET])

    await alarm_feed.Feed().tell([])  # процесс перезапущен, тревоги больше нет

    assert telegram == ["тревога: Ящик x молчит. ждут его", "прошло: Ящик x молчит"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код ops/alarm_feed.Feed.tell: нет гистерезиса — тревога, которая то есть, то "
        "нет, даёт сообщение на каждом проходе; правка — PR «общее» (ревью стыков R1, A6)"
    ),
)
async def test_a_flapping_alarm_is_told_once_and_its_end_once_it_holds(
    telegram: list[str],
) -> None:
    """Тревога мигает через проход (ящик на часовом потолке добивок: ушло письмо — 15 минут
    тихо): одно «тревога»; кончилась на деле (несколько проходов подряд) — одно «прошло»."""
    feed = alarm_feed.Feed()

    for state in ([QUIET], [], [QUIET], [], [QUIET]):
        await feed.tell(state)
    flapping = list(telegram)
    for _ in range(3):
        await feed.tell([])

    assert flapping == ["тревога: Ящик x молчит. ждут его"]
    assert telegram == ["тревога: Ящик x молчит. ждут его", "прошло: Ящик x молчит"]


# --- проход сторожа в процессе разбора: сеть — вне транзакции ---------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код workers/reaper.watch и ops/silence.report: опрос провайдеров и лента "
        "тревог идут внутри открытой сессии — соединение «idle in transaction» на время "
        "сетевых вызовов; правка — PR «общее» (ревью стыков R1, A6)"
    ),
)
async def test_the_watch_talks_to_the_network_outside_its_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    opened: list[AsyncSession] = []
    seen: list[tuple[str, bool]] = []

    async def alarms(session: AsyncSession, *, now: object = None) -> list[Alarm]:
        opened.append(session)
        await session.execute(text("SELECT 1"))
        return [QUIET]

    async def probe() -> Alarm | None:
        seen.append(("провайдеры", opened[0].in_transaction()))
        return None

    async def tell(_found: object) -> None:
        seen.append(("Telegram", opened[0].in_transaction()))

    monkeypatch.setattr(silence, "alarms", alarms)
    monkeypatch.setattr(silence, "probe_providers", probe)
    monkeypatch.setattr(reaper.FEED, "tell", tell)

    async with committed_sessions():
        await reaper.watch()

    assert seen == [("провайдеры", False), ("Telegram", False)]
