"""Ревью стыков, круг 2: агент продаж держит свой потолок продаж, а не только общий потолок
черновиков (порядок, договорённый с соседней сессией).

Потолок черновиков агента общий (`agent/guarding.drafts_cap`): операции агента продаж выбирали
бы долю доноров. Шаг первый — наш: операции агента продаж (черновик, ситуация письма, судья)
считает и проверяет свой потолок продаж (`sales/usage_cap.sales_cap`) — тем же журналом и тем
же путём, что вид ответа, сборку писем и судью сегмента. Пока они считаются и в общем
`drafts_cap`, потолок двойной — это безопасно. Шаг второй — правило `guarding.within_caps`
(29.10.2026): при сохранённых лимитах разделов на «Расходе» продажи судит лимит их раздела, а не
агента переписки; до сохранения — оба потолка. Ни в какой момент агент продаж не остаётся без
потолка: проверка продаж стоит до первого вызова модели черновика (ситуация письма в брифе).

- выбран потолок продаж — черновика нет, модель не звали, отказ словами;
- расход самого агента продаж — в счёте потолка продаж;
- черновики доноров потолок продаж не трогает;
- **свой потолок этапа у шва** («общее-3», #363): петля правки (`guarding.compose`) перед каждым
  вызовом писателя проверяет и потолок этапа (`AgentStage.own_cap`); у продаж это тот же потолок
  продаж (`SALES_STAGE.own_cap` — `agent/parts.cap`), второго счёта нет: пусто — доля общего, 0
  (или пусто при общем 0) — потолка нет, как у `usage.share_cap`.

База настоящая; модель ситуации и судьи — подставной HTTP, писатель — подставной.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from backend.config import llm as llm_cfg
from backend.features.agent import drafting, guarding
from backend.features.agent.stages import SALES_STAGE, GuardInput, Verdict, VerdictKind
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
#: Начало отказа — продаж: чей потолок и сколько выбрано. Хвост (что делать и когда сброс) —
#: общего сторожа модели, его слова проверяет `test_llm_cap.py`: здесь он не закрепляется.
REFUSED_HEAD = (
    f"дневной потолок вызовов модели продаж выбран: {SALES_CAP} из {SALES_CAP} токенов — "
)


def _ungrouped(text: str) -> str:
    """Отказ без разрядов: сторож может разбить число неразрывным пробелом (`numbers.spaced`,
    «4\u00a0391»), а начало сверяется с числом как есть — сверка держит оба вида."""
    return re.sub(r"(?<=\d)[ \u00a0\u202f](?=\d)", "", text)


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

    assert _ungrouped(str(refused.value)).startswith(REFUSED_HEAD)
    assert (model.sent["situation"], model.sent["judge"], writer.seen) == ([], [], [])
    assert await _drafts(session, reply_id) == []


@pytest.mark.usefixtures("sales_on", "caps")
async def test_below_the_sales_cap_the_draft_is_written(session: AsyncSession, llm: Plug) -> None:
    """Граница правила: ниже потолка продаж — с запасом на ситуацию, писателя и судью —
    черновик пишется как всегда."""
    reply_id = await lead_replied(session)
    usage.record(session, operation=parts.DRAFT_OPERATION, units=SALES_CAP // 2)
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


# --- свой потолок этапа у шва: петля правки держит потолок продаж -------------------------------

#: Запрос писателю продаж — без брифа: петля правки шва зовётся с ним напрямую.
ASKED = Request(
    stage=Stage.SALES,
    settings=SETTINGS,
    turns=(),
    sign_as="Отдел продаж",
    parsed={},
    prompt=SALES_STAGE.prompt,
    model=SALES_STAGE.model,
)


async def _blocks(_check: GuardInput) -> Verdict:
    """Судья, который всегда возвращает черновик на правку: петля идёт на следующий вызов."""
    return Verdict(VerdictKind.BLOCK, ("выдуманное замечание судьи",))


def test_the_sales_row_holds_its_own_cap_by_the_sales_cap_rule() -> None:
    """Свой потолок этапа у строки продаж — функция модуля продаж, и это тот же потолок продаж,
    что у вида ответа, сборки писем и судьи сегмента: та же настройка, те же операции."""
    assert SALES_STAGE.own_cap is parts.cap
    assert parts.cap() == usage_cap.sales_cap()


@pytest.mark.usefixtures("sales_on", "caps")
async def test_the_rewrite_loop_holds_the_sales_cap_before_every_writer_call(
    session: AsyncSession,
) -> None:
    """Потолок продаж выбран до петли — писатель не зовётся ни разу, отказ словами потолка."""
    usage.record(session, operation=parts.DRAFT_OPERATION, units=SALES_CAP)
    writer = Writer(GOOD)

    with pytest.raises(LlmCapExceededError) as refused:
        await guarding.compose(session, SALES_STAGE, writer, ASKED, incoming="Сколько стоит?")

    assert _ungrouped(str(refused.value)).startswith(REFUSED_HEAD)
    assert writer.seen == []


@pytest.mark.usefixtures("sales_on", "caps")
async def test_the_sales_cap_spent_by_the_first_draft_stops_the_rewrite(
    session: AsyncSession,
) -> None:
    """Первый черновик добрал потолок продаж, судья вернул его на правку — второго вызова
    писателя нет: потолок проверяется перед каждым вызовом, а не раз на черновик."""
    usage.record(session, operation=parts.DRAFT_OPERATION, units=SALES_CAP - 1)
    writer = Writer(GOOD)
    judged = replace(SALES_STAGE, guard=_blocks)

    with pytest.raises(LlmCapExceededError, match="дневной потолок вызовов модели продаж выбран"):
        await guarding.compose(session, judged, writer, ASKED, incoming="Сколько стоит?")

    assert len(writer.seen) == 1


@pytest.mark.usefixtures("sales_on")
@pytest.mark.parametrize("own", [0, None])
async def test_no_sales_cap_set_and_no_general_cap_hold_the_loop_by_nothing_of_their_own(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, own: int | None
) -> None:
    """`SALES_DAILY_TOKEN_CAP=0` или пусто при общем 0 — своего потолка у этапа нет, как у
    `usage.share_cap`: сколько бы ни потратили продажи, писатель пишет."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", own)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", DRAFTS_CAP)
    usage.record(session, operation=parts.DRAFT_OPERATION, units=DRAFTS_CAP - 1)
    writer = Writer(GOOD)

    composed = await guarding.compose(
        session, replace(SALES_STAGE, guard=None), writer, ASKED, incoming="Сколько стоит?"
    )

    assert (composed.held, len(writer.seen)) == (False, 1)


