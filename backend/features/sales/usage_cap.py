"""Свой дневной потолок вызовов модели продаж вне агента — внутри общего, тем же журналом.

Общий потолок (`LLM_DAILY_TOKEN_CAP`) считается по всем операциям модели. Вид ответа лида и
сборка писем очереди на сотни лидов выбрали бы его целиком, и разбор цен доноров встал бы до
завтра. У продаж свой потолок (`SALES_DAILY_TOKEN_CAP`, не задан — доля общего
`SALES_CAP_SHARE`) по своим операциям; общий проверяется всегда (`usage.ensure_llm_within_cap`).

Черновик, ситуация письма и судья агента продаж сюда не входят: их держит потолок черновиков
агента (`agent/guarding.drafts_cap`).
"""

from __future__ import annotations

from backend.config import llm as llm_cfg
from backend.features.core import usage
from backend.features.sales.reply_kind import OPERATION as REPLY_KIND

#: Переписывание зон письма очереди продаж моделью — своя операция журнала расхода, а не
#: `letter_rewrite` доноров: под общим именем счёт по операциям их не различил бы, и расход
#: сборки продаж прошёл бы мимо своего потолка.
LETTER_REWRITE = "sales_letter_rewrite"

#: Вызовы модели продаж вне агента — их считает свой дневной потолок.
OPERATIONS = frozenset({REPLY_KIND, LETTER_REWRITE})


def sales_cap() -> usage.OwnCap:
    """Свой дневной потолок вызовов модели продаж: настройка, не задана — доля общего."""
    tokens = usage.share_cap(llm_cfg.SALES_DAILY_TOKEN_CAP, llm_cfg.SALES_CAP_SHARE)
    return usage.OwnCap("вызовов модели продаж", OPERATIONS, tokens, "SALES_DAILY_TOKEN_CAP")
