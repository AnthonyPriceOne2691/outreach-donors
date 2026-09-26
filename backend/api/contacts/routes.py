"""Контакты: поиск по кнопке и ручная очередь форм.

**До этого раздела поиск контактов запускала только консоль.** Прогон
находил доноров, а чтобы найти им адреса, нужен был инженер — при том
что приёмка веб-слоя прямо требует, чтобы сотрудник доводил донора
до цены, ни разу к инженеру не обратившись.

Право `run`, а не `view`: лестница доходит до платной ступени, то есть
тратит деньги — ровно как прогон.

**Поиск идёт задачей.** Сотня доменов — это минуты; выполнять их
внутри запроса значит потерять работу, если человек закрыл вкладку.

**Один донор — тот же поиск.** С карточки донора ставится та же задача,
суженная до него, и под тем же правом: правило «кому искать» одно
(`contacts/repository._needs_contact`), и отказ называет, какое его
условие не выполнено.

**Адрес руками — с карточки донора, сколько угодно** (26.09.2026). Правило
то же, что у очереди форм (`contacts/manual.py`), право то же — `run`,
в журнале — кто вписал и кто удалил. Ответ — карточка донора целиком:
вписанный адрес меняет исход поиска и то, на какой адрес уйдёт письмо,
и экран показывает это словами сервера, а не своей догадкой.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from redis.exceptions import RedisError
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.contacts.schemas import (
    AddressBody,
    ContactsQueued,
    ContactsState,
    FilledBody,
    FormCard,
    FormsView,
    GiveUpBody,
    SearchBody,
)
from backend.api.deps import db_session, needs
from backend.api.donors.schemas import DonorFullCard
from backend.api.jobs.routes import JobCard
from backend.config import contacts as contacts_cfg
from backend.features.access.repository import AccessRepository
from backend.features.contacts import forms, manual
from backend.features.contacts.repository import (
    ContactRepository,
    SearchRefusedError,
    search_refusal,
)
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.browse import DonorBrowser, UnknownDonorError
from backend.features.ops.job_outcome import job_outcome
from backend.shared.database.ids import storable
from backend.shared.queue import (
    CONTACTS_JOB,
    contacts_job_id,
    job_alive,
    remember_contacts_job,
    runs_queue,
    with_retries,
    workers_alive,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contacts", tags=["контакты"])

_runner = Depends(needs(Permission.RUN))
_viewer = Depends(needs(Permission.VIEW))


@router.get("", response_model=ContactsState, summary="Состояние поиска контактов")
async def state(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> ContactsState:
    """Сколько доноров ждёт контакта и идёт ли поиск прямо сейчас."""
    pending = await ContactRepository(session).pending_count()
    job_id = contacts_job_id()
    running = bool(job_id) and job_alive(job_id) is True
    outcome = job_outcome(job_id) if job_id else None
    return ContactsState(
        pending=pending,
        running=running,
        job_id=job_id,
        last=_last_report(job_id) if job_id and not running else None,
        workers=workers_alive(),
        job=JobCard.of(outcome) if outcome is not None else None,
    )


@router.post("", response_model=ContactsQueued, summary="Найти контакты")
async def search(
    body: SearchBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> ContactsQueued:
    """Поставить поиск контактов в очередь."""
    pending = await ContactRepository(session).pending_count()
    job = runs_queue().enqueue(CONTACTS_JOB, body.limit, body.use_browser, False, **with_retries())
    remember_contacts_job(str(job.id))
    logger.info("контакты: %s поставил поиск, ждёт %s доноров", author.email, pending)
    return ContactsQueued(job_id=str(job.id), pending=pending)


@router.post("/donors/{donor_id}", response_model=ContactsQueued, summary="Найти адрес донору")
async def search_one(
    donor_id: int,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> ContactsQueued:
    """Поставить поиск адреса одному донору — с его карточки.

    Правило проверяется до постановки: отказ приходит сразу и словами,
    а не пустой задачей через минуту. Задача проверит его ещё раз — между
    нажатием и исполнением общий поиск мог успеть найти адрес.

    Номер задачи не запоминается как «поиск контактов»: то место — про
    общий поиск, и один донор выдал бы на экране списка чужой исход.
    """
    donor = await session.get(DonorModel, donor_id)
    if donor is None:
        raise UnknownDonorError(f"Донора №{donor_id} нет")
    refusal = await search_refusal(session, donor)
    if refusal is not None:
        raise SearchRefusedError(refusal)
    job = runs_queue().enqueue(CONTACTS_JOB, 1, False, False, donor_id, **with_retries())
    logger.info("контакты: %s поставил поиск адреса донору №%s", author.email, donor_id)
    return ContactsQueued(job_id=str(job.id), pending=1)


async def _donor(session: AsyncSession, donor_id: int) -> DonorModel:
    donor = await session.get(DonorModel, donor_id) if storable(donor_id) else None
    if donor is None:
        raise UnknownDonorError(f"Донора №{donor_id} нет")
    return donor


@router.post(
    "/donors/{donor_id}/addresses", response_model=DonorFullCard, summary="Вписать адрес донору"
)
async def add_address(
    donor_id: int,
    body: AddressBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> DonorFullCard:
    """Адрес, который человек знает сам, — к адресам донора.

    Кандидату тоже можно: письма всё равно уходят только донорам, принятым
    человеком, а адрес пригодится, если кандидата примут.
    """
    donor = await _donor(session, donor_id)
    contact = await manual.add(session, donor, body.email)
    await AccessRepository(session).record(
        AuditAction.CONTACT_ADDED,
        author_id=author.id,
        target=f"donor:{donor_id}",
        details={
            "донор": await manual.host_of(session, donor),
            "адрес": contact.email,
            "откуда": "карточка донора, вписан руками",
        },
    )
    await session.commit()
    return DonorFullCard.of(await DonorBrowser(session).card(donor_id))


@router.delete(
    "/donors/{donor_id}/addresses/{contact_id}",
    response_model=DonorFullCard,
    summary="Удалить адрес донора",
)
async def remove_address(
    donor_id: int,
    contact_id: int,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> DonorFullCard:
    """Удалить адрес, которому ещё не писали. С перепиской — отказ словами."""
    donor = await _donor(session, donor_id)
    removed = await manual.remove(session, donor, contact_id)
    await AccessRepository(session).record(
        AuditAction.CONTACT_REMOVED,
        author_id=author.id,
        target=f"donor:{donor_id}",
        details={
            "донор": await manual.host_of(session, donor),
            "адрес": removed.email,
            "исход поиска после": removed.contact_status.value
            if removed.contact_status
            else "не искали",
        },
    )
    await session.commit()
    return DonorFullCard.of(await DonorBrowser(session).card(donor_id))


@router.get("/forms", response_model=FormsView, summary="Ручная очередь форм")
async def form_queue(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> FormsView:
    """Доноры, у которых форма вместо адреса."""
    rows = await forms.queue(session)
    return FormsView(
        rows=[FormCard.of(row) for row in rows],
        total=await forms.total(session),
        monthly_left=await forms.monthly_left(session),
        monthly_cap=contacts_cfg.MANUAL_QUEUE_MONTHLY_CAP,
    )


@router.post("/forms/{donor_id}/filled", response_model=FormCard, summary="Форму заполнили")
async def filled(
    donor_id: int,
    body: FilledBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> FormCard:
    """Донор дал адрес через форму — дальше он обычный донор."""
    row = await forms.filled(session, donor_id, email=body.email)
    await AccessRepository(session).record(
        AuditAction.CONTACT_ADDED,
        author_id=author.id,
        target=f"donor:{donor_id}",
        details={"донор": row.host, "адрес": body.email, "откуда": "форма, заполнена руками"},
    )
    await session.commit()
    return FormCard.of(row)


@router.post("/forms/{donor_id}/give-up", response_model=FormCard, summary="Форма не вышла")
async def give_up(
    donor_id: int,
    body: GiveUpBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> FormCard:
    """Закрыть донора без адреса: висеть в очереди вечно он не должен."""
    row = await forms.gave_up(session, donor_id)
    await AccessRepository(session).record(
        AuditAction.CONTACT_ADDED,
        author_id=author.id,
        target=f"donor:{donor_id}",
        details={"донор": row.host, "исход": "форма не вышла", "почему": body.reason},
    )
    await session.commit()
    return FormCard.of(row)


def _last_report(job_id: str) -> dict[str, object] | None:
    """Отчёт законченной задачи. Живёт в самой очереди и исчезает
    вместе с ней — это нормально: числа нужны сразу после прохода,
    а не через неделю."""
    try:
        job = Job.fetch(job_id, connection=runs_queue().connection)
    except (NoSuchJobError, RedisError) as exc:
        logger.info("контакты: отчёт задачи %s недоступен (%s)", job_id, exc)
        return None
    result = job.latest_result() if job.is_finished else None
    value = result.return_value if result is not None else None
    return value if isinstance(value, dict) else None
