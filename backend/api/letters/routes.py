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

**Сборка и пачка — по одной на этап и аудиторию за раз** (`once.py`): второе
нажатие, пока первая задача не кончилась, — 409 словами, а не вторая задача.

**Очередь смотрится по этапу.** Вопрос донору о цене и оффер рекламодателю
читаются разными глазами и уходят с разных доменов; у каждого этапа своя
воронка и свой текст по умолчанию.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from rq.job import Job
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.letters import once
from backend.api.letters.schemas import (
    Audience,
    BuildQueued,
    BuildRequestBody,
    Corridor,
    EditRequestBody,
    LetterDraftView,
    LettersView,
    QueuedLetterCard,
    ResolveBody,
    ResolvedLetter,
    SendQueueBody,
    SendQueueQueued,
    SendResult,
    Transport,
    UnknownLetterCard,
    UnknownLettersView,
    stage_two_only,
)
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import CampaignModel
from backend.features.core.stages import check_connected
from backend.features.crawl.niche import LINKS, NICHE, WHOM
from backend.features.letters import batch, compose, draft, review, unknown_outcome
from backend.features.letters.building import run_scope
from backend.features.letters.compose import NicheOffer
from backend.features.letters.niche_recipients import NicheRecipients
from backend.features.letters.repository import LetterRepository, QueuedLetter
from backend.features.letters.sending import Sending
from backend.features.letters.template import Template, of_campaign
from backend.features.letters.transport_factory import Transports, in_use
from backend.shared.queue import BUILD_JOB, RESULT_TTL, SEND_QUEUE_JOB, runs_queue, with_retries

router = APIRouter(prefix="/letters", tags=["письма"])

logger = logging.getLogger(__name__)

_viewer = Depends(needs(Permission.VIEW))
_sender = Depends(needs(Permission.SEND))


#: Этап словами — для журнала действий. Каждый этап: без записи журнал падал бы
#: `KeyError` уже после постановки задачи (сверка — `tests/test_sales_stage_screens.py`).
_STAGE_TITLES = {Stage.DONORS: "доноры", Stage.ADVERTISERS: "рекламодатели", Stage.SALES: "продажи"}


@router.get("", response_model=LettersView, summary="Очередь писем этапа")
async def queue(
    stage: Stage = Stage.DONORS,
    audience: Audience = "links",
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> LettersView:
    """Очередь этапа и аудитории: у бизнесов ниши свои письма, счёт для пачки, текст
    и воронка — «Отправить очередь · N» на их вкладке называет только их письма."""
    _stage_two(stage, audience)
    repository = LetterRepository(session)
    queued = await repository.queued(stage=stage, audience=audience)
    return LettersView(
        stage=stage,
        audience=audience,
        letters=[QueuedLetterCard.of(row) for row in queued],
        queued_total=await repository.queued_count(stage=stage, audience=audience),
        batch_max=batch.BATCH_MAX,
        letter_default=LetterDraftView.of(draft.default_draft(stage, audience)),
        blocked_by=compose.missing_settings(),
        transport=Transport.current(stage.value),
        corridor=Corridor(),
        funnel=await repository.funnel_report(stage, audience=audience),
    )


def _audience_of(audience: str) -> dict[str, str]:
    """Аудитория в доводах задачи — только не «по ссылке»: задачу по ссылке тогда
    понимает и воркер прежней версии, если её поставили в секунды выкатки (замечание
    ревью продаж к P3, 09.10.2026). Задача без аудитории и так — по ссылке."""
    return {} if audience == LINKS else {"audience": audience}


async def _enqueue_once(
    job_id: str, refusal: str, path: str, /, *args: object, **options: object
) -> Job:
    """Поставить задачу этапа и аудитории, если та же не идёт; идёт — 409 словами.

    Клиент Redis синхронный: постановка — в потоке, а не в цикле событий, иначе
    задумавшийся Redis держал бы все запросы сервиса, а не только этот.
    """
    job = await asyncio.to_thread(once.enqueue_once, runs_queue(), job_id, path, *args, **options)
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, refusal)
    return job


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
    await _same_audience(body, session)
    # Прогоны — здесь, до очереди: разные страны и неоконченный поиск контактов
    # человек должен увидеть у формы, а не в отчёте задачи через минуты.
    scope = await run_scope(LetterRepository(session), body.run_ids, stage=body.stage)
    job = await _enqueue_once(
        once.build_job_id(body.stage, body.audience),
        once.BUILD_RUNNING,
        BUILD_JOB,
        body.campaign,
        scope.country or body.country,
        niche=body.niche,
        limit=body.limit,
        followup_days=body.followup_days,
        letter_template=letter_template,
        run_ids=body.run_ids,
        stage=body.stage.value,
        **_audience_of(body.audience),
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
            "кому": WHOM[body.audience],
        },
    )
    await session.commit()
    return BuildQueued(job_id=str(job.id))


def _stage_two(stage: Stage, audience: Audience) -> None:
    """Бизнесы ниши — только Этап 2, как у сборки и пачки: тело проверяет схема, адрес — здесь."""
    try:
        stage_two_only(stage, audience)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


async def _same_audience(body: BuildRequestBody, session: AsyncSession) -> None:
    """Одноимённая рассылка другой аудитории — отказ у формы, а не в задаче через минуты."""
    found = await LetterRepository(session).find_campaign(name=body.campaign, stage=body.stage)
    if found is not None:
        draft.assert_same_audience(
            campaign=body.campaign, stored=found.audience, sent=body.audience
        )


