"""Поиск адресов платит провайдеру — и журнал расхода это видит.

До 04.10.2026 платная ступень лестницы (Hunter) в журнал не писалась: на
вопрос «сколько запросов ушло на поиск» отвечала только квота в кабинете
провайдера, без разбивки по проходам. Проверяется на настоящей базе: запрос,
который провайдер принял, — строка журнала под Hunter; отказ провайдера
(квота, закрытая учётка) запросом не считается.
"""

from __future__ import annotations

import httpx
import pytest
from backend.features.contacts.provider import ProviderQuotaError
from backend.features.contacts.search import CONTACTS_OPERATION, search_contacts
from backend.features.core.domain import UsageProvider
from backend.features.core.models.ops import UsageRecordModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_contacts_no_answer import NOTHING, Paid, Site, _mx_is_fine

__all__ = ["_mx_is_fine"]  # почта у доменов есть — иначе спуск до платной ступени не дойдёт


class Refusing(Paid):
    """Провайдер, у которого кончилась квота: отказ, а не «не нашли»."""

    async def find_emails(self, host: str) -> list:  # type: ignore[type-arg]
        self.calls.append(host)
        raise ProviderQuotaError("квота исчерпана")


async def _search(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, provider: Paid, *hosts: str
) -> None:
    async def paid_step(_http: httpx.AsyncClient, _report: object) -> Paid:
        return provider

    site = Site({"/": NOTHING})
    monkeypatch.setattr("backend.features.contacts.search._paid_step", paid_step)
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(site)),
    )
    for host in hosts:
        await make_donor(session, host)
    await search_contacts(session, limit=10)


async def _journal(session: AsyncSession) -> tuple[int | None, set[UsageProvider]]:
    units = await session.scalar(
        select(func.sum(UsageRecordModel.units)).where(
            UsageRecordModel.operation == CONTACTS_OPERATION
        )
    )
    providers = set(
        (
            await session.scalars(
                select(UsageRecordModel.provider).where(
                    UsageRecordModel.operation == CONTACTS_OPERATION
                )
            )
        ).all()
    )
    return units, providers


async def test_each_accepted_provider_call_is_journaled_under_hunter(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    paid = Paid()

    await _search(session, monkeypatch, paid, "one.example.test", "two.example.test")

    assert len(paid.calls) == 2  # сайты без адресов — оба дошли до платной ступени
    assert await _journal(session) == (2, {UsageProvider.HUNTER})


async def test_provider_refusal_is_not_a_paid_call(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    refusing = Refusing()

    await _search(session, monkeypatch, refusing, "one.example.test")

    assert refusing.calls  # ступень дошла до провайдера
    assert await _journal(session) == (None, set())
