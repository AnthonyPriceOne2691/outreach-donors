"""Судья площадки: режим, модель и денилист платформ.

**Режим по умолчанию — наблюдение, а не отказ.** Признак, включённый
в отказ до замера, режет вслепую: узнать, скольких годных он забрал,
будет уже не по чему. Порядок обратный: сначала `shadow` со счётчиком,
и только когда доля названа числом — `enforce`.

Модель берётся дешёвая (`llm.JUDGE_MODEL`): работа судьи — прочитать
чужой текст и назвать способ заработка, а не придумать.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from backend.config._base import DomainSettings


class JudgeMode(StrEnum):
    """Что судья делает со своим вердиктом.

    OFF — не вызывается вовсе, ноль расхода.
    SHADOW — вызывается, пишет вердикт и считается, но НЕ режет.
    ENFORCE — режет.
    """

    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class _Judge(DomainSettings):
    mode: JudgeMode = Field(default=JudgeMode.OFF, validation_alias="JUDGE_MODE")
    # Сколько символов текста выдачи отдаём модели. Сниппет короткий,
    # потолок нужен против аномалии провайдера, а не ради экономии.
    max_text_chars: int = Field(default=1200, validation_alias="JUDGE_MAX_TEXT_CHARS")
    # Срок годности вердикта. Длиннее метрик (90 дней) намеренно: способ
    # заработка сайт меняет раз в годы, а не в квартал, и пересуживать его
    # каждый прогон значит платить токенами за уже известное.
    ttl_days: int = Field(default=180, validation_alias="JUDGE_TTL_DAYS")
    # Сколько доменов судим одновременно. Провайдер отвечает секундами,
    # а доменов в прогоне сотни: по одному это часы ожидания на ровном месте.
    concurrency: int = Field(default=8, validation_alias="JUDGE_CONCURRENCY")


_s = _Judge()

MODE: JudgeMode = _s.mode
MAX_TEXT_CHARS: int = _s.max_text_chars
TTL_DAYS: int = _s.ttl_days
CONCURRENCY: int = _s.concurrency

#: Платформы, которые по букве правила проходят: они правда отсылают наружу.
#: Размещать на них нельзя, и судью этим вопросом грузить незачем — денилист
#: стоит ДО него и стоит ноль.
#:
#: ⚠ Матч по целой DNS-метке, а не по вхождению строки: `google-maps-guide.co.ke`
#: не платформа и денить его нельзя. Метка `google` совпадёт у `google.com`
#: и `google.co.uk`, но не у `google-maps-guide.co.ke` — там метка целиком
#: другая.
PLATFORM_LABELS: frozenset[str] = frozenset(
    {
        "google",
        "youtube",
        "facebook",
        "instagram",
        "twitter",
        "x",
        "linkedin",
        "reddit",
        "wikipedia",
        "pinterest",
        "tiktok",
        "amazon",
        "ebay",
        "aliexpress",
        "medium",
        "quora",
        "tripadvisor",
        "yelp",
        "booking",
        "airbnb",
        "apple",
        "microsoft",
        "github",
    }
)
