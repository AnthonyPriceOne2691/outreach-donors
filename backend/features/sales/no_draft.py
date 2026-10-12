"""Почему агент продаж не напишет черновик, хотя путь ответа его даёт, — словами у ответа.

Путь «ответит агент» (`reply_routes.DRAFTED`) черновик обещает, а ставит его шов агента
(`agent_jobs.after_parse` → `drafting.wants_draft`). Шов молча не ставит задачу, когда диалог
закрыт или агента этапа нет в его настройках. Ответ тогда говорил «ответит агент; пока —
человек», и человек ждал черновика, которого не будет (ревью стыков, круг 2):

- **диалог закрыт раньше** — лид сказал «не сейчас» или «не интересно» (`replies._close`), назвал
  другого (`referral.refer`), а теперь пишет снова: шов в закрытый диалог не пишет. Вернуть ли
  агенту такого лида — решение владельца;
- **тумблер включён, а настроек нет** — `SALES_AGENT_ENABLED` ставит строку продаж в реестр
  этапов, но черновик пишется по версии настроек этапа, а её сохраняют на экране агента;
- **агент выключен в настройках** на экране.

**Решает по-прежнему шов**: здесь только слова, а условия — те же, что у `drafting._refusal`.
Ответ, которому слова названы, черновика не ждёт (`replies.Handled.drafted`), а шов и так его не
поставил бы. Агента продаж нет в реестре (тумблер выключен) — слов нет: прежние «ответит агент;
пока — человек» и есть это состояние по умолчанию, и настройки тогда не читаются.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.agent import stages
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.core.domain import Stage, ThreadStatus
from backend.features.core.models.outreach import ReplyModel, ThreadModel

#: Диалог закрыт раньше: лид сказал «не сейчас», «не интересно» или «пишите другому».
CLOSED = (
    "диалог закрыт раньше («не сейчас», «не интересно» или «пишите другому») — в закрытый "
    "диалог агент не пишет; отвечает человек"
)
#: Тумблер включён, а настройки агента продаж не сохранены ни разу.
NOT_SET = (
    "агент продаж не настроен — сохраните настройки «Агент переписки» → «Лидам»; отвечает человек"
)
#: Агент продаж выключен в своих настройках на экране.
OFF_ON_SCREEN = "агент продаж выключен в настройках «Лидам» — отвечает человек"


async def absent(session: AsyncSession, reply: ReplyModel) -> str | None:
    """Почему шов не поставит черновик этому ответу — словами; `None` — поставит (или агента
    продаж нет в реестре: тогда слов нет, см. шапку)."""
    if Stage.SALES not in stages.AGENT_STAGES:
        return None
    thread = await session.get(ThreadModel, reply.thread_id) if reply.thread_id else None
    if thread is not None and thread.status is ThreadStatus.CLOSED:
        return CLOSED
    settings = await AgentSettingsRepository(session).current(Stage.SALES)
    if settings is None:
        return NOT_SET
    return None if settings.enabled else OFF_ON_SCREEN
