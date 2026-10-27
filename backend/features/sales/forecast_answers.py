"""Ответы модуля продаж прогнозу рассылки (`core/stage_forecast.SalesForecast`).

Подключаются при загрузке пакета своим загрузчиком (`sales/__init__.py`, `register_forecast`), рядом с
ответами почте (`mail.py`): тот у предела длины файла. Правила — не здесь, а у тех, кто их держит:
сколько первых писем ещё напишет сборка — её же решением (`queue_plan.supply`), пояса получателей и
письма, которые не уйдут, пока их не поправят, — правилом прохода автоотправки (`autosend_window`),
почему автоотправка стоит — тем же снимком и словами, что у строки автоотправки (`auto_lines`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.features.sales import auto_lines, autosend
from backend.features.sales.autosend import Stop
from backend.features.sales.autosend_window import queue_stuck, queue_zones
from backend.features.sales.queue_plan import supply

__all__ = ["autosend_stop", "queue_stuck", "queue_zones", "supply"]


async def autosend_stop(session: AsyncSession, now: datetime) -> str | None:
    """Почему автоотправка стоит — словами строки автоотправки после «Стоит:», если прогноз этого
    сам не видит: исход письма неизвестен, предохранитель почты продаж не снят при включённом
    тумблере, ни одно письмо окна не уйдёт как есть. Прогноз считает план и срок так, будто
    остановку снимут; эти слова говорят, что сейчас письма не уходят.

    `None` — выключена, идёт или стоит по тому, что прогноз считает сам: очередь пуста, вне окна
    получателя, писать некому, пауза ящиков (темп прохода). Письма ждут проверки адреса, лимит
    первых писем не прочитан, продажи не подключены — у прогноза свои поля, здесь тоже `None`."""
    if not (sales_cfg.ENABLED and sales_cfg.AUTOSEND):
        return None
    guarded = autosend.guard()
    if guarded is not None:
        return f"автоотправка не включается: {guarded}"
    seen = await autosend.facts(session, now)
    stop = seen.stop()
    if stop is not None:
        return stop[1] if stop[0] is Stop.UNKNOWN else None
    return await auto_lines.none_goes(session, seen)
