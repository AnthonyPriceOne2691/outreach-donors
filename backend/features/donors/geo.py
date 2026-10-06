"""Правило региона донора.

Донор подходит по региону, если целевая страна входит в топ-N стран по
органическому трафику ЛИБО даёт не меньше заданной доли этого трафика.

Про знаменатель. Доля считается от `org_traffic` домена целиком, а не от суммы
стран в ответе. Запрос по странам мы делаем с `limit=5` (без него он стоит
1650 юнитов вместо 55, см. okf/unit-economy.md), и эти пять стран покрывают лишь
часть трафика — на замере techcrunch.com 86%. Деление на их сумму завысило бы
каждую долю примерно на шестую часть, и домены у границы порога проходили бы
фильтр, не имея на это права.

Исключение одно — когда сумма стран больше трафика домена (`share_base`): числа
приходят разными запросами и расходятся, и тогда база — сумма стран.

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
from collections.abc import Iterable
from dataclasses import dataclass

from backend.config import filters
from backend.config.startup_checks import ConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CountryShare:
    """Страна, её органический трафик и доля от всего трафика домена (база — `share_base`)."""

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
    #: Разбивка неполная: спросили только верхнюю страну, потому что её
    #: хватило для вердикта. Флаг нужен, чтобы карточка донора говорила
    #: правду: одна строка у полного ответа и одна строка у дешёвого —
    #: это разные вещи, и по длине списка их не отличить.
    partial: bool = False


def share_base(total_org_traffic: float, country_traffic: Iterable[float]) -> float:
    """Знаменатель доли страны: трафик домена — или сумма стран, если она больше.

    Трафик домена приходит пакетным анализом, страны — своим отчётом (или
    колонкой верхней страны), и у них свой счёт: боевой прогон показал на экране
    доноров «Канада · 128%» — одна страна дала больше, чем весь домен (правка
    07.10.2026). Сумма стран из ответа — нижняя граница трафика, который этот
    ответ видел, поэтому при расхождении база — она: ни одна доля не выходит
    за 100%, сумма долей — за единицу. Без расхождения база — трафик домена,
    как и прежде (см. «Про знаменатель» в начале модуля).

    Сумма долей не больше единицы — то, на чём стоит `top_n * min_share >= 1`:
    со старым знаменателем пять стран «давали» больше 100% трафика, и довод
    «страна с долей 20% не может быть шестой» переставал быть доводом.
    """
    return max(float(total_org_traffic), float(sum(country_traffic)))


def build_breakdown(rows: list[dict[str, object]], total_org_traffic: int) -> list[CountryShare]:
    """Строки ответа Ahrefs → доли, по убыванию трафика.

    Пустой ответ и нулевой трафик дают пустую разбивку: это «данных нет»,
    а не «не подходит».
    """
    if not rows or total_org_traffic <= 0:
        return []

    readable = _country_traffic(rows)
    # База — после разбора всех строк: она зависит от суммы стран.
    base = share_base(total_org_traffic, (traffic for _, traffic in readable))
    shares = [
        CountryShare(country=country, org_traffic=int(traffic), share=traffic / base)
        for country, traffic in readable
    ]
    shares.sort(key=lambda s: s.org_traffic, reverse=True)
    return shares


def _country_traffic(rows: list[dict[str, object]]) -> list[tuple[str, float]]:
    """Пары «страна, трафик» из строк ответа — без строк, которые не разобрать."""
    readable: list[tuple[str, float]] = []
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
        readable.append((country.lower(), float(traffic)))
    if skipped and not readable:
        logger.warning(
            "Ни одна из %s строк по странам не разобрана — вероятно, изменились имена полей",
            skipped,
        )
    elif skipped:
        logger.debug("Пропущено строк по странам: %s из %s", skipped, len(rows))
    return readable


def assert_settings_allow_limited_fetch(
    top_n: int = filters.GEO_TOP_N, min_share: float = filters.GEO_MIN_SHARE
) -> None:
    """Проверяет, что ответа с `limit=top_n` достаточно для правила.

    Вызывается перед прогоном. Если порог доли опустили, а лимит оставили,
    фильтр начнёт молча отсеивать подходящие домены: страна с долей 10% может
    быть шестой, и мы её просто не увидим.

    Несогласованность — ошибка настроек (`ConfigError`): точка входа скажет
    «Не хватает настроек» с кодом 2, а не упадёт трассировкой.
    """
    if top_n * min_share < 1.0:
        raise ConfigError(
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
                f"{wanted.upper()} на {position}-м месте по трафику ({item.share:.0%})",
                top_country,
                breakdown,
            )

    for item in breakdown:
        if item.country == wanted and item.share >= min_share:
            return GeoVerdict(
                True, f"{wanted.upper()} даёт {item.share:.0%} трафика", top_country, breakdown
            )

    return GeoVerdict(
        False,
        f"{wanted.upper()} не входит в топ-{top_n} и даёт меньше {min_share:.0%}",
        top_country,
        breakdown,
    )
