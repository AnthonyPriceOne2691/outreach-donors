"""Ревью стыков, круг 2: агент продаж включён, путь даёт черновик, а черновика нет — словами.

Путь вида ответа «ответит агент» (`reply_routes.DRAFTED`) черновик обещает, а ставит его шов
агента (`agent_jobs.after_parse` → `drafting.wants_draft`). Шов молча не ставит задачу, когда:

- диалог закрыт раньше — лид сказал «не сейчас» или «не интересно» (`replies._close`), а теперь
  пишет снова с вопросом: шов в закрытый диалог не пишет (`drafting._refusal`);
- тумблер `SALES_AGENT_ENABLED` включён, а настройки агента «Лидам» не сохранены ни разу — у
  этапа нет версии настроек;
- агент продаж выключен в своих настройках на экране.

До правки ответ говорил «ответит агент; пока — человек», и человек ждал черновика, которого не
будет. Теперь причина — словами у ответа (`no_draft.absent`), а решает по-прежнему шов: задачи
черновика нет ни до правки, ни после.

**Решение владельца** — вернуть ли агенту лида, который после «не сейчас» пишет снова с вопросом
(ТЗ, «Ответы»: «вопрос или интерес — отвечает ИИ-агент»): `xfail(strict=True)` ниже.

**Общий код** — задача черновика, которую модель не смогла написать (ключ или права модели,
потолок расхода, повторы кончились), кончается итогом задачи и строкой журнала: черновика нет,
в группу продаж ничего не уходит, а ответ говорит «ответит агент». `xfail(strict=True)` ниже;
правка — в разделе «Для PR «общее»» отчёта ревью.

База настоящая, очереди и вид ответа — подставные; модели нет.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import pytest
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.agent.writer import DraftUnavailableError
from backend.features.core.domain import DraftStatus, ReplyKind, Stage, ThreadStatus
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import ReplyModel, ThreadModel
from backend.features.sales import no_draft
from backend.features.sales.agent import notify, parts, situation
from backend.features.sales.reply_kind import KindFound, SalesKind
from backend.workers import agent_jobs, sales_jobs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests import test_sales_send_world as w
from tests.test_sales_agent_stage import sales_on
from tests.test_sales_reply_draft import QUESTION, Queues, queues
from tests.test_sales_reply_routing import FakeClassifier, _Closable
from tests.test_sales_seam_agent import sales_off
from tests.test_sales_send import _first_sent, _lead_of

__all__ = ["queues", "sales_off", "sales_on"]  # фикстуры — отсюда их видит pytest

NOT_NOW = KindFound(SalesKind.NOT_NOW, 0.94, quote="Not now, maybe next quarter.")
NOT_INTERESTED = KindFound(SalesKind.NOT_INTERESTED, 0.94, quote="Not for us, thanks.")
#: Прежние слова пути — их человек видит, пока агент правда напишет черновик.
AS_BEFORE = "задал вопрос: ответит агент; пока — человек"

Replied = Callable[..., Awaitable[tuple[int, dict[str, Any]]]]


@pytest.fixture
def replied(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Replied:
    """Подключённые продажи: первое письмо ушло лиду, лид отвечает письмами `kinds` по порядку,
    задача продаж разбирает каждое (вид — подставной). Итог — последний ответ и его разбор.
    `settings` — настройки агента продаж: «saved» (по умолчанию, включён), «none», «off»."""
    monkeypatch.setattr(sales_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        sales_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )

    async def answered(*kinds: KindFound, settings: str = "saved") -> tuple[int, dict[str, Any]]:
        world = await w.world(session, monkeypatch)
        _, [first] = await _first_sent(session, world)
        lead = await _lead_of(session, first)
        if settings != "none":
            saved = replace(parts.DEFAULTS, enabled=settings != "off")
            await AgentSettingsRepository(session).save(Stage.SALES, saved, author="тест")
        report: dict[str, Any] = {}
        reply_id = 0
        for number, found in enumerate(kinds, start=1):
            reply = ReplyModel(
                thread_id=first.thread_id,
                message_id=first.id,
                kind=ReplyKind.HUMAN,
                raw_body=found.quote or "",
                from_email=lead.email,
                subject=f"Re: {first.subject}",
                inbound_message_id=f"<in-{number}@acme.example.test>",
            )
            session.add(reply)
            await session.commit()
            monkeypatch.setattr(sales_jobs, "KindClient", lambda found=found: FakeClassifier(found))
            reply_id, report = reply.id, await sales_jobs.handle(reply.id)
        return reply_id, report

    return answered


async def _snapshot_reason(session: AsyncSession, reply_id: int) -> object:
    reply = await session.get(ReplyModel, reply_id)
    assert reply is not None
    await session.refresh(reply)
    return (reply.model_parse or {}).get("reason")


# --- диалог закрыт раньше — лид пишет снова ----------------------------------------------------


@pytest.mark.usefixtures("sales_on")
@pytest.mark.parametrize("before", [NOT_NOW, NOT_INTERESTED], ids=["not-now", "not-interested"])
async def test_lead_who_closed_the_dialog_and_asks_again_hears_why_there_is_no_draft(
    session: AsyncSession, queues: Queues, replied: Replied, before: KindFound
) -> None:
    """Шов в закрытый диалог не пишет — задачи черновика нет; причина — у ответа словами, а не
    «ответит агент». Мутант «без проверки закрытого диалога» — прежние слова, тест красный."""
    reply_id, report = await replied(before, QUESTION)

    reason = f"задал вопрос: {no_draft.CLOSED}"
    assert (report["route"], report["waits"], report["reason"]) == ("agent", True, reason)
    assert queues.drafts() == {"runs": [], "sales": []}
    assert await _snapshot_reason(session, reply_id) == reason


# --- агент включён тумблером, а на экране — нет -------------------------------------------------


@pytest.mark.usefixtures("sales_on")
@pytest.mark.parametrize(
    ("settings", "why"),
    [("none", no_draft.NOT_SET), ("off", no_draft.OFF_ON_SCREEN)],
    ids=["settings-never-saved", "switched-off-on-the-screen"],
)
async def test_switched_on_agent_without_its_settings_says_so_at_the_answer(
    session: AsyncSession, queues: Queues, replied: Replied, settings: str, why: str
) -> None:
    """Тумблер включён, а настройки «Лидам» не сохранены или агент выключен в них — черновика
    нет и не будет; это видно у ответа, а не только по пустой очереди."""
    reply_id, report = await replied(QUESTION, settings=settings)

    reason = f"задал вопрос: {why}"
    assert (report["route"], report["waits"], report["reason"]) == ("agent", True, reason)
    assert queues.drafts() == {"runs": [], "sales": []}
    assert await _snapshot_reason(session, reply_id) == reason


@pytest.mark.usefixtures("sales_on")
async def test_configured_agent_in_an_open_dialog_keeps_the_draft_and_the_words(
    queues: Queues, replied: Replied
) -> None:
    """Граница правила: настроенный агент, диалог открыт — черновик ставится, слова прежние."""
    reply_id, report = await replied(QUESTION)

    assert report["reason"] == AS_BEFORE
    assert queues.drafts() == {"runs": [], "sales": [reply_id]}


@pytest.mark.usefixtures("sales_off")
async def test_switched_off_agent_keeps_the_known_words(queues: Queues, replied: Replied) -> None:
    """Тумблер выключен — состояние по умолчанию: слова прежние («пока — человек»), черновика нет;
    закрытый диалог и пустые настройки тут не причина — агента нет вовсе."""
    _, report = await replied(NOT_NOW, QUESTION, settings="none")

    assert report["reason"] == AS_BEFORE
    assert queues.drafts() == {"runs": [], "sales": []}


@pytest.mark.usefixtures("sales_off")
async def test_reasons_do_not_reach_for_the_settings_when_the_switch_is_off(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тумблер выключен — ни одного запроса к настройкам агента: слова не про них."""

    async def must_not_read(*_: object) -> None:
        raise AssertionError("настройки агента не читаются при выключенном тумблере")

    monkeypatch.setattr(AgentSettingsRepository, "current", must_not_read)
    reply = ReplyModel(thread_id=None, kind=ReplyKind.HUMAN, raw_body="?")

    assert await no_draft.absent(session, reply) is None


