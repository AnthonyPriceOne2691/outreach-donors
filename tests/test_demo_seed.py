"""Демонстрация на тестовой базе: заводится, показывает цены списком, убирается.

Демонстрационные данные рисуют экраны раньше живых писем (`cli/demo_data.py`),
и до 06.10.2026 их не заводил ни один тест: сломанная демонстрация видна была
только на снимке экрана. Здесь — то, ради чего в неё добавлен список цен:
под ценой ответа стоят все цены письма, и каждое число из текста письма.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from backend.cli import demo_data
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies.money import appears_in
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


async def _seed(session: AsyncSession) -> None:
    senders = await demo_data._seed_senders(session, NOW)
    await demo_data._seed_sent_today(session, senders, NOW)
    await demo_data._seed_queue(session, NOW)
    await demo_data._seed_threads(session, NOW)
    await session.commit()


async def test_priced_demo_reply_lists_every_price_of_its_text(session: AsyncSession) -> None:
    await _seed(session)

    priced = (
        (
            await session.execute(
                select(ReplyModel)
                .where(ReplyModel.price_white.is_not(None))
                .order_by(ReplyModel.id)
            )
        )
        .scalars()
        .all()
    )

    assert priced
    for reply in priced:
        assert reply.offers
        # Та же проверка, что у разбора: числа нет в письме — выдумка.
        assert all(appears_in(Decimal(offer["price"]), reply.raw_body) for offer in reply.offers)
    both = priced[0]
    assert [(offer["product"], offer["price"], offer["period"]) for offer in both.offers or []] == [
        ("размещение статьи", "250", None),
        ("размещение статьи с пометкой «партнёрский материал»", "180", None),
        ("ссылка в опубликованной статье", "120", None),
        ("ссылка на главной", "300", "month"),
    ]
    assert len(priced[1].offers or []) == 3


async def test_clear_removes_only_the_demo(session: AsyncSession) -> None:
    session.add(DomainModel(host="real-donor.invalid"))
    await _seed(session)

    removed = await demo_data._clear(session)

    hosts = (await session.execute(select(DomainModel.host))).scalars().all()
    assert removed > 0
    assert hosts == ["real-donor.invalid"]
    assert (await session.execute(select(ReplyModel))).scalars().all() == []
