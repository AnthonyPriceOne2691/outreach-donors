"""Очередь поиска адреса для кандидатов сбора — третья реализация общего `ContactQueue`.

Лестница одна на все очереди (`contacts/search.search_contacts`, публичный вход): она
работает с голым хостом и про роль домена не знает. Разное у очередей только две вещи —
кого брать и куда писать исход (как у рекламодателей, `crawl/contacts.py`):

- **кого:** принятых кандидатов прогона без лида, у домена которых ещё нет адреса;
  правило «пора ли искать снова» — общее (`attempts.waiting`): сайт, который не ответил,
  повторяется по сроку, «адреса нет» — через срок годности;
- **куда:** исход — в колонки попыток кандидата (`attempts.record`), адрес — в общие
  `contacts` (`save_addresses`): адрес принадлежит домену, а не роли (решение 01.10 —
  единственная запись продаж в `contacts`).

**Живой адрес на домене уже есть — не искать**: лестница кончается платной ступенью, а лид
заведётся из готового адреса (`segment_leads.py`). Живой — без отметки отказа доставки:
домен с одним мёртвым адресом лида не даст, и без поиска кандидат ждал бы вечно. Условие
одно у очереди и у заведения лидов (`segment_leads.living_address`).

**Живой лид продаж на домене есть — тоже не искать** (решение владельца 14.10, «компания — в
одной гипотезе»): ни одной ступенью, и Hunter за такой домен не платится — лида другой
гипотезы очистка всё равно отклонит, а своей у компании уже есть. Лид из файла строки
`contacts` не заводит, поэтому прежнее условие такой домен не видело. Условие одно у очереди и
у итога кандидата в отчёте прогона (`company_hold.held`, `held_in_run`).

**Повтор — своими режимами** (`Retry`, разбор прогона сбора 20.10: «повтор по сроку» не
случался — проход ставили только запуск и «Продолжить»). Сам, по сроку (`DUE`), и кнопкой
«Повторить сейчас» (`NOW`) очередь берёт только сайты, поиск которым уже шёл и ждёт повтора:
не ответил (`no_answer`) и «не спросили» (квота, частота, сбой — `attempts.RETRIABLE`). Первый
поиск принятым — по-прежнему кнопкой: он стоит денег, и запускает его человек. Срок повтора
молчавшему сайту — общего правила, но не раньше `SALES_SEGMENT_AGAIN_MIN` минут после попытки
(`retry_at`): первый срок у правила — «следующий проход», а сам проход идёт сразу.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.config.contacts import CONTACT_TTL_DAYS
from backend.features.contacts import attempts
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.repository import domain_ids, save_addresses
from backend.features.core.domain import ContactStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.sales import company_hold
from backend.features.sales.segment_leads import living_address
from backend.features.sales.segment_models import Lane, SalesSegmentCandidateModel

logger = logging.getLogger(__name__)

Candidate = SalesSegmentCandidateModel

#: Исходы поиска, после которых он повторяется: сайт не ответил — по сроку, «не спросили» —
#: следующим проходом.
RETRIES = (ContactStatus.NO_ANSWER, *sorted(attempts.RETRIABLE))


class Retry(StrEnum):
    """Повтор поиска сайтам, которым он уже шёл: сам по сроку или кнопкой, не дожидаясь срока."""

    DUE = "due"
    NOW = "now"


def retry_at(attempted: datetime, tries: int) -> datetime:
    """Когда сайт, не ответивший поиску, спросят снова сами: срок общего правила
    (`attempts.delay_after`), но не раньше `SALES_SEGMENT_AGAIN_MIN` минут после попытки."""
    least = timedelta(minutes=sales_cfg.SEGMENT_AGAIN_MIN)
    return attempted + max(attempts.delay_after(tries), least)


def due_now(now: datetime) -> ColumnElement[bool]:
    """Пора ли искать снова — общим правилом попыток (`attempts.waiting`) со сроком годности
    адреса: одно у прохода и у чисел отчёта прогона."""
    return attempts.waiting(Candidate, now=now, ttl_days=CONTACT_TTL_DAYS)


def needs_address(run_id: int) -> ColumnElement[bool]:
    """Принятые кандидаты прогона без лида, адреса на домене и чужого живого лида — кому вообще
    искать адрес; когда — решает режим очереди."""
    return and_(
        Candidate.run_id == run_id,
        Candidate.lane == Lane.ACCEPT,
        Candidate.lead_id.is_(None),
        ~living_address(Candidate.domain_id),
        ~company_hold.held(Candidate.domain_id),
    )


class SegmentContactQueue:
    """Кому из кандидатов прогона пора искать адрес и куда писать исход."""

    #: Этап платной ступени поиска адреса — расход сбора считается продажам. Поле общего
    #: `ContactQueue`: поиск берёт этап из очереди, а не из вызова.
    stage: Stage | None = Stage.SALES

    def __init__(self, session: AsyncSession, run_id: int, *, retry: Retry | None = None) -> None:
        self._session = session
        self._run_id = run_id
        self._retry = retry

    def _due(self) -> ColumnElement[bool]:
        """Условие «нужен адрес» — одно на счёт и на выборку: два разных правила
        разошлись бы, и отчёт называл бы одно число, а поиск брал другое. Повтор (`Retry`) —
        только сайтам, поиск которым уже шёл: по сроку или сейчас."""
        due = due_now(datetime.now(UTC))
        if self._retry is None:
            return and_(needs_address(self._run_id), due)
        again = Candidate.contact_status.in_(RETRIES)
        if self._retry is Retry.NOW:
            return and_(needs_address(self._run_id), again)
        return and_(needs_address(self._run_id), again, due)

    async def pending_hosts(self, *, limit: int = 100) -> list[str]:
        """Принятые без адреса — в порядке выдачи: кого судья принял раньше, тот раньше."""
        hosts = select(DomainModel.host).join(Candidate, Candidate.domain_id == DomainModel.id)
        found = await self._session.scalars(
            hosts.where(self._due()).order_by(Candidate.id).limit(limit)
        )
        return list(found.all())

    async def pending_count(self) -> int:
        """Сколько принятых ждут поиска адреса — тем же условием, что и выборка."""
        return int(
            await self._session.scalar(select(func.count(Candidate.id)).where(self._due())) or 0
        )

    async def silent_retries(self) -> list[datetime]:
        """Когда сами спросят сайты, не ответившие поиску (`retry_at`), — по одному на сайт."""
        rows = await self._session.execute(
            select(Candidate.contact_attempted_at, Candidate.contact_tries).where(
                needs_address(self._run_id),
                Candidate.contact_status == ContactStatus.NO_ANSWER,
                Candidate.contact_attempted_at.is_not(None),
            )
        )
        found = rows.tuples().all()
        return [retry_at(attempted, tries) for attempted, tries in found if attempted is not None]

    async def last_tries(self, hosts: Sequence[str]) -> set[str]:
        """Кандидаты, для которых проход — последний шанс сайту ответить."""
        return await attempts.last_tries(self._session, Candidate, hosts)

    async def save(self, results: Sequence[LadderResult]) -> int:
        """Сохранить пачку исходов. Возвращает число доменов с адресом.

        Правило попыток одно на все очереди (`attempts.py`): счёт проходов без ответа
        и повтор по сроку — как у доноров и рекламодателей."""
        if not results:
            return 0
        ids = await domain_ids(self._session, [result.host for result in results])
        if lost := sorted({result.host for result in results} - set(ids)):
            logger.warning(
                "сбор продаж: доменов нет в базе — исход не записан", extra={"hosts": lost}
            )
        outcomes = [attempts.Outcome(r.host, r.status, r.reason) for r in results]
        await attempts.record(self._session, Candidate, outcomes, ids, moment=datetime.now(UTC))
        return await save_addresses(self._session, results, ids)
