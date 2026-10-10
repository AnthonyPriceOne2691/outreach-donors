"""Ревью стыков (A6, A8): сторож почты 4.5b, лента тревог и проход сторожа в процессе разбора.

Сторож — общий код (`ops/mail_watch.py`, `ops/alarm_feed.py`, `workers/reaper.py`). Что держится —
зелёные тесты с мутантом (пробелы ревью закрыты PR «общее»):

- ошибка базы у этапа — тревога «сторож этапа не досчитал» (своя точка сохранения): этапы после
  него, правила сторожа тишины (доноры) и `GET /api/watchdog` целы;
- сказанное лентой — хэшем в Redis: «прошло» о тревоге, кончившейся за перезапуском, приходит,
  действующая не повторяется; Redis не ответил — память процесса и строка журнала;
- дребезг: «тревога» — сразу, «прошло» — после двух проходов подряд без неё; мигающая тревога —
  одно сообщение;
- проход сторожа в reaper говорит с провайдерами и Telegram после закрытия своей сессии (не
  «idle in transaction» на время сетевых вызовов).

`_able` (A8) — правило фильтра отправки: пауза, выдержка и строка домена за другим
направлением — «писать некому» (PR «общее»: экран доменов и ящиков).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from backend.config import storage
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, SenderStatus, Stage, UserRole
from backend.features.core.models.outreach import SenderModel, SendingDomainModel
from backend.features.core.stages import MailPolicy
from backend.features.ops import alarm_feed, mail_watch, silence
from backend.features.ops.alarms import Alarm
from backend.features.outreach.repository import EVERY_STAGE
from backend.workers import reaper
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
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


# --- _able: выдержка и чужая строка домена — писать некому ------------------------------------


async def test_a_young_domain_leaves_nobody_to_send(session: AsyncSession) -> None:
    """Домен ящика на выдержке — писать нечем: тревога «отправить некому» (мутант «без
    выдержки» в `_able` убит; до ревью его держала только пауза домена)."""
    box = await _queued_with_box(session)
    session.add(
        SendingDomainModel(
            domain=box.domain, stage=Stage.SALES, daily_limit=5, young_until=NOW + timedelta(days=2)
        )
    )

    codes = [alarm.code for alarm in await mail_watch.alarms(session, NOW, stages=EVERY_STAGE)]

    assert codes == ["nobody-to-send:sales"]


async def test_a_domain_of_another_direction_leaves_nobody_to_send(session: AsyncSession) -> None:
    """Строка домена за другим направлением отсеивает ящик при отправке
    (`limits.DOMAIN_ELSEWHERE`) — и сторож считает его непишущим: правило одно."""
    box = await _queued_with_box(session)
    session.add(SendingDomainModel(domain=box.domain, stage=Stage.DONORS, daily_limit=5))

    codes = [alarm.code for alarm in await mail_watch.alarms(session, NOW, stages=EVERY_STAGE)]

    assert codes == ["nobody-to-send:sales"]


async def test_an_open_domain_of_its_direction_spent_for_today_is_no_alarm(
    session: AsyncSession,
) -> None:
    """Строка своего направления, выдержка прошла, лимит дня выбран — домен открыт: лимиты
    дня — не поломка, тревоги нет (мутант «любая строка закрывает домен» убит)."""
    box = await _queued_with_box(session)
    session.add(
        SendingDomainModel(
            domain=box.domain, stage=Stage.SALES, daily_limit=1, young_until=NOW - timedelta(days=1)
        )
    )
    await _letter(
        session, Stage.SALES, box, 1, status=MessageStatus.SENT, sent_at=NOW - timedelta(minutes=5)
    )

    assert await mail_watch.alarms(session, NOW, stages=EVERY_STAGE) == []


# --- _of_stage: ошибка базы — тревога, а не падение ------------------------------------------


def _break_the_quiet_check(monkeypatch: pytest.MonkeyPatch, only: Stage | None = None) -> None:
    """Запрос проверки «ящик молчит» падает в базе — как упавший запрос сторожа; `only` —
    только у ящиков этого этапа (остальные проверяются как есть)."""
    quiet = mail_watch._quiet

    async def broken(session: AsyncSession, box: SenderModel, now: datetime) -> Alarm | None:
        if only is not None and box.stage is not only:
            return await quiet(session, box, now)
        await session.execute(text("SELECT 1 FROM made_up_table_of_the_watch"))
        return None

    monkeypatch.setattr(mail_watch, "_quiet", broken)


async def test_a_base_error_of_one_stage_is_an_alarm_and_the_watch_goes_on(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _queued_with_box(session)
    _break_the_quiet_check(monkeypatch)

    found = await mail_watch.alarms(session, NOW, stages=EVERY_STAGE)

    assert [alarm.code.rpartition(":")[2] for alarm in found] == ["sales"]
    assert "made_up_table_of_the_watch" in found[0].detail
    assert await session.scalar(select(1)) == 1  # транзакция сторожа цела


async def test_the_stages_after_a_broken_one_are_still_watched(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сторож у всех этапов; запрос падает у доноров (их проверяют первыми) — у доноров
    тревога «не досчитал», продажи после них проверены как обычно."""

    async def everyone_watched(_session: AsyncSession, _stage: Stage, _what: str) -> MailPolicy:
        return MailPolicy(watch=True)

    monkeypatch.setattr(stages, "mail_policy", everyone_watched)
    await sender(session, "anna@mail-donors.example.test", Stage.DONORS)
    paused = await sender(session, BOX, Stage.SALES)
    paused.status = SenderStatus.PAUSED
    _break_the_quiet_check(monkeypatch, only=Stage.DONORS)

    codes = [alarm.code for alarm in await mail_watch.alarms(session, NOW, stages=EVERY_STAGE)]

    assert codes == ["watch-failed:donors", "all-paused:sales"]


