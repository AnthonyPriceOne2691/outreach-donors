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
from backend.features.letters import deferrals
from backend.features.ops.alarms import Alarm, direction
from backend.features.replies import recipient_stage
from backend.features.replies.unbound import is_unbound

#: Сколько письмо может висеть «отправлено» без единого события
#: доставки. Платформа присылает их минутами; шесть часов — это уже
#: не задержка, а обрыв.
DELIVERY_SILENCE_HOURS = 6

#: Сколько номеров висящих писем назвать в тревоге — остальные числом.
SHOWN_LETTERS = 3

#: Висящее письмо без единой отсрочки — причины платформа не назвала.
UNHEARD = "платформа о нём ещё ничего не сообщила"

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


async def _delivery(session: AsyncSession, moment: datetime, stage: Stage) -> Alarm | None:
    """Письма этапа ушли больше `DELIVERY_SILENCE_HOURS` назад, а исхода у них нет: ни доставки,
    ни отказа.

    Две разные поломки — разными словами (29.10.2026: одно письмо рекламодателю висело без исхода,
    а тревога велела проверять вебхук, хотя письма того же дня доставлялись): после отправки самого
    старого висящего платформа не сообщила доставки **ни одному** письму этапа — молчит вебхук
    (`delivery-silence`); сообщила другим — вебхук жив, а висящие ждут сервера получателя:
    платформа повторяет приём до 72 часов, и исход придёт сам (`delivery-pending`)."""
    edge = moment - timedelta(hours=DELIVERY_SILENCE_HOURS)
    rows = await session.execute(
        select(MessageModel.id, MessageModel.sent_at)
        .where(
            _of_stage(stage),
            MessageModel.status == MessageStatus.SENT,
            MessageModel.sent_at.is_not(None),
            MessageModel.sent_at < edge,
            # Нулевой транспорт событий не порождает — молчание при нём
            # не поломка, а его устройство.
            MessageModel.provider_message_id.not_like("null-%"),
        )
        .order_by(MessageModel.sent_at, MessageModel.id)
    )
    stale = list(rows.tuples().all())
    if not stale:
        return None
    heard = await session.scalar(
        select(func.count(MessageModel.id)).where(
            _of_stage(stage), MessageModel.delivered_at >= stale[0][1]
        )
    )
    numbers = _numbers([letter_id for letter_id, _ in stale])
    if heard:
        reasons = await _reasons(session, [letter_id for letter_id, _ in stale], moment)
        return Alarm(
            code=f"delivery-pending:{stage.value}",
            title=f"Письма без исхода доставки — {direction(stage)}",
            detail=(
                f"{_pending_words(len(stale), reasons)}. Другие письма направления после них "
                "доставлены — вебхук событий работает. Обычно так тянет сервер получателя: "
                "платформа повторяет доставку до 72 часов, и исход придёт сам. Что ответил сервер, "
                "видно в журнале писем платформы (Activity)"
            ),
            stage=stage,
        )
    return Alarm(
        code=f"delivery-silence:{stage.value}",
        title=f"Платформа молчит о доставке — {direction(stage)}",
        detail=(
            f"Писем направления без единого события больше {DELIVERY_SILENCE_HOURS} часов: "
            f"{len(stale)} ({numbers}), и после их отправки платформа не сообщила доставки ни одному "
            "письму направления. Проверить адрес вебхука событий в кабинете платформы и его подпись"
        ),
        stage=stage,
    )


def _numbers(ids: list[int]) -> str:
    """«№404, №405, №406 и ещё 2» — первые `SHOWN_LETTERS` номеров, остальные числом."""
    shown = ", ".join(f"№{letter_id}" for letter_id in ids[:SHOWN_LETTERS])
    more = len(ids) - SHOWN_LETTERS
    return shown if more <= 0 else f"{shown} и ещё {more}"


async def _reasons(
    session: AsyncSession, ids: list[int], moment: datetime
) -> list[tuple[int, str | None]]:
    """Первые `SHOWN_LETTERS` висящих писем — у каждого своя причина: его последняя отсрочка
    словами (`letters/deferrals.said`), «сервер получателя не отвечает (порт 25, тайм-аут),
    14 попыток с 10:26 по Москве». Отсрочек у письма не было — `None`."""
    shown = ids[:SHOWN_LETTERS]
    found = await session.scalars(select(MessageModel).where(MessageModel.id.in_(shown)))
    letters = {letter.id: letter for letter in found}
    reasons: list[tuple[int, str | None]] = []
    for letter_id in shown:
        letter = letters.get(letter_id)
        reasons.append((letter_id, None if letter is None else deferrals.said(letter, moment)))
    return reasons


def _pending_words(count: int, reasons: list[tuple[int, str | None]]) -> str:
    """Висящие письма словами — у каждого своя причина (29.10.2026): одно — «Письмо №404 ушло…»,
    несколько — числом, первые номера с причинами, остальные числом."""
    if count == 1:
        [(letter_id, why)] = reasons
        tail = "а платформа ещё не сказала, дошло ли оно" if why is None else f"а исхода нет: {why}"
        return f"Письмо №{letter_id} ушло больше {DELIVERY_SILENCE_HOURS} часов назад, {tail}"
    listed = "; ".join(f"№{letter_id} — {why or UNHEARD}" for letter_id, why in reasons)
    more = count - len(reasons)
    return f"Писем без исхода больше {DELIVERY_SILENCE_HOURS} часов: {count}. {listed}" + (
        f"; и ещё {more}" if more > 0 else ""
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
        title=f"Ответов нет ни одного — {direction(stage)}",
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
        title=f"Добивки стоят — {direction(stage)}",
        detail=(
            f"{late} писем направления ждут следующего шага дольше часа. Цепочка не рвётся — "
            "она просто не идёт: процесс добивок не запущен или падает"
        ),
        stage=stage,
    )
