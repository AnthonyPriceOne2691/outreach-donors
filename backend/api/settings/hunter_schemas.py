"""Hunter на экране «Расход»: общий остаток учётки и наш потолок проверок адреса (слово Anthony
02.11.2026: «на фронт выводился общий остаток, именно по учётке»).

Своим файлом, как кап Ahrefs и потолки модели: схемы расхода — у предела длины. Экран называет,
а не считает: доли и «осталось» — тем же чтением, что у тревоги (`ops/hunter_watch.standing`), и
число на экране не расходится с тем, о чём скажет сторож.

**Учётку видят все, кто видит «Расход»**: она общая с поиском адресов доноров и с соседней
системой. **Потолок и выбранное за месяц — только с правом «Продажи»**: проверки адреса — их расход
(П2б, как часть продаж у потолков модели). Без права — `month` пусто, и экран говорит это словами.

Счётчики — числами с плавающей точкой, а не деньгами: кредит дробный только половинками, сумм из
них никто не складывает, а экрану нужно число, а не строка `Decimal`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as contacts_cfg
from backend.features.contacts.provider import AccountCounter
from backend.features.core import hunter_cap
from backend.features.core.hunter_cap import HunterCapChange
from backend.features.ops.hunter_watch import Standing, standing
from backend.features.runs.spending import month_start

#: Почему остатка учётки на экране нет — ключа нет или Hunter ещё не отвечал.
NO_KEY = "Ключ Hunter на сервере не задан — остаток учётки спросить не у кого"
NOT_ASKED = "Hunter ещё не отвечал — остаток появится после следующего опроса, за несколько минут"


class HunterCapBody(BaseModel):
    """Новый месячный потолок проверок адреса: целое, не меньше нуля; ноль — проверки остановлены.
    Пустого значения нет: снять настройку с экрана нельзя. Верхнюю границу держит сама смена
    (`hunter_cap.refusal`) — тем же числом, что экран получает в `HunterMonthCard.most`."""

    verify_cap: int

    @field_validator("verify_cap", mode="before")
    @classmethod
    def _whole(cls, value: object) -> object:
        """Потолок — целое число: пусто, дробь, строка и «да/нет» — отказ словами, а не именем
        поля."""
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise PydanticCustomError(
            "hunter_cap_not_whole",
            "Потолок — целое число проверок адреса в месяц, не меньше нуля; 0 останавливает проверки",
        )


class HunterCapChangeCard(BaseModel):
    """Смена потолка: что поставили, кто и когда; `set_by` пусто — учётку удалили."""

    id: int
    verify_cap: int
    set_by: str | None
    set_at: datetime

    @classmethod
    def of(cls, change: HunterCapChange) -> HunterCapChangeCard:
        return cls(
            id=change.id,
            verify_cap=change.verify_cap,
            set_by=change.set_by,
            set_at=change.set_at,
        )


class CounterCard(BaseModel):
    """Счётчик учётки за её цикл: выбрано, всего и осталось."""

    used: float
    available: float
    remaining: float

    @classmethod
    def of(cls, counter: AccountCounter) -> CounterCard:
        return cls(
            used=float(counter.used),
            available=float(counter.available),
            remaining=float(counter.remaining),
        )


class HunterAccountCard(BaseModel):
    """Учётка Hunter по снимку: общая с соседней системой и поиском адресов."""

    plan: str
    #: День сброса учётки — как его называет Hunter: у неё свой цикл, не календарный месяц.
    reset_on: date
    #: Когда Hunter ответил в последний раз: снимок мог устареть, и экран это показывает.
    asked_at: datetime
    #: Общий счёт; `None` — у тарифа его нет, и поиск с проверкой считаются своими счётчиками.
    credits: CounterCard | None
    searches: CounterCard
    #: Сколько проверок адреса хватит на остаток: при общем счёте — тот же счёт в проверках.
    verifications: CounterCard
    #: Выбрано от главного счётчика — целыми процентами вниз, как у тревоги.
    used_percent: int


class HunterMonthCard(BaseModel):
    """Наш потолок проверок адреса на календарный месяц: что действует и откуда, сколько выбрано."""

    #: Действующий потолок; `None` — числа нет, `problem` говорит почему. Ноль — проверки
    #: остановлены с экрана.
    cap: int | None
    problem: str | None
    #: `setting` — сохранён на экране; `env` — пока из настроек сервера.
    source: Literal["setting", "env"]
    spent: int
    left: int | None
    #: Выбрано от потолка целыми процентами вниз; числа нет или ноль — `None`.
    used_percent: int | None
    #: Потолок считается с первого числа — у него своя граница, не сброс учётки.
    since: datetime
    #: Выше потолок не поставить: проверок тарифа, а снимка нет — граница от опечатки.
    most: int
    #: Последние смены, новые первыми; пусто — на экране не меняли.
    changes: list[HunterCapChangeCard]


class HunterCard(BaseModel):
    """Hunter на «Расходе»: учётка и наш потолок. Ни того ни другого нет — `unknown` говорит
    почему; `month` пусто — потолок виден только с правом «Продажи»."""

    account: HunterAccountCard | None
    unknown: str | None
    month: HunterMonthCard | None


def _account(found: Standing) -> HunterAccountCard | None:
    if found.snapshot is None:
        return None
    account = found.snapshot.account
    return HunterAccountCard(
        plan=account.plan,
        reset_on=account.reset_on,
        asked_at=found.snapshot.asked_at,
        credits=None if account.credits is None else CounterCard.of(account.credits),
        searches=CounterCard.of(account.searches),
        verifications=CounterCard.of(account.verifications),
        used_percent=found.snapshot.main.used_percent,
    )


async def _month(session: AsyncSession, found: Standing, now: datetime) -> HunterMonthCard:
    stored = await hunter_cap.stored_verify_cap(session)
    tariff = None if found.snapshot is None else found.snapshot.account.verifications.available
    number = found.cap if isinstance(found.cap, int) else None
    return HunterMonthCard(
        cap=number,
        problem=found.cap if isinstance(found.cap, str) else None,
        source="env" if stored is None else "setting",
        spent=found.spent,
        left=found.month_left,
        used_percent=found.month_percent,
        since=month_start(now),
        most=hunter_cap.MOST if tariff is None else min(hunter_cap.MOST, int(tariff)),
        changes=[
            HunterCapChangeCard.of(change) for change in await hunter_cap.cap_changes(session)
        ],
    )


async def hunter_card(session: AsyncSession, *, sales: bool, now: datetime) -> HunterCard:
    """Hunter на эту минуту. `sales` — видит ли учётка этап продаж: без права потолка на экране
    нет, а учётка остаётся."""
    found = await standing(session, now)
    unknown = None
    if found.snapshot is None:
        unknown = NOT_ASKED if contacts_cfg.HUNTER_API_KEY else NO_KEY
    return HunterCard(
        account=_account(found),
        unknown=unknown,
        month=await _month(session, found, now) if sales else None,
    )
