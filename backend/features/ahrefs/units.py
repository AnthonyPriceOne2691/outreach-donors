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
from datetime import UTC, datetime

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
# Доля НОВЫХ доменов прогона, которым прогон купит запрос по странам, — когда
# своей истории у страны ещё нет. С 16.10.2026 запрос идёт каждому, кто прошёл
# пороги метрик (топ-5 стран — обязательное поле ТЗ), и доля — доля прошедших:
# настоящее число — худшее из недавних прогонов страны
# (`RunRepository.country_call_share`). В 16 прогонах 18–23.09.2026 пороги прошли
# 70–97% новых доменов выдачи (okf/funnel-calibration.md), и без истории — 0,9:
# у верхнего края, но не единица. Единица на всё — смета, которая не пускает
# прогон, помещающийся в кап, а занижение — кап, который не держит трату.
COUNTRY_CALL_SHARE = 0.9
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
    country_share: float = COUNTRY_CALL_SHARE,
    batch_size: int = MAX_BATCH_TARGETS,
) -> RunEstimate:
    """Сколько юнитов уйдёт на прогон по трём ступеням.

    **Смета — верхняя граница, а не среднее.** Месячный кап проверяется
    против неё до первого платного запроса, и заниженная смета значит, что
    кап трату не ограничивает. Так и было: доля прохождения просева по DR
    стояла 83% (сырые домены), а в выдаче его проходят 85–97%, и смета
    занижала трату в 14 прогонах из 14, до +132%.

    Поэтому полные метрики считаются на ВСЕ новые домены: просев по DR
    отсекает немного (0–15% по всем прогонам), и платить за его ошибку
    в сторону занижения нельзя. Доля доменов с запросом по странам приходит
    снаружи — с 16.10.2026 это доля прошедших пороги метрик, худшая из недавних
    прогонов своей страны (`RunRepository.country_call_share`).

    Домены со свежими данными в смету не входят — за них уже заплачено,
    поэтому на вход идёт число НОВЫХ доменов.
    """
    if new_domains <= 0:
        return RunEstimate(0, 0, 0, 0)

    def staged(count: int, units_per_domain: int) -> int:
        full, tail = divmod(count, batch_size)
        total = full * batch_cost(batch_size, units_per_domain)
        return total + batch_cost(tail, units_per_domain)

    return RunEstimate(
        domains=new_domains,
        screen=staged(new_domains, UNITS_DR_SCREEN),
        metrics=staged(new_domains, UNITS_FULL_METRICS),
        # Запрос по странам идёт на один домен, пакетного аналога нет, —
        # каждому, кто прошёл пороги метрик (с 16.10.2026).
        by_country=round(new_domains * max(0.0, min(1.0, country_share))) * UNITS_BY_COUNTRY,
    )


def _units(data: Mapping[str, object], name: str) -> int | None:
    """Число поля ответа об остатке; пусто (null или поля нет) — `None`, а не ноль.

    Не число — тоже `None`, но строкой журнала: это уже «мы не поняли ответ», а не
    «Ahrefs лимита не назвал», и молча путать одно с другим нельзя."""
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        logger.warning(
            "Ahrefs: поле остатка %s — не число (%r), читаю как не названное", name, value
        )
        return None
    return int(value)


def _reset_moment(raw: object) -> datetime | None:
    """Дата обнуления — моментом UTC. По документации Ahrefs API v3 поле — дата
    («2026-11-21»); ответ с временем («…T00:00:00Z») читается тоже. Пусто — `None`;
    не разобрано — `None` и строка журнала: цикл капа тогда календарный, и экран
    говорит это словами (`runs/ahrefs_key.cycle_of`)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        logger.warning("Ahrefs: дата обнуления %r не разобрана — цикл капа календарный", raw)
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _left(limit: int | None, used: int | None) -> int | None:
    """Остаток по одному лимиту; `None` — такого лимита нет. Лимит назван, а расход нет —
    ноль: остаток неизвестен, и тратить вслепую нельзя."""
    if limit is None:
        return None
    return max(0, limit - used) if used is not None else 0


@dataclass(frozen=True, slots=True)
class Quota:
    """Лимиты и расход юнитов по ответу Ahrefs (`subscription-info/limits-and-usage`).

    Лимитов два и действуют они одновременно: на рабочее пространство и на
    конкретный ключ. Доступно нам меньшее из остатков — упереться можно
    в любой, и упираются в них обе системы на общем ключе.

    **Пустой лимит — не ноль** (16.10.2026). По документации Ahrefs API v3 null бывает
    у `units_limit_api_key` (своего лимита у ключа нет — действует лимит пространства),
    у `units_limit_workspace` и `units_usage_workspace`; `units_usage_api_key` и дата
    обнуления — без null. До 16.10 пустое читалось нулём, и ключ без своего лимита
    выглядел выбранным целиком: `available` — ноль, прогоны стояли.
    """

    workspace_limit: int | None
    workspace_used: int | None
    #: Свой лимит ключа; `None` — своего нет, ключ упирается в лимит пространства.
    key_limit: int | None
    key_used: int | None
    #: Дата обнуления — как пришла: её печатает консоль.
    reset_date: str | None = None
    #: Следующее обнуление моментом UTC; `None` — Ahrefs не назвал или не разобрано.
    reset_at: datetime | None = None

    @classmethod
    def from_payload(cls, data: Mapping[str, object]) -> Quota:
        reset = data.get("usage_reset_date")
        return cls(
            workspace_limit=_units(data, "units_limit_workspace"),
            workspace_used=_units(data, "units_usage_workspace"),
            key_limit=_units(data, "units_limit_api_key"),
            key_used=_units(data, "units_usage_api_key"),
            reset_date=reset if isinstance(reset, str) else None,
            reset_at=_reset_moment(reset),
        )

    @property
    def size(self) -> int | None:
        """Размер ключа за цикл: свой лимит, а своего нет — лимит пространства. Не назван
        ни тот, ни другой — `None`: размер неизвестен, и планировать от него нечего."""
        return self.key_limit if self.key_limit is not None else self.workspace_limit

    @property
    def key_left(self) -> int | None:
        """Остаток своего лимита ключа; `None` — своего лимита нет."""
        return _left(self.key_limit, self.key_used)

    @property
    def workspace_left(self) -> int | None:
        """Остаток лимита пространства; `None` — Ahrefs его не назвал."""
        return _left(self.workspace_limit, self.workspace_used)

    @property
    def available(self) -> int:
        """Сколько реально можно потратить прямо сейчас: меньший из названных остатков.
        Не назван ни один лимит — ноль: размер неизвестен, вслепую не тратим."""
        named = [left for left in (self.key_left, self.workspace_left) if left is not None]
        return min(named) if named else 0


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
