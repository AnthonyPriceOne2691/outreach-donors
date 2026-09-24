"""Очередь писем: посмотреть, собрать, поправить, отправить, не писать.

**Смотреть может каждый, у кого есть доступ к базе; менять — только
с правом на отправку.** Разделено не из иерархии: скомпрометированная
учётка обычного сотрудника не должна превращаться в рассылку с наших
доменов. Юниты возвращаются первого числа, репутация домена — никогда.

Правка и отказ тоже под правом на отправку: поправленный текст уходит
адресату так же, как собранный, а «не писать» вычёркивает донора
из будущих сборок.

**Сборка кладёт задачу в очередь, а не выполняет её.** Каждое письмо
стоит вызова модели, полсотни идут минутами, и выполнять это внутри
запроса значит потерять работу, если человек закрыл вкладку.

**Очередь смотрится по этапу.** Вопрос донору о цене и оффер рекламодателю
читаются разными глазами и уходят с разных доменов; у каждого этапа своя
воронка и свой текст по умолчанию.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.letters.schemas import (
    BuildQueued,
    BuildRequestBody,
    Corridor,
    EditRequestBody,
    LetterDraftView,
    LettersView,
    QueuedLetterCard,
    SendResult,
    Transport,
)
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import CampaignModel
from backend.features.letters import compose, draft, review
from backend.features.letters.building import run_scope
from backend.features.letters.repository import LetterRepository, QueuedLetter
from backend.features.letters.sending import Sending
from backend.features.letters.template import Template, of_campaign
from backend.features.letters.transport import TransportError
from backend.features.letters.transport_factory import build_transport
from backend.shared.queue import BUILD_JOB, runs_queue, with_retries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/letters", tags=["письма"])

_viewer = Depends(needs(Permission.VIEW))
_sender = Depends(needs(Permission.SEND))


def _transport_card() -> Transport:
    """Каким транспортом располагаем.

    Отказ собрать транспорт — это не поломка экрана, а его содержание:
    человек должен видеть, что отправлять нечем, и почему.
    """
    try:
        transport = build_transport()
    except TransportError as exc:
        # В лог тоже, а не только на экран: человек прочтёт и забудет,
        # а разбираться, почему рассылка стоит, будут по логам сервера.
        logger.warning("письма: транспорт не собран — %s", exc)
        return Transport(name="—", real=False, problem=str(exc))
    return Transport(name=transport.name, real=transport.real)


#: Этап словами — для журнала действий.
_STAGE_TITLES = {Stage.DONORS: "доноры", Stage.ADVERTISERS: "рекламодатели"}


@router.get("", response_model=LettersView, summary="Очередь писем этапа")
async def queue(
    stage: Stage = Stage.DONORS,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> LettersView:
    repository = LetterRepository(session)
    return LettersView(
        stage=stage,
        letters=[QueuedLetterCard.of(row) for row in await repository.queued(stage=stage)],
        letter_default=LetterDraftView.of(draft.default_draft(stage)),
        blocked_by=compose.missing_settings(),
        transport=_transport_card(),
        corridor=Corridor(),
        funnel=(await repository.funnel(stage)).as_report(),
    )


@router.post("/build", response_model=BuildQueued, summary="Собрать очередь")
async def build(
    body: BuildRequestBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> BuildQueued:
    """Поставить сборку в очередь задач. Ничего не отправляет.

    Текст письма проверяется здесь, до очереди: разбор шаблона идёт
    миллисекунды, а отказ из задачи человек увидел бы через минуты
    и не рядом с формой.
    """
    letter_template = await _checked_letter(body, session)
    # Прогоны — здесь, до очереди: разные страны и неоконченный поиск контактов
    # человек должен увидеть у формы, а не в отчёте задачи через минуты.
    scope = await run_scope(LetterRepository(session), body.run_ids, stage=body.stage)
    job = runs_queue().enqueue(
        BUILD_JOB,
        body.campaign,
        scope.country or body.country,
        niche=body.niche,
        limit=body.limit,
        followup_days=body.followup_days,
        letter_template=letter_template,
        run_ids=body.run_ids,
        stage=body.stage.value,
        **with_retries(),
    )
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details={
            "действие": "сборка писем",
            "этап": _STAGE_TITLES[body.stage],
            "кампания": body.campaign,
            "писем": body.limit,
            "добивки, дней": body.followup_days or "по умолчанию",
            "текст письма": "поправлен" if letter_template else "по умолчанию",
            "прогоны": body.run_ids or "все принятые",
        },
    )
    await session.commit()
    return BuildQueued(job_id=str(job.id))


async def _checked_letter(body: BuildRequestBody, session: AsyncSession) -> str | None:
    """Текст письма с экрана — проверенный, или `None`, если его не правили."""
    if body.letter is None:
        return None
    text = draft.to_text(body.letter.subject, body.letter.zones, body.stage)
    found = await LetterRepository(session).find_campaign(name=body.campaign, stage=body.stage)
    if found is not None:
        draft.assert_same(campaign=body.campaign, stored=found.letter_template, sent=text)
    return text


@router.patch("/{letter_id}", response_model=QueuedLetterCard, summary="Поправить письмо")
async def edit(
    letter_id: int,
    body: EditRequestBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> QueuedLetterCard:
    """Заменить текст руками. Отличие пересчитывается, запреты те же."""
    repository = LetterRepository(session)
    row = await repository.letter(letter_id)
    review.edit(
        row.message,
        host=row.host,
        subject=body.subject,
        body=body.body,
        template=await _letter_template(row, session),
        link=row.link,
    )
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author.id,
        target=f"message:{letter_id}",
        details={"действие": "письмо поправлено руками", "донор": row.host},
    )
    await session.commit()
    return QueuedLetterCard.of(row)


async def _letter_template(row: QueuedLetter, session: AsyncSession) -> Template:
    """Текст рассылки письма — от него меряется отличие правки.

    От утверждённого текста рассылки, а не от умолчания: иначе правка
    письма рассылки со своим текстом мерилась бы от чужого. У оффера
    рекламодателя, которого сняли после сборки, ссылки больше нет —
    мерить не от чего, и письмо стоит убрать, а не править.
    """
    if row.stage is Stage.ADVERTISERS and row.link is None:
        raise review.NotEditableError(
            f"Рекламодателя {row.host} сняли после сборки письма: править его незачем, "
            "письмо стоит убрать из очереди"
        )
    campaign = await session.get(CampaignModel, row.message.campaign_id)
    return of_campaign(row.stage, campaign.letter_template if campaign else None)


@router.post("/{letter_id}/skip", response_model=QueuedLetterCard, summary="Не писать этому донору")
async def skip(
    letter_id: int,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> QueuedLetterCard:
    """Убрать письмо из очереди. Донор считается написанным и в следующей
    сборке не появится: иначе от него пришлось бы отказываться каждую
    неделю заново."""
    repository = LetterRepository(session)
    row = await repository.letter(letter_id)
    review.skip(row.message)
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author.id,
        target=f"message:{letter_id}",
        details={"действие": "решено не писать", "донор": row.host},
    )
    await session.commit()
    return QueuedLetterCard.of(row)


@router.post("/{letter_id}/send", response_model=SendResult, summary="Отправить письмо")
async def send(
    letter_id: int,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> SendResult:
    """Отправить одно письмо.

    По одному, а не пачкой: смысл экрана в том, что спорное решение видит
    человек, и кнопка «отправить всё» этот смысл отменяет.
    """
    outcome = await Sending(session, build_transport()).send(letter_id, author_id=author.id)
    return SendResult(
        id=outcome.message_id,
        sender_email=outcome.sender_email,
        real=outcome.real,
    )
