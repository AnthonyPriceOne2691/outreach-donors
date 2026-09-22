"""Транспорт к Ahrefs: один запрос, его повторы и учёт расхода.

Три вещи, которые этот модуль обязан делать, и каждая из них про деньги
или про доступность ключа.

**Расход считается на каждом ответе.** Даже на неудачном: запрос, упавший
после списания, всё равно списал. Учёт идёт через `on_usage`, а не пишется
тут в базу — транспорт не должен знать про хранилище.

**Частота держится ниже лимита провайдера.** У Ahrefs динамический троттлинг
около 60 запросов в минуту; при превышении он отвечает 429, и дальше уже не
важно, сколько у нас осталось юнитов.

**`limit` в запросе по странам подставляется всегда.** Не параметром, который
можно забыть, а внутри метода: без него запрос возвращает все 150 стран и
стоит 1650 юнитов вместо 55 (okf/unit-economy.md).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from backend.config import ahrefs as cfg
from backend.features.ahrefs.units import MAX_BATCH_TARGETS, UnitsCost

logger = logging.getLogger(__name__)

# Повторяем на этих статусах: провайдер занят или сломался на своей стороне.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# На этих повторять бессмысленно — ключ, права или сам запрос.
FATAL_STATUSES = frozenset({400, 401, 403, 404, 422})

MAX_ATTEMPTS = 4
BACKOFF_BASE_SEC = 2.0
# Потолок ожидания: `Retry-After` иногда приходит в минутах, и слепо ему
# доверившись, можно повесить прогон на полчаса.
MAX_BACKOFF_SEC = 60.0


class AhrefsError(RuntimeError):
    """Запрос не удался и повторять его незачем."""


@dataclass(slots=True)
class Response:
    """Ответ и то, во что он обошёлся."""

    rows: list[dict[str, Any]]
    cost: UnitsCost


@dataclass(slots=True)
class _RateLimiter:
    """Простой ограничитель: не больше N запросов в минуту.

    Скользящего окна тут достаточно — очередь у нас одна, и точность до
    десятых долей секунды не нужна. Важно лишь не влететь в 429.
    """

    per_minute: int
    _times: list[float] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._times = [t for t in self._times if now - t < 60.0]
                if len(self._times) < self.per_minute:
                    self._times.append(now)
                    return
                await asyncio.sleep(60.0 - (now - self._times[0]) + 0.01)


def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
    """Сколько ждать перед повтором.

    `Retry-After` провайдера уважаем, но не безоговорочно: значение
    подрезается потолком, иначе один ответ способен остановить прогон надолго.
    """
    if response is not None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                return min(float(raw), MAX_BACKOFF_SEC)
            except ValueError:
                # Провайдер прислал Retry-After в формате, который мы не читаем
                # (например, дату вместо секунд). Откатываемся на экспоненту,
                # но молчать об этом нельзя: если формат сменился насовсем,
                # мы будем ждать не столько, сколько просят.
                logger.debug("Retry-After не разобран: %r — беру экспоненту", raw)
    return min(BACKOFF_BASE_SEC**attempt, MAX_BACKOFF_SEC)


class AhrefsClient:
    """Клиент Ahrefs. Ходит только туда, куда нужно нам, и считает расход."""

    def __init__(
        self,
        *,
        api_key: str = cfg.API_KEY,
        base_url: str = cfg.BASE_URL,
        timeout_s: float = cfg.TIMEOUT_S,
        rate_limit_per_min: int = cfg.RATE_LIMIT_PER_MIN,
        on_usage: Callable[[str, UnitsCost], None] | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.on_usage = on_usage
        """Куда сообщать о расходе. Открыто намеренно: прогон подключает свою
        копилку к уже созданному клиенту, а транспорт про базу не знает."""
        self._limiter = _RateLimiter(per_minute=rate_limit_per_min)
        self._http = http or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def batch_metrics(self, hosts: Sequence[str], select: Sequence[str]) -> Response:
        """Метрики пачкой. Больше `MAX_BATCH_TARGETS` за раз провайдер не берёт.

        Мелкие пачки невыгодны из-за минимума в 50 юнитов на запрос: десять
        доменов на просеве стоят столько же, сколько двадцать пять.
        """
        if not hosts:
            return Response(rows=[], cost=UnitsCost(None, None, None))
        if len(hosts) > MAX_BATCH_TARGETS:
            raise ValueError(
                f"В один пакет помещается {MAX_BATCH_TARGETS} доменов, передано {len(hosts)}"
            )
        payload = {
            "select": list(select),
            "targets": [{"url": h, "mode": "subdomains", "protocol": "both"} for h in hosts],
        }
        return await self._request(
            "POST", "/v3/batch-analysis/batch-analysis", operation="batch_metrics", json=payload
        )

    async def metrics_by_country(self, host: str, date: str, *, top_n: int = 5) -> Response:
        """Органический трафик по странам — только топ-N.

        `limit` и `order_by` подставляются здесь и всегда. Сделать их
        параметром вызова значило бы однажды его не передать и заплатить
        в тридцать раз больше, ничего не заметив.
        """
        if top_n <= 0:
            raise ValueError("top_n должен быть положительным: без лимита запрос стоит 1650 юнитов")
        params = {
            "target": host,
            "date": date,
            "mode": "subdomains",
            "protocol": "both",
            "volume_mode": "monthly",
            "select": "country,org_traffic",
            "order_by": "org_traffic:desc",
            "limit": top_n,
        }
        return await self._request(
            "GET", "/v3/site-explorer/metrics-by-country", operation="by_country", params=params
        )

    async def limits_and_usage(self) -> dict[str, Any]:
        """Остаток квоты. Запрос бесплатный — проверено замером.

        Это единственный источник правды об остатке. Своя таблица расхода
        знает только про наши траты, а ключ общий с соседней
        системой: сосед в нашу таблицу не пишет и не должен.
        """
        try:
            response = await self._http.get("/v3/subscription-info/limits-and-usage")
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            # Наружу идёт один тип ошибки: вызывающему важно не «что сломалось»,
            # а «остаток неизвестен, тратить нельзя».
            raise AhrefsError(f"Остаток квоты недоступен: {exc}") from exc
        data = payload.get("limits_and_usage") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise AhrefsError("Ahrefs не вернул остаток квоты")
        return data

    async def serp_overview(self, keyword: str, country: str, date: str) -> Response:
        """Выдача по ключевому слову.

        `select` намеренно минимальный: с метриками домена тот же запрос
        стоит 481 юнит вместо 74, а DR и трафик дешевле взять пакетом
        (2 и 18 юнитов на домен). Эндпоинт запасной — основной источник
        выдачи дешевле в двадцать раз, см. okf/unit-economy.md.
        """
        params = {
            "keyword": keyword,
            "country": country.lower(),
            "date": date,
            "select": "position,url",
        }
        return await self._request(
            "GET", "/v3/serp-overview/serp-overview", operation="serp", params=params
        )

    async def _request(self, method: str, path: str, *, operation: str, **kwargs: Any) -> Response:
        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.acquire()
            try:
                response = await self._http.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.debug(
                    "Ahrefs %s: сетевая ошибка на попытке %s из %s — %s",
                    operation,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )
                if attempt == MAX_ATTEMPTS:
                    break
                await asyncio.sleep(_retry_delay(attempt, None))
                continue

            # Расход считаем до разбора тела: неудачный запрос тоже списывает.
            cost = UnitsCost.from_headers(response.headers)
            if cost.actual is None and cost.estimated is None:
                # Расход неизвестен. Молча записать ноль значит занизить учёт
                # и однажды удивиться счёту: ключ общий с соседней системой.
                logger.warning("Ahrefs %s не вернул заголовки расхода — трата не учтена", operation)
            if self.on_usage is not None and cost.known:
                # Записываем и бесплатные запросы: Ahrefs отдаёт из кэша
                # за ноль, и журнал без этих строк выглядит так, будто
                # запросов не делали.
                self.on_usage(operation, cost)

            if response.status_code in FATAL_STATUSES:
                raise AhrefsError(f"{operation}: {response.status_code} {response.text[:200]}")
            if response.status_code in RETRY_STATUSES:
                last_error = AhrefsError(f"{operation}: {response.status_code}")
                if attempt == MAX_ATTEMPTS:
                    break
                delay = _retry_delay(attempt, response)
                logger.warning(
                    "Ahrefs %s ответил %s, повтор через %.1f с (попытка %s из %s)",
                    operation,
                    response.status_code,
                    delay,
                    attempt,
                    MAX_ATTEMPTS,
                )
                await asyncio.sleep(delay)
                continue

            response.raise_for_status()
            return Response(rows=_rows(response.json(), operation), cost=cost)

        raise AhrefsError(f"{operation}: не удалось за {MAX_ATTEMPTS} попыток") from last_error


def _rows(payload: Any, operation: str) -> list[dict[str, Any]]:
    """Ahrefs кладёт список под разными ключами в зависимости от эндпоинта
    (`metrics`, `domains`, …). Берём первый список — имя ключа нам не нужно.

    Форму, которую не удалось разобрать, считаем поломкой, а не пустым
    ответом. Разница принципиальная: пустой ответ значит «провайдер ничего
    не знает про эти домены», а неразобранная форма — «провайдер сменил
    ответ, и мы больше его не понимаем». Вернув на второе пустой список, мы
    получили бы прогон, где все домены «неизвестны», и искали бы причину
    в данных, а не в коде.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
            if isinstance(value, dict):
                return [value]
    raise AhrefsError(
        f"{operation}: ответ Ahrefs не разобран — ожидался список строк, "
        f"получено {type(payload).__name__}. Скорее всего, провайдер изменил "
        f"форму ответа."
    )
