"""Снимок учётки Hunter: тариф, сброс и остаток на минуту последнего ответа (02.11.2026).

Экран «Расход» говорит об остатке **всей учётки**, а не о наших тратах: она общая с соседней
системой, и наш журнал расхода её трат не видит (шапка `provider.py`). Спрашивать Hunter из экрана
незачем: ответ держит сторож — опрос раз в десять минут (`ops/hunter_watch.py`), — и экран
показывает, когда Hunter ответил в последний раз. Hunter недоступен — снимок остаётся прежним:
старое число со своим временем честнее пустого места.

Снимок пишет только короткая транзакция сторожа, поэтому замка, как у снимка ключа Ahrefs
(`runs/ahrefs_key.remember`), здесь нет: долгих транзакций с записью снимка не бывает.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import RowMapping, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.provider import AccountCounter, HunterAccount
from backend.features.core.models.ops import HunterAccountSnapshotModel

#: Номер единственной строки снимка.
SNAPSHOT_ID = 1

#: Сколько знаков имени тарифа держит снимок: длинное имя — не повод ронять запись снимка.
PLAN_CHARS = 64

#: Части счётчика — окончания колонок снимка.
PARTS = ("used", "available", "remaining")


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Что Hunter ответил об учётке в последний раз и когда."""

    account: HunterAccount
    asked_at: datetime

    @property
    def main(self) -> AccountCounter:
        """Счётчик, по которому судят остаток учётки: общий счёт, а без него — проверки."""
        return self.account.credits or self.account.verifications


def _columns(prefix: str, counter: AccountCounter | None) -> dict[str, Decimal | None]:
    """Счётчик — колонками снимка: `credits_used`, `credits_available`, `credits_remaining`."""
    return {
        f"{prefix}_{part}": None if counter is None else getattr(counter, part) for part in PARTS
    }


async def remember(session: AsyncSession, account: HunterAccount, *, at: datetime) -> None:
    """Ответ Hunter — снимком учётки. Без фиксации: снимок ложится транзакцией вызывающего.
    Ответ старше записанного строку не трогает: прошлое не возвращается."""
    values = {
        "plan": account.plan[:PLAN_CHARS],
        "reset_on": account.reset_on,
        **_columns("credits", account.credits),
        **_columns("searches", account.searches),
        **_columns("verifications", account.verifications),
        "asked_at": at.astimezone(UTC),
    }
    statement = insert(HunterAccountSnapshotModel).values(id=SNAPSHOT_ID, **values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[HunterAccountSnapshotModel.id],
            set_={name: statement.excluded[name] for name in values},
            where=HunterAccountSnapshotModel.asked_at <= statement.excluded.asked_at,
        )
    )


async def snapshot(session: AsyncSession) -> AccountSnapshot | None:
    """Последний снимок учётки; `None` — Hunter ещё не ответил ни разу.

    Столбцами, а не объектом: снимок переписывают запросом мимо карты сессии, и объект,
    прочитанный раньше в той же сессии, вернулся бы прежним."""
    found = await session.execute(
        select(HunterAccountSnapshotModel.__table__).where(
            HunterAccountSnapshotModel.id == SNAPSHOT_ID
        )
    )
    row = found.mappings().one_or_none()
    if row is None:
        return None
    credits_left = row["credits_remaining"]
    account = HunterAccount(
        plan=row["plan"],
        reset_on=row["reset_on"],
        credits=None if credits_left is None else _counter(row, "credits"),
        searches=_counter(row, "searches"),
        verifications=_counter(row, "verifications"),
    )
    return AccountSnapshot(account=account, asked_at=row["asked_at"])


def _counter(row: RowMapping, prefix: str) -> AccountCounter:
    return AccountCounter(*(row[f"{prefix}_{part}"] for part in PARTS))
