"""Правило региона донора.

Донор подходит по региону, если целевая страна входит в топ-N стран по
органическому трафику ЛИБО даёт не меньше заданной доли этого трафика.

Про знаменатель. Доля считается от `org_traffic` домена целиком, а не от суммы
стран в ответе. Запрос по странам мы делаем с `limit=5` (без него он стоит
1650 юнитов вместо 55, см. okf/unit-economy.md), и эти пять стран покрывают лишь
часть трафика — на замере techcrunch.com 86%. Деление на их сумму завысило бы
каждую долю примерно на шестую часть, и домены у границы порога проходили бы
фильтр, не имея на это права.

Почему `limit=5` вообще допустим. Если считать долю от суммы стран, нужны все строки, и запрос дорожает в тридцать раз. Нам хватает пяти без
лимита именно потому, что их гейты считают долю целевой страны от суммы стран
и потому нуждаются во всех строках. Нам хватает пяти по арифметической причине:
страна с долей не меньше 20% не может оказаться за пределами топ-5, иначе пять
стран выше неё дали бы больше 100% трафика. Условие звучит так:

    top_n * min_share >= 1

При настройках по умолчанию (5 и 20%) оно выполняется впритык. Если порог доли
опустить ниже 20% или сузить топ, ответа с пятью строками перестанет хватать —
`assert_settings_allow_limited_fetch` об этом скажет, а не промолчит.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.config import filters

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CountryShare:
    """Страна, её органический трафик и доля от всего трафика домена."""

    country: str
    org_traffic: int
    share: float


@dataclass(frozen=True, slots=True)
class GeoVerdict:
    """Решение по региону и то, чем оно объясняется."""

    passed: bool
    reason: str
    top_country: str | None
    breakdown: list[CountryShare]


def build_breakdown(rows: list[dict[str, object]], total_org_traffic: int) -> list[CountryShare]:
    """Строки ответа Ahrefs → доли, по убыванию трафика.

    Пустой ответ и нулевой трафик дают пустую разбивку: это «данных нет»,
    а не «не подходит».
    """
    if not rows or total_org_traffic <= 0:
        return []

    shares = []
    skipped = 0
    for row in rows:
        country = row.get("country")
        traffic = row.get("org_traffic")
        if not isinstance(country, str) or not isinstance(traffic, int | float):
            # Строку без страны или трафика пропускаем, но считаем: если
            # провайдер переименует поля, пропущенными окажутся все, и
            # домен станет «нет данных по странам» без всякой причины.
            skipped += 1
            continue
        shares.append(
            CountryShare(
                country=country.lower(),
                org_traffic=int(traffic),
                share=float(traffic) / total_org_traffic,
            )
        )
    if skipped and not shares:
        logger.warning(
            "Ни одна из %s строк по странам не разобрана — вероятно, изменились имена полей",
            skipped,
        )
    elif skipped:
        logger.debug("Пропущено строк по странам: %s из %s", skipped, len(rows))

    shares.sort(key=lambda s: s.org_traffic, reverse=True)
    return shares


def assert_settings_allow_limited_fetch(
    top_n: int = filters.GEO_TOP_N, min_share: float = filters.GEO_MIN_SHARE
) -> None:
    """Проверяет, что ответа с `limit=top_n` достаточно для правила.

    Вызывается перед прогоном. Если порог доли опустили, а лимит оставили,
    фильтр начнёт молча отсеивать подходящие домены: страна с долей 10% может
    быть шестой, и мы её просто не увидим.
    """
    if top_n * min_share < 1.0:
        raise ValueError(
            f"При топ-{top_n} и пороге доли {min_share:.0%} страна с достаточной долей "
            f"может не попасть в ответ. Нужно либо поднять порог до "
            f"{1 / top_n:.0%}, либо запрашивать страны без лимита — но это "
            f"1650 юнитов на домен вместо 55."
        )


def check_geo(
    target_country: str,
    breakdown: list[CountryShare],
    *,
    top_n: int = filters.GEO_TOP_N,
    min_share: float = filters.GEO_MIN_SHARE,
) -> GeoVerdict:
    """Проходит ли домен по региону.

    Оба условия из  проверяются явно, хотя при текущих настройках второе
    математически не добавляет ничего: чтобы иметь долю 20% и не попасть в топ-5,
    нужно пять стран с долей больше 20% каждая, то есть больше 100% трафика.
    Условие начинает работать, когда `top_n * min_share < 1` — например, при
    топ-3 и пороге 20%. Поэтому оно и оставлено в коде, а не свёрнуто.
    """
    if not breakdown:
        return GeoVerdict(False, "нет данных по странам", None, [])

    wanted = target_country.lower()
    top_country = breakdown[0].country

    for position, item in enumerate(breakdown[:top_n], start=1):
        if item.country == wanted:
            return GeoVerdict(
                True,
                f"{wanted} на {position}-м месте по трафику ({item.share:.0%})",
                top_country,
                breakdown,
            )

    for item in breakdown:
        if item.country == wanted and item.share >= min_share:
            return GeoVerdict(
                True, f"{wanted} даёт {item.share:.0%} трафика", top_country, breakdown
            )

    return GeoVerdict(
        False,
        f"{wanted} не входит в топ-{top_n} и даёт меньше {min_share:.0%}",
        top_country,
        breakdown,
    )
