"""Тишина почты по этапам: доставка, ответы и добивки — у каждого направления свои.

Три правила сторожа тишины (`silence.py`) считали письма и ответы всех этапов разом и давали одну
тревогу на всех, а лента говорит о коде один раз (`alarm_feed.py`). Чужая тишина прятала свою
поломку (ревью стыков 12.10.2026):

- продажи не подключены — проход добивок их сроки не берёт (`letters/followups.send_due`), и через
  час горело «Добивки стоят»; настоящая остановка добивок доноров новой тревоги уже не давала;
- ответ лида на домен продаж прятал сломанный приём ответов доноров;
- молчание вебхука одного направления прятало молчание вебхука другого.

**Каждое правило — по этапу рассылки**: письмо → рассылка → `CampaignModel.stage`; ответ →
переписка или письмо → рассылка, ответ без письма — по нашему адресу, на который пришёл
(`replies/recipient_stage.py`). Тревога несёт код `…:<этап>` и свой этап (`Alarm.stage`), как у
сторожа почты этапов (`mail_watch.py`): тревога продаж — только с правом «Продажи».

**Продажи — только подключённые** — тем же признаком, что у прохода добивок
(`core/stages.sales_connected`): не подключены — их сроки проход не берёт, а письма отправка не
шлёт, и тишина у них — устройство, а не поломка. Без права «Продажи» модуль продаж не спрашивается.

**Прежние коды без этапа** (`delivery-silence`, `reply-silence`, `followups-stuck`) после выкатки не
приходят: лента говорит о них «прошло», как о любой кончившейся тревоге, а действующая тишина
приходит кодом своего этапа.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

# Мост к модулю продаж — под своим именем: `stages` здесь — этапы, которые видит спрашивающий.
from backend.features.core import stages as mail_bridge
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.ops.alarms import Alarm
from backend.features.replies import recipient_stage
from backend.features.replies.unbound import is_unbound

#: Сколько письмо может висеть «отправлено» без единого события
#: доставки. Платформа присылает их минутами; шесть часов — это уже
#: не задержка, а обрыв.
DELIVERY_SILENCE_HOURS = 6

#: Сколько дней можно не получать ни одного ответа при живой рассылке.
#: Отвечают единицами процентов, поэтому порог считается
#: не по одному письму, а по доставленной пачке.
REPLY_SILENCE_DAYS = 3
REPLY_SILENCE_MIN_DELIVERED = 20

#: Насколько может опоздать добивка. Проход идёт раз в минуту;
#: час опоздания означает, что процесс не идёт вовсе.
FOLLOWUP_LATE_MINUTES = 60


async def alarms(
    session: AsyncSession, moment: datetime, *, stages: Collection[Stage]
) -> list[Alarm]:
    """Тишина почты этапов, которые видит спрашивающий (`stages`): у каждого — доставка, ответы
    и добивки, по порядку."""
    found: list[Alarm] = []
    for stage in await _watched(session, stages):
        for rule in (_delivery, _replies, _followups):
            alarm = await rule(session, moment, stage)
            if alarm is not None:
                found.append(alarm)
    return found


async def _watched(session: AsyncSession, stages: Collection[Stage]) -> list[Stage]:
    """Видимые этапы, чью почту считать: продажи — только подключённые (признак прохода добивок)."""
    seen = [stage for stage in Stage if stage in stages]
    if Stage.SALES in seen and not await mail_bridge.sales_connected(session):
        seen.remove(Stage.SALES)
    return seen


def _of_stage(stage: Stage) -> ColumnElement[bool]:
    """Письмо рассылки этапа — подзапросом, как у прохода добивок (`followups._chained`)."""
    return MessageModel.campaign_id.in_(
        select(CampaignModel.id).where(CampaignModel.stage == stage)
    )


def _named(stage: Stage) -> str:
    """Направление словами заголовка: те же «…», что у сторожа почты этапов."""
    return f"направление «{stage.value}»"


async def _delivery(session: AsyncSession, moment: datetime, stage: Stage) -> Alarm | None:
    """Письма этапа ушли, а платформа не сказала о них ни слова."""
    edge = moment - timedelta(hours=DELIVERY_SILENCE_HOURS)
    stale = await session.scalar(
        select(func.count(MessageModel.id)).where(
            _of_stage(stage),
            MessageModel.status == MessageStatus.SENT,
            MessageModel.sent_at.is_not(None),
            MessageModel.sent_at < edge,
            # Нулевой транспорт событий не порождает — молчание при нём
            # не поломка, а его устройство.
            MessageModel.provider_message_id.not_like("null-%"),
        )
    )
    if not stale:
        return None
    return Alarm(
        code=f"delivery-silence:{stage.value}",
        title=f"Платформа молчит о доставке — {_named(stage)}",
        detail=(
            f"{stale} писем направления отправлены больше {DELIVERY_SILENCE_HOURS} часов назад, "
            "и ни по одному не пришло события: ни доставки, ни отказа. "
            "Проверить адрес вебхука событий в кабинете платформы и его подпись"
        ),
        stage=stage,
    )


async def _replies(session: AsyncSession, moment: datetime, stage: Stage) -> Alarm | None:
    """Писем этапа за окно доставлено достаточно, а ответов этапа за то же окно нет совсем.

    Окно одно у писем и у ответов: доставленные за всё время после паузы рассылки длиной
    в окно зажигали тревогу, и она горела до первого ответа (ревью продаж 13.10.2026)."""
    since = moment - timedelta(days=REPLY_SILENCE_DAYS)
    delivered = await session.scalar(
        select(func.count(MessageModel.id)).where(
            _of_stage(stage),
            MessageModel.status == MessageStatus.DELIVERED,
            MessageModel.sent_at >= since,
        )
    )
    if not delivered or delivered < REPLY_SILENCE_MIN_DELIVERED:
        return None
    if await _heard(session, stage, since):
        return None
    return Alarm(
        code=f"reply-silence:{stage.value}",
        title=f"Ответов нет ни одного — {_named(stage)}",
        detail=(
            f"За {REPLY_SILENCE_DAYS} дня доставлено писем направления: {delivered}, а его "
            "входящих за то же время нет. Отвечают единицами процентов, но полный ноль на такой "
            "пачке чаще означает, что отвалился приём ответов на домены направления, а не что "
            "все промолчали"
        ),
        stage=stage,
    )


async def _heard(session: AsyncSession, stage: Stage, since: datetime) -> bool:
    """Пришёл ли с `since` хоть один ответ этапа: в переписку или на письмо его рассылки — тем
    же порядком, что у этапа ответа (`ReplyRepository.stage_of`), — или без письма на его адрес."""
    threads = (
        select(ThreadModel.id)
        .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
        .where(CampaignModel.stage == stage)
    )
    letters = select(MessageModel.id).where(_of_stage(stage))
    bound = await session.scalar(
        select(ReplyModel.id)
        .where(
            ReplyModel.created_at >= since,
            or_(
                ReplyModel.thread_id.in_(threads),
                and_(ReplyModel.thread_id.is_(None), ReplyModel.message_id.in_(letters)),
            ),
        )
        .limit(1)
    )
    if bound is not None:
        return True
    unbound = (
        await session.scalars(
            select(ReplyModel.to_addresses).where(ReplyModel.created_at >= since, is_unbound())
        )
    ).all()
    if not unbound:
        return False
    domains = await recipient_stage.known_domains(session)
    return any(recipient_stage.stage_of(listed, domains) is stage for listed in unbound)


async def _followups(session: AsyncSession, moment: datetime, stage: Stage) -> Alarm | None:
    """Срок добивки письма этапа прошёл, а её никто не забрал."""
    edge = moment - timedelta(minutes=FOLLOWUP_LATE_MINUTES)
    late = await session.scalar(
        select(func.count(MessageModel.id)).where(
            _of_stage(stage),
            MessageModel.next_action_at.is_not(None),
            MessageModel.next_action_at < edge,
        )
    )
    if not late:
        return None
    return Alarm(
        code=f"followups-stuck:{stage.value}",
        title=f"Добивки стоят — {_named(stage)}",
        detail=(
            f"{late} писем направления ждут следующего шага дольше часа. Цепочка не рвётся — "
            "она просто не идёт: процесс добивок не запущен или падает"
        ),
        stage=stage,
    )
