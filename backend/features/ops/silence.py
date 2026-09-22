"""Сторож тишины: поломки, которые выглядят как «ничего не происходит».

Самый дорогой класс отказов в этом сервисе — не падение, а молчание.
Вебхук платформы сменил адрес, приём ответов отвалился, процесс добивок
не поднялся после перезапуска: сервис при этом зелёный, экраны рисуются,
ошибок нет. Отличить «доноры не отвечают» от «мы перестали слышать
ответы» по одному экрану нельзя — нужно правило, знающее, чего ждать.

**Каждая тревога — это «при X не может быть Y».** Не «давно не было
событий» (может, и писем не было), а «письма ушли два дня назад,
а событий доставки нет ни одного». Тишина сама по себе не поломка;
поломка — тишина там, где обязано быть шумно.

**Сторож ничего не чинит и никого не останавливает.** Он только
называет. Автоматика, которая по своему подозрению останавливает
рассылку, однажды остановит её на ровном месте — а в день, когда
она понадобится, будет выключена.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import ahrefs as ahrefs_cfg
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.core.domain import CrawlOutcome, MessageStatus, RunStatus, StopReason
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.core.models.run import RunModel
from backend.features.runs.spending import ahrefs_spent_this_month
from backend.features.serp.dataforseo import SerpError
from backend.features.serp.factory import build_provider

logger = logging.getLogger(__name__)

#: Сколько письмо может висеть «отправлено» без единого события
#: доставки. Платформа присылает их минутами; шесть часов — это уже
#: не задержка, а обрыв.
DELIVERY_SILENCE_HOURS = 6

#: Сколько дней можно не получать ни одного ответа при живой рассылке.
#: Доноры отвечают единицами процентов, поэтому порог считается
#: не по одному письму, а по доставленной пачке.
REPLY_SILENCE_DAYS = 3
REPLY_SILENCE_MIN_DELIVERED = 20

#: Насколько может опоздать добивка. Проход идёт раз в минуту;
#: час опоздания означает, что процесс не идёт вовсе.
FOLLOWUP_LATE_MINUTES = 60

#: Сколько прогон может «идти» без единой отметки о жизни.
RUN_SILENCE_MINUTES = 30

#: За сколько последних обходов смотрим здоровье краула.
CRAWL_RUNS_WATCHED = 10


@dataclass(frozen=True, slots=True)
class Alarm:
    """Одна тревога: что молчит, с каких пор и что это значит."""

    code: str
    title: str
    detail: str


async def alarms(session: AsyncSession, *, now: datetime | None = None) -> list[Alarm]:
    """Всё, что сейчас молчит не по делу."""
    moment = now or datetime.now(UTC)
    found = [
        await _delivery_silence(session, moment),
        await _reply_silence(session, moment),
        await _followups_stuck(session, moment),
        await _runs_stuck(session, moment),
        await _cap_reached(session),
        await _crawl_blocked(session),
    ]
    return [alarm for alarm in found if alarm is not None]


async def probe_providers() -> Alarm | None:
    """Провайдеры не отвечают на бесплатный вопрос.

    Отдельно от остальных правил намеренно: это единственная проверка,
    которая ходит в сеть. Смешав её с чтением базы, мы получили бы
    «тревоги», которые нельзя посчитать без интернета, — и набор
    тестов, которому нужен чужой сервер.

    Требование просит сигнал «API недоступен», и отдельно от прогона:
    узнать об этом отказом посреди платной работы — значит узнать
    поздно. Оба запроса бесплатные: остаток юнитов и остаток на счету.
    """
    dead: list[str] = []

    client = AhrefsClient()
    try:
        await client.limits_and_usage()
    except (AhrefsError, OSError) as exc:
        logger.warning("сторож тишины: Ahrefs не ответил (%s)", exc)
        dead.append("Ahrefs")
    finally:
        await client.aclose()

    ahrefs = AhrefsClient()
    provider = build_provider(ahrefs)
    balance = getattr(provider, "balance", None)
    try:
        if balance is not None:
            await balance()
    except (SerpError, OSError) as exc:
        logger.warning("сторож тишины: источник выдачи не ответил (%s)", exc)
        dead.append("источник выдачи")
    finally:
        await ahrefs.aclose()
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()

    if not dead:
        return None
    return Alarm(
        code="provider-unreachable",
        title="Провайдер не отвечает",
        detail=(
            f"Не отвечает: {', '.join(dead)}. Прогон при недоступном остатке "
            "не запускается вовсе — узнать об этом лучше здесь, чем отказом "
            "посреди оплаченной работы"
        ),
    )


async def _delivery_silence(session: AsyncSession, moment: datetime) -> Alarm | None:
    """Письма ушли, а платформа не сказала о них ни слова."""
    edge = moment - timedelta(hours=DELIVERY_SILENCE_HOURS)
    stale = await session.scalar(
        select(func.count(MessageModel.id)).where(
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
        code="delivery-silence",
        title="Платформа молчит о доставке",
        detail=(
            f"{stale} писем отправлены больше {DELIVERY_SILENCE_HOURS} часов назад, "
            "и ни по одному не пришло события: ни доставки, ни отказа. "
            "Проверить адрес вебхука событий в кабинете платформы и его подпись"
        ),
    )


async def _reply_silence(session: AsyncSession, moment: datetime) -> Alarm | None:
    """Доставлено достаточно, а ответов нет совсем."""
    delivered = await session.scalar(
        select(func.count(MessageModel.id)).where(
            MessageModel.status == MessageStatus.DELIVERED,
        )
    )
    if not delivered or delivered < REPLY_SILENCE_MIN_DELIVERED:
        return None

    edge = moment - timedelta(days=REPLY_SILENCE_DAYS)
    last = await session.scalar(select(func.max(ReplyModel.created_at)))
    if last is not None and last >= edge:
        return None
    return Alarm(
        code="reply-silence",
        title="Ответов нет ни одного",
        detail=(
            f"Доставлено писем: {delivered}, а входящих за {REPLY_SILENCE_DAYS} дня нет. "
            "Доноры отвечают единицами процентов, но полный ноль на такой пачке "
            "чаще означает, что отвалился приём ответов, а не что все промолчали"
        ),
    )


async def _followups_stuck(session: AsyncSession, moment: datetime) -> Alarm | None:
    """Срок добивки прошёл, а её никто не забрал."""
    edge = moment - timedelta(minutes=FOLLOWUP_LATE_MINUTES)
    late = await session.scalar(
        select(func.count(MessageModel.id)).where(
            MessageModel.next_action_at.is_not(None),
            MessageModel.next_action_at < edge,
        )
    )
    if not late:
        return None
    return Alarm(
        code="followups-stuck",
        title="Добивки стоят",
        detail=(
            f"{late} писем ждут следующего шага дольше часа. Цепочка не рвётся — "
            "она просто не идёт: процесс добивок не запущен или падает"
        ),
    )


async def _runs_stuck(session: AsyncSession, moment: datetime) -> Alarm | None:
    """Прогон «идёт», но давно не подавал признаков жизни."""
    edge = moment - timedelta(minutes=RUN_SILENCE_MINUTES)
    stuck = await session.scalar(
        select(func.count(RunModel.id)).where(
            RunModel.status == RunStatus.RUNNING,
            # Отметка о жизни — это `updated_at`: прогон трогает строку
            # каждым ударом, и отдельного поля для того же смысла заводить
            # не стали (`features/runs/lifecycle`).
            RunModel.updated_at < edge,
        )
    )
    if not stuck:
        return None
    return Alarm(
        code="runs-stuck",
        title="Прогон идёт, но молчит",
        detail=(
            f"{stuck} прогонов не подавали признаков жизни дольше "
            f"{RUN_SILENCE_MINUTES} минут. Их должен разобрать отдельный процесс; "
            "если тревога держится — он не запущен"
        ),
    )


async def _crawl_blocked(session: AsyncSession) -> Alarm | None:
    """Обход упирается в защиту сайтов.

    Требование просит при доле отказов выше 30% «паузу и алерт». Паузу
    обход ставит себе сам и останавливается; тревоги до этого правила
    не было — останов виден был только в логе, а сторож про краул
    не знал вовсе.

    Считается по последним обходам, а не за всё время: доля за всю
    историю прячет защиту, включившуюся вчера.
    """
    rows = (
        (
            await session.execute(
                select(CrawlRunModel).order_by(CrawlRunModel.id.desc()).limit(CRAWL_RUNS_WATCHED)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    stopped = [row for row in rows if row.stop_reason is StopReason.UNHEALTHY]
    blocked = [row for row in rows if row.outcome is CrawlOutcome.BLOCKED]
    if not stopped and len(blocked) * 2 < len(rows):
        return None

    return Alarm(
        code="crawl-blocked",
        title="Обход упирается в защиту",
        detail=(
            f"Из последних {len(rows)} обходов {len(blocked)} закрылись целиком"
            + (f", {len(stopped)} остановлены по доле отказов" if stopped else "")
            + ". Дальше это лечится не повтором, а следующим уровнем каскада: "
            "браузером (CRAWL_BROWSER_ENABLED) или резидентным прокси (CRAWL_PROXY_URL)"
        ),
    )


async def _cap_reached(session: AsyncSession) -> Alarm | None:
    """Месячный кап выбран. Прогоны молчат не потому, что сломались."""
    spent = await ahrefs_spent_this_month(session)
    if spent < ahrefs_cfg.UNITS_CAP:
        return None
    return Alarm(
        code="cap-reached",
        title="Месячный кап выбран",
        detail=(
            f"Потрачено {spent} юнитов при капе {ahrefs_cfg.UNITS_CAP}. "
            "Новые прогоны не запустятся до первого числа или до поднятия капа — "
            "это не поломка, но выглядит одинаково"
        ),
    )


async def report(session: AsyncSession, *, now: datetime | None = None) -> list[Alarm]:
    """Проход сторожа для фонового процесса: посчитать и сказать в лог.

    Здесь проверяются и провайдеры — в фоне это уместно: проход идёт
    раз в несколько минут, и два бесплатных запроса ничего не стоят.
    """
    found = await alarms(session, now=now)
    unreachable = await probe_providers()
    if unreachable is not None:
        found = [unreachable, *found]
    if not found:
        logger.info("сторож тишины: тихо и правильно")
        return found
    for alarm in found:
        logger.warning("сторож тишины: %s — %s", alarm.title, alarm.detail)
    return found
