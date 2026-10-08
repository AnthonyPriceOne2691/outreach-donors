"""Реестр этапов агента и таблица черновиков: шов, на который встают этапы.

Проверяется то, что обещано соседнему модулю и чего не видно по зелёному
прогону: у первых двух этапов части прежние, и запрос к модели без брифа —
тот же, что до шва; факты брифа, промпт и модель этапа доходят до модели;
таблица черновиков откатывается и накатывается со своим типом статуса.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import llm as llm_cfg
from backend.features.agent import settings, stages
from backend.features.agent import writer as agent_writer
from backend.features.agent.stages import AGENT_STAGES, PriceSide
from backend.features.core.domain import Stage
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration
from tests.test_agent_writer import Model, _request, _write

_DRAFT = {"body": "Thanks!", "needs_human": False, "reason": ""}


class TestFirstStagesUnchanged:
    def test_registry_keeps_their_parts(self) -> None:
        assert set(AGENT_STAGES) == {Stage.DONORS, Stage.ADVERTISERS}
        for stage, price in ((Stage.DONORS, PriceSide.BUY), (Stage.ADVERTISERS, PriceSide.SELL)):
            parts = AGENT_STAGES[stage]
            assert parts.price is price
            assert parts.defaults == settings.defaults(stage)
            assert (parts.prompt, parts.prompt_version) == (
                agent_writer.PROMPT_PATH,
                agent_writer.PROMPT_VERSION,
            )
            assert (parts.model, parts.usage_operation) == (llm_cfg.AGENT_MODEL, "agent_draft")
            assert parts.brief is stages.no_brief
            assert (parts.autopilot, parts.guard, parts.on_draft) == (False, None, None)

    async def test_request_without_brief_is_what_it_was(self) -> None:
        """Без брифа в модель уходит то же, что до шва: ни ключа фактов, ни
        другого промпта, ни другой модели."""
        model = Model(_DRAFT)

        await _write(model)

        [sent] = model.sent
        assert sent["model"] == "gpt-5"
        assert sent["messages"][0]["content"] == agent_writer.load_prompt()
        facts = json.loads(sent["messages"][1]["content"].split("\n")[1])
        assert set(facts) == {"stage", "settings", "sign_as", "parsed_from_last_message"}


async def test_brief_facts_stage_prompt_and_model_reach_the_model(tmp_path: Path) -> None:
    prompt = tmp_path / "stage.md"
    prompt.write_text("Stage prompt: use the facts.\n", encoding="utf-8")
    model = Model(_DRAFT)
    request = replace(
        _request(), facts=("Аудит — цена на созвоне",), prompt=prompt, model="stage-model"
    )

    await _write(model, request)

    [sent] = model.sent
    assert sent["model"] == "stage-model"
    assert sent["messages"][0]["content"] == "Stage prompt: use the facts."
    facts = json.loads(sent["messages"][1]["content"].split("\n")[1])
    assert facts["facts"] == ["Аудит — цена на созвоне"]


def _drafts_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / (
        "backend/migrations/versions/7ccbaf6d840a_agent_drafts.py"
    )
    spec = importlib.util.spec_from_file_location("agent_drafts_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _drafts_table(connection: Connection) -> tuple[bool, bool, list[str]]:
    def exists() -> bool:
        return connection.execute(text("SELECT to_regclass('agent_drafts')")).scalar() is not None

    migration = _drafts_migration()
    # Журнал сообщений продаж ссылается на черновик — снимается первым, как по цепочке.
    notices = load_migration("d64e2cd71614_sales_draft_notices.py")
    with Operations.context(MigrationContext.configure(connection)):
        notices.downgrade()
        migration.downgrade()
        down = exists()
        migration.upgrade()
        notices.upgrade()
    statuses = connection.execute(
        text("SELECT unnest(enum_range(NULL::draftstatus))::text")
    ).scalars()
    return down, exists(), list(statuses)


async def test_drafts_migration_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()
    down, up, statuses = await connection.run_sync(_drafts_table)

    assert (down, up) == (False, True)
    assert statuses == ["drafted", "skipped", "escalated", "sent", "rejected"]


def test_the_common_agent_seam_loads_only_the_parts_of_the_sales_agent() -> None:
    """Общий шов агента — его грузят воркер доноров и API — берёт от агента продаж только `parts`:
    константы, пин и настройки. Бриф, судья и сообщения о черновиках — лениво, первым вопросом:
    сломанный модуль агента продаж не должен ронять общий воркер (ревью соседней сессии к #224).
    Чистый интерпретатор — то, что грузит импорт шва, а не то, что успели загрузить тесты."""
    code = (
        "import sys, backend.features.agent.stages; "
        "print(sorted(m for m in sys.modules if m.startswith('backend.features.sales.agent')))"
    )
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=root
    )
    loaded = done.stdout.strip()
    assert loaded == "['backend.features.sales.agent', 'backend.features.sales.agent.parts']", (
        loaded + done.stderr
    )
