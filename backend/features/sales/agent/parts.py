"""Части агента продаж для строки `Stage.SALES` реестра этапов шва.

Строку собирает шов (`agent/stages.SALES_STAGE`) из того, что здесь: умолчания
настроек, промпт черновика и его версия, операции расхода, бриф и судья, имя этапа
на экране настроек агента. В реестр этапов строка встаёт только по тумблеру
`SALES_AGENT_ENABLED` (по умолчанию выключен).

**Круг импорта разорван здесь.** Бриф и судья продаж стоят на типах шва
(`Brief`, `Verdict`, …), а шов, собирая реестр, берёт этот модуль. Поэтому
модуль не импортирует ни шов, ни бриф, ни судью при загрузке: типы — только для
проверки типов, бриф и судья — в момент вызова. Шов можно загрузить первым, и
бриф — тоже (тест в чистом процессе).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from backend.config import llm as llm_cfg
from backend.features.agent.settings import AgentSettings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.agent.stages import Brief, Conversation, GuardInput, Verdict

#: Промпт черновика ответа лиду — файлом в package-data.
PROMPT = Path(__file__).with_name("prompts") / "reply.md"
#: Меняется при каждой правке промпта: черновик хранит, по какой версии написан.
PROMPT_VERSION = "sales-reply-v1"
#: Модель черновика — пин продаж (`LLM_SALES_DRAFT_MODEL`): письмо живому человеку.
MODEL = llm_cfg.SALES_DRAFT_MODEL
#: Операции расхода: черновик — писатель шва, судья — вызов модели в судье,
#: ситуация письма — вызов модели в брифе. Все три — в своём потолке черновиков.
DRAFT_OPERATION = "sales_draft"
JUDGE_OPERATION = "sales_judge"
SITUATION_OPERATION = "sales_situation"
#: Правок по замечаниям судьи, прежде чем отдать черновик человеку (план судьи).
MAX_REWRITES = 3
#: Этап на экране настроек агента: кому агент пишет и что он там делает.
TITLE = "Лидам"
LEAD = (
    "Лидам продаж агент отвечает фактами из базы знаний и предлагает один следующий "
    "шаг — созвон или Telegram; цены — только по политике цен из базы."
)

#: С чего экран предлагает начать настройки агента продаж. Тексты — правила
#: разговора, а не продажи: что и почём предлагаем, знает база знаний.
DEFAULTS = AgentSettings(
    enabled=True,
    goal=(
        "Ответить лиду по существу фактами из базы знаний и довести разговор "
        "до созвона или переписки в Telegram."
    ),
    tone="Вежливо, коротко и по делу, как пишет живой менеджер. Без давления и штампов.",
    points=(
        "Ответить на вопрос собеседника фактами из базы знаний.",
        "Предложить один следующий шаг: созвон или Telegram.",
        "Не сейчас — вежливо закрыть без давления.",
    ),
    price_limit_usd=None,
    stop_topics=(
        "Договор, счёт на компанию, юридические и налоговые вопросы",
        "Претензии, возвраты и споры об оплате",
        "Скидки и цены сверх политики цен из базы знаний",
    ),
)


async def brief(session: AsyncSession, conversation: Conversation) -> Brief:
    """Бриф продаж до письма (`brief.brief`)."""
    from backend.features.sales.agent import brief as briefing  # noqa: PLC0415 — круг импорта

    return await briefing.brief(session, conversation)


async def guard(check: GuardInput) -> Verdict:
    """Судья черновика продаж (`judge.guard`)."""
    from backend.features.sales.agent import judge  # noqa: PLC0415 — круг импорта

    return await judge.guard(check)
