"""Лимиты домена и направления — фильтр ящиков до выбора ящика (Ф4, срез 4.5a).

`senders.pick` знает только ящик, а репутация живёт выше — у домена (два ящика по
двадцать — сорок писем с домена) и у направления. Фильтр отдаёт годные ящики и
причину словами для каждого отсеянного; `pick` выбирает среди годных по прежнему
правилу. Счёт — тот же, что у разгона (первые письма, сутки по UTC), сложенный по
домену и этапу. Фильтр — только для первого письма: добивка и ответ уходят с ящика
своей переписки (`letters/mailbox.py`). Нет строки `sending_domains` и лимита
`OUTREACH_<ЭТАП>_DAILY_LIMIT` — всё как было (у доноров их нет).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import SenderModel, SendingDomainModel

DIRECTION_SPENT = "у направления кончился дневной лимит"
DOMAIN_SPENT = "домен исчерпан на сегодня"
DOMAIN_PAUSED = "домен на паузе"
DOMAIN_YOUNG = "домен на выдержке"
DOMAIN_ELSEWHERE = "домен записан за другим направлением"


@dataclass(frozen=True, slots=True)
class Screened:
    """Ящики этапа после фильтра: годные и причина словами для каждого отсеянного."""

    fit: tuple[SenderModel, ...]
    refused: dict[int, str] = field(default_factory=dict)

    def why(self) -> str | None:
        """Причины отсева без повторов — для отказа «писать некому». `None` — не было."""
        return "; ".join(dict.fromkeys(self.refused.values())) or None


def screen(
    senders: Sequence[SenderModel],
    *,
    stage: Stage,
    sent_today: Mapping[int, int],
    domains: Mapping[str, SendingDomainModel],
    direction_limit: int | None,
    now: datetime,
) -> Screened:
    """Кто из включённых ящиков этапа сегодня проходит лимиты домена и направления."""
    own = [sender for sender in senders if sender.enabled and sender.stage is stage]
    spent = sum(sent_today.get(sender.id, 0) for sender in senders if sender.stage is stage)
    if direction_limit is not None and spent >= direction_limit:
        why = f"{DIRECTION_SPENT} ({spent} из {direction_limit} первых писем)"
        return Screened(fit=(), refused=dict.fromkeys((sender.id for sender in own), why))

    by_domain: Counter[str] = Counter()
    for sender in senders:
        by_domain[sender.domain] += sent_today.get(sender.id, 0)
    fit: list[SenderModel] = []
    refused: dict[int, str] = {}
    for sender in own:
        verdict = _domain_refusal(domains.get(sender.domain), stage, by_domain[sender.domain], now)
        if verdict is None:
            fit.append(sender)
        else:
            refused[sender.id] = verdict
    return Screened(fit=tuple(fit), refused=refused)


def _domain_refusal(
    row: SendingDomainModel | None, stage: Stage, sent: int, now: datetime
) -> str | None:
    """Почему домен сегодня не пишет — или `None`, если пишет."""
    if row is None:
        return None
    if row.stage is not stage:
        return f"{DOMAIN_ELSEWHERE}: {row.domain} — {row.stage.value}"
    if row.paused_at is not None:
        return f"{DOMAIN_PAUSED}: {row.domain} — {row.pause_reason or 'без причины'}"
    if row.young_until is not None and now < row.young_until:
        return f"{DOMAIN_YOUNG}: {row.domain} — до {row.young_until:%d.%m %H:%M} UTC"
    if sent >= row.daily_limit:
        return f"{DOMAIN_SPENT}: {row.domain} — {sent} из {row.daily_limit}"
    return None


async def sending_domains(session: AsyncSession) -> dict[str, SendingDomainModel]:
    """Строки доменов рассылки по имени домена — таблица маленькая, читается целиком."""
    rows = await session.execute(select(SendingDomainModel))
    return {row.domain: row for row in rows.scalars().all()}
