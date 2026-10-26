"""Ответы модуля продаж прогнозу рассылки (`core/stage_forecast.SalesForecast`).

Подключаются при загрузке пакета своим загрузчиком (`sales/__init__.py`, `register_forecast`), рядом с
ответами почте (`mail.py`): тот у предела длины файла. Правила — не здесь, а у тех, кто их держит:
сколько первых писем ещё напишет сборка — её же решением (`queue_plan.supply`), пояса получателей и
письма, которые не уйдут, пока их не поправят, — правилом прохода автоотправки (`autosend_window`).
"""

from backend.features.sales.autosend_window import queue_stuck, queue_zones
from backend.features.sales.queue_plan import supply

__all__ = ["queue_stuck", "queue_zones", "supply"]
