"""«Искать глубже» — поиск адреса одному донору с исходом «адреса нет».

Право `run`, как у «Найти адрес»: лестница — это запросы к чужому сайту и браузер,
а с ними и время единственного процесса очереди адресов. Платная ступень глубже
не зовётся (`contacts/deeper.py`).

**Отказ — до постановки, словами** (`deeper_refusal`): тем же правилом, что видит
карточка, и то же правило задача проверит ещё раз — между нажатием и исполнением
донору могли вписать адрес. **Одна задача на донора за раз**: второе нажатие, пока
первая не кончилась, — 409 словами (`api/letters/once.enqueue_once`).

В журнале — кто поставил поиск и по какому донору: поиск тратит время сайта и наше.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.contacts.schemas import ContactsQueued
from backend.api.deps import db_session, needs
from backend.api.letters.once import enqueue_once
from backend.features.access.repository import AccessRepository
from backend.features.contacts import manual
from backend.features.contacts.deeper import JOB_TIMEOUT_SEC, deeper_refusal
from backend.features.contacts.repository import SearchRefusedError
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.standing import UnknownDonorError
from backend.shared.database.ids import storable
from backend.shared.queue import DEEPER_JOB, contacts_queue, deeper_job_id, with_retries

logger = logging.getLogger(__name__)

router = APIRouter()

_runner = Depends(needs(Permission.RUN))

#: Второе нажатие, пока первый поиск глубже не кончился.
DEEPER_RUNNING = (
    "Поиск глубже по этому донору уже идёт — дождитесь его итога у кнопки: второй "
    "прошёл бы тот же сайт ещё раз"
)


async def donor_of(session: AsyncSession, donor_id: int) -> DonorModel:
    """Донор по номеру; номер больше столбца — «нет», а не пятисотка (`storable`)."""
    donor = await session.get(DonorModel, donor_id) if storable(donor_id) else None
    if donor is None:
        raise UnknownDonorError(f"Донора №{donor_id} нет")
    return donor


@router.post("/donors/{donor_id}/deeper", response_model=ContactsQueued, summary="Искать глубже")
async def search_deeper(
    donor_id: int,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> ContactsQueued:
    """Поставить поиск адреса глубже одному донору — с карточки или из списка."""
    donor = await donor_of(session, donor_id)
    refusal = await deeper_refusal(session, donor)
    if refusal is not None:
        raise SearchRefusedError(refusal)
    job = await asyncio.to_thread(
        enqueue_once,
        contacts_queue(),
        deeper_job_id(donor_id),
        DEEPER_JOB,
        donor_id,
        # Свой потолок, а не час очереди: процесс очереди адресов один (`deeper.py`).
        job_timeout=JOB_TIMEOUT_SEC,
        **with_retries(),
    )
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, DEEPER_RUNNING)
    host = await manual.host_of(session, donor)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"donor:{donor_id}",
        details={
            "действие": "поиск адреса глубже",
            "донор": host,
            "платная ступень": "не зовётся",
        },
    )
    await session.commit()
    logger.info("контакты: %s поставил поиск глубже донору №%s", author.email, donor_id)
    return ContactsQueued(job_id=str(job.id), pending=1)
