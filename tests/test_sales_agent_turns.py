"""Предел ходов агента продаж: черновик — на 1-й и 2-й ответ лида в диалоге, с 3-го — человек.

ТЗ, строка MVP: «ИИ-агент ведёт первый ответ в диалоге, максимум второй». Ход — ответ лида по
порядку в этом диалоге (письма человека; автоответ ходом не считается). Число — настройка
`SALES_AGENT_MAX_TURNS` (2, от 1 до 10). Сверх предела черновика нет: модель агента не зовётся,
ответ ждёт человека с причиной словами в карточке ответа. Настройку разбирает задача ответа, а не
импорт: негодная — тоже черновика нет, причина словами (импорт общего кода с опечаткой —
`tests/test_sales_agent_stage.py`). Разбор вида — подставной; база — настоящая; адреса и тексты
выдуманные.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

import pytest
from backend.config import sales as sales_cfg
from backend.config.startup_checks import ConfigError
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.outreach.threads import review_of
from backend.features.sales import agent_turns
from backend.features.sales.replies import Handled, SalesReplies
from backend.features.sales.reply_kind import KindFound, SalesKind
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_handoff_rows import SENT_AT, Dialog, answer, sales_dialog
from tests.test_sales_reply_routing import FakeClassifier
from tests.test_sales_switch import sales_switched_on

__all__ = ["sales_switched_on"]  # продажи включены: разбор вида ответа

QUESTION = KindFound(SalesKind.QUESTION, 0.93, quote="А что входит в аудит?")
LIMIT = "задал вопрос: агент уже вёл два ответа — дальше отвечает человек"


async def turns(session: AsyncSession, count: int) -> tuple[Dialog, list[ReplyModel]]:
    """Диалог, где лид написал `count` раз: первый ответ — от `sales_dialog`, дальше — вопросы."""
    dialog = await sales_dialog(session, text="А что входит в аудит?")
    written = [dialog.reply]
    for number in range(1, count):
        at = SENT_AT + timedelta(days=number + 1)
        written.append(
            await answer(session, dialog.thread, dialog.message, "А что входит в аудит?", at=at)
        )
    return dialog, written


async def handled(session: AsyncSession, reply: ReplyModel) -> Handled:
    return await SalesReplies(session, FakeClassifier(QUESTION)).handle(reply.id)


async def test_first_and_second_answer_get_a_draft_the_third_goes_to_a_human(
    session: AsyncSession,
) -> None:
    _, (first, second, third) = await turns(session, 3)

    done = [await handled(session, reply) for reply in (first, second, third)]

    assert [item.drafted for item in done] == [True, True, False]
    assert [item.no_draft for item in done[:2]] == [None, None]
    assert (done[2].waits, done[2].reason) == (True, LIMIT)
    assert done[2].route == "agent", "путь прежний — вопрос; черновика нет"
    await session.flush()
    await session.refresh(third)
    review = review_of(third, Stage.SALES)
    assert (review.waiting, review.reason) == (True, LIMIT), "причина — в карточке ответа"


async def test_setting_one_stops_the_agent_from_the_second_answer(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "AGENT_MAX_TURNS", "1")
    _, (first, second) = await turns(session, 2)

    done = [await handled(session, reply) for reply in (first, second)]

    assert [item.drafted for item in done] == [True, False]
    assert done[1].reason == "задал вопрос: агент уже вёл один ответ — дальше отвечает человек"


async def test_autoreply_is_not_a_turn(session: AsyncSession) -> None:
    """Автоответ между письмами лида ходом не считается: на него агент не пишет."""
    dialog, (first,) = await turns(session, 1)
    away = await answer(
        session, dialog.thread, dialog.message, "Я в отпуске.", at=SENT_AT + timedelta(days=2)
    )
    away.kind = ReplyKind.AUTO_REPLY
    second = await answer(
        session,
        dialog.thread,
        dialog.message,
        "Вернулся. Сколько стоит?",
        at=SENT_AT + timedelta(days=9),
    )
    await session.flush()

    assert [await agent_turns.turn_of(session, reply) for reply in (first, second)] == [1, 2]
    assert (await handled(session, second)).drafted is True


@pytest.mark.parametrize(("limit", "words"), [(2, "два ответа"), (5, "пять ответов")])
def test_reason_names_the_limit_in_words(limit: int, words: str) -> None:
    assert agent_turns.led(limit) == f"агент уже вёл {words} — дальше отвечает человек"


def test_limit_setting_is_from_one_to_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    """По умолчанию два — ТЗ; от одного до десяти — как задано."""
    monkeypatch.delenv("SALES_AGENT_MAX_TURNS", raising=False)
    monkeypatch.setattr(
        sales_cfg, "AGENT_MAX_TURNS", sales_cfg._Sales(_env_file=None).agent_max_turns
    )
    assert agent_turns.max_turns() == 2
    for good in ("1", "3", "10"):
        monkeypatch.setattr(sales_cfg, "AGENT_MAX_TURNS", good)
        assert agent_turns.max_turns() == int(good)


@pytest.mark.parametrize("wrong", ["0", "11", "два", "2.5", "-1", ""])
def test_crooked_limit_is_refused_in_words_when_read_not_at_import(
    monkeypatch: pytest.MonkeyPatch, wrong: str
) -> None:
    """Ноль — агент не вёл бы ни одного ответа, больше десяти — опечатка. Чтение настроек её
    принимает как есть (импорт не падает), отказ — у того, кто предел читает."""
    monkeypatch.setenv("SALES_AGENT_MAX_TURNS", wrong)
    assert sales_cfg._Sales(_env_file=None).agent_max_turns == wrong
    monkeypatch.setattr(sales_cfg, "AGENT_MAX_TURNS", wrong)

    words = f"SALES_AGENT_MAX_TURNS=«{wrong}» — нужно целое от 1 до 10"
    with pytest.raises(ConfigError, match=re.escape(words)):
        agent_turns.max_turns()


async def test_crooked_limit_means_no_draft_and_the_reason_in_words(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Вид ответа уже оплачен: задача не падает (повтор позвал бы модель снова), а агент без
    проверенного предела не пишет — ответ ждёт человека, почему — словами у ответа и в журнале."""
    monkeypatch.setattr(sales_cfg, "AGENT_MAX_TURNS", "два")
    _, (first,) = await turns(session, 1)

    with caplog.at_level(logging.ERROR, logger=agent_turns.__name__):
        done = await handled(session, first)

    assert (done.drafted, done.waits) == (False, True)
    assert done.reason == (
        "задал вопрос: SALES_AGENT_MAX_TURNS=«два» — нужно целое от 1 до 10: сколько ответов лида "
        "в диалоге агент ведёт черновиком; черновика агента нет — отвечает человек"
    )
    assert "предел ходов агента негоден — черновика нет" in caplog.text
