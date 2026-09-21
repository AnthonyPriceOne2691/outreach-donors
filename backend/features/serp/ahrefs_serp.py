"""Выдача через Ahrefs `serp-overview`.

Это запасной и отладочный источник, не основной. Замер (okf/unit-economy.md):
74 юнита на ключевое слово против $0,0006 у DataForSEO — при равном
результате это дороже примерно в двадцать раз, а тратит тот ресурс,
которого у нас мало.

Зачем он всё-таки есть: отладка не требует чужого аккаунта и баланса,
а прогон переживёт падение основного провайдера. Десяток ключей на проверку
логики обходится в 740 юнитов, и это приемлемо.

Поля метрик в запрос НЕ включаются намеренно: с ними тот же запрос стоит
481 юнит вместо 74, а DR и трафик мы всё равно берём пакетом из
batch-analysis, где домен стоит 2 и 18 юнитов соответственно. Сам запрос
собирает клиент Ahrefs — знание об эндпоинтах провайдера живёт там, а не
размазано по адаптерам.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from backend.features.ahrefs.client import AhrefsClient
from backend.features.serp.protocol import SerpResult

RESULTS_PER_PAGE = 10


class AhrefsSerpProvider:
    """Источник выдачи поверх клиента Ahrefs."""

    name = "ahrefs"

    #: Деньгами этот источник не платит: его расход — юниты, и они едут
    #: в журнал копилкой клиента Ahrefs. Ноль здесь — не «бесплатно»,
    #: а «не в этой валюте».
    spent = 0.0

    def __init__(self, client: AhrefsClient) -> None:
        self._client = client

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        if depth_pages < 1:
            raise ValueError("Глубина выдачи считается страницами по десять; минимум одна")

        date = datetime.now(UTC).date().isoformat()
        wanted = depth_pages * RESULTS_PER_PAGE

        # У этого провайдера запрос синхронный и быстрый, поэтому пачка
        # раскладывается в последовательные вызовы. Ограничитель частоты
        # в клиенте не даёт превысить лимит Ahrefs.
        out: dict[str, list[SerpResult]] = {}
        for keyword in keywords:
            response = await self._client.serp_overview(keyword, country, date)
            out[keyword] = _take_organic(response.rows, wanted)
        return out


def _take_organic(rows: list[dict[str, Any]], wanted: int) -> list[SerpResult]:
    """Органические позиции из ответа.

    Строки без адреса пропускаются: Ahrefs отдаёт в выдаче и блоки вроде
    «люди также спрашивают», а это не доноры.
    """
    results: list[SerpResult] = []
    for row in rows:
        url = row.get("url")
        position = row.get("position")
        if not isinstance(url, str) or not isinstance(position, int):
            continue
        results.append(SerpResult(position=position, url=url))
        if len(results) >= wanted:
            break
    return results
