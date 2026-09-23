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
    # Наблюдение по умолчанию, а не «выключен»: вердикт судьи — ярлык
    # у каждого кандидата на рассмотрении и мерило его точности против
    # человека. Боевой прогон 23.09.2026 прошёл вовсе без судьи, потому что
    # умолчание было «выключен», а в окружении режим не задали.
    mode: JudgeMode = Field(default=JudgeMode.SHADOW, validation_alias="JUDGE_MODE")
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
    # Усилие рассуждения. `low`, а не `minimal`: замер 23.09 на 98 доменах —
    # на `minimal` модель судит только по тексту и не вспоминает, чей домен.
    # MediaMarkt, dm и четыре госоргана проходили изданиями. На `low` их
    # опознаёт, издания при этом не переворачивает ни одно. Цена — ~720
    # токенов на домен вместо ~500.
    reasoning_effort: str = Field(default="low", validation_alias="JUDGE_REASONING_EFFORT")
    # Смотреть ли главную. Выключается для прогонов без сети наружу; при
    # выключенной главной «продаёт своё» решает модель, а не правило.
    home_check: bool = Field(default=True, validation_alias="JUDGE_HOME_CHECK")
    home_timeout_sec: float = Field(default=12.0, validation_alias="JUDGE_HOME_TIMEOUT_SEC")


_s = _Judge()

MODE: JudgeMode = _s.mode
MAX_TEXT_CHARS: int = _s.max_text_chars
TTL_DAYS: int = _s.ttl_days
CONCURRENCY: int = _s.concurrency
REASONING_EFFORT: str = _s.reasoning_effort
HOME_CHECK: bool = _s.home_check
HOME_TIMEOUT_SEC: float = _s.home_timeout_sec

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
        "trustpilot",
        "booking",
        "airbnb",
        "apple",
        "microsoft",
        "github",
        # Прогон 23.09.2026 на 100 ключах: UGC-площадки и сервисы, прошедшие
        # пороги как «годные». Разместить гостевую статью на них нельзя
        # ни в какой нише — список общий, а не под этот пул.
        "substack",
        "slideshare",
        "scribd",
        "issuu",
        "tumblr",
        "blogspot",
        "flickr",
        "imgur",
        "vimeo",
        "twitch",
        "soundcloud",
        "spotify",
        "telegram",
        "discord",
        "whatsapp",
        "threads",
        "glassdoor",
        "indeed",
        "producthunt",
        "crunchbase",
        "behance",
        "dribbble",
    }
)
