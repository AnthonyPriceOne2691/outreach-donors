"""Лента тревог сторожа (`ops/alarm_feed.py`): сказанное — по каналу, чужое и зависшее в Redis
(ревью #234).

- Сказанное строкой журнала — не сказанное в Telegram. Бот задан позже или сменён чат, процесс
  сторожа перезапущен — у нового канала свой пустой хэш сказанного, и действующая тревога
  приходит туда один раз, а не молчит, пока не кончится и не вернётся.
- Значение в хэше не UTF-8 (чужая запись) — проход сторожа не падает.
- Запись в Redis не удалась — до конца прохода лента Redis не ждёт; дописывает следующий проход.
"""

from __future__ import annotations

import pytest
from backend.config import alerts as alerts_cfg
from backend.features.ops import alarm_feed
from backend.features.ops.alarms import Alarm
from tests.test_mail_watch_seams import QUIET, FakeRedis

TOLD = "тревога: Ящик x молчит. ждут его"


@pytest.fixture
def told_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Redis сказанного — подделкой (вместо щита `tests/conftest.py`)."""
    redis = FakeRedis()
    monkeypatch.setattr(alarm_feed, "connection", lambda: redis)
    return redis


@pytest.fixture
def channels(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Куда ушли слова ленты: («журнал» или чат Telegram, текст). Как настоящий `send_alert`:
    бот не задан — строка журнала и `False`; задан — Telegram принял."""
    said: list[tuple[str, str]] = []

    async def send(text: str) -> bool:
        if alerts_cfg.TELEGRAM_BOT_TOKEN and alerts_cfg.TELEGRAM_CHAT_ID:
            said.append((alerts_cfg.TELEGRAM_CHAT_ID, text))
            return True
        said.append(("журнал", text))
        return False

    monkeypatch.setattr(alarm_feed, "send_alert", send)
    return said


def _bot(monkeypatch: pytest.MonkeyPatch, chat: str) -> None:
    """Владелец задал бота (или сменил чат); процесс сторожа перезапускается после этого."""
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "made-up-token")
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_CHAT_ID", chat)


async def test_a_bot_set_up_later_gets_the_active_alarm_once(
    channels: list[tuple[str, str]], told_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    await alarm_feed.Feed().tell([QUIET])  # бот не задан — строка журнала
    _bot(monkeypatch, "made-up-chat-1")

    for _ in range(2):  # перезапуск с ботом, затем ещё один
        await alarm_feed.Feed().tell([QUIET])

    assert channels == [("журнал", TOLD), ("made-up-chat-1", TOLD)]


async def test_a_new_chat_gets_the_active_alarm_once(
    channels: list[tuple[str, str]], told_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bot(monkeypatch, "made-up-chat-1")
    await alarm_feed.Feed().tell([QUIET])
    _bot(monkeypatch, "made-up-chat-2")

    for _ in range(2):  # перезапуск с новым чатом, затем ещё один
        await alarm_feed.Feed().tell([QUIET])

    assert channels == [("made-up-chat-1", TOLD), ("made-up-chat-2", TOLD)]


async def test_a_foreign_value_in_the_hash_does_not_stop_the_watch(
    channels: list[tuple[str, str]], told_redis: FakeRedis
) -> None:
    """Заголовок в хэше не UTF-8 — знаки с заменой, а не `UnicodeDecodeError` (это не
    `RedisError`, и он ронял бы каждый проход сторожа вместе с тревогами доноров)."""
    told_redis.hashes[alarm_feed.told_key()] = {QUIET.code: b"\xd0B\xff"}
    feed = alarm_feed.Feed()

    for _ in range(alarm_feed.QUIET_PASSES):
        await feed.tell([])

    assert channels == [("журнал", "прошло: \ufffdB\ufffd")]


async def test_after_a_failed_write_the_pass_does_not_wait_for_redis_again(
    channels: list[tuple[str, str]], told_redis: FakeRedis
) -> None:
    """Redis завис после чтения: первая запись прохода ждёт таймаута и не удаётся — остальные
    слова того же прохода Redis не ждут (каждое ждало бы ещё 5 с); дописывает следующий проход."""
    other = Alarm(code="quiet-box:y", title="Ящик y молчит", detail="ждут и его")
    feed = alarm_feed.Feed()
    await feed.tell([])  # сказанное прежним процессом прочитано: пусто
    told_redis.down = True
    await feed.tell([QUIET, other])
    tries = told_redis.writes
    told_redis.down = False
    await feed.tell([QUIET, other])

    assert tries == 1
    assert told_redis.hashes == {
        alarm_feed.told_key(): {QUIET.code: QUIET.title, other.code: other.title}
    }
