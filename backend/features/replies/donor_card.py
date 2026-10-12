"""Ответ донора — в карточку донора: цена со всем, что донор сказал рядом, и сам ответ.

Запросы репозитория ответов, которые меняют чужие записи — `donors` и `domains` —
по правилам `donors/price.py`: `ReplyRepository` наследует их отсюда, и вызывающие
зовут их у него, как прежде. Отдельным модулем с 12.10.2026: цена донора стала
шире одного числа (серая, оплата, срок, тип ссылки, номер ответа), и репозиторий
упёрся в потолок длины файла; шов — по смыслу: здесь всё, что ответ кладёт донору.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import PriceSource
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.donors.price import (
    NO_DETAILS,
    PriceDetails,
    put_answer,
    put_price,
    said_later,
)


def reply_details(reply: ReplyModel) -> PriceDetails:
    """Что донор сказал рядом с ценой — как оно лежит у ответа: после разбора
    (`pipeline._write_back`) или после решения человека (`repository.confirm`)."""
    return PriceDetails(
        white=reply.price_white,
        grey=reply.price_grey,
        payment_methods=tuple(reply.payment_methods or ()),
        placement_days=reply.placement_days,
        link_type=reply.link_type,
        reply_id=reply.id,
    )


class DonorCardWrites:
    """Цена и ответ донора — в его карточку (часть `ReplyRepository`)."""

    _session: AsyncSession

    async def store_price(
        self,
        *,
        domain_id: int,
        price: Decimal,
        currency: str | None,
        offers: list[dict[str, Any]] | None,
        at: datetime | None = None,
        details: PriceDetails = NO_DETAILS,
    ) -> bool:
        """Перенести цену в карточку донора — вместе со всеми ценами того же
        ответа (`offers`, как они лежат у ответа) и тем, что донор сказал рядом
        (`details`, `reply_details`). `at` — когда ответ пришёл (пусто — сейчас);
        слово донора новее не перебивается (`price.said_later`) — `False`.

        Цена кладётся в той валюте, в которой её назвали: конвертации
        в сервисе нет, и подписать евро долларами значит записать
        неверное число. Список и подробности — всегда от того же ответа, что цена,
        и пустые затирают прежние: оплата чужого ответа рядом с новой ценой врала бы.
        Источник — «из ответа», и заметка с автором ручной цены уходят с ней.
        """
        donor = await self._donor(domain_id)
        domain = await self._session.get(DomainModel, domain_id)
        at = at or datetime.now(UTC)
        if donor is None or said_later(donor, domain, at):
            return False
        put_price(
            donor,
            amount=price,
            currency=currency,
            at=at,
            source=PriceSource.REPLY,
            offers=offers,
            details=details,
        )
        return True

    async def record_seller_answer(
        self, *, domain_id: int, answer: str, reply_id: int | None, at: datetime
    ) -> bool:
        """Ответ самого донора — на домен, рядом с вердиктом судьи; `at` — когда ответ
        пришёл. Ответ старше лежащего на домене не ложится — `False`; «не продаём»
        и «бесплатно» снимают цену донора, названную не позже (`price.put_answer`).

        Вердикт судьи и решение человека не трогаются: по расхождению с ними
        считается, как часто отбор ошибается в главном для гест-постинга.
        """
        domain = await self._session.get(DomainModel, domain_id)
        if domain is None:
            return False
        donor = await self._donor(domain_id)
        return put_answer(domain, donor, answer=answer, reply_id=reply_id, at=at)

    async def _donor(self, domain_id: int) -> DonorModel | None:
        found: DonorModel | None = await self._session.scalar(
            select(DonorModel).where(DonorModel.domain_id == domain_id)
        )
        return found
