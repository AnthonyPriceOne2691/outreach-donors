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
случался — проход ставили только запуск и «Продолжить»), и только тем, чей срок настал
(ревью 20.10: кнопка, бравшая всех, сжигала расписание — три нажатия, и «адреса нет» на полгода):

- **сам, по сроку** (`DUE`) — только «сайт не ответил» (`no_answer`), чей срок настал
  (`term_reached`). «Не спросили» платный сервис (квота, частота, сбой — `attempts.RETRIABLE`)
  без платной ступени повтор только испортил бы: бесплатные ступени уже прошли, и сайт ушёл бы в
  «адреса нет»;
- **кнопкой «Повторить сейчас»** (`NOW`) — те же и «не спросили», когда платная ступень у прохода
  правда есть (`paid`: включена у сбора, ключ есть и смета не выбрана, `paid_left`).

Первый поиск принятым — по-прежнему кнопкой: он стоит денег, и запускает его человек. **Срок —
одним правилом** у прохода, у чисел отчёта и у задачи на срок: ступень общего расписания
(`attempts.delay_after`), но не раньше `SALES_SEGMENT_AGAIN_MIN` минут после попытки — в Python
(`retry_at`) и в SQL (`term_reached`); у первой ступени общего правила срок — «следующий проход».

**Платный поиск повтора — в пределах сметы** (`paid_left`): смета запуска минус запросы, которые
провайдер уже принял у прогона (`segment_spend.searched`). Повтор идёт сам, без человека, и
платить сверх того, что человек подтвердил запуском, он не должен.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as contacts_cfg
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
from backend.features.sales.segment_spend import pending, searched

logger = logging.getLogger(__name__)

Candidate = SalesSegmentCandidateModel

#: «Не спросили» платный сервис: квота, частота, сбой — повтор кнопкой, когда платная ступень есть.
UNASKED = tuple(sorted(attempts.RETRIABLE))


class Retry(StrEnum):
    """Повтор поиска сайтам, которым он уже шёл и чей срок настал: сам или кнопкой."""

    DUE = "due"
    NOW = "now"


def least() -> timedelta:
    """Срок повтора не раньше этого после попытки (`SALES_SEGMENT_AGAIN_MIN`)."""
    return timedelta(minutes=sales_cfg.SEGMENT_AGAIN_MIN)


def retry_at(attempted: datetime, tries: int) -> datetime:
    """Когда сайт, не ответивший поиску, спросят снова: срок общего правила
    (`attempts.delay_after`), но не раньше `SALES_SEGMENT_AGAIN_MIN` минут после попытки."""
    return attempted + max(attempts.delay_after(tries), least())


def _on_step(step: int) -> ColumnElement[bool]:
    """Ступень расписания по счёту проходов без ответа — как `attempts.delay_after`: первая берёт
    и ноль, последняя — всё, что выше."""
    tries = Candidate.contact_tries
    if step == 1:
        return tries <= 1
    if step == attempts.MAX_RETRIES:
        return tries >= step
    return tries == step


def term_reached(now: datetime) -> ColumnElement[bool]:
    """«Сайт не ответил», и срок повтора (`retry_at`) настал — тем же правилом в SQL."""
    attempted = Candidate.contact_attempted_at
    steps = [
        and_(_on_step(step), attempted <= now - max(attempts.delay_after(step), least()))
        for step in range(1, attempts.MAX_RETRIES + 1)
    ]
    return and_(Candidate.contact_status == ContactStatus.NO_ANSWER, or_(*steps))


def due_now(now: datetime) -> ColumnElement[bool]:
    """Пора ли искать — общим правилом попыток (`attempts.waiting`), а «сайт не ответил» — когда
    настал его срок (`term_reached`): одно у прохода, у числа отчёта и у задачи на срок."""
    return and_(
        attempts.waiting(Candidate, now=now, ttl_days=CONTACT_TTL_DAYS),
        or_(
            Candidate.contact_status.is_(None),
            Candidate.contact_status != ContactStatus.NO_ANSWER,
            term_reached(now),
        ),
    )


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


def paid_left(report: dict[str, object], estimate: dict[str, object]) -> int:
    """Сколько платных поисков адреса осталось повтору прогона: платный поиск у сбора включён и
    ключ есть — смета запуска минус принятые провайдером запросы прогона и резерв, не снятый
    упавшим проходом (`segment_spend.pending`); иначе ноль."""
    if not (sales_cfg.SEGMENT_PAID_CONTACTS and contacts_cfg.HUNTER_API_KEY):
        return 0
    promised = estimate.get("hunter_searches")
    promised = int(promised) if isinstance(promised, int | float) else 0
    return max(0, promised - searched(report) - pending(report))


class SegmentContactQueue:
    """Кому из кандидатов прогона пора искать адрес и куда писать исход."""

    #: Этап платной ступени поиска адреса — расход сбора считается продажам. Поле общего
    #: `ContactQueue`: поиск берёт этап из очереди, а не из вызова.
    stage: Stage | None = Stage.SALES

    def __init__(
        self,
        session: AsyncSession,
        run_id: int,
        *,
        retry: Retry | None = None,
        paid: bool = False,
        now: datetime | None = None,
    ) -> None:
        self._session = session
        self._run_id = run_id
        self._retry = retry
        self._paid = paid
        self._now = now

    def _due(self) -> ColumnElement[bool]:
        """Условие «нужен адрес» — одно на счёт и на выборку: два разных правила
        разошлись бы, и отчёт называл бы одно число, а поиск брал другое. Повтор (`Retry`) —
        только сайтам, чей срок настал; «не спросили» — кнопкой и с платной ступенью (`paid`)."""
        now = self._now or datetime.now(UTC)
        if self._retry is None:
            return and_(needs_address(self._run_id), due_now(now))
        if self._retry is Retry.NOW and self._paid:
            return and_(
                needs_address(self._run_id),
                or_(term_reached(now), Candidate.contact_status.in_(UNASKED)),
            )
        return and_(needs_address(self._run_id), term_reached(now))

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
