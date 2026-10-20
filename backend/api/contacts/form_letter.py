"""«Форма и письмо»: где форма донора и какой текст в неё вставить.

Смотреть — право `view`: ссылка на форму и собранный текст ничего не тратят. Собрать
текст — право `run`: это вызов модели, как у сборки писем (`letters/form_letter.py`),
и запись в журнале — кто попросил. Собранный текст хранится: второй раз его отдают
без вызова; заново — только по просьбе (`again`).

**Форму сервис не отправляет** — отправляет человек. У форм капча и защита от спама,
и робот, который их проходит, ведёт себя как спам-робот: такой поток режут и форму,
и домены рассылки. Экран так и говорит (`frontend/src/forms`).

Донор — только из очереди «Формы» (`forms.form_row`): с адресом он уже обычный, и его
письмо собирает экран писем; закрытый «Не вышло» в форме не нуждается.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.contacts.deeper import donor_of
from backend.api.contacts.schemas import FormLetterBody, FormLetterCard, FormLetterView
from backend.api.deps import db_session, needs
from backend.api.letters.once import enqueue_once
from backend.features.access.repository import AccessRepository
from backend.features.contacts import forms
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.form_letter import FormLetterModel
from backend.features.letters import compose
from backend.features.letters.form_letter import stored
from backend.shared.queue import (
    FORM_LETTER_JOB,
    form_letter_job_id,
    job_alive,
    quick_queue,
    with_retries,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_viewer = Depends(needs(Permission.VIEW))
_runner = Depends(needs(Permission.RUN))

#: Второе нажатие, пока текст ещё собирается.
LETTER_RUNNING = (
    "Текст письма для этой формы уже собирается — дождитесь его в окне: второй вызов "
    "модели потратил бы токены на тот же текст"
)


def _card(letter: FormLetterModel | None) -> FormLetterCard | None:
    if letter is None:
        return None
    return FormLetterCard(
        subject=letter.subject,
        body=letter.body,
        based_on=letter.based_on,
        built_at=letter.updated_at,
        uniqueness_pct=letter.uniqueness_pct,
        notes=letter.notes,
        unset=compose.unset_in(f"{letter.subject}\n{letter.body}"),
    )


def _building(donor_id: int) -> str | None:
    """Номер задачи сборки, если она ещё идёт: окно ведёт её, а не ставит вторую.
    Клиент Redis синхронный — зовётся в пуле потоков (`routes._queue_state`)."""
    job_id = form_letter_job_id(donor_id)
    return job_id if job_alive(job_id) is True else None


@router.get(
    "/forms/{donor_id}/letter", response_model=FormLetterView, summary="Форма и текст письма"
)
async def letter_of(
    donor_id: int,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> FormLetterView:
    """Где форма донора и собранный текст — без вызова модели. Смотреть можно у любого
    донора: собирать текст — только тому, кто в очереди «Формы» (`build_letter`)."""
    donor = await donor_of(session, donor_id)
    domain = await session.get(DomainModel, donor.domain_id)
    assert domain is not None  # внешний ключ донора
    return FormLetterView(
        donor_id=donor.id,
        host=domain.host,
        form_url=domain.contact_form_url,
        letter=_card(await stored(session, donor.domain_id)),
        job_id=await asyncio.to_thread(_building, donor_id),
    )


@router.post(
    "/forms/{donor_id}/letter", response_model=FormLetterView, summary="Собрать текст письма"
)
async def build_letter(
    donor_id: int,
    body: FormLetterBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> FormLetterView:
    """Собрать текст письма для формы задачей; собранный — отдать, не тратя модель."""
    row = await forms.form_row(session, donor_id)
    known = await stored(session, row.domain_id)
    view = FormLetterView(
        donor_id=row.donor_id, host=row.host, form_url=row.form_url, letter=_card(known)
    )
    if known is not None and not body.again:
        return view
    job = await asyncio.to_thread(
        enqueue_once,
        quick_queue(),
        form_letter_job_id(donor_id),
        FORM_LETTER_JOB,
        donor_id,
        author.email,
        **with_retries(),
    )
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, LETTER_RUNNING)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"donor:{donor_id}",
        details={
            "действие": "текст письма для формы",
            "донор": row.host,
            "заново": "да" if known is not None else "нет",
        },
    )
    await session.commit()
    return view.model_copy(update={"job_id": str(job.id)})
