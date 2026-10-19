"""Замок решения по домену: строки домена во всех прогонах — под один замок, по порядку номеров.

Решение по домену одно (`candidates.RunReview.decide`): оно пишет строку своего прогона, а
потом копию в строки домена в чужих. Два решения по одному домену из разных прогонов брали
каждое свою строку и ждали чужую — взаимная блокировка, и Postgres обрывал одно из решений
«deadlock detected» (хвост аудита 11.10.2026, правка 19.10.2026). Под общим замком, взятым до
любой правки и в одном порядке, второе решение ждёт первое и ложится после него.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.run import RunCandidateModel
from backend.shared.database.ids import storable


async def lock_domains_of(session: AsyncSession, candidate_ids: Sequence[int]) -> None:
    """Все строки доменов этих кандидатов, во всех прогонах, — `FOR UPDATE` по номерам."""
    domains = select(RunCandidateModel.domain_id).where(
        RunCandidateModel.id.in_([one for one in candidate_ids if storable(one)])
    )
    await session.execute(
        select(RunCandidateModel.id)
        .where(RunCandidateModel.domain_id.in_(domains))
        .order_by(RunCandidateModel.id)
        .with_for_update()
    )
