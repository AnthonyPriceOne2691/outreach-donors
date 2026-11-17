"""Конвейер «Плана работ»: тумблер и тормоза (`features/sales/plan_tick.py`, 17.11.2026).

Свой модуль, а не `config/sales.py`: тот у предела длины файла, а читают эти настройки только проход
конвейера и процесс разбора (`workers/reaper.py`) — как у переадресации и автокруга
(`config/redirect.py`, `config/auto_round.py`).

**Выключен по умолчанию** (`SALES_PLAN`): конвейер сам покупает выдачу и зовёт модель судьи — траты
без человека; включает владелец. Идёт только при включённых продажах (`SALES_ENABLED`).

**Тормоза — щадящий режим «Плана работ»:**

- проход раз в `SALES_PLAN_PASS_SEC` секунд (120): прогон идёт минутами, чаще смотреть незачем;
- прогон — не больше `SALES_PLAN_KEYS_PER_RUN` ключей (100, и не больше предела ключей прогона
  выдачи `SERP_MAX_KEYWORDS_PER_RUN`), один прогон плана за раз на весь сервис и пауза
  `SALES_PLAN_PAUSE_MIN` минут (10) между прогонами: долгие задачи продаж делят один воркер с
  письмами;
- доля `SALES_PLAN_CAP_SHARE` (0,5) дневного потолка модели продаж: остальное — письмам, разбору
  ответов и агенту;
- временный сбой (потолок модели, нет кредитов, сеть) — повтор не чаще `SALES_PLAN_RETRY_MIN` минут
  (30): беда, повторённая каждым проходом, — круг, а не повтор;
- ключ, которого источник выдачи не отдал (заплачено, а не пришло) в `SALES_PLAN_LOST_TRIES`
  прогонах одной волны (2), конвейер считает отработанным: третий раз за него не платят.

Опечатка в значении не роняет процессы, которые грузят настройку (`_base.spared`): конвейер выключен,
пока её не поправят, и при старте — громкая строка журнала с именем переменной.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings, Fallback, spared


class _Plan(DomainSettings):
    enabled: bool = Field(default=False, validation_alias="SALES_PLAN")
    pass_sec: int = Field(default=120, ge=10, le=3600, validation_alias="SALES_PLAN_PASS_SEC")
    cap_share: float = Field(default=0.5, gt=0, le=1, validation_alias="SALES_PLAN_CAP_SHARE")
    keys_per_run: int = Field(
        default=100, ge=1, le=1000, validation_alias="SALES_PLAN_KEYS_PER_RUN"
    )
    pause_min: int = Field(default=10, ge=0, le=1440, validation_alias="SALES_PLAN_PAUSE_MIN")
    retry_min: int = Field(default=30, ge=1, le=1440, validation_alias="SALES_PLAN_RETRY_MIN")
    lost_tries: int = Field(default=2, ge=1, le=10, validation_alias="SALES_PLAN_LOST_TRIES")


#: Что значит взятое вместо негодного: умолчание поля, а конвейер выключен (`ENABLED`).
_OFF = "конвейер плана работ выключен, пока настройку не поправят"

_s, _unread = spared(
    _Plan,
    {
        "SALES_PLAN": Fallback(False, "true или false", _OFF),
        "SALES_PLAN_PASS_SEC": Fallback(120, "целое от 10 до 3600", _OFF),
        "SALES_PLAN_CAP_SHARE": Fallback(0.5, "число больше 0 до 1", _OFF),
        "SALES_PLAN_KEYS_PER_RUN": Fallback(100, "целое от 1 до 1000", _OFF),
        "SALES_PLAN_PAUSE_MIN": Fallback(10, "целое от 0 до 1440", _OFF),
        "SALES_PLAN_RETRY_MIN": Fallback(30, "целое от 1 до 1440", _OFF),
        "SALES_PLAN_LOST_TRIES": Fallback(2, "целое от 1 до 10", _OFF),
    },
)

#: Конвейер плана работ — только при прочитанных настройках.
ENABLED: bool = _s.enabled and not _unread
#: Как часто проход конвейера смотрит планы, секунд.
PASS_SEC: int = _s.pass_sec
#: Доля дневного потолка модели продаж, которую конвейер тратит на судью.
CAP_SHARE: float = _s.cap_share
#: Ключей на прогон плана — не больше; и не больше предела ключей прогона выдачи.
KEYS_PER_RUN: int = _s.keys_per_run
#: Пауза между прогонами плана, минут; 0 — без паузы.
PAUSE_MIN: int = _s.pause_min
#: Повтор после временного сбоя — не чаще, минут.
RETRY_MIN: int = _s.retry_min
#: Прогонов одной волны, в которых источник не отдал ключ, — дальше ключ отработан.
LOST_TRIES: int = _s.lost_tries
