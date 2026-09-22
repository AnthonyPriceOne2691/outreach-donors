"""Учёт расхода юнитов и предрасчёт сметы прогона.

Две задачи, и обе про деньги.

Первая — знать, сколько списали на самом деле. Ahrefs возвращает это тремя
заголовками, и без их разбора учёт расхода превращается в гадание.

Вторая — сказать, сколько прогон будет стоить, ДО запуска. Цены ниже
замерены живым ключом 18.09.2026, подробности в okf/unit-economy.md. Прогон
сверяет смету с фактом: расхождение значит, что цены у провайдера изменились
и таблицу пора перемерить.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# --- Замеренные цены (okf/unit-economy.md) ---

# Пакетный запрос только с domain_rating, пачками по 100 доменов.
UNITS_DR_SCREEN = 2
# Пакетный запрос со всеми четырьмя порогами и верхней страной.
# Замер 22.09.2026: без страны 18 юнитов на домен, со страной 28.
# Десять юнитов сверху окупаются тем, что отдельный запрос по странам
# стоит 55 и делается по одному домену.
UNITS_FULL_METRICS = 28
# metrics-by-country с limit=5. БЕЗ limit тот же запрос стоит 1650:
# он возвращает все 150 стран, и платим за каждую.
UNITS_BY_COUNTRY = 55
# Доля доменов, которым верхней страны из пакета не хватило: у них наверху
# не целевая страна, и про целевую мы не знаем ничего. Замер 22.09.2026
# на 132 годных донорах прогонов по США и ЮАР: верхняя совпала с целевой
# у 118, то есть платить пришлось бы примерно каждому девятому.
# Число консервативное: смета лучше завысит, чем недосчитает.
COUNTRY_CALL_SHARE = 0.2
# Минимум на любой запрос. Из-за него пачка из 10 доменов на просеве стоит
# 50 юнитов, а не 20, — то есть 5 на домен вместо 2.
MIN_REQUEST_UNITS = 50
# Больше провайдер в один пакет не принимает.
MAX_BATCH_TARGETS = 100


@dataclass(frozen=True, slots=True)
class UnitsCost:
    """Сколько стоил один запрос, по заголовкам ответа."""

    actual: int | None
    """`x-api-units-cost-total-actual` — фактически списано."""

    estimated: int | None
    """`x-api-units-cost-total` — оценка провайдера до выполнения."""

    per_row: int | None
    """`x-api-units-cost-row` — цена одной строки; по ней видно,
    сработал ли минимум в 50 юнитов."""

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> UnitsCost:
        """Разбирает заголовки ответа. Отсутствие или мусор дают None,
        а не ноль: ноль означал бы «бесплатно», а это не то же самое."""

        def read(name: str) -> int | None:
            raw = headers.get(name) or headers.get(name.title())
            try:
                return int(raw) if raw is not None else None
            except (TypeError, ValueError):
                logger.debug("Заголовок %s нечитаем: %r", name, raw)
                return None

        return cls(
            actual=read("x-api-units-cost-total-actual"),
            estimated=read("x-api-units-cost-total"),
            per_row=read("x-api-units-cost-row"),
        )

    @property
    def known(self) -> bool:
        """Провайдер сказал, во что обошёлся запрос.

        Отличать это от нулевой цены обязательно. Ahrefs кэширует ответы на
        своей стороне и за повторный запрос берёт ноль — тогда `actual = 0`
        при непустом `estimated`. Такой запрос был, он бесплатен, и записать
        его надо: журнал без бесплатных строк выглядит так, будто запросов
        не делали, и по нему нельзя увидеть, что кэш провайдера работает.
        """
        return self.actual is not None or self.estimated is not None

    @property
    def billable(self) -> int:
        """Сколько записать в расход. При отсутствии факта берём оценку,
        при отсутствии обеих — ноль, но это уже неизвестность, а не бесплатность."""
        if self.actual is not None:
            return self.actual
        return self.estimated or 0

    @property
    def was_free(self) -> bool:
        """Запрос обслужен кэшем провайдера."""
        return self.actual == 0 and bool(self.estimated)


def batch_cost(targets: int, units_per_domain: int) -> int:
    """Цена одного пакетного запроса с учётом минимума.

    Пачка из 10 доменов на просеве стоит не 20 юнитов, а 50 — поэтому
    собирать пачки меньше `MIN_REQUEST_UNITS / units_per_domain` невыгодно.
    """
    if targets <= 0:
        return 0
    return max(MIN_REQUEST_UNITS, targets * units_per_domain)


@dataclass(frozen=True, slots=True)
class RunEstimate:
    """Смета прогона по ступеням."""

    domains: int
    screen: int
    metrics: int
    by_country: int

    @property
    def total(self) -> int:
        return self.screen + self.metrics + self.by_country

    @property
    def per_domain(self) -> float:
        return self.total / self.domains if self.domains else 0.0


def estimate_run(
    new_domains: int,
    *,
    dr_pass_share: float = 0.83,
    all_pass_share: float = 0.39,
    batch_size: int = MAX_BATCH_TARGETS,
) -> RunEstimate:
    """Сколько юнитов уйдёт на прогон по трём ступеням.

    Доли прохождения по умолчанию — замер на 200 сырых доменах выдачи.
    Они уточняются по мере накопления своей статистики: на нише, отличной
    от замеренной, воронка будет другой.

    Домены со свежими данными в смету не входят — за них уже заплачено,
    поэтому на вход идёт число НОВЫХ доменов.
    """
    if new_domains <= 0:
        return RunEstimate(0, 0, 0, 0)

    def staged(count: int, units_per_domain: int) -> int:
        full, tail = divmod(count, batch_size)
        total = full * batch_cost(batch_size, units_per_domain)
        return total + batch_cost(tail, units_per_domain)

    passed_dr = int(new_domains * dr_pass_share)
    passed_all = int(new_domains * all_pass_share)

    return RunEstimate(
        domains=new_domains,
        screen=staged(new_domains, UNITS_DR_SCREEN),
        metrics=staged(passed_dr, UNITS_FULL_METRICS),
        # Запрос по странам идёт на один домен, пакетного аналога нет —
        # и зовётся он только для тех, кому верхней страны не хватило.
        by_country=int(passed_all * COUNTRY_CALL_SHARE) * UNITS_BY_COUNTRY,
    )


@dataclass(frozen=True, slots=True)
class Quota:
    """Остаток юнитов по данным Ahrefs.

    Лимитов два и действуют они одновременно: на рабочее пространство и на
    конкретный ключ. Доступно нам меньшее из остатков — упереться можно
    в любой, и упираются в них обе системы на общем ключе.
    """

    workspace_limit: int
    workspace_used: int
    key_limit: int
    key_used: int
    reset_date: str | None = None

    @classmethod
    def from_payload(cls, data: Mapping[str, object]) -> Quota:
        def read(name: str) -> int:
            value = data.get(name)
            return int(value) if isinstance(value, int | float) else 0

        reset = data.get("usage_reset_date")
        return cls(
            workspace_limit=read("units_limit_workspace"),
            workspace_used=read("units_usage_workspace"),
            key_limit=read("units_limit_api_key"),
            key_used=read("units_usage_api_key"),
            reset_date=reset if isinstance(reset, str) else None,
        )

    @property
    def available(self) -> int:
        """Сколько реально можно потратить прямо сейчас."""
        return max(
            0, min(self.workspace_limit - self.workspace_used, self.key_limit - self.key_used)
        )


@dataclass(slots=True)
class UsageCollector:
    """Копилка трат между чекпоинтами.

    Клиент сообщает о расходе синхронным вызовом, а журнал пишется в базу —
    асинхронно. Складывать одно в другое напрямую значит тянуть в транспорт
    знание про сессию. Поэтому траты копятся здесь, а прогон сливает их
    в журнал на каждом чекпоинте и обязательно — при остановке.
    """

    pending: list[tuple[str, UnitsCost]] = field(default_factory=list)

    def __call__(self, operation: str, cost: UnitsCost) -> None:
        self.pending.append((operation, cost))

    def drain(self) -> list[tuple[str, UnitsCost]]:
        """Отдаёт накопленное и очищает копилку."""
        collected, self.pending = self.pending, []
        return collected
