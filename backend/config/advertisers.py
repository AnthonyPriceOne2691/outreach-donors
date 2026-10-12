"""Отбор рекламодателей (Этап 2): пороги, веса и списки — настройками, а не кодом.

Требование: «все пороги заданы цифрами и вынесены в настройки прогона». Здесь — то
из Этапа 2, что до сверки ТЗ 11.10.2026 жило константами в коде: веса и пороги
скоринга ссылок (`crawl/scoring.py`), «DR > 80 — не пишем» (`crawl/big_sites.py`),
списки «кому не пишем» (`crawl/denylist.py`) и срок записи поставщика.

**Почему окружение, а не версии в базе, как у порогов Этапа 1.** Версия порогов
Этапа 1 существует, чтобы смена порога НЕ переписывала вердикты прошлых прогонов.
Пересчёт кандидатов Этапа 2 устроен наоборот: он обязан заменить прежний вердикт
вердиктом новых весов (`crawl/gate.py`), а причины балла хранятся текстом
у кандидата — по ним вердикт объясним и после правки. Значения, которые действуют,
показывает экран «Пороги» (`GET /settings/advertisers`).

Значения по умолчанию — замеренные и записанные в каноне (`okf/link-scoring.md`).
Список задаётся окружением целиком, JSON-массивом:
`ADVERTISERS_DENY_MEDIA='["bbc.com", "cnn.com"]'`. Запись, которая не похожа на домен
или суффикс, — отказ при старте, а не молчаливый пропуск.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, field_validator, model_validator

from backend.config._base import DomainSettings


class _Advertisers(DomainSettings):
    # --- Скоринг ссылок: шкала требования ---
    points_rel_sponsored: int = Field(
        default=5, ge=0, le=20, validation_alias="ADVERTISERS_POINTS_REL_SPONSORED"
    )
    points_marker: int = Field(default=4, ge=0, le=20, validation_alias="ADVERTISERS_POINTS_MARKER")
    points_dofollow_in_body: int = Field(
        default=2, ge=0, le=20, validation_alias="ADVERTISERS_POINTS_DOFOLLOW_IN_BODY"
    )
    points_commercial_anchor: int = Field(
        default=1, ge=0, le=20, validation_alias="ADVERTISERS_POINTS_COMMERCIAL_ANCHOR"
    )
    points_off_topic: int = Field(
        default=1, ge=0, le=20, validation_alias="ADVERTISERS_POINTS_OFF_TOPIC"
    )
    # --- Скоринг ссылок: признаки из замера (`okf/link-scoring.md`) ---
    points_affiliate: int = Field(
        default=2, ge=0, le=20, validation_alias="ADVERTISERS_POINTS_AFFILIATE"
    )
    points_paid_source: int = Field(
        default=2, ge=0, le=20, validation_alias="ADVERTISERS_POINTS_PAID_SOURCE"
    )
    points_repeated: int = Field(
        default=3, ge=0, le=20, validation_alias="ADVERTISERS_POINTS_REPEATED"
    )
    repeat_pages: int = Field(default=3, ge=2, le=100, validation_alias="ADVERTISERS_REPEAT_PAGES")
    # Усилитель требования: «домен-получатель встречается на ≥ 2 наших донорах».
    boost_donors: int = Field(default=2, ge=2, le=100, validation_alias="ADVERTISERS_BOOST_DONORS")
    points_paid_looking: int = Field(
        default=2, ge=0, le=20, validation_alias="ADVERTISERS_POINTS_PAID_LOOKING"
    )
    # --- Пороги вердикта: «≥ 4 — купленная, 2–3 — на ручную проверку» ---
    bought_at: int = Field(default=4, ge=1, le=50, validation_alias="ADVERTISERS_BOUGHT_AT")
    review_at: int = Field(default=2, ge=1, le=50, validation_alias="ADVERTISERS_REVIEW_AT")
    # --- Кому не пишем ---
    # «Домены DR > 80»: ровно этот DR — ещё пишем.
    max_dr: int = Field(default=80, ge=0, le=100, validation_alias="ADVERTISERS_MAX_DR")
    deny_zones: list[str] = Field(
        default=[".gov", ".gov.uk", ".gov.za", ".mil", ".edu", ".ac.uk", ".ac.za", ".edu.au"],
        validation_alias="ADVERTISERS_DENY_ZONES",
    )
    deny_reference: list[str] = Field(
        default=[
            "wikipedia.org", "wikimedia.org", "wiktionary.org", "britannica.com",
            "archive.org", "doi.org", "arxiv.org", "researchgate.net", "scholar.google.com",
        ],
        validation_alias="ADVERTISERS_DENY_REFERENCE",
    )  # fmt: skip
    deny_social: list[str] = Field(
        default=[
            "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
            "youtube.com", "youtu.be", "tiktok.com", "pinterest.com", "reddit.com",
            "t.me", "telegram.me", "whatsapp.com", "threads.net", "vk.com",
        ],
        validation_alias="ADVERTISERS_DENY_SOCIAL",
    )  # fmt: skip
    deny_infrastructure: list[str] = Field(
        default=[
            "statcounter.com", "regery.com", "google-analytics.com", "googletagmanager.com",
            "cloudflare.com", "gravatar.com", "wordpress.org", "wordpress.com", "wp.com",
            "jquery.com", "w3.org", "schema.org", "gstatic.com", "googleapis.com",
            "monsterinsights.com", "yoast.com", "elementor.com", "cookiebot.com",
        ],
        validation_alias="ADVERTISERS_DENY_INFRASTRUCTURE",
    )  # fmt: skip
    deny_shorteners: list[str] = Field(
        default=["bit.ly", "goo.gl", "tinyurl.com", "ow.ly", "buff.ly", "cutt.ly", "t.co", "rb.gy"],
        validation_alias="ADVERTISERS_DENY_SHORTENERS",
    )
    deny_media: list[str] = Field(
        default=[
            "bbc.com", "bbc.co.uk", "cnn.com", "reuters.com", "theguardian.com",
            "nytimes.com", "forbes.com", "bloomberg.com", "espn.com", "skysports.com",
            "independent.co.uk", "dailymail.co.uk", "news24.com", "iol.co.za",
            "hbr.org", "mybroadband.co.za",
        ],
        validation_alias="ADVERTISERS_DENY_MEDIA",
    )  # fmt: skip
    deny_documents: list[str] = Field(
        default=[".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"],
        validation_alias="ADVERTISERS_DENY_DOCUMENTS",
    )
    # Зона, которой «куплено» без человека не дают: фонды и сообщества.
    nonprofit_zones: list[str] = Field(
        default=[".org"], validation_alias="ADVERTISERS_NONPROFIT_ZONES"
    )

    # --- Стоп-лист поставщиков: строка требования «стоп-лист доноров» ---
    # Сколько держит запись поставщика, внесённая списком: требование считает
    # «всех, у кого агентство размещалось за 12 мес.», то есть окно съезжает.
    # Текущий партнёр держится бессрочно — это флаг команды, а не срок.
    supplier_term_months: int = Field(
        default=12, ge=1, validation_alias="ADVERTISERS_SUPPLIER_TERM_MONTHS"
    )

    @field_validator(
        "deny_reference",
        "deny_social",
        "deny_infrastructure",
        "deny_shorteners",
        "deny_media",
    )
    @classmethod
    def _domains(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().lower() for value in values]
        bad = [value for value in cleaned if "." not in value or value.startswith(".")]
        if bad:
            raise ValueError(f"не похоже на домен: {bad} — нужен корень сайта, `site.com`")
        return cleaned

    @field_validator("deny_zones", "deny_documents", "nonprofit_zones")
    @classmethod
    def _suffixes(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().lower() for value in values]
        bad = [value for value in cleaned if not value.startswith(".") or len(value) < 2]
        if bad:
            raise ValueError(f"не похоже на суффикс: {bad} — нужна точка в начале, `.gov`")
        return cleaned

    @model_validator(mode="after")
    def _thresholds_in_order(self) -> Self:
        if self.review_at >= self.bought_at:
            raise ValueError(
                f"ADVERTISERS_REVIEW_AT ({self.review_at}) должен быть меньше "
                f"ADVERTISERS_BOUGHT_AT ({self.bought_at}): между ними — «на ручную проверку»"
            )
        return self


_s = _Advertisers()

POINTS_REL_SPONSORED: int = _s.points_rel_sponsored
POINTS_MARKER: int = _s.points_marker
POINTS_DOFOLLOW_IN_BODY: int = _s.points_dofollow_in_body
POINTS_COMMERCIAL_ANCHOR: int = _s.points_commercial_anchor
POINTS_OFF_TOPIC: int = _s.points_off_topic
POINTS_AFFILIATE: int = _s.points_affiliate
POINTS_PAID_SOURCE: int = _s.points_paid_source
POINTS_REPEATED: int = _s.points_repeated
REPEAT_PAGES: int = _s.repeat_pages
BOOST_DONORS: int = _s.boost_donors
POINTS_PAID_LOOKING: int = _s.points_paid_looking
BOUGHT_AT: int = _s.bought_at
REVIEW_AT: int = _s.review_at
MAX_DR: int = _s.max_dr
DENY_ZONES: tuple[str, ...] = tuple(_s.deny_zones)
DENY_REFERENCE: frozenset[str] = frozenset(_s.deny_reference)
DENY_SOCIAL: frozenset[str] = frozenset(_s.deny_social)
DENY_INFRASTRUCTURE: frozenset[str] = frozenset(_s.deny_infrastructure)
DENY_SHORTENERS: frozenset[str] = frozenset(_s.deny_shorteners)
DENY_MEDIA: frozenset[str] = frozenset(_s.deny_media)
DENY_DOCUMENTS: tuple[str, ...] = tuple(_s.deny_documents)
NONPROFIT_ZONES: tuple[str, ...] = tuple(_s.nonprofit_zones)
SUPPLIER_TERM_MONTHS: int = _s.supplier_term_months


def setting_name(field: str) -> str:
    """Имя переменной окружения поля — для экрана: где это меняется."""
    alias = _Advertisers.model_fields[field].validation_alias
    return str(alias)
