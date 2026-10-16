"""Ключ Ahrefs: снимок ответа, цикл от обнуления и сторож доли капа (решение владельца 16.10.2026).

«Сервис должен владеть текущей ситуацией по ключу, а не данными, которые зашиты жёстко».
Проверяется: ответ Ahrefs разбирается честно (null лимита — не ноль, дата — и днём, и моментом);
снимок один, старший ответ новый не затирает, и запись не ждёт чужой транзакции; цикл — от
обнуления ключа, с обрезкой до конца короткого месяца и шагом от дня самой даты; сторож говорит
80 % — предупреждением, 95 % — тревогой, весь — «кап выбран», уровень держит код тревоги, а не
слова; ответ опроса сторожа ложится снимком — проходом сторожа и экраном сторожа; прежние
`AHREFS_*` в `.env` не роняют старт. Числа — выдуманные.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from backend.config import ahrefs as ahrefs_cfg
from backend.config import storage
from backend.features.ahrefs.units import Quota
from backend.features.core import usage
from backend.features.core.domain import Stage
from backend.features.core.models.ops import AhrefsCapModel
from backend.features.ops import alarm_feed, key_watch, silence
from backend.features.ops.alarm_feed import Feed
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs.ahrefs_key import Cycle, cycle_of, day_words, remember, snapshot
from backend.workers import reaper
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN, bearer
from tests.test_ahrefs_cap import KEY, NBSP, WORKSPACE, known_key
from tests.test_send_race import committed_sessions

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
RESET = datetime(2026, 11, 21, tzinfo=UTC)

PAYLOAD: dict[str, object] = {
    "units_limit_workspace": WORKSPACE,
    "units_usage_workspace": 120_000,
    "units_limit_api_key": KEY,
    "units_usage_api_key": 12_000,
    "usage_reset_date": "2026-11-21",
}


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def _soon() -> datetime:
    """Обнуление через десять дней от настоящей минуты: расход в тесте пишется настоящим
    временем, и цикл должен его содержать в любой день прогона набора."""
    return (datetime.now(UTC) + timedelta(days=10)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


class TestTheAnswer:
    """Ответ `subscription-info/limits-and-usage`: что nullable — по документации Ahrefs API v3."""

    def test_a_key_without_its_own_limit_is_the_workspace_not_zero(self) -> None:
        quota = Quota.from_payload({**PAYLOAD, "units_limit_api_key": None})

        assert (quota.key_limit, quota.size, quota.key_left) == (None, WORKSPACE, None)
        assert quota.available == WORKSPACE - 120_000

    def test_both_limits_named_the_smaller_remainder(self) -> None:
        quota = Quota.from_payload(PAYLOAD)

        assert (quota.size, quota.key_left, quota.workspace_left) == (
            KEY,
            KEY - 12_000,
            WORKSPACE - 120_000,
        )
        assert quota.available == KEY - 12_000

    def test_no_limit_named_is_an_unknown_size_and_nothing_to_spend(self) -> None:
        quota = Quota.from_payload({"units_usage_api_key": 5})

        assert (quota.size, quota.available) == (None, 0)

    def test_a_named_limit_without_its_usage_is_not_spendable(self) -> None:
        """Лимит пространства назван, а расход нет: остаток неизвестен — ноль, а не весь лимит."""
        quota = Quota.from_payload({**PAYLOAD, "units_usage_workspace": None})

        assert (quota.workspace_left, quota.available) == (0, 0)

    @pytest.mark.parametrize("junk", ["50000", True, [1]])
    def test_not_a_number_is_not_named_and_said(
        self, junk: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            quota = Quota.from_payload({**PAYLOAD, "units_limit_api_key": junk})

        assert quota.key_limit is None
        assert "units_limit_api_key — не число" in caplog.text

    @pytest.mark.parametrize(
        ("raw", "moment"),
        [
            ("2026-11-21", _utc(2026, 11, 21)),
            ("2026-11-21T00:00:00Z", _utc(2026, 11, 21)),
            ("2026-11-21T03:00:00+03:00", _utc(2026, 11, 21)),
            (None, None),
            ("", None),
        ],
    )
    def test_the_reset_date_as_a_day_or_a_moment(
        self, raw: str | None, moment: datetime | None
    ) -> None:
        assert Quota.from_payload({**PAYLOAD, "usage_reset_date": raw}).reset_at == moment

    def test_an_unreadable_reset_date_is_said(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            quota = Quota.from_payload({**PAYLOAD, "usage_reset_date": "21 ноября"})

        assert (quota.reset_date, quota.reset_at) == ("21 ноября", None)
        assert "не разобрана — цикл капа календарный" in caplog.text


class TestTheSnapshot:
    async def test_one_row_and_the_newer_answer_wins(self, session: AsyncSession) -> None:
        assert await snapshot(session) is None
        await known_key(session, key_used=1_000, asked_at=NOW - timedelta(minutes=10))
        await known_key(session, key_used=2_000, asked_at=NOW)

        found = await snapshot(session)

        assert found is not None
        assert (found.quota.key_used, found.asked_at) == (2_000, NOW)

    async def test_an_older_answer_does_not_bring_back_the_past(
        self, session: AsyncSession
    ) -> None:
        """Два опроса разом пишут каждый в своём порядке: ответ, услышанный раньше, но
        положенный позже, строку не трогает."""
        await known_key(session, key_used=2_000, asked_at=NOW)
        await known_key(session, key_used=1_000, asked_at=NOW - timedelta(minutes=10))

        found = await snapshot(session)

        assert found is not None
        assert (found.quota.key_used, found.asked_at) == (2_000, NOW)

    async def test_the_reset_and_nulls_survive_the_base(self, session: AsyncSession) -> None:
        await known_key(session, key_limit=None, workspace_used=7, reset_at=RESET)

        found = await snapshot(session)

        assert found is not None
        assert (found.quota.key_limit, found.quota.workspace_used, found.quota.reset_at) == (
            None,
            7,
            RESET,
        )
        assert found.quota.size == WORKSPACE

    async def test_the_write_does_not_wait_for_another_transaction(self) -> None:
        """Снимок пишут и долгие транзакции (пересчёт кандидатов Этапа 2 ждёт DR у Ahrefs):
        вторая запись не ждёт первую, а пропускает свой ответ — и сторож не молчит вместе
        с ними. На настоящих фиксациях: в одной транзакции замок взял бы сам себя."""
        quota = Quota.from_payload(PAYLOAD)
        async with committed_sessions() as factory:
            async with factory() as first, factory() as second:
                assert await remember(first, quota, at=NOW)
                kept = await asyncio.wait_for(remember(second, quota, at=NOW), timeout=5)
                await first.commit()
                await second.commit()
            async with factory() as reading:
                found = await snapshot(reading)

        assert kept is False
        assert found is not None
        assert found.quota.key_used == 12_000


class TestTheCycle:
    @pytest.mark.parametrize(
        ("reset", "now", "start", "following"),
        [
            # «Сейчас» раньше даты обнуления — дата минус месяц, тем же днём.
            (_utc(2026, 11, 21), _utc(2026, 10, 23, 12), _utc(2026, 10, 21), _utc(2026, 11, 21)),
            # Короткий месяц — его последний день: 31-е → 30-е, 28-е, 29-е.
            (_utc(2026, 12, 31), _utc(2026, 12, 15), _utc(2026, 11, 30), _utc(2026, 12, 31)),
            (_utc(2027, 3, 31), _utc(2027, 3, 10), _utc(2027, 2, 28), _utc(2027, 3, 31)),
            (_utc(2028, 3, 31), _utc(2028, 3, 10), _utc(2028, 2, 29), _utc(2028, 3, 31)),
            # Снимок устарел, дата прошла — вперёд по месяцу; шаг — от дня самой даты.
            (_utc(2026, 10, 21), _utc(2026, 12, 25), _utc(2026, 12, 21), _utc(2027, 1, 21)),
            (_utc(2027, 1, 31), _utc(2027, 3, 5), _utc(2027, 2, 28), _utc(2027, 3, 31)),
            # Ровно в минуту обнуления — уже новый цикл.
            (_utc(2026, 11, 21), _utc(2026, 11, 21), _utc(2026, 11, 21), _utc(2026, 12, 21)),
            # Через новый год назад и вперёд.
            (_utc(2027, 1, 10), _utc(2026, 12, 20), _utc(2026, 12, 10), _utc(2027, 1, 10)),
        ],
    )
    def test_from_the_last_reset_not_later_than_now(
        self, reset: datetime, now: datetime, start: datetime, following: datetime
    ) -> None:
        assert cycle_of(reset, now) == Cycle(start=start, next_reset=following, by_key=True)

    def test_without_a_date_the_calendar_month(self) -> None:
        assert cycle_of(None, _utc(2026, 12, 20, 9)) == Cycle(
            start=_utc(2026, 12, 1), next_reset=_utc(2027, 1, 1), by_key=False
        )


async def _spent(session: AsyncSession, units: int) -> None:
    usage.record(session, operation="batch_metrics", units=units, stage=Stage.DONORS)
    await session.flush()


class TestTheWatch:
    """80 % — предупреждение, 95 % — тревога, весь — «кап выбран»; одна тревога за проход."""

    async def _found(self, session: AsyncSession) -> dict[str, Any]:
        return {alarm.code: alarm for alarm in await silence.alarms(session, stages=EVERY_STAGE)}

    async def test_below_eighty_is_quiet(self, session: AsyncSession) -> None:
        await known_key(session, key_used=39_999, reset_at=_soon())
        await _spent(session, 39_999)

        codes = set(await self._found(session))

        assert not codes & {key_watch.WARN_CODE, key_watch.ALARM_CODE, key_watch.REACHED_CODE}

    async def test_eighty_warns_with_what_is_left_of_what_and_when(
        self, session: AsyncSession
    ) -> None:
        reset = _soon()
        await known_key(session, key_used=40_000, reset_at=reset)
        await _spent(session, 40_000)

        found = await self._found(session)
        alarm = found[key_watch.WARN_CODE]

        assert key_watch.ALARM_CODE not in found
        assert alarm.title == "Кап Ahrefs выбран на 80% и больше"
        assert alarm.detail.startswith(
            f"Предупреждение: осталось 10{NBSP}000 юнитов из 50{NBSP}000 — выбрано 80%, "
            f"обнуление ключа {day_words(reset)}."
        )
        assert "поднять кап — на экране «Расход»" in alarm.detail

    async def test_ninety_five_by_the_neighbour_on_the_key(self, session: AsyncSession) -> None:
        """Упираемся в лимит ключа, а не в свой кап: соседняя система выбрала ключ — и слова
        об этом, и куда идти: лимит ключа поднимают в Ahrefs, а не наш кап."""
        await known_key(session, key_used=48_000, reset_at=_soon())
        await _spent(session, 5_000)

        alarm = (await self._found(session))[key_watch.ALARM_CODE]

        assert alarm.title == "Кап Ahrefs выбран на 95% и больше"
        assert alarm.detail.startswith(f"Тревога: осталось 2{NBSP}000 юнитов из 50{NBSP}000")
        assert f"в лимит ключа: на нём выбрано 48{NBSP}000 из 50{NBSP}000 вместе с соседней" in (
            alarm.detail
        )
        assert alarm.detail.endswith("поднять лимит ключа — в Ahrefs")

    async def test_all_of_it_is_the_cap_reached_until_the_reset(
        self, session: AsyncSession
    ) -> None:
        reset = _soon()
        await known_key(session, key_used=50_000, reset_at=reset)
        session.add(AhrefsCapModel(units_cap=30_000))
        await _spent(session, 30_000)

        found = await self._found(session)
        alarm = found[key_watch.REACHED_CODE]

        assert not {key_watch.WARN_CODE, key_watch.ALARM_CODE} & set(found)
        assert alarm.title == "Кап Ahrefs выбран"
        assert f"Кап 30{NBSP}000 юнитов, обнуление ключа {day_words(reset)}" in alarm.detail
        assert "до обнуления или пока кап не поднимут на экране «Расход»" in alarm.detail

    async def test_without_a_reset_date_the_calendar_is_named(self, session: AsyncSession) -> None:
        await known_key(session, key_used=0)
        await _spent(session, 45_000)

        alarm = (await self._found(session))[key_watch.WARN_CODE]

        assert "цикл по календарю — до 1 " in alarm.detail
        assert "Ahrefs не назвал даты обнуления" in alarm.detail

    async def test_unknown_key_is_not_this_alarm(self, session: AsyncSession) -> None:
        """Ahrefs не отвечал ни разу — о нём говорит «провайдер не отвечает», не эта тревога."""
        await _spent(session, 10**6)

        codes = set(await self._found(session))

        assert not codes & {key_watch.WARN_CODE, key_watch.ALARM_CODE, key_watch.REACHED_CODE}


async def test_the_feed_speaks_by_the_level_not_by_the_numbers(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Лента тревог решает по коду: числа в словах меняются каждый проход — сообщения нет;
    уровень сменился — сообщение сразу. Если бы уровень жил в словах, переход 80 → 95
    прошёл бы молча."""
    said: list[str] = []

    async def telegram(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(alarm_feed, "send_alert", telegram)
    feed = Feed("watchdog-test")
    await known_key(session, reset_at=_soon())

    # Наш расход за цикл: 80 %, 82 %, 84 % — тот же уровень, другие числа; 96 % — следующий.
    for more in (40_000, 1_000, 1_000, 6_000):
        await _spent(session, more)
        await feed.tell(await silence.alarms(session, stages=EVERY_STAGE))

    assert [text.split(".")[0] for text in said] == [
        "тревога: Кап Ahrefs выбран на 80% и больше",
        "тревога: Кап Ahrefs выбран на 95% и больше",
    ]


class _Answering:
    """Ahrefs, который отвечает об остатке, как настоящий клиент."""

    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload = PAYLOAD if payload is None else payload

    async def limits_and_usage(self) -> dict[str, object]:
        return self.payload

    async def aclose(self) -> None:
        return None


async def _no_serp() -> None:
    return None


class TestTheProbeKeepsTheKey:
    """Ответ опроса сторожа — в снимок ключа, тем, у кого сессия, и после сети."""

    async def test_heard_then_kept_once(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(silence, "AhrefsClient", _Answering)
        monkeypatch.setattr(silence, "_serp_silent", _no_serp)

        assert await silence.ask_providers() == []
        assert await key_watch.keep_heard(session)
        assert not await key_watch.keep_heard(session), "положенное второй раз не кладётся"

        found = await snapshot(session)
        assert found is not None
        assert (found.quota.size, found.quota.reset_at) == (KEY, RESET)

    async def test_an_answer_that_is_not_a_mapping_is_said_and_not_kept(
        self, session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            key_watch.heard(None)

        assert not await key_watch.keep_heard(session)
        assert "ответ Ahrefs об остатке — не словарь" in caplog.text

    async def test_the_watch_pass_keeps_it_after_the_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(storage, "DSN", TEST_DSN)
        monkeypatch.setattr(silence, "AhrefsClient", _Answering)
        monkeypatch.setattr(silence, "_serp_silent", _no_serp)
        monkeypatch.setattr(reaper, "PROVIDERS", reaper.Providers())
        told: list[object] = []

        async def tell(found: object, **_: object) -> None:
            told.append(found)

        monkeypatch.setattr(reaper.FEED, "tell", tell)
        async with committed_sessions() as factory:
            await reaper.watch()
            async with factory() as reading:
                found = await snapshot(reading)

        assert len(told) == 1
        assert found is not None
        assert found.quota.key_used == 12_000

    async def test_the_watchdog_screen_keeps_it_too(
        self,
        client: AsyncClient,
        session: AsyncSession,
        make_user: Any,
        sign_in: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(silence, "AhrefsClient", _Answering)
        monkeypatch.setattr(silence, "_serp_silent", _no_serp)
        await make_user("смотрит-ключ@team.example.test")
        headers = bearer(await sign_in("смотрит-ключ@team.example.test"))

        response = await client.get("/api/watchdog", headers=headers)

        assert response.status_code == 200, response.text
        found = await snapshot(session)
        assert found is not None
        assert found.quota.size == KEY


class TestTheOldSettings:
    """`AHREFS_MONTHLY_UNITS` и `AHREFS_UNITS_CAP` логику не задают: на проде они ещё в `.env`,
    и старт с ними не падает — настройки их не читают."""

    def test_left_in_the_env_file_and_the_environment_they_are_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = tmp_path / ".env"
        stale = (
            "AHREFS_MONTHLY_UNITS=10\nAHREFS_UNITS_CAP=not-a-number\n"  # pragma: allowlist secret
        )
        env.write_text(stale, encoding="utf-8")
        monkeypatch.setenv("AHREFS_UNITS_CAP", "also-not-a-number")

        settings = type(ahrefs_cfg._s)(_env_file=env)  # type: ignore[call-arg]

        assert not hasattr(settings, "units_cap")
        assert not hasattr(ahrefs_cfg, "UNITS_CAP")
        assert not hasattr(ahrefs_cfg, "MONTHLY_UNITS")
