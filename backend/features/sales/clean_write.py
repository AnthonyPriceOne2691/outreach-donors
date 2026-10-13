"""Запись исходов очистки в лидов — одним UPDATE на набор колонок, без чтения перед записью.

**Время правки лида — выражением колонки** (`updated_at = sales_leads.updated_at`), а не
значением, прочитанным заранее. Очистка не меняет ни имени, ни компании лида и время правки не
двигает: иначе письмо лида в очереди сошло бы за «собранное до правки» (`lead_edit.edited`), и
сборка переписала бы его моделью зря. Прочитанное значение затирало бы правку человека: правка
делает flush строки раньше коммита (`lead_edit.change`), очистка в этом окне прочла бы прежнее
время, а её UPDATE, дождавшись блокировки, записал бы его поверх нового — и письмо со старыми
именем или компанией ушло бы. Выражение PostgreSQL считает по свежей версии строки — той, что
после коммита правки. Явное значение в SET перебивает `onupdate` колонки.

**Лида удалили между выборкой прохода и записью** (`prune --test-traces`) — его строка просто
не обновится: UPDATE по номеру не находит строки и не падает. Вердикты остальных лидов и строка
расхода (её пишет очистка за все проверки) целы: откат партии стёр бы оплаченные проверки,
месячный потолок их недосчитал бы, а повтор задачи проверил бы те же адреса заново.

Запись — оператором ядра: ORM-запись по первичному ключу выражений не принимает, а условие по
номеру у неё запрещено. Лиды, загруженные в сессию, получают записанные значения сами (`_seen`) —
как при ORM-записи: второй проход той же сессии и вызывающий видят исход, а не прежнее.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from backend.features.sales.models import SalesLeadModel

#: Номер лида в строке исхода; остальные ключи — колонки, их задаёт строка.
LEAD_ID = "lead_id"
_LEADS = SalesLeadModel.metadata.tables[SalesLeadModel.__tablename__]


async def write(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    """Исходы партии: в строке — номер лида (`LEAD_ID`) и колонки исхода. Строки с одним набором
    колонок уходят одним executemany; время правки — выражением колонки."""
    same: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        same.setdefault(tuple(sorted(row)), []).append(row)
    statement = (
        update(_LEADS)
        .where(_LEADS.c.id == bindparam(LEAD_ID))
        .values(updated_at=_LEADS.c.updated_at)
    )
    for batch in same.values():
        await session.execute(statement, batch)
    _seen(session, rows)


def _seen(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    """Записанное — и в объекты лидов, загруженные в сессию: оператор ядра их не трогает."""
    for row in rows:
        lead = session.identity_map.get(session.identity_key(SalesLeadModel, row[LEAD_ID]))
        if lead is None:
            continue
        for key, value in row.items():
            if key != LEAD_ID:
                set_committed_value(lead, key, value)
