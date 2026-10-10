"""Свой дневной потолок черновиков агента: черновики не выбирают день у разбора ответов.

Общий потолок (`LLM_DAILY_TOKEN_CAP`) считается по всем операциям модели, и
черновики на сотни лидов выбрали бы его целиком — разбор ответов доноров встал
бы до завтра. У черновиков свой потолок (`AGENT_DAILY_TOKEN_CAP`, не задан — доля
общего) тем же журналом расхода, по операциям агента из реестра этапов. На
настоящей базе: разбор ответа донора проходит, а черновик получает отказ
словами, и модель не зовётся; общий «потолка нет» без своего — нет его и у
черновиков; свой задан при общем 0 — действует свой.

Воркер и API при старте говорят словами, на каких этапах агент ведёт переписку,
включён ли агент продаж и какой потолок черновиков не держит (`guarding.said_at_start`).
"""

from __future__ import annotations

import logging
from types import MappingProxyType

import pytest
from backend.api import app as app_module
from backend.config import llm as llm_cfg
from backend.features.agent import drafting, guarding
from backend.features.agent.stages import AGENT_STAGES, SALES_STAGE
from backend.features.core import usage
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.replies.pipeline import Parser
from backend.workers import main as worker_main
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_agent_drafting import FakeWriter, agent_on, reply
from tests.test_parse_requeue import CountingExtractor
from tests.test_replies_inbox import NOW, sent

__all__ = ["reply", "sent"]  # фикстуры черновика — отсюда их видит pytest

#: Реестр этапов агента без строки продаж и с ней — как с выключенным и включённым
#: `SALES_AGENT_ENABLED`, что бы ни стояло в окружении разработчика.
WITHOUT_SALES = MappingProxyType({k: v for k, v in AGENT_STAGES.items() if k is not Stage.SALES})
WITH_SALES = MappingProxyType({**WITHOUT_SALES, Stage.SALES: SALES_STAGE})
OFF = "агент переписки ведёт этапы «donors», «advertisers»; агент продаж выключен (SALES_AGENT_ENABLED)"
NO_GENERAL = "потолка расхода на модель за день нет (LLM_DAILY_TOKEN_CAP=0): "


async def test_spent_drafts_cap_lets_the_parse_go_and_refuses_the_draft_in_words(
    session: AsyncSession, reply: ReplyModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 5_437)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", 83)
    await agent_on(session)
    usage.record(session, operation="agent_draft", units=61)  # почти выбран
    await drafting.draft_answer(session, FakeWriter(), reply.id)  # +40 — последний, что влез
    model = CountingExtractor()
    agent = FakeWriter()

    await Parser(session, model, now=NOW).parse(reply.id)  # type: ignore[arg-type]
    with pytest.raises(LlmCapExceededError) as refused:
        await drafting.draft_answer(session, agent, reply.id, again=True)

    assert model.calls == 1  # разбор ответа донора прошёл: общий потолок не выбран
    assert agent.seen == []  # модель черновика не звали
    # 321 токен разбора — не в счёте черновиков: только 61 + 40.
    assert str(refused.value) == (
        "дневной потолок черновиков агента выбран: 101 из 83 токенов — продолжение завтра; "
        "поднять потолок может администратор"
    )


@pytest.mark.parametrize(
    ("general", "own", "spent", "refused"),
    [
        (5_437, None, 1_631, True),  # не задан — 30 % общего: 1631 из 1631
        (5_437, None, 1_630, False),  # и разбор (97) в счёт черновиков не идёт
        (0, None, 98_765, False),  # общий «потолка нет», свой не задан — нет и у черновиков
        (0, 83, 83, True),  # свой задан при общем 0 — действует свой
    ],
)
async def test_drafts_cap_is_the_setting_or_a_share_of_the_general_one(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    general: int,
    own: int | None,
    spent: int,
    refused: bool,
) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", general)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", own)
    usage.record(session, operation="agent_draft", units=spent)
    usage.record(session, operation="reply_parse", units=97)

    if refused:
        with pytest.raises(LlmCapExceededError, match="потолок черновиков агента"):
            await usage.ensure_llm_within_cap(session, own=guarding.drafts_cap())
    else:
        await usage.ensure_llm_within_cap(session, own=guarding.drafts_cap())


