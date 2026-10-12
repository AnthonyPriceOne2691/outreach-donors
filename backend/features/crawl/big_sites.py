"""«Домены DR > 80 — не пишем»: строка требования, которую не задать списком.

DR — метрика провайдера, её нет ни в ссылке, ни на странице донора.
До 06.10.2026 эта строка требования не выполнялась нигде. Живой обход
финансового донора показал цену этого: оба «куплено» оказались крупными
сайтами — маркетплейс, на который донор ставит свои партнёрские ссылки,
и рекламный редиректор с `rel=sponsored`. Письмо любому из них ушло бы
в пустоту, а на экране они стояли «куплено».

**Спрашиваем только тех, кому собрались писать.** DR берётся пакетом
у Ahrefs (2 юнита на домен, минимум 50 на запрос, до 100 доменов
в пакете) и только для кандидатов «куплено» и «спорно»: «мимо» письма
не получает и так, а их сотни на каждом доноре.

**Спрошенное не спрашиваем второй раз.** DR лежит у кандидата и при
пересчёте переносится по домену — как решение человека (`gate.py`).
Пересчёт весов не должен стоить юнитов.

**Неизвестный DR — не «маленький».** Нет ключа, провайдер не ответил —
вердикт остаётся, а в причинах так и написано: «DR не проверен».
Молча пропустить крупный сайт значит выдать его за рекламодателя;
молча отсеять — потерять рекламодателя. Домен, которого Ahrefs не знает
вовсе, крупным не бывает — его вердикт тоже остаётся.

**Кап — до пакета, правилом прогона Этапа 1** (`runs/budget.units_free`, требование
ТЗ к расходу Этапа 2). DR кандидатов — те же юниты Ahrefs на ключе, общем с соседней
системой: смета пакетов сверяется с остатком месячного капа и остатком у провайдера,
оба — за вычетом обещанного идущими прогонами. Не влезает — не спрашивается ничего,
а в причинах кандидатов сказано почему. До 12.10.2026 пакет капа не спрашивал вовсе.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import advertisers as advertisers_cfg
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import (
    MAX_BATCH_TARGETS,
    UNITS_DR_SCREEN,
    UsageCollector,
    batch_cost,
)
from backend.features.core import usage
from backend.features.core.domain import Stage, Verdict
from backend.features.crawl.denylist import Denial, DenyReason
from backend.features.crawl.scoring import Candidate
from backend.features.donors.collect import parse_domain_rating
from backend.features.runs.budget import QuotaUnavailableError, units_free

logger = logging.getLogger(__name__)

#: Порог требования: «Кому не пишем … домены DR > 80» — настройкой (`config/advertisers.py`).
MAX_DR = advertisers_cfg.MAX_DR

#: Кому нужен DR: тем, кому собрались писать или кого покажут человеку.
ASKED_VERDICTS = frozenset({Verdict.BOUGHT, Verdict.PENDING})

#: Ответ на вопрос «какой DR у этих доменов»: домен → DR или `None`, если
#: провайдер домена не знает. Домена нет в ответе — DR не проверен.
Ratings = Callable[[Sequence[str]], Awaitable[dict[str, int | None]]]

#: Сколько юнитов можно потратить прямо сейчас — `runs.budget.units_free` с сессией.
Allowance = Callable[[], Awaitable[int]]


class RatingsRefusedError(RuntimeError):
    """DR не спрошен: кап или остаток у провайдера не пускают пакет.

    Не сбой провайдера: вердикты остаются, а причина едет в причины кандидатов
    (`apply_ratings`), — следующий пересчёт спросит снова, когда юниты будут.
    """


def _host_of(row: dict[str, Any]) -> str:
    """Ahrefs возвращает `url` со схемой и завершающим слэшем — приводим к хосту."""
    raw = row.get("url")
    if not isinstance(raw, str):
        return ""
    return raw.removeprefix("https://").removeprefix("http://").rstrip("/").lower()


def dr_cost(count: int) -> int:
    """Смета DR `count` доменов: пакетами по `MAX_BATCH_TARGETS`, у каждого — минимум
    запроса. Пакет на одного донора почти всегда один и упирается в минимум: 50 юнитов."""
    full, tail = divmod(count, MAX_BATCH_TARGETS)
    return full * batch_cost(MAX_BATCH_TARGETS, UNITS_DR_SCREEN) + batch_cost(tail, UNITS_DR_SCREEN)


async def _ensure_allowed(allowance: Allowance, count: int) -> None:
    """Смета всех пакетов — против того, что можно потратить. Не влезает — отказ словами."""
    cost = dr_cost(count)
    try:
        free = await allowance()
    except QuotaUnavailableError as exc:
        raise RatingsRefusedError(
            "остаток юнитов у Ahrefs неизвестен — тратить вслепую нельзя"
        ) from exc
    if cost > free:
        raise RatingsRefusedError(
            f"месячный кап юнитов Ahrefs: пакет стоит до {cost}, свободно {free}"
        )


def capped_ratings(client: AhrefsClient, session: AsyncSession) -> Ratings:
    """DR у Ahrefs под капом — источник для пересчёта кандидатов (задача и консоль)."""
    allowance = functools.partial(units_free, session, client)
    return functools.partial(ahrefs_ratings, client, allowance=allowance)


def journal(session: AsyncSession, spent: UsageCollector) -> None:
    """Купленный DR — в журнал расходом Этапа 2, той же сессией, что вердикты.

    Звать после каждого пересчёта, а не раз в конце: кап следующего пакета считается
    по журналу (`runs/budget.units_free`), и пакеты одного запуска иначе не видели бы
    купленного друг другом.
    """
    for operation, cost in spent.drain():
        usage.record(session, operation=operation, units=cost.billable, stage=Stage.ADVERTISERS)


async def ahrefs_ratings(
    client: AhrefsClient, roots: Sequence[str], *, allowance: Allowance
) -> dict[str, int | None]:
    """DR доменов пакетами по `MAX_BATCH_TARGETS`. Расход клиент сообщает сам.

    `allowance` — сколько юнитов можно потратить, и он обязателен: DR без капа — ровно
    та дыра, которую закрывает этот модуль. Смета всех пакетов сверяется с ним до первого,
    не влезает — `RatingsRefusedError`, и не спрошено ничего. Половину («первая сотня —
    да, остальные — нет») не спрашиваем нарочно: пакет на донора почти всегда один,
    а половина ответа читалась бы как «остальные не крупные».
    """
    await _ensure_allowed(allowance, len(roots))
    found: dict[str, int | None] = {}
    for start in range(0, len(roots), MAX_BATCH_TARGETS):
        chunk = list(roots[start : start + MAX_BATCH_TARGETS])
        response = await client.batch_metrics(chunk, ("url", "domain_rating"))
        by_host = {_host_of(row): parse_domain_rating(row) for row in response.rows}
        for root in chunk:
            rating = by_host.get(root)
            found[root] = None if rating is None else round(rating)
    return found


def needs_rating(candidate: Candidate) -> bool:
    """Нужен ли домену DR: ему собрались писать или его покажут человеку."""
    return candidate.verdict in ASKED_VERDICTS


def apply_ratings(
    candidates: list[Candidate], ratings: dict[str, int | None], *, refused: str | None = None
) -> int:
    """Отсеять крупные сайты. Возвращает, сколько отсеяно.

    `ratings` — всё, что известно: из прошлых пересчётов и только что
    спрошенное. Домена нет в `ratings` — DR не проверен, и это пишется
    в причинах, а вердикт остаётся. `refused` — почему DR не спрашивали
    (`RatingsRefusedError`): кап — не то же, что отказ провайдера.
    """
    unchecked = "DR не проверен — крупный ли сайт, неизвестно"
    if refused is not None:
        unchecked = f"DR не проверен: {refused} — крупный ли сайт, неизвестно"
    blocked = 0
    for candidate in candidates:
        if not needs_rating(candidate):
            continue
        if candidate.target_root not in ratings:
            candidate.reasons.append(unchecked)
            continue
        rating = ratings[candidate.target_root]
        candidate.dr = rating
        if rating is None or rating <= MAX_DR:
            continue
        candidate.verdict = Verdict.BLOCKED
        candidate.denial = Denial(DenyReason.BIG_SITE, f"DR {rating}")
        candidate.reasons.append(f"кому не пишем: DR {rating} > {MAX_DR}")
        blocked += 1
    return blocked
