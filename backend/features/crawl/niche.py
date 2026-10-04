"""Бизнесы ниши из выдачи прогона — в кандидаты в рекламодатели.

Решение Anthony 04.10.2026: сайт, который сам продаёт в нише прогона, — не
донор, а кандидат в рекламодатели. Судья площадки различает это по тексту
выдачи (`donors/publisher_judge.Intent.SELLS_OWN`): букмекер в выдаче по
ставкам не продаст нам статью — он сам покупает их у площадок вроде наших.
До этого такой сайт только отсеивался из доноров, и вердикт, за который уже
заплачено, дальше не шёл.

**Кто попадает.** Хост из выдачи прогона, про который человек сказал
«продаёт своё», — или судья, если человек не говорил ничего: слово человека
главнее вердикта судьи в обе стороны. Принятый человеком донор не попадает
никогда: он уже наш, и писать ему оффер — путать роли.

**Что получает.** Строку рекламодателя с источником `niche` и прогоном, без
балла и без ссылки: писать ему можно, только когда человек скажет «пишем»
(`decide`). До решения ни поиск адреса, ни письмо его не трогают.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel

#: Источник рекламодателя: найден по ссылке на нашем доноре или в выдаче.
LINKS = "links"
NICHE = "niche"

#: Вердикт «продаёт своё» — одно слово у судьи и у человека.
SELLS_OWN = "sells_own"

#: Решение по донору, после которого домен уже наш.
_ACCEPTED = "accepted"


class UnknownNicheRunError(LookupError):
    """Прогона с таким номером нет — собирать не из чего."""


class UnknownNicheAdvertiserError(LookupError):
    """Бизнеса ниши с таким номером нет."""


@dataclass(frozen=True, slots=True)
class NicheReport:
    """Сколько бизнесов нашлось в выдаче прогона и сколько из них новых."""

    run_id: int
    found: int
    added: int


async def collect(session: AsyncSession, run_id: int) -> NicheReport:
    """Бизнесы ниши из выдачи прогона — в кандидаты, кого там ещё нет.

    Повторный сбор того же прогона ничего не дублирует: рекламодатель —
    один на домен, и уже заведённый (по ссылке или раньше из выдачи) не
    трогается.
    """
    run = await session.get(RunModel, run_id)
    if run is None:
        raise UnknownNicheRunError(f"Прогона №{run_id} нет — бизнесы ниши собирать не из чего")
    hosts = [str(host).lower() for host in (run.candidates or {}).get("hosts", [])]
    if not hosts:
        return NicheReport(run_id=run_id, found=0, added=0)

    ours = select(DonorModel.domain_id).where(DonorModel.review == _ACCEPTED)
    rows = (
        await session.scalars(
            select(DomainModel.id).where(
                DomainModel.host.in_(hosts),
                _sells_own(),
                DomainModel.id.not_in(ours),
            )
        )
    ).all()
    known = set(
        (
            await session.scalars(
                select(AdvertiserModel.domain_id).where(AdvertiserModel.domain_id.in_(rows))
            )
        ).all()
    )
    fresh = [domain_id for domain_id in rows if domain_id not in known]
    for domain_id in fresh:
        session.add(
            AdvertiserModel(
                domain_id=domain_id,
                source=NICHE,
                found_run_id=run_id,
                points=0,
                donors=0,
                links=0,
                confirmed_by_human=False,
            )
        )
    await session.flush()
    return NicheReport(run_id=run_id, found=len(rows), added=len(fresh))


def _sells_own() -> ColumnElement[bool]:
    """Слово человека, а без него — вердикт судьи."""
    return or_(
        DomainModel.human_intent == SELLS_OWN,
        and_(DomainModel.human_intent.is_(None), DomainModel.site_intent == SELLS_OWN),
    )


async def decide(
    session: AsyncSession, advertiser_id: int, *, write: bool, by: str, now: datetime | None = None
) -> AdvertiserModel:
    """Решение человека по бизнесу ниши: пишем или нет.

    «Пишем» открывает ему поиск адреса и письмо; «не пишем» оставляет строку
    с решением — чтобы следующий сбор того же прогона не вернул его в очередь.
    """
    advertiser = await session.get(AdvertiserModel, advertiser_id)
    if advertiser is None or advertiser.source != NICHE:
        raise UnknownNicheAdvertiserError(f"Бизнеса ниши №{advertiser_id} нет — решать не о ком")
    advertiser.confirmed_by_human = write
    advertiser.decided_at = now or datetime.now(UTC)
    advertiser.decided_by = by
    await session.flush()
    return advertiser
