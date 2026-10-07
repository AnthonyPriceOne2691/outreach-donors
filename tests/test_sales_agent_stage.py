"""Этап продаж на шве агента — срез 3.2a, шаг 7: строка `Stage.SALES` целиком.

Черновик пишется настоящим путём шва (`drafting.draft_answer`) по настоящей
переписке продаж (`sales_world`), базе знаний и отправителю; модель ситуации и
судьи — подставной HTTP, писатель — подставной. Проверяется то, чего не видно по
тестам частей: строка реестра собрана из частей продаж; бриф, писатель и судья
сходятся на одном черновике; подпись — персоной продаж, а не именем отправителя
доноров; замечание судьи доходит до писателя, и черновик выходит без суммы
(3.3 A1); три неудачные правки и судья, не сумевший проверить, отдают черновик
человеку (3.3 A5, A6); «спасибо» не тратит писателя (A2); расход ложится тремя
операциями продаж. Шов и продажи импортируются первыми без круга.

Строка продаж в реестре этапов — только по тумблеру `SALES_AGENT_ENABLED`
(по умолчанию выключен): тумблер проверяется в чистом процессе, а путь шва —
с фикстурой `sales_on`, которая кладёт строку в реестр, как это сделал бы тумблер.
"""

from __future__ import annotations

import os
import subprocess
import sys
from types import MappingProxyType

import pytest
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.features.agent import drafting, stages
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.agent.stages import SALES_STAGE, PriceSide
from backend.features.agent.writer import Request, Written
from backend.features.core.domain import DraftStatus, MessageStatus, Stage
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.sales.agent import parts, situation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_agent_brief import CALL, NAME, world
from tests.test_sales_agent_situation import LETTER, TOKENS, Plug, llm, talk
from tests.test_sales_model import ROOT
from tests.test_sales_stage_mail import sales_world

__all__ = ["llm"]  # фикстура подставной модели — отсюда её видит pytest

pytestmark = pytest.mark.usefixtures("sales_on")

WRITER_TOKENS = 41
GOOD = (
    "Hello,\n"
    "Thank you for the reply. We audit websites, and it is easier to show the details "
    f"on a short call: {CALL}. Pick any time that suits you.\n"
    "Sales desk"
)
BAD = GOOD.replace("We audit websites", "An audit costs $500")
INFORM = {"situation": "asks_info", "confidence": 0.9}
ALLOW = {"claims": [], "promises": [], "tone": {"ok": True, "problem": ""}}


