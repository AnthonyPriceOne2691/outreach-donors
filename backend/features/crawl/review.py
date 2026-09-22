"""Ручная проверка пограничных кандидатов: очередь и решение.

Порядок работы живёт здесь, а не в маршруте и не в команде. Экран
и консоль делают одно и то же, и когда правила разойдутся — а они
разойдутся, — разойдутся они молча.

**Экран нужен не для удобства.** Требование ограничивает долю ложных
рекламодателей десятью процентами, а скоринг выносит вердикт по пяти
признакам, два из которых выведены из замера на одной нише. Без
человека, который смотрит пограничные, эта доля не измерена и не
удержана.

**Решение человека сильнее вердикта скоринга и не переписывает его.**
Балл остаётся как был: по расхождению между ним и решением и считается,
как часто скоринг ошибается. Перезаписав балл, мы получили бы базу,
по которой он всегда прав.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel

logger = logging.getLogger(__name__)

#: Сколько кандидатов отдаём за раз. Очередь ручной проверки читают
#: глазами, и страница на тысячу строк — это не очередь, а свалка.
PAGE_SIZE = 50


class UnknownCandidateError(ValueError):
    """Кандидата нет. Отдельный тип: маршрут отвечает 404, а не 500."""


class AlreadyDecidedError(ValueError):
    """Решение по кандидату уже принято.

    Отказ, а не тихая перезапись: два человека, открывшие одну очередь,
    иначе затрут решения друг друга и не узнают об этом. Передумать
    можно — но явно, а не случайным вторым нажатием.
    """


async def queue(
    session: AsyncSession, *, limit: int = PAGE_SIZE, include_decided: bool = False
) -> list[CandidateModel]:
    """Пограничные кандидаты, которых ещё никто не смотрел.

    Порядок — по баллу вниз: сначала те, что ближе всего к «куплена»,
    потому что ошибка на них дороже.
    """
    query = (
        select(CandidateModel)
        .where(CandidateModel.verdict == Verdict.PENDING)
        .order_by(CandidateModel.points.desc(), CandidateModel.id)
        .limit(limit)
    )
    if not include_decided:
        query = query.where(CandidateModel.confirmed.is_(None))
    return list((await session.execute(query)).scalars().all())


async def waiting(session: AsyncSession) -> int:
    """Сколько пограничных ждёт человека. Число с главной."""
    query = (
        select(func.count())
        .select_from(CandidateModel)
        .where(CandidateModel.verdict == Verdict.PENDING, CandidateModel.confirmed.is_(None))
    )
    return int((await session.execute(query)).scalar_one())


async def counts(session: AsyncSession) -> dict[str, int]:
    """Сколько кандидатов с каждым вердиктом. Для шапки экрана.

    Отсеянные показываются наравне с принятыми намеренно: по ним видно,
    что список «кому не пишем» работает, а не молчит.
    """
    rows = (
        await session.execute(
            select(CandidateModel.verdict, func.count()).group_by(CandidateModel.verdict)
        )
    ).all()
    return {verdict.value: number for verdict, number in rows}


async def decide(
    session: AsyncSession,
    candidate_id: int,
    *,
    confirmed: bool,
    by: str,
    force: bool = False,
) -> CandidateModel:
    """Записать решение человека по кандидату.

    `force` нужен, чтобы передумать: без него повторное решение —
    отказ, потому что два человека в одной очереди затрут друг друга
    и не заметят.
    """
    row = await session.get(CandidateModel, candidate_id)
    if row is None:
        raise UnknownCandidateError(f"кандидата {candidate_id} нет")
    if row.confirmed is not None and not force:
        raise AlreadyDecidedError(
            f"{row.target_root}: решение уже принято ({row.decided_by}) — "
            "чтобы изменить его, нужно сказать об этом явно"
        )

    row.confirmed = confirmed
    row.decided_by = by
    row.decided_at = datetime.now(UTC)
    logger.info(
        "кандидат %s (%s): %s, решил %s; балл скоринга %s остался прежним",
        row.target_root,
        row.donor_host,
        "подтверждён" if confirmed else "отклонён",
        by,
        row.points,
    )
    return row