@pytest.mark.parametrize(
    ("own", "general", "expected"),
    [
        (None, 10_000, 2_000),  # не задан — доля общего
        (None, 3, 1),  # доля меньше токена — хотя бы один, а не «потолка нет»
        (None, 0, 0),  # общий «потолка нет» — нет и у части
        (500, 10_000, 500),  # задан — как есть
        (0, 10_000, 0),  # задан 0 — своего потолка нет, общий проверяется всё равно
    ],
)
def test_share_cap_is_the_setting_or_a_share_of_the_general_one(
    monkeypatch: pytest.MonkeyPatch, own: int | None, general: int, expected: int
) -> None:
    """Одно правило своих потолков — у черновиков агента и у вызовов модели продаж."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", general)

    assert usage.share_cap(own, llm_cfg.SALES_CAP_SHARE) == expected


@pytest.mark.parametrize(("raw", "parsed"), [("", None), ("1200", 1200), ("0", 0)])
def test_empty_sales_cap_in_env_is_unset_not_a_parse_error(
    monkeypatch: pytest.MonkeyPatch, raw: str, parsed: int | None
) -> None:
    """Пустое `SALES_DAILY_TOKEN_CAP=` в `.env` — «не задан» (доля общего), как у агента."""
    monkeypatch.setenv("SALES_DAILY_TOKEN_CAP", raw)

    assert llm_cfg._Llm().sales_daily_token_cap == parsed


# --- строка при старте -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("general", "own", "warned"),
    [
        (
            0,
            None,
            NO_GENERAL + "черновики агента, вызовы модели продаж, судья и разбор ответов тратят "
            "без предела",
        ),
        (
            0,
            83,
            NO_GENERAL + "вызовы модели продаж, судья и разбор ответов тратят без предела, "
            "черновики агента — до своего потолка 83 (AGENT_DAILY_TOKEN_CAP)",
        ),
        (5_437, None, None),  # не задан — доля общего, 1631
        (5_437, 1_631, None),  # ровно доля
        (
            5_437,
            1_632,
            "свой потолок черновиков 1632 (AGENT_DAILY_TOKEN_CAP) больше доли 30 % общего 5437 "
            "(LLM_DAILY_TOKEN_CAP): черновики агента могут выбрать день разбору ответов и судье",
        ),
        (
            5_437,
            5_438,
            "свой потолок черновиков 5438 (AGENT_DAILY_TOKEN_CAP) больше общего 5437 "
            "(LLM_DAILY_TOKEN_CAP) — он ничего не ограничивает",
        ),
        (
            5_437,
            0,
            "своего потолка черновиков нет (AGENT_DAILY_TOKEN_CAP=0): черновики агента могут выбрать "
            "весь общий 5437 (LLM_DAILY_TOKEN_CAP) — разбору ответов и судье не останется",
        ),
    ],
    ids=[
        "no-general",
        "no-general-own",
        "share",
        "at-share",
        "above-share",
        "above-general",
        "own-0",
    ],
)
def test_start_says_in_words_which_cap_does_not_hold_the_drafts(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    general: int,
    own: int | None,
    warned: str | None,
) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", general)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", own)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", None)
    monkeypatch.setattr(guarding, "AGENT_STAGES", WITHOUT_SALES)

    with caplog.at_level(logging.INFO, logger=guarding.__name__):
        said = guarding.said_at_start()

    expected = [(logging.INFO, OFF)] + ([] if warned is None else [(logging.WARNING, warned)])
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == expected
    assert said == [line for _, line in expected]


@pytest.mark.parametrize(
    ("registry", "said"),
    [
        (WITHOUT_SALES, OFF),
        (
            WITH_SALES,
            "агент переписки ведёт этапы «donors», «advertisers», «sales»; агент продаж включён "
            "(SALES_AGENT_ENABLED)",
        ),
    ],
    ids=["sales-off", "sales-on"],
)
def test_start_names_the_agent_stages_and_the_sales_switch(
    monkeypatch: pytest.MonkeyPatch, registry: object, said: str
) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 5_437)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", None)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", None)
    monkeypatch.setattr(guarding, "AGENT_STAGES", registry)

    assert guarding.said_at_start() == [said]


@pytest.mark.parametrize(
    ("general", "agent", "sales", "warned"),
    [
        (5_437, None, None, []),
        (5_437, None, 1_087, []),  # ровно доля 20 % общего
        (
            5_437,
            None,
            1_088,
            [
                "свой потолок вызовов модели продаж 1088 (SALES_DAILY_TOKEN_CAP) больше доли 20 % "
                "общего 5437 (LLM_DAILY_TOKEN_CAP): вызовы модели продаж могут выбрать день "
                "разбору ответов, судье и черновикам"
            ],
        ),
        (
            5_437,
            0,
            0,
            [
                "своего потолка черновиков нет (AGENT_DAILY_TOKEN_CAP=0): черновики агента могут "
                "выбрать весь общий 5437 (LLM_DAILY_TOKEN_CAP) — разбору ответов и судье не "
                "останется",
                "своего потолка вызовов модели продаж нет (SALES_DAILY_TOKEN_CAP=0): вызовы модели "
                "продаж могут выбрать весь общий 5437 (LLM_DAILY_TOKEN_CAP) — разбору ответов, "
                "судье и черновикам не останется",
            ],
        ),
        (
            0,
            83,
            50,
            [
                NO_GENERAL + "судья и разбор ответов тратят без предела, черновики агента — до "
                "своего потолка 83 (AGENT_DAILY_TOKEN_CAP), вызовы модели продаж — до своего "
                "потолка 50 (SALES_DAILY_TOKEN_CAP)"
            ],
        ),
    ],
    ids=["shares", "sales-at-share", "sales-above-share", "both-own-0", "no-general-both-own"],
)
def test_start_says_which_cap_does_not_hold_the_sales_calls(
    monkeypatch: pytest.MonkeyPatch,
    general: int,
    agent: int | None,
    sales: int | None,
    warned: list[str],
) -> None:
    """Потолок продаж (`SALES_DAILY_TOKEN_CAP`) — тем же правилом доли, что у черновиков:
    вид ответа лида и сборка очереди продаж выбрали бы общий день целиком."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", general)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", agent)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", sales)
    monkeypatch.setattr(guarding, "AGENT_STAGES", WITHOUT_SALES)

    assert guarding.said_at_start() == [OFF, *warned]


@pytest.mark.usefixtures("jwt_secret")
def test_without_sales_the_worker_and_the_api_say_so_at_start_and_start(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Доноры и рекламодатели: строка при старте есть и у них, о продажах — «выключен»,
    и ни воркер, ни приложение от неё не падают."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 5_437)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", None)
    monkeypatch.setattr(guarding, "AGENT_STAGES", WITHOUT_SALES)
    worked: list[list[str]] = []

    class _Worker:
        def __init__(self, queues: list[str], connection: object) -> None:
            self.queues = queues

        def work(self, with_scheduler: bool) -> None:
            worked.append(self.queues)

    monkeypatch.setattr(worker_main, "Worker", _Worker)
    monkeypatch.setattr(worker_main, "connection", lambda: None)
    for module in (worker_main, app_module):  # журнал — в caplog, а не в свой обработчик
        monkeypatch.setattr(module, "setup_logging", lambda: None)

    with caplog.at_level(logging.INFO):
        worker_main.main([])
        app = app_module.create_app()

    assert worked == [[worker_main.QUEUE_NAME]]
    assert isinstance(app, FastAPI)
    said = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == guarding.__name__]
    assert said == [(logging.INFO, OFF), (logging.INFO, OFF)]