@pytest.fixture
def sales_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Строка продаж в реестре этапов — как с тумблером `SALES_AGENT_ENABLED`.

    Реестр собирается при импорте, а модули шва берут его своим именем
    (`from … import AGENT_STAGES`): подмена — в каждом загруженном модуле проекта,
    где лежит тот же реестр, а не в одном.
    """
    original = stages.AGENT_STAGES
    registry = MappingProxyType({**original, Stage.SALES: SALES_STAGE})
    for name, module in list(sys.modules.items()):
        if name.startswith(("backend.", "tests.")) and vars(module).get("AGENT_STAGES") is original:
            monkeypatch.setattr(module, "AGENT_STAGES", registry)


class Writer:
    """Писатель на месте модели: отвечает по очереди, последний повторяет."""

    def __init__(self, *bodies: str) -> None:
        self.bodies = list(bodies)
        self.seen: list[Request] = []

    async def write(self, request: Request) -> Written:
        self.seen.append(request)
        body = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        return Written(body=body, needs_human=False, reason=None, tokens=WRITER_TOKENS)


async def lead_replied(session: AsyncSession) -> int:
    """Переписка продаж с ответом лида, база знаний, отправитель, настройки агента."""
    await world(session)
    found = await sales_world(session, status=MessageStatus.SENT)
    await AgentSettingsRepository(session).save(Stage.SALES, parts.DEFAULTS, author="тест")
    return found.reply.id


async def stored(session: AsyncSession, reply_id: int) -> AgentDraftModel:
    draft = await session.scalar(
        select(AgentDraftModel).where(AgentDraftModel.reply_id == reply_id)
    )
    assert draft is not None
    await session.refresh(draft)
    return draft


async def spent(session: AsyncSession) -> list[tuple[str, int]]:
    rows = await session.execute(
        select(UsageRecordModel.operation, UsageRecordModel.units).order_by(UsageRecordModel.id)
    )
    return [(operation, units) for operation, units in rows]


def test_sales_row_is_built_from_the_sales_parts() -> None:
    row = stages.AGENT_STAGES[Stage.SALES]

    assert row is SALES_STAGE
    assert (row.price, row.defaults) == (PriceSide.SELL, parts.DEFAULTS)
    assert (row.prompt, row.prompt_version, row.model) == (
        parts.PROMPT,
        parts.PROMPT_VERSION,
        llm_cfg.SALES_DRAFT_MODEL,
    )
    assert (row.usage_operation, row.guard_operation) == ("sales_draft", "sales_judge")
    assert (row.brief, row.guard) == (parts.brief, parts.guard)
    assert (row.autopilot, row.on_draft, row.max_rewrites) == (False, parts.on_draft, 3)
    assert (row.title, row.lead) == (parts.TITLE, parts.LEAD)
    assert (row.reject_reasons, row.strict_reasons) == (parts.REJECT_REASONS, True)


#: Что знает реестр в чистом процессе: есть ли строка продаж и чьи операции считает
#: потолок черновиков агента.
_REGISTRY = (
    "from backend.features.agent.stages import AGENT_STAGES, SALES_STAGE, agent_operations\n"
    "from backend.features.core.domain import Stage\n"
    "print(AGENT_STAGES.get(Stage.SALES) is SALES_STAGE, sorted(agent_operations()))\n"
)


def test_sales_agent_switch_is_off_by_default() -> None:
    assert sales_cfg._Sales.model_fields["agent_enabled"].default is False


@pytest.mark.parametrize(
    ("switch", "printed"),
    [
        ("false", "False ['agent_draft']"),
        ("true", "True ['agent_draft', 'sales_draft', 'sales_judge', 'sales_situation']"),
    ],
)
def test_sales_joins_the_registry_only_when_switched_on(switch: str, printed: str) -> None:
    """Слово владельца: агент продаж в реестре этапов не включён, пока тумблер
    `SALES_AGENT_ENABLED` выключен; включён — строка продаж в реестре, и её
    операции считает свой потолок черновиков агента."""
    done = subprocess.run(
        [sys.executable, "-c", _REGISTRY],
        cwd=ROOT,
        env={**os.environ, "SALES_AGENT_ENABLED": switch},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == printed


async def test_drafts_cap_stops_the_situation_and_counts_its_spend(
    session: AsyncSession, llm: Plug, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ситуация письма — первый вызов модели черновика продаж, до писателя: свой
    дневной потолок черновиков агента проверяется до неё и считает её расход
    (операция брифа этапа), иначе бриф тратил бы мимо потолка черновиков."""
    model = llm(situation=[{"situation": "ack", "confidence": 0.9}])
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", 2 * TOKENS)

    for _ in range(2):  # ровно потолок — расходом одной ситуации
        await situation.classify(session, talk((False, LETTER)), tags=())
    await session.flush()
    with pytest.raises(LlmCapExceededError, match="потолок черновиков агента"):
        await situation.classify(session, talk((False, LETTER)), tags=())

    assert len(model.sent["situation"]) == 2  # третий раз модель не звали


@pytest.mark.parametrize(
    "module",
    [
        "backend.features.agent.stages",
        "backend.features.sales.agent.brief",
        "backend.features.sales.agent.judge",
        "backend.workers.agent_jobs",
    ],
)
def test_seam_and_sales_import_first_in_a_clean_process(module: str) -> None:
    """Шов, собирая реестр, берёт части продаж, а бриф и судья продаж — типы шва.
    Первый импорт любой стороны в чистом процессе не должен замкнуть круг."""
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


