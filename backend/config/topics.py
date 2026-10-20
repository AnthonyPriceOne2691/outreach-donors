"""Тематика сайта: включена ли, как читает модель, сколько живёт тема и во что обходится.

Модель — `llm.TOPIC_MODEL`, дешёвая, как у судьи площадки: прочесть текст сайта и назвать
тему из списка, а не придумать. Рассуждение — `low`, по той же причине, что у судьи: на
`minimal` модель судит по одним словам текста и не вспоминает, что это за сайт.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from backend.config._base import DomainSettings


class TopicMode(StrEnum):
    """Определять ли тему новым сайтам в прогоне.

    OFF — модель не зовётся: ни токенов, ни темы у новых сайтов (досуд — командой).
    ON — тема определяется в прогоне рядом с судьёй, тем же текстом.
    """

    OFF = "off"
    ON = "on"


class _Topics(DomainSettings):
    mode: TopicMode = Field(default=TopicMode.ON, validation_alias="TOPIC_MODE")
    reasoning_effort: str = Field(default="low", validation_alias="TOPIC_REASONING_EFFORT")
    # Сколько символов текста сайта уходит модели: заголовок и описание выдачи, заголовок,
    # описание и меню главной. Потолок — против простыни меню, а не ради экономии.
    max_text_chars: int = Field(
        default=1500, ge=200, le=8000, validation_alias="TOPIC_MAX_TEXT_CHARS"
    )
    # Срок годности темы. Длиннее вердикта судьи (180 дней): о чём сайт, меняется реже,
    # чем то, как он зарабатывает. Новая версия промпта делает тему несвежей сразу.
    ttl_days: int = Field(default=365, ge=1, le=3650, validation_alias="TOPIC_TTL_DAYS")
    # Срок «не ясна» — короче: модель не назвала темы чаще по бедному тексту (один заголовок,
    # заглушка), а текст у сайта со временем бывает богаче — новая выдача, открывшаяся главная.
    unclear_ttl_days: int = Field(
        default=30, ge=1, le=3650, validation_alias="TOPIC_UNCLEAR_TTL_DAYS"
    )
    # Сколько сайтов определяем одновременно — как у судьи: модель отвечает секундами.
    concurrency: int = Field(default=16, ge=1, le=64, validation_alias="TOPIC_CONCURRENCY")
    # Цена токена модели, $ за миллион входа и вывода (рассуждение — в выводе): по ней досуд
    # называет деньги рядом с токенами до траты. Публичный прайс OpenAI для GPT-5 mini;
    # сменили модель — смените и цены.
    input_usd_per_1m_tokens: float = Field(
        default=0.25, gt=0, le=1000, validation_alias="TOPIC_INPUT_USD_PER_1M_TOKENS"
    )
    output_usd_per_1m_tokens: float = Field(
        default=2.0, gt=0, le=1000, validation_alias="TOPIC_OUTPUT_USD_PER_1M_TOKENS"
    )


_s = _Topics()

MODE: TopicMode = _s.mode
REASONING_EFFORT: str = _s.reasoning_effort.strip()
MAX_TEXT_CHARS: int = _s.max_text_chars
TTL_DAYS: int = _s.ttl_days
UNCLEAR_TTL_DAYS: int = _s.unclear_ttl_days
CONCURRENCY: int = _s.concurrency
INPUT_USD_PER_1M_TOKENS: float = _s.input_usd_per_1m_tokens
OUTPUT_USD_PER_1M_TOKENS: float = _s.output_usd_per_1m_tokens
