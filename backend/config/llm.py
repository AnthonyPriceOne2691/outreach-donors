"""Модель: ключ, выбор модели и потолки.

**Генерация ключей идёт на сильной модели, и это замерено, а не выбрано
по вкусу.** Слабые дают общие фразы вместо местных терминов: выдача
по ним ловит глобальные сайты, а наше правило региона их отсеивает —
и прогон приносит мало местных доноров. Наш замер на одном рынке:
четвёрка и мини-версии дают либо примесь английского, либо шаблон
«новости в таком-то месте», который дедуп выбрасывает как почти
одинаковые фразы.

Дорого это не выходит: пул генерируется редко и кэшируется, а платится
потом за выдачу — она дороже генерации на порядки.

**Судья релевантности, наоборот, дешёвый.** Его работа — отбросить явно
выдуманное, а не придумать.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Llm(DomainSettings):
    api_key: str = Field(default="", validation_alias="LLM_API_KEY")
    # Модель генерации. Не ниже пятой: проверено замером.
    keygen_model: str = Field(default="gpt-5", validation_alias="LLM_KEYGEN_MODEL")
    # Модель проверки набора. Здесь дешёвая уместна.
    judge_model: str = Field(default="gpt-5-mini", validation_alias="LLM_JUDGE_MODEL")
    timeout_s: float = Field(default=180.0, validation_alias="LLM_TIMEOUT_S")
    # Фраз за один вызов. Больше — растёт доля почти одинаковых, а ответ
    # обрывается на середине токенного лимита.
    max_phrases_per_call: int = Field(default=120, validation_alias="LLM_MAX_PHRASES_PER_CALL")
    # Запас сверх нужного на угол: часть фраз отсеют гигиена и дедуп.
    angle_buffer: int = Field(default=10, validation_alias="LLM_ANGLE_BUFFER")
    # Раундов добора. Больше трёх обычно не даёт новых фраз, только счёт.
    backfill_rounds: int = Field(default=3, validation_alias="LLM_BACKFILL_ROUNDS")


_s = _Llm()

API_KEY: str = _s.api_key
KEYGEN_MODEL: str = _s.keygen_model
JUDGE_MODEL: str = _s.judge_model
TIMEOUT_S: float = _s.timeout_s
MAX_PHRASES_PER_CALL: int = _s.max_phrases_per_call
ANGLE_BUFFER: int = _s.angle_buffer
BACKFILL_ROUNDS: int = _s.backfill_rounds
