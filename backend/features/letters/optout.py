"""Последствия отписки: кому больше не пишем и что делать с начатым.

Отписка нажатием кнопки и отписка письмом — **разной ширины, и это
решение, а не недосмотр.** Ответ «unsubscribe» приходит с конкретного
ящика, и в стоп-лист идёт адрес: писал человек, говорил за себя.
Кнопку в письме нажимает тот, кому мы написали как сайту, и закрывает
она сайт целиком — иначе цепочка продолжится на втором найденном
адресе того же донора, и это будет рассылка в обход отписки.

**Повторное нажатие ничего не ломает.** Донор жмёт кнопку дважды,
почтовый клиент открывает ссылку заранее, человек возвращается на
страницу через неделю — во всех случаях ответ один: «больше не пишем».
Вторая строка в стоп-листе при этом не заводится.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import SuppressionReason, ThreadStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import ThreadModel
from backend.features.letters.stoplist import stop_pending


@dataclass(frozen=True, slots=True)
class OptOut:
    """Что стало с донором после нажатия."""

    host: str
    #: Впервые или он уже был в стоп-листе. Страница показывает одно и то
    #: же, но в журнале разница видна: повторы — это предпросмотр ссылок
    #: почтовыми клиентами, и по ним судят, не отписывает ли нас робот.
    first_time: bool
    #: Сколько писем сняли с очереди и сроков.
    stopped: int


async def unsubscribe_domain(
    session: AsyncSession, domain_id: int, *, source: str
) -> OptOut | None:
    """Донор в стоп-лист целиком. `None` — такого донора нет.

    Строка стоп-листа без этапа: отписка действует и на второй этап
    тоже. Человек, попросивший больше не писать, не давал согласия
    на письмо в другой роли.
    """
    domain = await session.get(DomainModel, domain_id)
    if domain is None:
        return None

    rows = await session.execute(
        select(SuppressionModel.id).where(
            SuppressionModel.domain_id == domain_id,
            SuppressionModel.stage.is_(None),
        )
    )
    first_time = rows.first() is None
    if first_time:
        session.add(
            SuppressionModel(
                domain_id=domain_id,
                reason=SuppressionReason.UNSUBSCRIBED,
                created_by=source,
            )
        )

    stopped = await _stop_everything(session, domain_id)
    return OptOut(host=domain.host, first_time=first_time, stopped=stopped)


async def _stop_everything(session: AsyncSession, domain_id: int) -> int:
    """Снять всё назначенное и пометить диалоги отписавшимися.

    Снятие писем и сроков — общее со стоп-листом (`stoplist.stop_pending`):
    два экземпляра одного правила разъехались бы на первой правке, и тише
    всех разошёлся бы тот, который реже зовут.
    """
    stopped = await stop_pending(session, domain_id=domain_id)
    threads = await session.execute(select(ThreadModel).where(ThreadModel.domain_id == domain_id))
    for thread in threads.scalars().all():
        thread.status = ThreadStatus.UNSUBSCRIBED
    return stopped
