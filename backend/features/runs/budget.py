"""Бюджет прогона: остаток у провайдера, чужие удержания, наш кап.

Отдельный модуль, а не часть прогона: этот же расчёт нужен маршруту
сметы, консольной команде и самому прогону. Пока он жил внутри прогона,
маршрут применял кап второй раз поверх — и потолок в пять тысяч юнитов
вычитал из себя месячную трату. Один расчёт — одно место.
"""

from __future__ import annotations

import logging

from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import Quota

logger = logging.getLogger(__name__)


class CapExceededError(RuntimeError):
    """Прогон дороже, чем осталось юнитов. Не запускаем."""


class QuotaUnavailableError(RuntimeError):
    """Остаток узнать не удалось. Тратить вслепую нельзя."""


async def units_left(client: AhrefsClient, *, cap: int | None = None, claimed: int = 0) -> int:
    """Сколько юнитов можно потратить: остаток провайдера минус чужие удержания,
    и всё это не выше нашего капа.

    Кап ограничивает нас добровольно, остаток провайдера — жёстко.

    **`claimed` — не украшение.** Остаток у Ahrefs говорит о потраченном, а не
    об обещанном: идущий прогон свою смету обещал, но ещё не потратил, и для
    Ahrefs этих юнитов как будто нет. Следующий спланируется под них второй
    раз, вместе они выберут больше, чем есть, и узнается это отказом API
    посреди платной работы. Юниты не возвращаются — вычитаем ДО планирования.

    ⚠ **Не закрыто двумя местами, прямым текстом.** Прогоны, у которых окна
    оценки перекрылись целиком (оба спросили удержания раньше, чем любой
    записал смету), увидят ноль: окно узкое и запускает их человек, но оно
    есть — закрывается перепроверкой после записи сметы. И соседняя система
    на том же ключе не видна вовсе: у неё своя база, её траты доходят до нас
    только через остаток Ahrefs, с задержкой в один её прогон.
    """
    try:
        quota = Quota.from_payload(await client.limits_and_usage())
    except (AhrefsError, OSError) as exc:
        raise QuotaUnavailableError(
            "Не удалось узнать остаток юнитов у Ahrefs. Прогон не запускается: "
            "тратить, не зная остатка, значит рисковать лимитом соседней системы "
            "на том же ключе."
        ) from exc

    logger.info(
        "Остаток Ahrefs: %s (ключ %s из %s, пространство %s из %s)",
        quota.available,
        quota.key_used,
        quota.key_limit,
        quota.workspace_used,
        quota.workspace_limit,
    )
    free = max(0, quota.available - claimed)
    if claimed:
        logger.info(
            "Удержано идущими прогонами: %s, свободно %s",
            claimed,
            free,
            extra={"claimed": claimed, "available": quota.available, "free": free},
        )
    return min(free, cap) if cap is not None else free
