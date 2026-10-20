"""Отказ вердикта — порогом: ключ причины в отчёте прогона и порог по словам вердикта.

Одно место на троих: отчёт прогона хранит причину отказа ключом (`reject_key`), экран
отчёта и вердикт прогона (`runs/verdicts.py`) узнают по ключу порог (`threshold_of`).
Слова — вердикта (`donors/verdict.py`, `donors/geo.py`): поменяют их — покраснеет
`tests/test_run_outcome.py`, а не экран тихо назовёт отсев «другой причиной».

**Крупный сайт — свой порог, а не «другая причина»** (аудит 20.10.2026). DR выше
порога версии — «кому не пишем» ТЗ, а не нижний порог DR: отчёт прогона №30 показывал
39 таких строкой «Другая причина: «DR > 80»». Ключ у него свой — «DR > 80», с порогом
версии, — и порог свой: `BIG`.
"""

from __future__ import annotations

from enum import StrEnum

from backend.features.donors.geo import region_word
from backend.features.donors.verdict import BIG_SITE_WORDS


class Threshold(StrEnum):
    """Порог, по которому домен отсеян. Значения уходят на экран кодом, слова — там."""

    DR = "dr"
    #: DR выше порога версии — «кому не пишем»: крупному сайту не пишем, взять нельзя.
    BIG = "big"
    TRAFFIC = "org_traffic"
    KEYWORDS = "keywords"
    REFDOMAINS = "refdomains"
    GEO = "geo"
    #: Причина, которой нет среди порогов: записана до сегодняшних слов вердикта или
    #: чем-то новым. Показывается как записана — не пропадает и не подменяется.
    OTHER = "other"


#: Первое слово причины отказа (`donors/verdict.py`) → порог. Регион — кодом страны.
FIRST_WORDS: dict[str, Threshold] = {
    "DR": Threshold.DR,
    "органический": Threshold.TRAFFIC,
    "ключей": Threshold.KEYWORDS,
    "реф.": Threshold.REFDOMAINS,
}

#: Ключ крупного сайта начинается так: «DR > 80» — с порогом версии.
BIG_KEY_PREFIX = "DR >"

#: Пороги на экране — в порядке проверок (`donors/collect.py`): DR и крупный сайт первой
#: ступенью, трафик, ключи и реф. домены второй, регион последним.
ORDER: tuple[Threshold, ...] = (
    Threshold.DR,
    Threshold.BIG,
    Threshold.TRAFFIC,
    Threshold.KEYWORDS,
    Threshold.REFDOMAINS,
    Threshold.GEO,
)


def reject_key(reason: str) -> str:
    """Причина отказа в отчёте — первым словом: «DR», «органический», «US»…

    Крупный сайт — своим ключом, с порогом версии («DR > 80»): первым словом он слился
    бы с «DR 12 ниже 20» под одним «DR», а это «кому не пишем», а не нижний порог.
    """
    if not reason:
        return "без причины"
    if reason.endswith(BIG_SITE_WORDS):
        return reason.split(" — ", maxsplit=1)[0]
    return reason.split(" ", maxsplit=1)[0]


def threshold_of(key: str, country: str) -> Threshold:
    """Порог по ключу причины. Регион вердикт пишет кодом страны прогона:
    «US не входит в топ-5…» (`donors/geo.region_word`)."""
    if key in FIRST_WORDS:
        return FIRST_WORDS[key]
    if key.startswith(BIG_KEY_PREFIX):
        return Threshold.BIG
    if region_word(key, country):
        return Threshold.GEO
    return Threshold.OTHER


def kind_of(reason: str, country: str) -> Threshold:
    """Порог по причине отказа целиком — тем же правилом, что у отчёта."""
    return threshold_of(reject_key(reason), country)
