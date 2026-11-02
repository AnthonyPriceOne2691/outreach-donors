"""Загрузки доноров: откуда пришла партия, чтобы панель могла спросить, что она дала.

Запись — там же, где донор записан: загрузкой файла (`api/donors/bulk.py`) и кнопкой «Завести
донора вручную» (`api/donors/routes.py`), одной фиксацией с ним. Сама запись ничего не решает:
обход ставят те же очередь загрузки и кнопка, что и раньше, а панель (`panel.py`,
`load_quality.py`) только читает её рядом с обходами.

В партию ложится только записанное: пропущенная строка файла донором не стала, и считать её
«добавленной на обход» значило бы завысить начало воронки.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.crawl import DonorLoadHostModel, DonorLoadModel

#: Партия файлом и донор, заведённый вручную; значения стоят и в ограничении таблицы.
BULK = "bulk"
MANUAL = "manual"

#: Строк за одну вставку: два значения на строку, а у Postgres предел — 32 767 на запрос.
CHUNK = 5_000
#: Имя файла — как в колонке; браузер волен прислать длиннее.
FILE_NAME_MAX = 255


async def record(
    session: AsyncSession,
    *,
    kind: str,
    hosts: Iterable[str],
    by: str | None,
    file_name: str | None = None,
    crawl: bool = False,
) -> DonorLoadModel | None:
    """Записать загрузку и её домены. Нечего записывать — `None`: файл, в котором все строки
    пропущены, партией не стал. Без коммита: коммитит вызывающий, вместе с донорами."""
    unique = list(dict.fromkeys(hosts))
    if not unique:
        return None
    load = DonorLoadModel(
        kind=kind,
        file_name=file_name[:FILE_NAME_MAX] if file_name else None,
        requested_by=by,
        crawl=crawl,
    )
    session.add(load)
    await session.flush()
    for start in range(0, len(unique), CHUNK):
        await session.execute(
            insert(DonorLoadHostModel),
            [{"load_id": load.id, "host": host} for host in unique[start : start + CHUNK]],
        )
    return load


async def recent(session: AsyncSession, *, limit: int) -> list[tuple[DonorLoadModel, int]]:
    """Последние загрузки с числом их доменов — свежие первыми, до номера: равное время у
    двух загрузок одного запроса не должно менять порядок между обновлениями."""
    rows = await session.execute(
        select(DonorLoadModel, func.count(DonorLoadHostModel.host))
        .join(DonorLoadHostModel, DonorLoadHostModel.load_id == DonorLoadModel.id)
        .group_by(DonorLoadModel.id)
        .order_by(DonorLoadModel.created_at.desc(), DonorLoadModel.id.desc())
        .limit(limit)
    )
    return [(load, int(hosts)) for load, hosts in rows.tuples().all()]
