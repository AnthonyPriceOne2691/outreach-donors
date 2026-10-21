"""Падение воркера посреди пачки не стоит оплаченного ответа (ревью продаж 21.10.2026).

Пачка поиска адресов ждёт самый медленный сайт. Раньше исходы всей пачки ложились в базу
разом в её конце, и ответ платной ступени по быстрому домену до того жил только в памяти:
воркер упал — ответ потерян, а повтор задачи платил за тот же домен снова. Теперь исход
домена записывается и закрепляется, как только лестница по нему кончилась (`search._Keeper`).

Проверяется на настоящей базе: сосед по пачке не отвечает вовсе, задачу обрывают — исход
быстрого домена и его платный запрос в журнале уже лежат, и повтор его не берёт.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx
import pytest
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.repository import ContactRepository
from backend.features.contacts.search import search_contacts
from backend.features.core.domain import ContactStatus, UsageProvider
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_contacts_attempts import _state
from tests.test_contacts_no_answer import Paid, _mx_is_fine
from tests.test_contacts_usage import _journal

__all__ = ["_mx_is_fine"]  # почта у доменов есть — иначе спуск до платной ступени не дойдёт

FAST = "fast.example.test"
SLOW = "slow.example.test"


class Sites(httpx.AsyncBaseTransport):
    """Сайты без адреса на страницах. `SLOW`, пока не `answer()`, не отвечает вовсе: пачка
    ждёт его, а до платной ступени он не доходит — в счёте только быстрый сосед."""

    def __init__(self) -> None:
        self._slow_answers = asyncio.Event()

    def answer(self) -> Sites:
        self._slow_answers.set()
        return self

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host.removeprefix("www.") == SLOW:
            await self._slow_answers.wait()
        return httpx.Response(404, request=request)


def _install(monkeypatch: pytest.MonkeyPatch, provider: Paid, sites: Sites) -> None:
    async def paid_step(_http: httpx.AsyncClient, _report: object) -> Paid:
        return provider

    monkeypatch.setattr("backend.features.contacts.search.paid_step", paid_step)
    # Платная ступень доноров — только флагом (решение 12.10.2026), здесь она включена.
    monkeypatch.setattr("backend.config.contacts.PAID_SEARCH_ENABLED", True)
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=sites),
    )


async def _crash_after_the_first_record(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Поиск по очереди доноров; как только легла первая запись, задачу обрывают — как
    воркер, который упал посреди пачки."""
    recorded = asyncio.Event()
    commit = session.commit

    async def committed() -> None:
        await commit()
        recorded.set()

    monkeypatch.setattr(session, "commit", committed)
    walking = asyncio.create_task(search_contacts(session, limit=10))
    await asyncio.wait_for(recorded.wait(), timeout=5)
    walking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await walking


@pytest.fixture
async def two_donors(session: AsyncSession) -> None:
    await make_donor(session, FAST)
    await make_donor(session, SLOW)


@pytest.mark.usefixtures("two_donors")
async def test_a_paid_answer_is_kept_while_its_batch_still_waits(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    paid = Paid()
    _install(monkeypatch, paid, Sites())

    await _crash_after_the_first_record(session, monkeypatch)

    assert paid.calls == [FAST]
    assert (await _state(session, FAST))[0] is ContactStatus.NOT_FOUND
    assert (await _state(session, SLOW))[0] is None, "сосед не прошёл — его исхода нет"
    assert await _journal(session) == (1, {UsageProvider.HUNTER})


@pytest.mark.usefixtures("two_donors")
async def test_a_failed_record_waits_for_its_neighbours_before_it_is_raised(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Запись быстрого домена падает — сбой выходит из прохода только после соседа: тот пишет в ту
    же сессию, и вызывающий, получив сбой раньше, работал бы с ней одновременно с ним (CI #449).
    Ответ соседа, за который заплачено, при этом ложится в базу."""
    sites = Sites()
    _install(monkeypatch, Paid(), sites)
    saving = ContactRepository.save

    async def save(queue: ContactRepository, results: Sequence[LadderResult]) -> int:
        if results[0].host == FAST:
            sites.answer()  # сосед отвечает только теперь — сбой его уже не ждал бы
            raise ConnectionError("запись исхода оборвалась")
        return await saving(queue, results)

    monkeypatch.setattr(ContactRepository, "save", save)

    with pytest.raises(ConnectionError):
        await search_contacts(session, limit=10)

    assert (await _state(session, SLOW))[0] is ContactStatus.NOT_FOUND
    assert (await _state(session, FAST))[0] is None


@pytest.mark.usefixtures("two_donors")
async def test_the_retry_after_a_crash_does_not_pay_for_a_kept_domain_again(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, Paid(), Sites())
    await _crash_after_the_first_record(session, monkeypatch)
    retry = Paid()
    _install(monkeypatch, retry, Sites().answer())  # повтор задачи: сосед теперь отвечает

    await search_contacts(session, limit=10)

    assert retry.calls == [SLOW], "за записанный домен повтор не платит"
    assert await _journal(session) == (2, {UsageProvider.HUNTER})
