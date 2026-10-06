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
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import MAX_BATCH_TARGETS
from backend.features.core.domain import Verdict
from backend.features.crawl.denylist import Denial, DenyReason
from backend.features.crawl.scoring import Candidate
from backend.features.donors.collect import parse_domain_rating

logger = logging.getLogger(__name__)

#: Порог требования: «Кому не пишем … домены DR > 80».
MAX_DR = 80

#: Кому нужен DR: тем, кому собрались писать или кого покажут человеку.
ASKED_VERDICTS = frozenset({Verdict.BOUGHT, Verdict.PENDING})

#: Ответ на вопрос «какой DR у этих доменов»: домен → DR или `None`, если
#: провайдер домена не знает. Домена нет в ответе — DR не проверен.
Ratings = Callable[[Sequence[str]], Awaitable[dict[str, int | None]]]


def _host_of(row: dict[str, Any]) -> str:
    """Ahrefs возвращает `url` со схемой и завершающим слэшем — приводим к хосту."""
    raw = row.get("url")
    if not isinstance(raw, str):
        return ""
    return raw.removeprefix("https://").removeprefix("http://").rstrip("/").lower()


async def ahrefs_ratings(client: AhrefsClient, roots: Sequence[str]) -> dict[str, int | None]:
    """DR доменов пакетами по `MAX_BATCH_TARGETS`. Расход клиент сообщает сам."""
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


def apply_ratings(candidates: list[Candidate], ratings: dict[str, int | None]) -> int:
    """Отсеять крупные сайты. Возвращает, сколько отсеяно.

    `ratings` — всё, что известно: из прошлых пересчётов и только что
    спрошенное. Домена нет в `ratings` — DR не проверен, и это пишется
    в причинах, а вердикт остаётся.
    """
    blocked = 0
    for candidate in candidates:
        if not needs_rating(candidate):
            continue
        if candidate.target_root not in ratings:
            candidate.reasons.append("DR не проверен — крупный ли сайт, неизвестно")
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