async def test_a_broken_stage_keeps_the_donor_alarms(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сторож почты продаж упал в базе — правила сторожа тишины (доставка доноров) целы."""
    await _letter(
        session,
        Stage.DONORS,
        None,
        7,
        status=MessageStatus.SENT,
        sent_at=NOW - timedelta(hours=8),
        provider_message_id="sg-made-up-71",
    )
    await _queued_with_box(session)
    _break_the_quiet_check(monkeypatch)

    codes = {alarm.code for alarm in await silence.alarms(session, stages=EVERY_STAGE, now=NOW)}

    assert {"delivery-silence", "watch-failed:sales"} <= codes


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


class FakeRedis:
    """Redis сказанного: хэши в памяти теста — переживают «перезапуск» (новый `Feed()`);
    значение байтами отдаётся как есть; `down` — Redis не отвечает ни на чтение, ни на запись;
    `fails` — не ответит на столько ближайших обращений; `writes` — сколько было попыток записи."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str | bytes]] = {}
        self.down = False
        self.fails = 0
        self.writes = 0

    def __enter__(self) -> FakeRedis:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        self.answer()
        return {_raw(code): _raw(title) for code, title in self.hashes.get(key, {}).items()}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def answer(self) -> None:
        if self.down or self.fails > 0:
            self.fails = max(self.fails - 1, 0)
            raise RedisConnectionError("выдуманный Redis не отвечает")


class FakePipeline:
    """Транзакция Redis: команды копятся и применяются вместе на `execute`."""

    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.commands: list[Callable[[], object]] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def delete(self, key: str) -> None:
        self.commands.append(lambda: self.redis.hashes.pop(key, None))

    def hset(self, key: str, *, mapping: dict[str, str]) -> None:
        self.commands.append(lambda: self.redis.hashes.setdefault(key, {}).update(mapping))

    def execute(self) -> None:
        self.redis.writes += 1
        self.redis.answer()
        for command in self.commands:
            command()


def _raw(value: str | bytes) -> bytes:
    return value if isinstance(value, bytes) else value.encode()


#: Настоящая `connection` ленты: щит `tests/conftest.py` подменяет её на время каждого теста,
#: а модуль тестов собирается раньше.
REAL_CONNECTION = alarm_feed.connection


def test_the_feed_waits_for_redis_no_longer_than_its_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Клиент Redis ленты — с таймаутами: клиент синхронный, а сторож делит процесс с разбором
    прогонов — зависший Redis держал бы и его (замер на зависшем Redis — 5 с и строка журнала)."""
    asked: dict[str, object] = {}

    class Recorder:
        @staticmethod
        def from_url(url: str, **options: object) -> str:
            asked.update(url=url, **options)
            return "клиент"

    monkeypatch.setattr(alarm_feed, "Redis", Recorder)

    assert REAL_CONNECTION() == "клиент"
    assert asked == {
        "url": storage.REDIS_URL,
        "socket_connect_timeout": alarm_feed.REDIS_TIMEOUT_SEC,
        "socket_timeout": alarm_feed.REDIS_TIMEOUT_SEC,
    }


@pytest.fixture
def told_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Redis сказанного — подделкой (вместо щита `tests/conftest.py`)."""
    redis = FakeRedis()
    monkeypatch.setattr(alarm_feed, "connection", lambda: redis)
    return redis


async def test_the_end_of_an_alarm_told_before_a_restart_is_still_told(
    telegram: list[str], told_redis: FakeRedis
) -> None:
    """Выкатка между тревогой и её концом: новый процесс знает, что о тревоге сказано
    (хранилище сказанного — Redis; тест получает его подделку). «Прошло» — как всегда, после
    двух проходов без тревоги (дребезг)."""
    await alarm_feed.Feed().tell([QUIET])

    restarted = alarm_feed.Feed()  # процесс перезапущен, тревоги больше нет
    for _ in range(alarm_feed.QUIET_PASSES):
        await restarted.tell([])

    assert telegram == ["тревога: Ящик x молчит. ждут его", "прошло: Ящик x молчит"]
    assert told_redis.hashes == {}


async def test_an_alarm_told_before_a_restart_is_not_told_again(
    telegram: list[str], told_redis: FakeRedis
) -> None:
    await alarm_feed.Feed().tell([QUIET])

    await alarm_feed.Feed().tell([QUIET])  # процесс перезапущен, тревога держится

    assert telegram == ["тревога: Ящик x молчит. ждут его"]
    assert told_redis.hashes == {alarm_feed.told_key(): {QUIET.code: QUIET.title}}


async def test_without_redis_the_feed_remembers_its_own_process(
    telegram: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    """Redis не отвечает (щит `tests/conftest.py`) — строка журнала и память процесса."""
    feed = alarm_feed.Feed()

    with caplog.at_level(logging.WARNING, logger=alarm_feed.__name__):
        for state in ([QUIET], [QUIET], [], [], []):
            await feed.tell(state)

    assert telegram == ["тревога: Ящик x молчит. ждут его", "прошло: Ящик x молчит"]
    assert "сказанное из Redis не прочитано" in caplog.text


async def test_what_was_said_while_redis_blinked_reaches_it_next_pass(
    telegram: list[str], told_redis: FakeRedis, caplog: pytest.LogCaptureFixture
) -> None:
    """Redis лёг на проходе с тревогой — запись не удалась (строка журнала), следующий
    проход дописывает тревогу в Redis, и процесс после перезапуска знает о ней."""
    feed = alarm_feed.Feed()
    await feed.tell([])  # сказанное прежним процессом прочитано: пусто
    told_redis.down = True
    with caplog.at_level(logging.WARNING, logger=alarm_feed.__name__):
        await feed.tell([QUIET])
    told_redis.down = False
    await feed.tell([QUIET])

    restarted = alarm_feed.Feed()
    for _ in range(2):
        await restarted.tell([])  # тревоги больше нет

    assert telegram == ["тревога: Ящик x молчит. ждут его", "прошло: Ящик x молчит"]
    assert "сказанное в Redis не записано" in caplog.text


async def test_what_the_previous_process_said_is_read_once_redis_answers(
    telegram: list[str], told_redis: FakeRedis
) -> None:
    """Новый процесс: Redis не ответил на чтение в начале прохода — сказанное прежним
    читается следующим проходом и до того не затирается записью своего."""
    old = Alarm(code="quiet-box:y", title="Ящик y молчит", detail="ждут и его")
    told_redis.hashes[alarm_feed.told_key()] = {old.code: old.title}
    told_redis.fails = 1
    feed = alarm_feed.Feed()

    for _ in range(3):
        await feed.tell([QUIET])

    assert telegram == ["тревога: Ящик x молчит. ждут его", "прошло: Ящик y молчит"]
    assert told_redis.hashes == {alarm_feed.told_key(): {QUIET.code: QUIET.title}}


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


async def test_the_watch_talks_to_the_network_outside_its_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    opened: list[AsyncSession] = []
    seen: list[tuple[str, bool]] = []

    async def alarms(session: AsyncSession, *, stages: object, now: object = None) -> list[Alarm]:
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
    # Правка может взять опрос в reaper по имени — и там он подменён: живых провайдеров нет.
    monkeypatch.setattr(reaper, "probe_providers", probe, raising=False)
    monkeypatch.setattr(reaper.FEED, "tell", tell)

    async with committed_sessions():
        await reaper.watch()

    assert seen == [("провайдеры", False), ("Telegram", False)]
