"""Письма оффера по найденной ссылке рекламодателю, которого сняли: из очереди и со сроков.

Отправка сама проверяет, что рекламодатель ещё есть (`Sending._check_advertiser`), но до
этого письмо висело бы в очереди готовым — пачка брала бы его и считала отказом, — а
добивка живёт не в очереди, а сроком у ушедшего письма: пока срок цел, адресат остаётся
в планах. Тот же довод, что у стоп-листа (`letters/stoplist.stop_pending`) и у «не пишем»
бизнесу ниши (`niche.decide`).

**Только рассылки по найденной ссылке.** Бизнесу ниши пишут по решению человека о нём
самом (`niche.decide`): «не пишем» по ссылке на тот же домен его не отменяет.

**Из очереди письмо выходит условием** (`settle.leave`): пачка могла секундой раньше
взять его в «отправляется», и «остановлено» поверх врало бы о письме, которое ушло.
«Отправляется» не трогается вовсе — его исход решает событие платформы или человек.

**Снятое так письмо не держит следующее** (решение 12.10.2026: рекламодателю пишем под
действующую ссылку). У него своя причина (`attempts.LINK_WITHDRAWN`): отбор его не
считает, и письмо под ссылку другого донора собирается на тот же адрес. Остановленное
человеком или стоп-листом держит по-прежнему — у него этой причины нет.

**Письмо, которое могло уйти, своей причины не получает** (ревью 12.10.2026). У письма,
возвращённого в очередь после неизвестного исхода (`unknown_outcome.py`), есть наш
`Message-ID` прежней попытки: она могла дойти. Такое письмо останавливается как
«писали» — адрес не освобождается, диалог остаётся, — иначе пересборка отправила бы
тому же человеку второе первое письмо.
"""

from __future__ import annotations

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outgoing_attachment import OutgoingAttachmentModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.crawl.niche import LINKS
from backend.features.letters import attempts, settle
from backend.features.letters.chain import FIRST_STEP


async def stop_offers(
    session: AsyncSession,
    domain_id: int,
    *,
    keep: str | None = None,
    reason: str = attempts.LINK_WITHDRAWN,
) -> int:
    """Снять письма рекламодателю `domain_id` в рассылках по найденной ссылке. Число снятых.

    `keep` — действующая страница рекламодателя: он остался (на него ссылается другой
    наш донор), и первые письма в очереди под эту страницу остаются, а остальные собраны
    под ссылку, которой у него больше нет, — снятую человеком, сменённую повторным
    обходом или на площадке поставщика, — и снимаются. Страница стоит в неизменяемой зоне
    оффера (`template.ADVERTISER`), и её текст в письме — тот, что был при сборке. Добивки
    и их сроки тогда не трогаются: переписка с настоящим рекламодателем уже идёт.
    `reason` — почему снято письмо в очереди (`attempts.WITHDRAWN_REASONS`).
    """
    letters = await session.scalars(
        select(MessageModel)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(
            MessageModel.domain_id == domain_id,
            CampaignModel.stage == Stage.ADVERTISERS,
            CampaignModel.audience == LINKS,
            or_(
                MessageModel.status == MessageStatus.QUEUED,
                MessageModel.next_action_at.is_not(None),
            ),
        )
        .order_by(MessageModel.id)
    )
    taken = 0
    for letter in letters.all():
        if keep is None and letter.status is not MessageStatus.QUEUED:
            letter.next_action_at = None
            taken += 1
        elif keep is None or _off_page(letter, keep):
            taken += await out_of_queue(session, letter, reason=reason)
    return taken


def _off_page(letter: MessageModel, page: str) -> bool:
    """Первое письмо в очереди собрано не под действующую страницу рекламодателя."""
    return (
        letter.step == FIRST_STEP
        and letter.status is MessageStatus.QUEUED
        and page not in (letter.body or "")
    )


async def out_of_queue(
    session: AsyncSession,
    letter: MessageModel,
    *,
    reason: str = attempts.LINK_WITHDRAWN,
    keep_thread: bool = False,
) -> int:
    """Письмо из очереди — в «остановлено», если его не взяла отправка. 1 — снято.

    Своя причина (`attempts.WITHDRAWN_REASONS`: снята ссылка, пример площадки, площадка
    поставщика, сайт в работе у другого направления) и освобождённый диалог — только
    у письма, которое точно не уходило: у возвращённого после неизвестного исхода есть наш
    `Message-ID` прежней попытки, и оно остаётся «писали». `keep_thread` — диалог остаётся
    и у такого: к диалогу продаж привязан лид (`letters/site_role.py`).
    """
    gone = letter.internet_message_id is not None
    said = {} if gone else {"failure_reason": reason}
    left = await settle.leave(
        session,
        letter,
        was=MessageStatus.QUEUED,
        status=MessageStatus.STOPPED,
        next_action_at=None,
        **said,
    )
    if left and not gone and letter.step == FIRST_STEP and not keep_thread:
        await _free_thread(session, letter)
    return int(left)


async def _free_thread(session: AsyncSession, letter: MessageModel) -> None:
    """Диалог письма, которое так и не ушло, — долой, если в нём больше ничего нет.

    Диалог один на домен, рассылку и адрес, и следующее письмо тому же адресату той же
    рассылки — под действующую ссылку — легло бы в него же: тема добивки бралась бы
    от снятого письма, а диалог показывался бы «остановленным» при живой переписке.
    Письмо остаётся в базе, без диалога: оно не уходило, переписки с ним не было.
    Диалог, где есть другое письмо, ответ или файл, не трогается.
    """
    thread_id = letter.thread_id
    if thread_id is None:
        return
    busy = await session.scalar(
        select(
            or_(
                exists().where(MessageModel.thread_id == thread_id, MessageModel.id != letter.id),
                exists().where(ReplyModel.thread_id == thread_id),
                exists().where(OutgoingAttachmentModel.thread_id == thread_id),
            )
        )
    )
    if busy:
        return
    letter.thread_id = None
    await session.flush()
    thread = await session.get(ThreadModel, thread_id)
    if thread is not None:
        await session.delete(thread)
        await session.flush()
