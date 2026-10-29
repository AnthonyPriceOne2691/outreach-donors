"""Свой дневной потолок вызовов модели продаж вне агента — внутри общего, тем же журналом.

Общий потолок (`LLM_DAILY_TOKEN_CAP`) считается по всем операциям модели. Вид ответа лида,
сборка писем очереди и судья сегмента сбора базы на сотни сайтов выбрали бы его целиком, и
разбор цен доноров встал бы до завтра. У продаж свой потолок (`SALES_DAILY_TOKEN_CAP`, не задан — доля общего
`SALES_CAP_SHARE`) по своим операциям; общий проверяется всегда (`usage.ensure_llm_within_cap`).

**Агент продаж — тоже здесь** (черновик, ситуация письма, судья — `agent/parts.py`): потолок
черновиков агента (`agent/guarding.drafts_cap`) общий с донорами, и черновики продаж выбирали бы
их долю (ревью стыков, круг 2). Свой потолок продаж их считает,
ситуация письма проверяет его до первого вызова модели черновика (`agent/situation.classify`), а
петля правки шва — перед каждым вызовом писателя: это свой потолок этапа у шва
(`SALES_STAGE.own_cap` — `agent/parts.cap`, тот же потолок, второго счёта нет). Пока лимиты
разделов на «Расходе» не сохранены, они считаются и в общем потолке черновиков — потолок двойной,
это безопасно; после сохранения продажи судит лимит их раздела, а не агента переписки
(`guarding.within_caps`, 29.10.2026), и без потолка агент продаж не остаётся. Цена: черновики и
разбор ответов делят один потолок продаж.
"""

from __future__ import annotations

from datetime import datetime

from backend.config import llm as llm_cfg
from backend.features.core import llm_caps, usage
from backend.features.sales.agent import parts
from backend.features.sales.reply_kind import OPERATION as REPLY_KIND
from backend.features.sales.segment_helper import OPERATION as SEGMENT_HELPER
from backend.features.sales.segment_judge import OPERATION as SEGMENT_JUDGE

#: Переписывание зон письма очереди продаж моделью — своя операция журнала расхода, а не
#: `letter_rewrite` доноров: под общим именем счёт по операциям их не различил бы, и расход
#: сборки продаж прошёл бы мимо своего потолка.
LETTER_REWRITE = "sales_letter_rewrite"

#: Агент продаж: черновик, ситуация письма, судья.
AGENT = frozenset({parts.DRAFT_OPERATION, parts.SITUATION_OPERATION, parts.JUDGE_OPERATION})
#: Вызовы модели продаж — их считает свой дневной потолок; помощник описания сегмента — тоже.
OPERATIONS = frozenset({REPLY_KIND, LETTER_REWRITE, SEGMENT_JUDGE, SEGMENT_HELPER}) | AGENT


def sales_cap() -> usage.OwnCap:
    """Свой дневной потолок вызовов модели продаж: настройка, не задана — доля общего."""
    tokens = usage.share_cap(llm_cfg.SALES_DAILY_TOKEN_CAP, llm_cfg.SALES_CAP_SHARE)
    return usage.OwnCap("вызовов модели продаж", OPERATIONS, tokens, "SALES_DAILY_TOKEN_CAP")


def after_spent(now: datetime) -> str:
    """Что делать, когда смета модели продаж выше остатка дневного потолка, — словами сторожа
    модели (`core/usage`): лимиты частей на «Расходе» сохранены — добавить продажам из
    свободного остатка или поднять общий (`llm_caps.after_part`), не сохранены — поднять потолок
    (`llm_caps.after_cap`); или дождаться сброса. Блок — в памяти процесса: смета перечитывает
    его вместе с остатком (`segment_estimate.tokens_left`)."""
    return llm_caps.after_part(now) if llm_caps.block() is not None else llm_caps.after_cap(now)
