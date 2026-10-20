"""Задачи «Адреса нет» и «Форм»: поиск адреса глубже и текст письма для формы.

Обе — по кнопке, по одному донору. Свой модуль, а не `jobs.py`: тот у потолка длины,
а у этих задач свои имена и номера (как у агента переписки, `agent_jobs.py`).

**Поиск глубже — в очереди адресов** (`contacts`, процесс один): там же идёт общий
поиск, и один процесс не пройдёт один домен двумя лестницами разом. **Текст письма —
в очереди коротких** (`quick`): его ждёт человек у открытого окна, а не прогон.

Пути и номера задач — в `shared/queue.py`, рядом с остальными: их ставит маршрут.
Номер постоянный — на донора: второе нажатие, пока первая не кончилась, — отказ
словами, а не вторая задача (`api/letters/once.enqueue_once`).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.contacts.deeper import search_deeper
from backend.features.letters.form_letter import build
from backend.features.letters.rewrite import RewriteClient
from backend.shared.logs import setup_logging

# Постоянный отказ — итогом задачи, временный — повтором очереди: правило общее
# у всех задач сервиса и живёт рядом с ними (`jobs._settled`).
from backend.workers.jobs import _settled

logger = logging.getLogger(__name__)


async def _deeper(donor_id: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            outcome = await search_deeper(session, donor_id)
            await session.commit()
            return outcome.as_dict()
    finally:
        await engine.dispose()


def find_deeper(donor_id: int) -> dict[str, Any]:
    """Искать адрес глубже одному донору: шире обход, карта сайта, браузер, без платной.

    Итог — словами в отчёте задачи (`note`) и у донора: экран показывает его у кнопки
    и в карточке завтра.
    """
    setup_logging()
    check_storage()
    return _settled(
        lambda: asyncio.run(_deeper(donor_id)), what=f"поиск адреса глубже донора №{donor_id}"
    )


async def _form_letter(donor_id: int, by: str | None) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rewriter = RewriteClient()
    try:
        async with factory() as session:
            letter = await build(session, donor_id, rewriter=rewriter, by=by)
            await session.commit()
            return {"donor_id": donor_id, "tokens": letter.tokens, "based_on": letter.based_on}
    finally:
        await rewriter.aclose()
        await engine.dispose()


def build_form_letter(donor_id: int, by: str | None = None) -> dict[str, Any]:
    """Собрать текст письма для формы донора — тот же, что ушёл бы письмом. Вызов модели."""
    setup_logging()
    check_storage()
    return _settled(
        lambda: asyncio.run(_form_letter(donor_id, by)),
        what=f"текст письма для формы донора №{donor_id}",
    )