@pytest.mark.usefixtures("sales_on")
async def test_an_empty_sales_cap_is_its_share_of_the_general_cap_in_the_loop(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`SALES_DAILY_TOKEN_CAP` пусто при общем потолке — у этапа доля общего (`SALES_CAP_SHARE`):
    выбрана — писатель не зовётся, хотя общий и потолок черновиков ещё не выбраны."""
    general = 10_000
    share = int(general * llm_cfg.SALES_CAP_SHARE)
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", general)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", None)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", DRAFTS_CAP)
    usage.record(session, operation=parts.DRAFT_OPERATION, units=share)
    writer = Writer(GOOD)

    with pytest.raises(LlmCapExceededError) as refused:
        await guarding.compose(session, SALES_STAGE, writer, ASKED, incoming="Сколько стоит?")

    assert _ungrouped(str(refused.value)).startswith(
        f"дневной потолок вызовов модели продаж выбран: {share} из {share} токенов"
    )
    assert writer.seen == []


@pytest.mark.usefixtures("sales_on", "caps")
async def test_the_situation_asks_the_caps_by_the_rule_of_the_sales_stage(
    session: AsyncSession, llm: Plug, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ситуация письма — первый вызов модели черновика продаж: потолки она судит правилом петли
    правки (`guarding.within_caps`) своим этапом — при сохранённых лимитах на «Расходе» лимит
    агента переписки продажам не отказывает (`test_llm_caps`). Отказ правила — модель не звали."""
    reply_id = await lead_replied(session)
    model = llm(situation=[INFORM], judge=[ALLOW])
    writer = Writer(GOOD)
    asked: list[object] = []

    async def within(_session: AsyncSession, stage: object) -> None:
        asked.append(stage)
        raise LlmCapExceededError("потолок выбран")

    monkeypatch.setattr(guarding, "within_caps", within)

    with pytest.raises(LlmCapExceededError):
        await drafting.draft_answer(session, writer, reply_id)

    assert asked == [SALES_STAGE]
    assert (model.sent["situation"], writer.seen) == ([], [])