# --- решение владельца: лид после «не сейчас» снова спрашивает --------------------------------


@pytest.mark.usefixtures("sales_on")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "решение владельца: лид после «не сейчас» или «не интересно» пишет снова с вопросом — "
        "открыть диалог снова (REPLIED) и дать агенту черновик (ТЗ, «Ответы»: вопрос — отвечает "
        "агент) или оставить человеку, как сейчас. Правка — в replies._follow: путь AGENT и "
        "HANDOFF открывают закрытый модулем диалог"
    ),
)
async def test_lead_who_said_not_now_and_asks_again_gets_the_agent_back(
    session: AsyncSession, queues: Queues, replied: Replied
) -> None:
    reply_id, report = await replied(NOT_NOW, QUESTION)

    reply = await session.get(ReplyModel, reply_id)
    assert reply is not None
    thread = await session.get(ThreadModel, reply.thread_id)
    assert thread is not None
    await session.refresh(thread)
    assert thread.status is ThreadStatus.REPLIED
    assert report["reason"] == AS_BEFORE
    assert queues.drafts() == {"runs": [], "sales": [reply_id]}


# --- общий код: модель не смогла написать черновик — группа не узнаёт ------------------------


@pytest.mark.usefixtures("sales_on")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код (workers/agent_jobs.py, agent/drafting.py): задача черновика, которую модель "
        "не смогла написать (ключ, права, потолок; повторы кончились), кончается итогом задачи "
        "и строкой журнала — черновика нет, крючок этапа не зовётся, группа продаж не узнаёт, "
        "ответ говорит «ответит агент». Правка: черновик escalated без текста с причиной "
        "«черновик не написан: …» и announce, как у пропуска брифа «человеку»"
    ),
)
async def test_a_draft_the_model_could_not_write_reaches_the_group_in_words(
    session: AsyncSession,
    queues: Queues,
    replied: Replied,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply_id, _ = await replied(QUESTION)
    assert queues.drafts() == {"runs": [], "sales": [reply_id]}

    async def refused(*_args: object, **_kwargs: object) -> object:
        raise DraftUnavailableError("ключ модели отклонён (HTTP 401)", permanent=True)

    monkeypatch.setattr(situation, "classify", refused)
    monkeypatch.setattr(agent_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        agent_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    told: list[int] = []
    monkeypatch.setattr(notify, "queue_notice", told.append)

    with contextlib.suppress(DraftUnavailableError):
        await agent_jobs._draft_answer(reply_id)

    draft = await session.scalar(
        select(AgentDraftModel).where(AgentDraftModel.reply_id == reply_id)
    )
    assert draft is not None, "черновика нет — и в группу ничего не ушло"
    assert draft.status is DraftStatus.ESCALATED
    assert "черновик не написан" in (draft.reason or "")
    assert told == [draft.id]
