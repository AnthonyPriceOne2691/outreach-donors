"""Калибровка разбора: что предложила модель против того, что решил человек.

Приём соседней системы, где он работал в бою: там в таблицу ложилось,
что модель предложила ответить и что оператор сделал в той же ситуации, —
и по этой статистике правили поведение агента. Здесь то же для разбора
условий: снимок модели (`replies.model_parse`) против полей, которые
подтвердил человек в очереди цены. Там этого для полей условий не было.

**Считается только там, где смотрел человек.** Автоматически положенная
цена здесь не судится: сверить её не с чем, и её доля — отдельное число,
а не «точность».

**По версиям промпта.** Правка промпта без метки версии была бы неотличима
от смены писем; сравнивать версии и есть назначение этого счёта.

**Перекрытый ответ человека не ждёт** (`threads.superseded_by`) — и здесь тоже:
решения по нему не будет, цену переписки задаёт более поздний ответ. Иначе
«ждут человека» росло бы навсегда и расходилось с числом у меню (проверка прода
10.10.2026).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.outreach.threads import superseded_by
from backend.features.replies.outcome import waiting_for_review

logger = logging.getLogger(__name__)

#: Поля, по которым модель сверяется с человеком.
FIELDS: tuple[str, ...] = ("price_white", "price_grey", "currency", "placement")


@dataclass(slots=True)
class VersionScore:
    """Итог одной версии промпта."""

    version: str
    reviewed: int = 0
    as_is: int = 0
    edited: int = 0
    #: Поле → сколько раз человек его поправил.
    wrong: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    #: Цена положена сама, без человека — сверять не с чем.
    auto_stored: int = 0
    #: Ждут человека: разбор неуверенный, решения ещё нет.
    waiting: int = 0


def _money(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        logger.warning("калибровка: в снимке модели не число — %r", raw)
        return None


def human_placement(reply: ReplyModel) -> str:
    """Что сказал человек о продаже: цена — продаёт, «не продаёт» — отказ."""
    if reply.placement == "declines":
        return "declines"
    if reply.price_white is not None or reply.price_grey is not None:
        return "sells"
    return reply.placement or "unclear"


def differences(reply: ReplyModel) -> list[str]:
    """Какие поля человек поправил относительно снимка модели."""
    model = reply.model_parse or {}
    human = {
        "price_white": reply.price_white,
        "price_grey": reply.price_grey,
        "currency": (reply.currency or "").upper() or None,
        "placement": human_placement(reply),
    }
    proposed = {
        "price_white": _money(model.get("price_white")),
        "price_grey": _money(model.get("price_grey")),
        "currency": (model.get("currency") or "").upper() or None,
        "placement": model.get("placement") or "unclear",
    }
    if proposed["placement"] == "free" and human["placement"] == "unclear":
        # «Бесплатно» человек подтверждает пустыми полями цены — это согласие.
        human["placement"] = "free"
    return [name for name in FIELDS if proposed[name] != human[name]]


async def calibrate(session: AsyncSession) -> list[VersionScore]:
    """Счёт по версиям промпта, свежая версия первой."""
    rows = await session.execute(
        select(ReplyModel)
        .where(ReplyModel.kind == ReplyKind.HUMAN)
        .where(ReplyModel.model_parse.is_not(None))
    )
    replies = rows.scalars().all()
    moot = await _superseded(session, replies)
    scores: dict[str, VersionScore] = {}
    order: defaultdict[str, int] = defaultdict(int)
    for reply in replies:
        if (reply.model_parse or {}).get("stage") == Stage.SALES.value:
            continue  # снимок вида ответа продаж: цены в нём нет, судит свой eval
        version = str((reply.model_parse or {}).get("prompt_version") or "без версии")
        score = scores.setdefault(version, VersionScore(version))
        order[version] = max(order[version], reply.id)
        if reply.reviewed_at is None:
            _unreviewed(score, reply, superseded=reply.id in moot)
            continue
        score.reviewed += 1
        wrong = differences(reply)
        if wrong:
            score.edited += 1
            for name in wrong:
                score.wrong[name] += 1
        else:
            score.as_is += 1
    return sorted(scores.values(), key=lambda s: -order[s.version])


def _unreviewed(score: VersionScore, reply: ReplyModel, *, superseded: bool) -> None:
    """Ответ без решения человека: ждёт его, перекрыт более поздним или цена легла сама."""
    if waiting_for_review(reply.kind, reply.confidence, reviewed=False):
        if not superseded:
            score.waiting += 1
    elif reply.price_white is not None or reply.price_grey is not None:
        score.auto_stored += 1


async def _superseded(session: AsyncSession, replies: Sequence[ReplyModel]) -> set[int]:
    """Номера ждущих ответов, перекрытых более поздним с принятой ценой.

    Ответы переписки — все, а не только разобранные моделью: цену переписки
    задаёт и подтверждённая человеком (`threads.settled_price`). Читаются только
    переписки, где кто-то ждёт, — их единицы.
    """
    waiting = [
        reply
        for reply in replies
        if reply.reviewed_at is None
        and reply.thread_id is not None
        and waiting_for_review(reply.kind, reply.confidence, reviewed=False)
    ]
    if not waiting:
        return set()
    rows = await session.execute(
        select(ReplyModel).where(ReplyModel.thread_id.in_({r.thread_id for r in waiting}))
    )
    by_thread: defaultdict[int | None, list[ReplyModel]] = defaultdict(list)
    for reply in rows.scalars().all():
        by_thread[reply.thread_id].append(reply)
    return {r.id for r in waiting if superseded_by(r, by_thread[r.thread_id]) is not None}
