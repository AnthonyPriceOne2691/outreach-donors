"""Бюджет прогона: остаток у провайдера, чужие удержания, наш кап.

Отдельный модуль, а не часть прогона: этот же расчёт нужен маршруту
сметы, консольной команде и самому прогону. Пока он жил внутри прогона,
маршрут применял кап второй раз поверх — и потолок в пять тысяч юнитов
вычитал из себя месячную трату. Один расчёт — одно место.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import ahrefs as ahrefs_cfg
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import Quota
from backend.features.runs.repository import RunRepository
from backend.features.runs.spending import cap_left

logger = logging.getLogger(__name__)


class CapExceededError(RuntimeError):
    """Прогон дороже, чем осталось юнитов. Не запускаем."""


class QuotaUnavailableError(RuntimeError):
    """Остаток узнать не удалось. Тратить вслепую нельзя.

    `permanent` — от причины (`runs/failures.is_permanent`): отказ по ключу или
    правам повтор не исправит, сеть и 5xx — исправят.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


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
        # Причина — в тексте и в признаке. До 08.10.2026 не было ни того, ни
        # другого: «остаток неизвестен» читался одинаково для сетевой минуты и
        # для отозванного ключа, и прогон с отозванным ключом шёл «сбой, будет
        # продолжен» до конца продолжений, хотя повтор его не исправит.
        raise QuotaUnavailableError(
            "Не удалось узнать остаток юнитов у Ahrefs. Прогон не запускается: "
            "тратить, не зная остатка, значит рисковать лимитом соседней системы "
            f"на том же ключе. Причина: {exc}",
            permanent=getattr(exc, "permanent", False),  # у OSError признака нет
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


async def ceiling_at_start(session: AsyncSession, *, run_id: int, promised: int) -> int:
    """Потолок прогона на старте задачи: обещанное при нажатии — но не больше, чем к этой
    минуте осталось по месячному капу за вычетом обещанного идущими прогонами.

    **При старте, а не при нажатии** (аудит 10.10.2026, №2). Потолок с нажатия — остаток
    капа на ту минуту: два прогона, поставленные подряд, получали каждый весь остаток,
    единственный воркер шёл ими по очереди, и второй тратил ещё раз то, что съел первый, —
    до двух капов за месяц на ключе, общем с соседней системой.

    **Удержания вычитаются и из нашего капа**, а не только из остатка провайдера
    (`units_left`): обещанное идущим прогоном ещё не потрачено, и таблица расхода его
    не видит. Сам прогон из удержаний исключён: продолжению после смерти воркера своё же
    обещание не соперник, а потраченное им уже лежит в таблице расхода.
    """
    month = await cap_left(session, cap=ahrefs_cfg.UNITS_CAP)
    claimed = await RunRepository(session).claimed_units(exclude_run_id=run_id)
    ceiling = max(0, min(promised, month - claimed))
    if ceiling < promised:
        logger.info(
            "Потолок прогона при старте ниже обещанного: от месячного капа осталось меньше",
            extra={
                "run_id": run_id,
                "promised": promised,
                "ceiling": ceiling,
                "month_left": month,
                "claimed": claimed,
            },
        )
    return ceiling