async def test_lead_question_gets_a_judged_draft_signed_by_the_sales_persona(
    session: AsyncSession, llm: Plug
) -> None:
    reply_id = await lead_replied(session)
    llm(situation=[INFORM], judge=[ALLOW])
    writer = Writer(GOOD)

    outcome = await drafting.draft_answer(session, writer, reply_id)

    assert outcome.status is DraftStatus.DRAFTED
    [request] = writer.seen
    assert (request.sign_as, request.prompt, request.model) == (
        NAME,
        parts.PROMPT,
        llm_cfg.SALES_DRAFT_MODEL,
    )
    assert f"[cta call] {CALL}" in request.facts
    assert request.facts[0].startswith("[move inform]")
    draft = await stored(session, reply_id)
    assert (draft.body, draft.prompt_version, draft.model) == (
        GOOD,
        parts.PROMPT_VERSION,
        llm_cfg.SALES_DRAFT_MODEL,
    )
    assert (draft.meta["situation"], draft.meta["move"], draft.meta["language"]) == (
        "asks_info",
        "inform",
        "en",
    )
    assert draft.meta["attempts"] == [{"attempt": 0, "verdict": "allow", "reasons": []}]
    assert await spent(session) == [
        ("sales_situation", TOKENS),
        ("sales_draft", WRITER_TOKENS),
        ("sales_judge", TOKENS),
    ]


async def test_sum_not_in_the_base_goes_back_to_the_writer_and_comes_out_without_it(
    session: AsyncSession, llm: Plug
) -> None:  # 3.3 A1
    reply_id = await lead_replied(session)
    llm(situation=[INFORM], judge=[ALLOW])
    writer = Writer(BAD, GOOD)

    outcome = await drafting.draft_answer(session, writer, reply_id)

    assert outcome.status is DraftStatus.DRAFTED
    first, second = writer.seen
    why = "сумма 500 не из базы — суммы называем только из базы знаний, даже если собеседник назвал свою: уберите её или возьмите из фактов"
    assert (first.corrections, second.corrections, second.previous) == ((), (why,), BAD)
    draft = await stored(session, reply_id)
    assert draft.body == GOOD
    assert [attempt["verdict"] for attempt in draft.meta["attempts"]] == ["block", "allow"]


async def test_three_failed_rewrites_go_to_a_human_with_the_attempts(
    session: AsyncSession, llm: Plug
) -> None:  # 3.3 A6
    reply_id = await lead_replied(session)
    llm(situation=[INFORM], judge=[ALLOW])

    outcome = await drafting.draft_answer(session, Writer(BAD), reply_id)

    assert outcome.status is DraftStatus.ESCALATED
    draft = await stored(session, reply_id)
    assert draft.reason is not None
    assert draft.reason.startswith("судья не пропустил черновик и после 3 правок")
    assert [attempt["attempt"] for attempt in draft.meta["attempts"]] == [0, 1, 2, 3]


async def test_judge_that_could_not_check_hands_the_draft_to_a_human(
    session: AsyncSession, llm: Plug
) -> None:  # 3.3 A5
    reply_id = await lead_replied(session)
    llm(situation=[INFORM], judge=["Всё хорошо."])

    outcome = await drafting.draft_answer(session, Writer(GOOD), reply_id)

    assert outcome.status is DraftStatus.ESCALATED
    draft = await stored(session, reply_id)
    assert draft.body == GOOD  # человек видит, что не проверено
    assert draft.reason == "судья: судья-модель ответила не по форме — черновик не проверен"


async def test_thanks_costs_only_the_situation(session: AsyncSession, llm: Plug) -> None:  # A2
    reply_id = await lead_replied(session)
    llm(situation=[{"situation": "ack", "confidence": 0.95}], judge=[ALLOW])
    writer = Writer(GOOD)

    outcome = await drafting.draft_answer(session, writer, reply_id)

    assert outcome.status is DraftStatus.SKIPPED
    assert writer.seen == []
    assert await spent(session) == [("sales_situation", TOKENS)]
