"""Бюджет прогона: остаток у провайдера, чужие удержания, наш кап.

Отдельный модуль, а не часть прогона: этот же расчёт нужен маршруту
сметы, консольной команде и самому прогону. Пока он жил внутри прогона,
маршрут применял кап второй раз поверх — и потолок в пять тысяч юнитов
вычитал из себя месячную трату. Один расчёт — одно место.

**Каждый удачный ответ Ahrefs — в снимок ключа** (`keep`, 16.10.2026): от него считаются
кап и его цикл (`runs/ahrefs_cap.key_cap`). Кто спрашивает с сессией, тот и кладёт.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import Quota
from backend.features.runs.ahrefs_cap import key_cap
from backend.features.runs.ahrefs_key import Keep, keeper
from backend.features.runs.repository import RunRepository

logger = logging.getLogger(__name__)


class CapExceededError(RuntimeError):
    """Прогон дороже, чем осталось юнитов. Не запускаем."""


class QuotaUnavailableError(RuntimeError):
    """Остаток или размер ключа узнать не удалось. Тратить вслепую нельзя.

    `permanent` — от причины (`runs/failures.is_permanent`): отказ по ключу или
    правам повтор не исправит, сеть и 5xx — исправят.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


#: Во что упёрся бюджет на старте прогона — словами отказа (`planning.plan_run`). Что делать,
#: зависит от того, чей лимит: 16.10.2026 отказ советовал «поднимите кап», когда кончался
#: ключ Ahrefs, и подъём капа не помогал.
LIMIT_WORDS = {
    "key": "упор — лимит ключа Ahrefs: кап здесь не поможет, лимит ключа поднимают в самом Ahrefs",
    "workspace": (
        "упор — лимит пространства Ahrefs, общий для всех его ключей: его поднимают в самом Ahrefs"
    ),
    "cap": (
        "упор — наш кап цикла или потолок прогона: кап поднимают на экране «Расход», "
        "потолок — в форме прогона"
    ),
}


@dataclass(frozen=True, slots=True)
class Budget:
    """Бюджет и во что он упирается: «key», «workspace» — лимит Ahrefs, «cap» — наш кап
    цикла или потолок прогона (`cap` у `budget_of` — уже меньшее из них)."""

    units: int
    binding: str

    @property
    def limit(self) -> str:
        """Упор — словами отказа."""
        return LIMIT_WORDS[self.binding]


async def units_left(
    client: AhrefsClient, *, cap: int | None = None, claimed: int = 0, keep: Keep | None = None
) -> int:
    """Сколько юнитов можно потратить — число `budget_of` без упора: смете, консоли
    и докупке стран слова отказа не нужны."""
    return (await budget_of(client, cap=cap, claimed=claimed, keep=keep)).units


async def budget_of(
    client: AhrefsClient, *, cap: int | None = None, claimed: int = 0, keep: Keep | None = None
) -> Budget:
    """Сколько юнитов можно потратить и во что это упирается: остаток провайдера минус
    чужие удержания, и всё это не выше нашего капа. `keep` — куда положить ответ снимком
    ключа (`ahrefs_key.keeper`): у кого сессия, тот и кладёт.

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
    if keep is not None:
        await keep(quota)

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
    # Поровну — Ahrefs: при упоре и в остаток ключа, и в кап подъём капа не помог бы.
    if cap is not None and cap < free:
        return Budget(cap, "cap")
    return Budget(free, quota.tighter)


async def cap_left(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Сколько юнитов осталось по нашему капу в этом цикле ключа.

    **Кап — на цикл, и вычитать из него потраченное обязательно.** Без этого он
    работает потолком одного прогона: потратив девяносто тысяч из ста, следующий
    прогон снова видит все сто — и кап, названный на экране расхода, в бюджете
    прогона означает совсем другое. Одно число с двумя смыслами расходится молча.

    Считается по своей таблице, а не по остатку провайдера: остаток включает траты
    соседней системы на общем ключе, а наш кап — про нас. Кап — действующий, от ключа
    (`ahrefs_cap.key_cap`), и параметра, через который вызов подставил бы свой, нет.

    **Размер ключа неизвестен — отказ словами** (`QuotaUnavailableError`, 16.10.2026):
    Ahrefs ещё не ответил ни разу или не назвал лимитов, и кап считать не от чего. Повтор
    поможет: сторож спрашивает Ahrefs каждые десять минут.
    """
    cap = await key_cap(session, now=now)
    if cap.unknown is not None:
        raise QuotaUnavailableError(
            f"{cap.unknown}. Тратить не от чего: кап считается от размера ключа, а без него "
            "под риском лимит соседней системы на том же ключе — сторож спросит Ahrefs "
            "в ближайшие минуты"
        )
    return cap.cap_left


async def ceiling_at_start(session: AsyncSession, *, run_id: int, promised: int) -> int:
    """Потолок прогона на старте задачи: обещанное при нажатии — но не больше, чем к этой
    минуте осталось по капу цикла ключа за вычетом обещанного идущими прогонами.

    **При старте, а не при нажатии** (аудит 10.10.2026, №2). Потолок с нажатия — остаток
    капа на ту минуту: два прогона, поставленные подряд, получали каждый весь остаток,
    единственный воркер шёл ими по очереди, и второй тратил ещё раз то, что съел первый, —
    до двух капов за цикл на ключе, общем с соседней системой.

    **Удержания вычитаются и из нашего капа**, а не только из остатка провайдера
    (`units_left`): обещанное идущим прогоном ещё не потрачено, и таблица расхода его
    не видит. Сам прогон из удержаний исключён: продолжению после смерти воркера своё же
    обещание не соперник, а потраченное им уже лежит в таблице расхода.

    **Кап — тот, что действует на старте** (`cap_left`): опущенный на экране «Расход»
    между нажатием и стартом режет потолок сразу; поднятый — обещанного при нажатии не
    поднимает: человек видел и согласовал ту цифру. Размер ключа неизвестен — отказ
    `QuotaUnavailableError`, прогон продолжат, когда Ahrefs ответит.
    """
    left = await cap_left(session)
    claimed = await RunRepository(session).claimed_units(exclude_run_id=run_id)
    ceiling = max(0, min(promised, left - claimed))
    if ceiling < promised:
        logger.info(
            "Потолок прогона при старте ниже обещанного: от капа цикла осталось меньше",
            extra={
                "run_id": run_id,
                "promised": promised,
                "ceiling": ceiling,
                "cap_left": left,
                "claimed": claimed,
            },
        )
    return ceiling


async def units_free(session: AsyncSession, client: AhrefsClient) -> int:
    """Сколько юнитов можно потратить вне прогона прямо сейчас — правилом прогона.

    Зовётся до пакета DR кандидатов Этапа 2 (`crawl/big_sites.py`). До 12.10.2026 этот
    пакет капа не спрашивал вовсе: DR шёл той же операцией, что метрики Этапа 1, и мимо
    месячного капа — после выбранного месяца пересчёт кандидатов тратил дальше.

    Правило то же, что у прогона, и в тех же двух местах: остаток у провайдера
    (`units_left`) и остаток капа цикла по своей таблице (`cap_left`) — оба за вычетом
    обещанного идущими прогонами: их смета не потрачена, и таблица её не видит. Провайдер
    спрашивается первым: его ответ ложится в снимок ключа, и кап считается уже от него.
    Остаток у провайдера или размер ключа неизвестен — `QuotaUnavailableError`.
    """
    claimed = await RunRepository(session).claimed_units()
    free = await units_left(client, claimed=claimed, keep=keeper(session))
    return min(free, max(0, await cap_left(session) - claimed))
