"""Ревью стыков, круг 2: агент продаж держит свой потолок продаж, а не только общий потолок
черновиков (порядок, договорённый с соседней сессией).

Потолок черновиков агента общий (`agent/guarding.drafts_cap`): операции агента продаж выбирали
бы долю доноров. Шаг первый — наш: операции агента продаж (черновик, ситуация письма, судья)
считает и проверяет свой потолок продаж (`sales/usage_cap.sales_cap`) — тем же журналом и тем
же путём, что вид ответа, сборку писем и судью сегмента. Пока они считаются и в общем
`drafts_cap`, потолок двойной — это безопасно. Шаг второй — соседней сессии: убрать продажи из
`drafts_cap`. Ни в какой момент агент продаж не остаётся без потолка: проверка продаж стоит до
первого вызова модели черновика (ситуация письма в брифе).

- выбран потолок продаж — черновика нет, модель не звали, отказ словами;
- расход самого агента продаж — в счёте потолка продаж;
- черновики доноров потолок продаж не трогает;
- **общий код:** петля правки (`guarding.compose`) перед каждым вызовом писателя проверяет только
  `drafts_cap` — потолок этапа продаж между правками не держится: `xfail(strict=True)`, правка —
  в разделе «Для PR «общее»» отчёта ревью.

База настоящая; модель ситуации и судьи — подставной HTTP, писатель — подставной.
"""

from __future__ import annotations

import pytest
from backend.config import llm as llm_cfg
from backend.features.agent import drafting, guarding
from backend.features.agent.stages import SALES_STAGE
from backend.features.agent.writer import Request
from backend.features.core import usage
from backend.features.core.domain import DraftStatus, Stage
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.sales import usage_cap
from backend.features.sales.agent import parts
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_agent_drafting import FakeWriter, agent_on, reply
from tests.test_replies_inbox import sent
from tests.test_sales_agent_situation import SETTINGS, Plug, llm
from tests.test_sales_agent_stage import ALLOW, GOOD, INFORM, Writer, lead_replied, sales_on

__all__ = ["llm", "reply", "sales_on", "sent"]  # фикстуры — отсюда их видит pytest

#: Свой потолок продаж — выдуманный, некруглый; потолок черновиков — заведомо больше.
SALES_CAP = 4_391
DRAFTS_CAP = 97_531
REFUSED = (
    f"дневной потолок вызовов модели продаж выбран: {SALES_CAP} из {SALES_CAP} токенов — "
    "продолжение завтра; поднять потолок может администратор"
)


@pytest.fixture
def caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Общего потолка нет, свой потолок продаж — `SALES_CAP`, черновиков — больше него."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", SALES_CAP)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", DRAFTS_CAP)


async def _drafts(session: AsyncSession, reply_id: int) -> list[AgentDraftModel]:
    found = await session.scalars(
        select(AgentDraftModel).where(AgentDraftModel.reply_id == reply_id)
    )
    return list(found)


@pytest.mark.usefixtures("sales_on", "caps")
@pytest.mark.parametrize(
    "operation",
    [
        "sales_reply_kind",  # разбор ответов выбрал потолок продаж
        parts.DRAFT_OPERATION,  # черновики самого агента продаж
        parts.SITUATION_OPERATION,
        parts.JUDGE_OPERATION,
    ],
)
async def test_spent_sales_cap_leaves_the_lead_without_a_draft_in_words(
    session: AsyncSession, llm: Plug, operation: str
) -> None:
    """Потолок продаж выбран — чьими бы вызовами продаж ни был: черновика нет, ни ситуацию, ни
    писателя не звали, отказ словами (задача черновика кладёт его в итог и журнал)."""
    reply_id = await lead_replied(session)
    usage.record(session, operation=operation, units=SALES_CAP)
    model = llm(situation=[INFORM], judge=[ALLOW])
    writer = Writer(GOOD)

    with pytest.raises(LlmCapExceededError) as refused:
        await drafting.draft_answer(session, writer, reply_id)

    assert str(refused.value) == REFUSED
    assert (model.sent["situation"], model.sent["judge"], writer.seen) == ([], [], [])
    assert await _drafts(session, reply_id) == []


@pytest.mark.usefixtures("sales_on", "caps")
async def test_below_the_sales_cap_the_draft_is_written(session: AsyncSession, llm: Plug) -> None:
    """Граница правила: на токен ниже потолка продаж черновик пишется как всегда."""
    reply_id = await lead_replied(session)
    usage.record(session, operation=parts.DRAFT_OPERATION, units=SALES_CAP - 1)
    llm(situation=[INFORM], judge=[ALLOW])

    outcome = await drafting.draft_answer(session, Writer(GOOD), reply_id)

    assert outcome.status is DraftStatus.DRAFTED


def test_the_sales_cap_counts_the_sales_agent_and_nothing_of_the_donors() -> None:
    """Операции агента продаж — в своём потолке продаж; операции агента доноров — нет."""
    counted = usage_cap.sales_cap().operations

    assert {parts.DRAFT_OPERATION, parts.SITUATION_OPERATION, parts.JUDGE_OPERATION} <= counted
    assert "agent_draft" not in counted


@pytest.mark.usefixtures("caps")
async def test_donor_drafts_do_not_feel_the_sales_cap(
    session: AsyncSession, reply: ReplyModel
) -> None:
    """Потолок продаж выбран — черновик донору пишется: доноров он не трогает."""
    await agent_on(session)
    usage.record(session, operation=parts.DRAFT_OPERATION, units=SALES_CAP)

    outcome = await drafting.draft_answer(session, FakeWriter(), reply.id)

    assert outcome.status is DraftStatus.DRAFTED


# --- общий код: потолок этапа продаж между правками ---------------------------------------------


@pytest.mark.usefixtures("sales_on", "caps")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код (agent/guarding.compose): перед каждым вызовом писателя проверяется только "
        "drafts_cap — свой потолок этапа продаж (sales/usage_cap.sales_cap) между правками не "
        "держится, а после шага 2 (продажи вне drafts_cap) писатель продаж в петле правки не "
        "держал бы ничей потолок. Правка: потолок этапа (AgentStage.cap; у продаж — sales_cap) "
        "проверяется перед каждым вызовом писателя вместе с drafts_cap"
    ),
)
async def test_the_rewrite_loop_holds_the_sales_cap_before_every_writer_call(
    session: AsyncSession,
) -> None:
    usage.record(session, operation=parts.DRAFT_OPERATION, units=SALES_CAP)
    writer = Writer(GOOD)
    request = Request(
        stage=Stage.SALES,
        settings=SETTINGS,
        turns=(),
        sign_as="Отдел продаж",
        parsed={},
        prompt=SALES_STAGE.prompt,
        model=SALES_STAGE.model,
    )

    with pytest.raises(LlmCapExceededError):
        await guarding.compose(session, SALES_STAGE, writer, request, incoming="Сколько стоит?")

    assert writer.seen == []