async def _checked_letter(body: BuildRequestBody, session: AsyncSession) -> str | None:
    """Текст письма с экрана — проверенный, или `None`, если его не правили."""
    if body.letter is None:
        return None
    text = draft.to_text(body.letter.subject, body.letter.zones, body.stage, body.audience)
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
    niche = await _niche_offer(row, session)
    await review.edit(
        session,
        row.message,
        host=row.host,
        subject=body.subject,
        body=body.body,
        template=await _letter_template(row, session),
        niche=niche,
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
    if row.stage is Stage.ADVERTISERS and row.audience != NICHE and row.link is None:
        raise review.NotEditableError(
            f"Рекламодателя {row.host} сняли после сборки письма: править его незачем, "
            "письмо стоит убрать из очереди"
        )
    campaign = await session.get(CampaignModel, row.message.campaign_id)
    return of_campaign(row.stage, campaign.letter_template if campaign else None, row.audience)


async def _niche_offer(row: QueuedLetter, session: AsyncSession) -> NicheOffer | None:
    """Оффер бизнесу ниши сейчас. Собрать его больше нельзя — письмо убрать."""
    if row.stage is not Stage.ADVERTISERS or row.audience != NICHE:
        return None
    offer = await NicheRecipients(session).offer_of(row.message.domain_id)
    if offer is None:
        raise review.NotEditableError(
            f"Бизнесу ниши {row.host} письмо больше не собрать — нет площадки со свежей ценой "
            "или его сняли: письмо стоит убрать из очереди"
        )
    return offer


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
    await review.skip(session, row.message)
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
    """Отправить одно письмо — то, что человек прочёл и решил отправить сам.

    Пачкой — `send-queue` (слово Anthony 06.10.2026: «пачкой, вся очередь»).
    """
    # Транспорт — этапа письма: у направления бывает своя учётка платформы.
    async with in_use(Transports()) as transports:
        outcome = await Sending(session, transports).send(letter_id, author_id=author.id)
    return SendResult(
        id=outcome.message_id,
        sender_email=outcome.sender_email,
        real=outcome.real,
    )


@router.post("/send-queue", response_model=SendQueueQueued, summary="Отправить очередь этапа")
async def send_queue(
    body: SendQueueBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> SendQueueQueued:
    """Отправить всю очередь этапа пачкой — задачей, по одному письму.

    До 06.10.2026 кнопки «отправить всё» не было намеренно: каждое письмо
    читал человек (`docs/WEB_LAYER.md`). Для запуска Anthony выбрал пачку.
    Каждое письмо идёт тем же путём, что одно (`letters/batch.py`), и
    в журнал пишется так же — по письму, с тем, кто нажал.

    Пачка — одной аудитории (`body.audience`): кнопка на вкладке «Бизнесам ниши»
    отправляет только их письма, на «Рекламодателям» — только письма по найденной
    ссылке. Без аудитории — по ссылке, как до бизнесов ниши.
    """
    # Продажи, не подключённые к почте (ответ моста `core/stages`), — отказ словами (409)
    # до счёта и до задачи.
    await check_connected(session, body.stage, "Очередь писем не отправлена")
    waiting = await LetterRepository(session).queued_count(stage=body.stage, audience=body.audience)
    if waiting == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "В очереди этого этапа писем нет — отправлять нечего"
        )
    # Итог пачки — неделю, как у остальных задач (`RESULT_TTL`): с умолчанием rq в восемь
    # минут строка пачки через десять минут отвечала «задачи нет», и причина остановки
    # пачки пропадала с экрана (аудит 10.10.2026).
    job = await _enqueue_once(
        once.send_job_id(body.stage, body.audience),
        once.SEND_RUNNING,
        SEND_QUEUE_JOB,
        body.stage.value,
        author.id,
        **_audience_of(body.audience),
        result_ttl=RESULT_TTL,
    )
    # Пачка берёт не больше своего потолка: то же число, что «Отправить N» в окне, —
    # и потолок отсюда же, откуда его берёт экран писем (`batch_max`).
    taken = min(waiting, batch.BATCH_MAX)
    logger.info(
        "письма: %s поставил отправку очереди этапа %s (%s) — в очереди %s, пачка берёт %s",
        author.email,
        body.stage.value,
        body.audience,
        waiting,
        taken,
    )
    return SendQueueQueued(job_id=str(job.id), queued=taken)


@router.get("/unknown", response_model=UnknownLettersView, summary="Письма с неизвестным исходом")
async def unknown(
    stage: Stage = Stage.DONORS,
    audience: Audience = "links",
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> UnknownLettersView:
    """Письма этапа и аудитории, застрявшие в «отправляется»: связь с почтой оборвалась
    посреди передачи, и ушли ли они, неизвестно (`letters/unknown_outcome.py`). У каждой
    вкладки — свои: письмо бизнеса ниши решают там, откуда ушла его пачка."""
    _stage_two(stage, audience)
    found = await unknown_outcome.stuck(session, stage=stage, audience=audience)
    return UnknownLettersView(stage=stage, letters=[UnknownLetterCard.of(row) for row in found])


@router.post("/{letter_id}/resolve", response_model=ResolvedLetter, summary="Решить исход письма")
async def resolve(
    letter_id: int,
    body: ResolveBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> ResolvedLetter:
    """«Ушло» или «Вернуть в очередь» — по журналу платформы.

    Под правом на отправку: «вернуть в очередь» — это разрешение отправить
    письмо, которое, возможно, уже ушло.
    """
    done = await unknown_outcome.resolve(session, letter_id, body.outcome, author_id=author.id)
    await session.commit()
    return ResolvedLetter(id=letter_id, status=done.status, said=done.said)
