"""Предел ходов агента продаж в диалоге: черновик — на первые ответы лида, дальше — человек.

ТЗ, строка MVP: «ИИ-агент ведёт первый ответ в диалоге, максимум второй»; следующий этап —
«каждый следующий ход включаем, когда агент надёжно справляется с ним на живых диалогах».
Сколько ходов — настройка `SALES_AGENT_MAX_TURNS` (2).

**Ход — ответ лида по порядку в этом диалоге**: письма человека (`ReplyKind.HUMAN`) с номером
не больше этого. Это тот же ход, что агент видит в переписке (`agent/situation.turn_of`,
`meta["turn"]` черновика), только по всей переписке, а не по окну последних писем брифа.
Автоответ и отписка ходом не считаются: на них агент не пишет. Меряется по письмам лида, а не
по черновикам агента: черновика могло не быть (агент выключен, человек ответил сам), а третий
ответ лида всё равно глубже в разговоре, чем агент проверен; калибровка судит агента по тому
же номеру хода.

Предел смотрит задача ответа (`replies._no_draft`): черновика нет — модель агента не зовётся,
ответ ждёт человека с причиной словами. Черновик по кнопке человека предел не держит: это решение
человека, а не ход агента.

**Настройку разбирает задача, а не импорт** (`max_turns`): настройки продаж импортирует и общий
код, и опечатка в них не должна ронять его на старте (шапка `config/sales.py`). Негодный предел —
черновика нет, причина словами у ответа и строка в журнал: вид ответа уже оплачен, и упавшая
задача позвала бы модель снова, а агент без проверенного предела не пишет.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.config.startup_checks import ConfigError
from backend.features.core.domain import ReplyKind
from backend.features.core.models.outreach import ReplyModel

logger = logging.getLogger(__name__)

#: Предел ходов — от одного до десяти: ноль — агент не вёл бы ни одного ответа, больше — опечатка.
FEWEST, MOST = 1, 10
#: Сколько ответов агент уже вёл — словами для причины (предел — от 1 до 10).
_LED = {
    1: "один ответ",
    2: "два ответа",
    3: "три ответа",
    4: "четыре ответа",
    5: "пять ответов",
    6: "шесть ответов",
    7: "семь ответов",
    8: "восемь ответов",
    9: "девять ответов",
    10: "десять ответов",
}


async def turn_of(session: AsyncSession, reply: ReplyModel) -> int:
    """Какой это ответ лида по счёту в диалоге: письма человека с номером не больше этого."""
    counted = await session.scalar(
        select(func.count())
        .select_from(ReplyModel)
        .where(
            ReplyModel.thread_id == reply.thread_id,
            ReplyModel.kind == ReplyKind.HUMAN,
            ReplyModel.id <= reply.id,
        )
    )
    return int(counted or 0)


def led(turns: int) -> str:
    """Причина словами: агент уже вёл столько ответов — дальше человек."""
    return f"агент уже вёл {_LED.get(turns, f'{turns} ответов')} — дальше отвечает человек"


def max_turns() -> int:
    """Предел ходов из `SALES_AGENT_MAX_TURNS` — целое от одного до десяти; иное — `ConfigError`
    словами."""
    raw = cfg.AGENT_MAX_TURNS
    number = int(raw) if raw.isdecimal() else 0
    if not FEWEST <= number <= MOST:
        raise ConfigError(
            f"SALES_AGENT_MAX_TURNS=«{raw}» — нужно целое от {FEWEST} до {MOST}: сколько ответов "
            "лида в диалоге агент ведёт черновиком"
        )
    return number


async def beyond(
    session: AsyncSession, reply: ReplyModel, *, limit: int | None = None
) -> str | None:
    """Почему агент этот ответ не ведёт — словами; `None` — ведёт (ход не дальше предела).
    Негодная настройка предела — тоже словами: черновика нет (шапка модуля)."""
    if limit is None:
        try:
            limit = max_turns()
        except ConfigError as exc:
            logger.error(  # noqa: TRY400 — трассировка разбора ничего не добавит к словам
                "продажи: предел ходов агента негоден — черновика нет, отвечает человек",
                extra={"reply": reply.id, "error": str(exc)},
            )
            return f"{exc}; черновика агента нет — отвечает человек"
    if reply.thread_id is None or await turn_of(session, reply) <= limit:
        return None
    return led(limit)
