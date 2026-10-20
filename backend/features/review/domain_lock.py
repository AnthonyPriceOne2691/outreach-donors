"""Замок решения по домену: строки домена во всех прогонах — под один замок, по порядку номеров.

Решение по домену одно (`candidates.RunReview.decide`): оно пишет строку своего прогона, а
потом копию в строки домена в чужих. Два решения по одному домену из разных прогонов брали
каждое свою строку и ждали чужую — взаимная блокировка, и Postgres обрывал одно из решений
«deadlock detected» (хвост аудита 11.10.2026, правка 19.10.2026). Под общим замком, взятым до
любой правки и в одном порядке, второе решение ждёт первое и ложится после него.

«Взять на рассмотрение» из резерва (`reserve.py`, ревью 20.10.2026) берёт тот же замок — и
ещё сам домен: строк очереди у домена из резерва может не быть вовсе, а два «взять» одного
домена из резервов двух прогонов без замка положили бы его ждать в обе очереди.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.domain import DomainModel
from backend.features.core.models.run import RunCandidateModel
from backend.shared.database.ids import storable


async def lock_domains_of(session: AsyncSession, candidate_ids: Sequence[int]) -> None:
    """Все строки доменов этих кандидатов, во всех прогонах, — `FOR UPDATE` по номерам."""
    domains = select(RunCandidateModel.domain_id).where(
        RunCandidateModel.id.in_([one for one in candidate_ids if storable(one)])
    )
    await _lock_rows(session, domains)


async def lock_domains(session: AsyncSession, domain_ids: Sequence[int]) -> None:
    """Домены — под замок решения, когда строк очереди у них может ещё не быть.

    Сначала сам домен: `FOR NO KEY UPDATE` — второй такой замок ждёт первый, а запись строк,
    которые на домен ссылаются (кандидат, журнал), — нет. Потом все его строки во всех
    прогонах, как у решения: решение ждёт «взять», «взять» — решение."""
    wanted = sorted({one for one in domain_ids if storable(one)})
    if not wanted:
        return
    await session.execute(
        select(DomainModel.id)
        .where(DomainModel.id.in_(wanted))
        .order_by(DomainModel.id)
        .with_for_update(key_share=True)
    )
    await _lock_rows(session, wanted)


async def _lock_rows(session: AsyncSession, domains: Select[tuple[int]] | Sequence[int]) -> None:
    await session.execute(
        select(RunCandidateModel.id)
        .where(RunCandidateModel.domain_id.in_(domains))
        .order_by(RunCandidateModel.id)
        .with_for_update()
    )
