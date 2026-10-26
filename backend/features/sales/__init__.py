"""Модуль «Продажи»: лиды, гипотезы и всё, что им пишет.

Своё — в этом пакете; почтовые сущности (`domains`, `contacts`, письма,
треды) — общие с донорами. Непреложное модуля — раздел «Продажи»
в `delivery/CONSTITUTION.md`.

**Реестр моделей грузится первым.** `core/models/__init__.py` импортирует
модели продаж, чтобы их видел Alembic, а они берут примесь из `core/models/`.
Начни процесс с `sales.models` — так начнёт будущий воркер продаж — и круг
замкнулся бы на недостроенном модуле (`ImportError`). Строка ниже достраивает
реестр раньше; держит это тест первого импорта в чистом процессе.

**Почта ведёт письма продаж, когда модуль подключён к её мосту.** Пакет грузится в
каждом процессе, где есть модели (реестр моделей импортирует модели продаж), — здесь же
модуль продаж и подключает себя к мосту почты (`core/stages.register_sales`): API,
воркер и консоль спрашивают об адресе, проверке и добивке письма продаж его, а почта
модуль продаж по-прежнему не импортирует. Загружается `mail.py` при первом письме
продаж: он стоит на почте, и загрузка здесь замкнула бы круг. Ответы прогнозу рассылки —
так же, своим загрузчиком (`core/stage_forecast.register_forecast`, модуль `forecast_answers.py`).
"""

import backend.features.core.models  # noqa: F401 — порядок импорта, см. выше
from backend.features.core.stage_forecast import SalesForecast, register_forecast
from backend.features.core.stages import SalesMail, register_sales


def _mail() -> SalesMail:
    """Ответы модуля продаж о письме продаж — модуль `mail.py`."""
    from backend.features.sales import mail  # noqa: PLC0415 — круг импорта, см. выше

    return mail


def _forecast() -> SalesForecast:
    """Ответы модуля продаж прогнозу рассылки — модуль `forecast_answers.py`."""
    from backend.features.sales import forecast_answers  # noqa: PLC0415 — круг импорта, см. выше

    return forecast_answers


register_sales(_mail)
register_forecast(_forecast)
