"""Кредиты Hunter для платного поиска адресов — общий счёт всех, кто ищет (25.11.2026).

**Зачем.** Учётка Hunter одна на всё: платный поиск адресов доноров и рекламодателей (кнопка
«найти», автокруг, автопоиск после «пишем»), проверка адресов перед первым письмом, проверки и
сбор базы продаж — и соседняя система. «найти» ставит поиск всем ждущим разом, тысячам сайтов, и
без общего счёта выходило бы «кредиты вроде есть, а потом их не хватает»: кнопка видела остаток, а
автокруг и проверка писем в ту же минуту его ели.

**Свободно** (`Budget.free`) — остаток учётки (живой ответ Hunter `/account`; не ответил —
последний снимок сторожа со своим временем) минус неприкосновенный запас (`CONTACTS_PAID_RESERVE`:
соседняя система и непредвиденное), минус проверки адресов до конца месяца (потолок с экрана
«Расход» без сделанных, по полкредита — только у тарифа с общим счётом), минус кредиты, которые
держат идущие поиски (`held`).

**Проход занимает до старта и тратит только занятое** (`claim` → `release`): под замком он берёт
долю свободного под ожидаемые платные запросы и держит её в Redis до конца (у доли — срок задачи:
умерла задача — доля вернулась сама). Платная ступень прохода сверх доли не спрашивает
(`provider_step.ProviderStep`, бюджет): кончилась доля — остальные сайты «кончилась квота», повтор
по сроку. Неистраченное возвращается с `release`.

**Платных запросов на сайт** (`PAID_SHARE`): платная ступень зовётся только тем, у кого страницы не
дали адреса и сайт ответил. Замер проходов 18–25.11.2026 — от 0,2 до 0,4 запроса на сайт; смета
берёт половину, с запасом: недосчитать дороже, чем пересчитать.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.contacts import hunter_account
from backend.features.contacts.address_check import month_spent
from backend.features.contacts.provider import (
    AccountCounter,
    HunterAccount,
    HunterProvider,
    ProviderError,
)
from backend.features.core.hunter_cap import verify_month_cap
from backend.shared.queue import connection

logger = logging.getLogger(__name__)

#: Платных запросов на сайт в смете прохода (шапка модуля).
PAID_SHARE = Decimal("0.5")
#: Кредитов за проверку адреса у тарифа с общим счётом.
CHECK_COST = Decimal("0.5")
#: Доли идущих проходов: поле — метка прохода, значение — «кредитов|срок в секундах эпохи».
HOLDS_KEY = "outreach:hunter:holds"
#: Замок занятия: два прохода, занимающих в одну секунду, иначе увидели бы одно свободное.
LOCK_KEY = "outreach:hunter:holds:lock"
#: Сколько держится замок, если процесс умер, не сняв его.
LOCK_TTL_SEC = 30
#: Сколько ждать замка, прежде чем сказать «занято».
LOCK_WAIT_SEC = 10.0

#: Остаток не узнать — словами для экрана и отчёта прохода.
UNKNOWN = "остаток кредитов Hunter не узнать: сервис не ответил, а снимка учётки ещё нет"


#: Идёт ли проход по общему счёту (`shared`): так ищут доноры и рекламодатели.
_SHARED: ContextVar[bool] = ContextVar("hunter_shared_count", default=False)


@contextmanager
def shared() -> Iterator[None]:
    """Проходы поиска внутри — по общему счёту: занимают долю и тратят только её
    (`search.search_contacts`). Задача поиска доноров и рекламодателей открывает его вокруг себя
    (`workers/jobs.find_contacts`); сбор базы продаж — своей сметой, вне его."""
    token = _SHARED.set(True)
    try:
        yield
    finally:
        _SHARED.reset(token)


def counting() -> bool:
    """Проход идёт по общему счёту кредитов (`shared`)."""
    return _SHARED.get()


class BudgetBusyError(RuntimeError):
    """Замок занятия не дался за `LOCK_WAIT_SEC`: кто-то занимает кредиты слишком долго."""


@dataclass(frozen=True, slots=True)
class Budget:
    """Кредиты Hunter на минуту вопроса: из чего сложилось свободное."""

    account_left: int
    asked_at: datetime
    #: Живой ответ Hunter; `False` — снимок сторожа (`hunter_account.snapshot`).
    live: bool
    reserve: int
    checks_hold: int
    held: int

    @property
    def free(self) -> int:
        return max(0, self.account_left - self.reserve - self.checks_hold - self.held)


def needed(sites: int) -> int:
    """Платных запросов, которые смета закладывает на столько сайтов."""
    return math.ceil(sites * PAID_SHARE)


def affordable(credits: int) -> int:
    """Скольким сайтам хватит стольких кредитов по смете."""
    return math.floor(credits / PAID_SHARE)


def _left(account: HunterAccount) -> int:
    """Остаток для поиска: общий счёт, а у тарифа без него — счётчик поисков."""
    counter: AccountCounter = account.credits or account.searches
    return max(0, math.floor(counter.remaining))


async def _checks_hold(session: AsyncSession, account: HunterAccount) -> int:
    """Кредиты под проверки адресов до конца месяца: потолок минус сделанные, по полкредита.
    Проверки считаются своим счётчиком (тариф без общего счёта) или потолка нет — ноль."""
    if account.credits is None:
        return 0
    cap = await verify_month_cap(session)
    if isinstance(cap, str):
        return 0
    left = max(0, cap - await month_spent(session))
    return math.ceil(left * CHECK_COST)


async def _account(
    session: AsyncSession, http: httpx.AsyncClient
) -> tuple[HunterAccount, datetime, bool] | None:
    """Учётка: живой ответ Hunter, а не ответил — последний снимок сторожа."""
    try:
        return await HunterProvider(http).account(), datetime.now(UTC), True
    except ProviderError as exc:
        logger.warning("кредиты Hunter: живой остаток не узнать (%s) — беру снимок сторожа", exc)
    stored = await hunter_account.snapshot(session)
    if stored is None:
        return None
    return stored.account, stored.asked_at, False


async def budget(
    session: AsyncSession, http: httpx.AsyncClient, *, redis: Redis | None = None
) -> Budget | str:
    """Свободные кредиты Hunter сейчас; строка — почему их не узнать (`UNKNOWN`)."""
    found = await _account(session, http)
    if found is None:
        return UNKNOWN
    account, asked_at, live = found
    return Budget(
        account_left=_left(account),
        asked_at=asked_at,
        live=live,
        reserve=cfg.PAID_RESERVE,
        checks_hold=await _checks_hold(session, account),
        # Клиент Redis синхронный — в пуле потоков, а не в цикле событий (аудит 10.10.2026).
        held=await asyncio.to_thread(held, redis),
    )


def held(redis: Redis | None = None, *, now: float | None = None) -> int:
    """Кредиты, которые держат идущие проходы; доли с истёкшим сроком убираются тут же."""
    store = redis or connection()
    moment = time.time() if now is None else now
    total = 0
    for token, raw in store.hgetall(HOLDS_KEY).items():
        amount, _, until = _text(raw).partition("|")
        if float(until or 0) <= moment:
            store.hdel(HOLDS_KEY, _text(token))
            continue
        total += int(amount)
    return total


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _locked(store: Redis) -> bool:
    """Взять замок занятия, подождав не дольше `LOCK_WAIT_SEC`."""
    deadline = time.monotonic() + LOCK_WAIT_SEC
    while not store.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL_SEC):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


async def claim(
    session: AsyncSession,
    http: httpx.AsyncClient,
    *,
    token: str,
    sites: int,
    ttl_sec: int,
    redis: Redis | None = None,
) -> tuple[int, Budget | str]:
    """Занять кредиты под проход: не больше сметы на `sites` и не больше свободного. Ответ —
    занятое (ноль — занять нечего) и счёт, по которому решали."""
    store = redis or connection()
    if not await asyncio.to_thread(_locked, store):
        raise BudgetBusyError("кредиты Hunter занимает другой проход — замок не дался")
    try:
        counted = await budget(session, http, redis=store)
        if isinstance(counted, str):
            return 0, counted
        taken = min(needed(sites), counted.free)
        if taken > 0:
            until = time.time() + ttl_sec
            await asyncio.to_thread(store.hset, HOLDS_KEY, token, f"{taken}|{until}")
        return taken, counted
    finally:
        await asyncio.to_thread(store.delete, LOCK_KEY)


async def release(token: str, redis: Redis | None = None) -> None:
    """Проход кончился: его доля больше не держится — неистраченное снова свободно."""
    await asyncio.to_thread((redis or connection()).hdel, HOLDS_KEY, token)
