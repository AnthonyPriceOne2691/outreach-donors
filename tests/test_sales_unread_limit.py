"""Лимит первых писем продаж с опечаткой — автоотправка стоит не по делу, с тревогой и словами.

`OUTREACH_SALES_DAILY_LIMIT` не прочитался (`-5`, `10k`): конфиг не роняет процессы, а даёт
продажам ноль первых писем в сутки и громкую строку при старте (`config/_base.spared`, #363). Выбор
ящика называет это «писать некому», как набранный кап, и проход стоял бы «по делу» — без тревоги,
пока опечатку не найдут. Теперь это остановка не по делу (`Stop.UNREAD`): тревога через 15 минут
словами, как у неподключённых продаж, — и с пустой очередью: ноль писем в день сам не пройдёт;
строка экрана называет настройку и что в ней должно стоять. Прочитанный ноль — прежний «лимит
выбран»: так решил администратор.

База настоящая, почта — нулевой транспорт, тревога — подставная. Адреса выдуманы.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.core.models.outreach import MessageModel
from backend.features.ops.alarms import marked
from backend.features.sales import auto_lines, autosend, autosend_jobs, policy, queue
from backend.features.sales.auto_lines import AutoLine
from backend.features.sales.autosend import Pass, Stop
from backend.features.sales.autosend_jobs import OFFLINE, RESUMED, STALLED, Watch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_send import _transports

NOW = w.NOW
MINUTE = timedelta(minutes=1)
JANE = "jane@acme.example.test"
SETTING = "OUTREACH_SALES_DAILY_LIMIT"
WORDS = (
    "настройка лимита первых писем продаж не прочитана (OUTREACH_SALES_DAILY_LIMIT) — первых "
    "писем ноль, пока её не поправят в .env: нужно целое число не меньше нуля или пусто"
)


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    found = await w.world(session, monkeypatch)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    return found


@pytest.fixture
def unread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Опечатка в лимите продаж — как её оставляет конфиг: лимит ноль и имя настройки."""
    monkeypatch.setitem(outreach_cfg.DIRECTION_LIMITS, "sales", 0)
    monkeypatch.setattr(outreach_cfg, "UNREAD_LIMITS", frozenset({SETTING}))


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    said: list[str] = []

    async def alert(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(autosend_jobs, "send_alert", alert)
    return said


async def _queued(session: AsyncSession, world: w.World) -> MessageModel:
    await w.lead(session, world.hypothesis_id, JANE)
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=1)
    letter = await session.scalar(select(MessageModel))
    assert letter is not None
    return letter


def _on_test_base(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> list[Pass]:
    source = _transports()
    seen: list[Pass] = []

    async def work(now: datetime) -> Pass:
        found = await autosend.run(session, source, now=now)
        seen.append(found)
        return found

    monkeypatch.setattr(autosend_jobs, "_pass", work)
    return seen


def test_the_words_name_the_setting_and_what_it_takes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Слова — с именем настройки, которую не прочитали, и тем, что в ней должно стоять;
    прочитанная — слов нет."""
    monkeypatch.setattr(outreach_cfg, "UNREAD_LIMITS", frozenset())
    read = policy.unread_limit()
    monkeypatch.setattr(outreach_cfg, "UNREAD_LIMITS", frozenset({SETTING}))

    assert (read, policy.unread_limit()) == (None, WORDS)


@pytest.mark.usefixtures("unread")
async def test_an_unread_limit_stops_the_pass_off_duty_with_an_alarm_after_15_minutes(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    told: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Письмо в окне, ящик есть, а лимит не прочитан: проход стоит не по делу — через 15 минут
    одна тревога словами, журнал — громкой строкой один раз. Опечатку поправили (процесс
    перечитал настройки) — письмо уходит, «прошло»."""
    caplog.set_level(logging.INFO, logger="backend.features.sales.autosend_jobs")
    letter = await _queued(session, world)
    passes = _on_test_base(session, monkeypatch)
    watch = Watch()

    for minutes in (0, 5, 14):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    quiet = list(told)
    for minutes in (15, 16):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    alarmed = list(told)
    monkeypatch.setattr(outreach_cfg, "UNREAD_LIMITS", frozenset())
    monkeypatch.setitem(outreach_cfg.DIRECTION_LIMITS, "sales", None)
    await autosend_jobs.tick(now=NOW + MINUTE * 17, watch=watch)

    assert (passes[0].stop, passes[0].stopped, passes[0].sent) == (Stop.UNREAD, WORDS, {})
    assert quiet == []
    assert alarmed == [marked(f"{OFFLINE}. {WORDS}", STALLED)]
    assert told[1:] == [f"{RESUMED} (стояла: {WORDS})"]
    assert passes[-1].sent == {letter.id: w.SALES_BOX}
    [line] = [r for r in caplog.records if "автоотправка стоит" in r.getMessage()]
    assert (line.levelno, line.getMessage()) == (
        logging.WARNING,
        f"продажи: автоотправка стоит — {WORDS}",
    )


@pytest.mark.usefixtures("unread")
async def test_an_unread_limit_alarms_with_an_empty_queue_too(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch, told: list[str]
) -> None:
    """Очередь пуста — ноль писем в день всё равно сам не пройдёт: тревога, как у неподключённых
    продаж, и без писем в окне."""
    _on_test_base(session, monkeypatch)
    watch = Watch()

    for minutes in (0, 15):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)

    assert told == [marked(f"{OFFLINE}. {WORDS}", STALLED)]


@pytest.mark.usefixtures("unread")
async def test_the_screen_line_says_which_setting_to_fix_and_how(
    session: AsyncSession, world: w.World
) -> None:
    """Строка экрана «Очередь писем»: автоотправка включена, а не шлёт — и какую настройку как
    исправить; «письма уходят всем лидам очереди» тут врало бы."""
    await _queued(session, world)

    line = await auto_lines.autosend_line(session, NOW)

    assert line == AutoLine(enabled=True, line=f"{auto_lines.SEND_STOPPED}: {WORDS}.")


async def test_a_read_zero_limit_is_the_spent_limit_with_no_alarm(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch, told: list[str]
) -> None:
    """Граница: ноль прочитан — лимит выбран администратором, «писать некому» по делу: прежние
    слова выбора ящика, тревоги нет и через полчаса, письмо ждёт."""
    monkeypatch.setitem(outreach_cfg.DIRECTION_LIMITS, "sales", 0)
    monkeypatch.setattr(outreach_cfg, "UNREAD_LIMITS", frozenset())
    await _queued(session, world)
    passes = _on_test_base(session, monkeypatch)
    watch = Watch()

    for minutes in range(0, 35, 5):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)

    assert told == []
    assert {found.stop for found in passes} == {Stop.CAP}
    assert [found.sent for found in passes] == [{}] * len(passes)
    assert passes[0].stopped == (
        "Сегодня писать некому: у направления кончился дневной лимит (0 из 0 первых писем). "
        "Письмо остаётся в очереди"
    )
