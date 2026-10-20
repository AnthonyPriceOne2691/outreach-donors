"""Платный поиск адресов доноров и рекламодателей — флагом, а не ключом.

Решение Anthony 12.10.2026: ключ Hunter общий с проверкой адресов продаж, и вставленный
ради неё ключ включал платную ступень и у доноров с рекламодателями — с автопоиском после
каждого «принять». Теперь её включает только `CONTACTS_PAID_SEARCH_ENABLED`; выключен —
бесплатные ступени идут как раньше, а проход говорит, почему без платной. Сбор базы продаж
платит по своему флагу, и этот его не держит.

Платная ступень здесь настоящая (`search.paid_step`, `HunterProvider`) — подменена сеть:
по запросам к сервису видно, звали его или нет.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx
import pytest
from backend.config import contacts as contacts_cfg
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.search import PAID_SEARCH_OFF, search_contacts
from backend.features.core.domain import Stage
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_contacts_no_answer import NOTHING, _mx_is_fine

__all__ = ["_mx_is_fine"]  # почта у доменов есть — иначе спуск до платной ступени не дойдёт

HOST = "site.example.test"
QUOTA = {"data": {"requests": {"searches": {"used": 0, "available": 100}}}}
FOUND = {"data": {"emails": [{"value": f"editor@{HOST}", "confidence": 95}]}}


class Net:
    """Сайт без адреса и платный сервис с адресом — одна сеть на проход."""

    def __init__(self) -> None:
        self.paid: list[str] = []
        self.site: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.hunter.io":
            self.paid.append(request.url.path)
            reply = QUOTA if request.url.path.endswith("/account") else FOUND
            return httpx.Response(200, json=reply, request=request)
        if request.url.host == HOST:
            self.site.append(request.url.path)
            if request.url.path in ("", "/"):
                return httpx.Response(200, html=NOTHING, request=request)
        return httpx.Response(404, request=request)


class Queue:
    """Очередь из одного домена — этапа `stage`, как у настоящих очередей."""

    def __init__(self, stage: Stage | None) -> None:
        self.stage = stage
        self.saved: list[LadderResult] = []

    async def pending_hosts(self, *, limit: int = 100) -> list[str]:
        return [HOST][:limit]

    async def save(self, results: Sequence[LadderResult]) -> int:
        self.saved.extend(results)
        return sum(1 for result in results if result.found)

    async def last_tries(self, hosts: Sequence[str]) -> set[str]:
        return set()


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Net:
    """Ключ платного сервиса задан — как будет на проде после вставки ради продаж."""
    world = Net()
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "k-test")
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(world)),
    )
    return world


@pytest.mark.parametrize(
    "stage",
    [Stage.DONORS, Stage.ADVERTISERS, None],
    ids=["donors", "advertisers", "refind"],
)
async def test_a_key_alone_does_not_switch_the_paid_step_on(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, net: Net, stage: Stage | None
) -> None:
    monkeypatch.setattr(contacts_cfg, "PAID_SEARCH_ENABLED", False)
    queue = Queue(stage)

    report = await search_contacts(session, limit=10, queue=queue)

    assert net.paid == [], "ни запроса остатка, ни поиска — платная ступень не звалась"
    assert net.site, "бесплатные ступени прошли домен как раньше"
    assert f"Ступень 3 не зовётся: {PAID_SEARCH_OFF} — идём по бесплатным." in report.notes
    assert PAID_SEARCH_OFF == "платный поиск адресов выключен (CONTACTS_PAID_SEARCH_ENABLED)"
    assert not any("не настроен" in note for note in report.notes), "не «нет ключа»"
    assert report.saved == 0
    assert report.counters["provider_entered"] == 0


async def test_the_flag_and_the_key_pay_as_before(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, net: Net
) -> None:
    monkeypatch.setattr(contacts_cfg, "PAID_SEARCH_ENABLED", True)

    report = await search_contacts(session, limit=10, queue=Queue(Stage.DONORS))

    assert net.paid == ["/v2/account", "/v2/domain-search"]
    assert report.saved == 1
    assert report.counters["provider_found"] == 1


async def test_the_sales_collection_keeps_its_own_flag(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, net: Net
) -> None:
    """Сбор базы продаж решает своим флагом (`SALES_SEGMENT_PAID_CONTACTS`, вызывающий
    передаёт `no_paid`): флаг доноров и рекламодателей его не держит."""
    monkeypatch.setattr(contacts_cfg, "PAID_SEARCH_ENABLED", False)

    report = await search_contacts(session, limit=10, queue=Queue(Stage.SALES))

    assert net.paid == ["/v2/account", "/v2/domain-search"]
    assert report.saved == 1


async def test_the_sales_collection_without_its_flag_pays_nothing_even_with_ours(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, net: Net
) -> None:
    """Обратный случай (ревью продаж #358): флаг доноров включён, а сбор базы продаж без
    своего (`no_paid=True`) — ни запроса к платному сервису."""
    monkeypatch.setattr(contacts_cfg, "PAID_SEARCH_ENABLED", True)

    report = await search_contacts(session, limit=10, queue=Queue(Stage.SALES), no_paid=True)

    assert net.paid == [], "ни запроса остатка, ни поиска"
    assert net.site, "бесплатные ступени прошли домен как раньше"
    assert report.saved == 0
