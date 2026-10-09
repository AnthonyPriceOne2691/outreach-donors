"""Бот продаж без токена — одна сводная тревога, а не тревога на каждый черновик.

Решение владельца по ревью стыков (R3, C4): без токена бота продаж не уходит ни одно
сообщение о черновике, и тревога на каждый черновик заваливала бы чат эксплуатации. Теперь —
одна тревога в общей ленте (`ops/alarm_feed.Feed`, по смене состояния) с числом черновиков,
которые ждут человека без сообщения; «прошло» — когда токен задан, по правилу ленты: после
`QUIET_PASSES` проходов без тревоги. Лента — проходом продаж процесса разбора
(`handoff_jobs.retry_pass`).

Черновики — строками базы (их путь шва — `test_sales_draft_notify.py`); бот продаж —
настоящий поверх подставного Bot API; лента тревог говорит в список. Сети нет.
"""

from __future__ import annotations

import pytest
from backend.config import sales as cfg
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.core.domain import DraftStatus, Stage
from backend.features.core.models.agent import AgentDraftModel
from backend.features.ops import alarm_feed
from backend.features.ops.alarm_feed import Feed
from backend.features.sales import handoff_jobs
from backend.features.sales.agent import notify, parts
from backend.features.sales.models import NoticeStatus, SalesDraftNoticeModel
from backend.features.sales.telegram import SalesBot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_sales_draft_notify import GROUP, TOKEN, BotApi, ok
from tests.test_sales_handoff_rows import sales_dialog

ALARM = "тревога: Бот продаж без токена."


class _Closable:
    async def dispose(self) -> None:
        return None


@pytest.fixture
def said(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Что лента тревог сказала человеку — в список, а не в Telegram эксплуатации."""
    told: list[str] = []

    async def send(text: str) -> bool:
        told.append(text)
        return True

    monkeypatch.setattr(alarm_feed, "send_alert", send)
    return told


@pytest.fixture(autouse=True)
def group(monkeypatch: pytest.MonkeyPatch) -> None:
    """Группа продаж задана; токен бота — в каждом тесте свой."""
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_CHAT_ID", GROUP)
    monkeypatch.setattr(cfg, "APP_URL", "https://app.example.test")


async def _drafts(
    session: AsyncSession, count: int, status: DraftStatus = DraftStatus.DRAFTED, *, tag: str
) -> list[AgentDraftModel]:
    """Черновики агента продаж к ответам лидов — по одному на диалог (домены `tag-N`)."""
    settings = await AgentSettingsRepository(session).current(Stage.SALES)
    if settings is None:
        settings = await AgentSettingsRepository(session).save(
            Stage.SALES, parts.DEFAULTS, author="тест"
        )
    made = []
    for number in range(count):
        host = f"{tag}-{number}.example.test"
        dialog = await sales_dialog(session, host=host, email=f"ceo@{host}")
        draft = AgentDraftModel(
            reply_id=dialog.reply.id,
            settings_id=settings.id,
            status=status,
            body="Выдуманный черновик ответа.",
            model="fake-model",
            prompt_version="fake-version",
        )
        session.add(draft)
        await session.flush()
        await session.refresh(draft)
        made.append(draft)
    return made


# --- одна тревога с числом -----------------------------------------------------------------


async def test_five_drafts_without_a_token_raise_one_alarm_with_their_number(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    """Пять черновиков ждут без сообщения: у каждого «не доставлено», своих тревог нет, а в
    ленте — одна тревога с числом 5; второй проход молчит. Черновик, о котором сообщено, и
    решённый человеком в число не входят."""
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", "")
    [announced] = await _drafts(session, 1, tag="told")
    session.add(
        SalesDraftNoticeModel(
            draft_id=announced.id,
            written_at=announced.updated_at,
            status=NoticeStatus.SENT.value,
            text="сообщено, пока токен был",
        )
    )
    await _drafts(session, 1, DraftStatus.SENT, tag="decided")
    waiting = await _drafts(session, 5, tag="waiting")
    alerts: list[str] = []

    async def alert(text: str) -> bool:
        alerts.append(text)
        return True

    api = BotApi(ok())
    async with api.client() as http:
        for draft in waiting:
            noticed = await notify.notify(session, draft.id, SalesBot(http), alert=alert)
            assert noticed.status is NoticeStatus.UNDELIVERED
    feed = Feed()

    await notify.watch_token(session, feed)
    await notify.watch_token(session, feed)

    assert (api.seen, alerts) == ([], []), "ни запроса, ни тревоги на каждый черновик"
    [told] = said
    assert told.startswith(ALARM)
    assert "ждут человека без сообщения в группе продаж: 5." in told
    assert "SALES_TELEGRAM_BOT_TOKEN" in told


async def test_with_the_token_set_waiting_drafts_raise_no_alarm(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)
    await _drafts(session, 5, tag="waiting")

    assert await notify.token_alarm(session) is None
    await notify.watch_token(session, Feed())

    assert said == []


async def test_the_alarm_passes_once_the_token_is_set(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", "")
    await _drafts(session, 2, tag="waiting")
    feed = Feed()
    await notify.watch_token(session, feed)

    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)
    for _ in range(alarm_feed.QUIET_PASSES - 1):
        await notify.watch_token(session, feed)
    assert [text.split(".")[0] for text in said] == ["тревога: Бот продаж без токена"]

    await notify.watch_token(session, feed)
    assert [text.split(".")[0] for text in said] == [
        "тревога: Бот продаж без токена",
        "прошло: Бот продаж без токена",
    ]


async def test_the_alarm_stays_while_the_token_is_missing_though_nothing_waits(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    """Черновики разобрали люди, а токена всё нет: «прошло» сказать нельзя — бот не пишет."""
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", "")
    [draft] = await _drafts(session, 1, tag="waiting")
    feed = Feed()
    await notify.watch_token(session, feed)

    draft.status = DraftStatus.REJECTED
    await session.flush()
    for _ in range(alarm_feed.QUIET_PASSES + 1):
        await notify.watch_token(session, feed)

    [told] = said
    assert told.startswith(ALARM)
    assert notify.BOT_ALARM in feed.told


async def test_the_sales_pass_of_the_reaper_tells_the_alarm(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    """Сторож бота продаж идёт тем же кругом, что повтор передач (`retry_pass`), — своей
    лентой процесса разбора."""
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(notify, "BOT_FEED", Feed())
    monkeypatch.setattr(handoff_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        handoff_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    await _drafts(session, 3, tag="waiting")
    await session.commit()

    await handoff_jobs.retry_pass()
    await handoff_jobs.retry_pass()

    [told] = said
    assert "ждут человека без сообщения в группе продаж: 3." in told
