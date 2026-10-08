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

from pydantic import Field, field_validator

from backend.config._base import DomainSettings


class _Llm(DomainSettings):
    api_key: str = Field(default="", validation_alias="LLM_API_KEY")
    # Модель генерации. Не ниже пятой: проверено замером.
    keygen_model: str = Field(default="gpt-5", validation_alias="LLM_KEYGEN_MODEL")
    # Модель проверки набора. Здесь дешёвая уместна.
    judge_model: str = Field(default="gpt-5-mini", validation_alias="LLM_JUDGE_MODEL")
    # Модель уникализации письма. Та же причина, что у генерации ключей:
    # слабая примешивает чужой язык и сваливается в шаблон, а письмо,
    # похожее на тысячу других, — это и есть то, от чего уникализация
    # защищает.
    letters_model: str = Field(default="gpt-5", validation_alias="LLM_LETTERS_MODEL")
    # Модель агента переписки: письмо живому собеседнику, и слабая модель
    # пишет его шаблонно и путает языки — та же причина, что у уникализации.
    agent_model: str = Field(default="gpt-5", validation_alias="LLM_AGENT_MODEL")
    # Модель вида ответа лида продаж (`features/sales/reply_kind.py`): от вида
    # зависит, позвонят ли человеку и закроют ли его адрес, — та же, что у
    # разбора цены, пока замер на наборе не скажет, что дешёвая не хуже.
    sales_classify_model: str = Field(default="gpt-5", validation_alias="LLM_SALES_CLASSIFY_MODEL")
    # Модели агента продаж (`features/sales/agent/`), по одной на вызов: черновик —
    # сильная, письмо живому человеку, как у агента переписки; ситуация письма и
    # судья черновика — дешёвая: метка и строгая форма, а не текст. Не замерено —
    # меряет прогон на накопленных ответах (`scripts/sales_replay.py`).
    sales_draft_model: str = Field(default="gpt-5", validation_alias="LLM_SALES_DRAFT_MODEL")
    sales_situation_model: str = Field(
        default="gpt-5-mini", validation_alias="LLM_SALES_SITUATION_MODEL"
    )
    sales_judge_model: str = Field(default="gpt-5-mini", validation_alias="LLM_SALES_JUDGE_MODEL")
    timeout_s: float = Field(default=180.0, validation_alias="LLM_TIMEOUT_S")
    # Фраз за один вызов. Больше — растёт доля почти одинаковых, а ответ
    # обрывается на середине токенного лимита.
    max_phrases_per_call: int = Field(default=120, validation_alias="LLM_MAX_PHRASES_PER_CALL")
    # Запас сверх нужного на угол: часть фраз отсеют гигиена и дедуп.
    angle_buffer: int = Field(default=10, validation_alias="LLM_ANGLE_BUFFER")
    # Раундов добора. Больше трёх обычно не даёт новых фраз, только счёт.
    backfill_rounds: int = Field(default=3, validation_alias="LLM_BACKFILL_ROUNDS")
    # Потолки расхода на модель, в токенах по журналу расхода; 0 — потолка нет.
    # У Ahrefs предел стоит давно, у модели не было: ошибка в промпте или
    # зацикленный повтор тратили бы без границы (BACKLOG 28.09, решение
    # Anthony 04.10.2026). За день — по всем операциям модели; за прогон —
    # по строкам журнала с номером прогона.
    daily_token_cap: int = Field(default=0, ge=0, validation_alias="LLM_DAILY_TOKEN_CAP")
    run_token_cap: int = Field(default=0, ge=0, validation_alias="LLM_RUN_TOKEN_CAP")
    # Свой дневной потолок черновиков агента переписки — внутри общего (проверяются
    # оба). Не задан — доля общего (`AGENT_CAP_SHARE`); 0 — своего потолка нет.
    agent_daily_token_cap: int | None = Field(
        default=None, ge=0, validation_alias="AGENT_DAILY_TOKEN_CAP"
    )

    @field_validator("agent_daily_token_cap", mode="before")
    @classmethod
    def _unset_if_empty(cls, value: object) -> object:
        """Пустое значение в `.env` — «не задан», а не ошибка разбора числа."""
        return None if value == "" else value


_s = _Llm()

API_KEY: str = _s.api_key.strip()  # хвостовой пробел из .env ломает заголовок
KEYGEN_MODEL: str = _s.keygen_model
JUDGE_MODEL: str = _s.judge_model
LETTERS_MODEL: str = _s.letters_model
AGENT_MODEL: str = _s.agent_model
SALES_CLASSIFY_MODEL: str = _s.sales_classify_model
SALES_DRAFT_MODEL: str = _s.sales_draft_model.strip()
SALES_SITUATION_MODEL: str = _s.sales_situation_model.strip()
SALES_JUDGE_MODEL: str = _s.sales_judge_model.strip()
TIMEOUT_S: float = _s.timeout_s
MAX_PHRASES_PER_CALL: int = _s.max_phrases_per_call
ANGLE_BUFFER: int = _s.angle_buffer
BACKFILL_ROUNDS: int = _s.backfill_rounds
DAILY_TOKEN_CAP: int = _s.daily_token_cap
RUN_TOKEN_CAP: int = _s.run_token_cap
AGENT_DAILY_TOKEN_CAP: int | None = _s.agent_daily_token_cap
#: Доля общего дневного потолка у черновиков агента, когда свой не задан. Черновики
#: на сотни лидов иначе выбрали бы день целиком, и разбор ответов доноров, судья
#: прогона, письма и ключи встали бы до завтра; черновик же подождёт завтра или
#: кнопки «написать заново».
AGENT_CAP_SHARE = 0.3
