"""Строки панели лимитов о почте: первые письма этапа, ящик и добивки — одной функцией на этап.

Правила те же, что у отправки и экрана «Домены рассылки»: мощность дня — `outreach/capacity.capacity`,
счёт писем — `sent_today(first_only=True)`, разгон — `senders.warmup_state`, закрытый домен —
`limits.domain_shut`. Панель называет их, а не считает второй раз. У этапа без своего лимита
направления (доноры, рекламодатели по умолчанию) в строке пусто, а что держит письма — в `now`.

**Только чтение в этом круге.** Лимит направления читают все процессы при импорте (`config/outreach`),
и правка с экрана в них не дойдёт: перенос в базу — отдельным кругом. Лимит ящика и потолок добивок
не меняются вовсе: это репутация доменов, а она не восстанавливается.

**Час ящика — не настройка, а следствие паузы** (`SALES_AUTOSEND_GAP_MIN`): с разбросом его не
удержать точно, поэтому `now` говорит «около». Автоотправка выключена — письма уходят кнопкой, и
темпа нет: `per_session` пусто.

Стоимость — пусто: почту платят тарифом, а не за письмо.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.config import outreach as cfg
from backend.config import sales as sales_cfg
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import MessageModel, SenderModel, SendingDomainModel
from backend.features.core.window import SendWindow
from backend.features.letters import autosend_window
from backend.features.ops.limit_fields import BLANK, OUTREACH, SALES
from backend.features.outreach import capacity, followup_caps, limits
from backend.features.outreach import senders as rules
from backend.features.outreach.repository import DAILY_STEPS, OutreachRepository
from backend.features.runs.spending import month_start

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.core.limit_rows import LimitRow

SERVICE, UNIT = "Почта", "писем"

#: Платят тарифом почтовой платформы; ноль сказал бы «бесплатно».
COST_NOTE = "Платят тарифом почтовой платформы, а не за письмо"

#: Почему не меняется — разные причины, обе словами строки.
NOT_YET = "Пока меняется только настройкой сервера"
NEVER = "Не меняется: это репутация доменов"

#: Минут в часе: сколько писем за час даёт пауза между письмами ящика.
HOUR_MIN = 60


@dataclass(frozen=True, slots=True)
class _Lane:
    """Этап в строках почты: задача, начало ключа, чьи ящики, темп и окно."""

    stage: Stage
    task: str
    prefix: str
    whose: str
    #: Пауза между первыми письмами ящика, минут, и какой настройкой задана.
    gap_min: int
    gap_setting: str
    #: Уходят ли письма сами: выключено — кнопкой, и темпа нет.
    autosend: bool
    #: Окно получателя — слова строки, не лимит.
    window: SendWindow
    window_words: str


def _lanes() -> tuple[_Lane, ...]:
    """Этапы строк почты. Настройки читаются при вызове, как и у отправки."""
    sales_window = SendWindow(
        days=sales_cfg.SEND_DAYS, start=sales_cfg.SEND_OPENS, end=sales_cfg.SEND_CLOSES
    )
    frame, queue = autosend_window.frame(), "Очередь сама уходит"
    return (
        _Lane(
            Stage.SALES,
            SALES,
            "sales.mail",
            "продаж",
            sales_cfg.AUTOSEND_GAP_MIN,
            "SALES_AUTOSEND_GAP_MIN",
            sales_cfg.AUTOSEND,
            sales_window,
            "Окно",
        ),
        *(
            _Lane(
                stage,
                OUTREACH,
                f"outreach.{stage.value}.mail",
                whose,
                cfg.AUTOSEND_GAP_MIN,
                "OUTREACH_AUTOSEND_GAP_MIN",
                cfg.AUTOSEND,
                frame,
                queue,
            )
            for stage, whose in ((Stage.DONORS, "доноров"), (Stage.ADVERTISERS, "рекламодателей"))
        ),
    )


@dataclass(frozen=True, slots=True)
class _Facts:
    """Что почта знает на эту минуту, одним чтением на все этапы."""

    moment: datetime
    boxes: Sequence[SenderModel]
    domains: Mapping[str, SendingDomainModel]
    #: Первых писем ушло сегодня с ящика (`sent_today(first_only=True)`).
    sent: Mapping[int, int]
    #: Первых писем с первого числа по этапам ящика.
    month: Mapping[Stage, int]
    #: Добивок цепочки ушло сегодня с ящика (`followup_caps.today_by_box`).
    chained: Mapping[int, int]


async def _month_letters(session: AsyncSession, since: datetime) -> dict[Stage, int]:
    """Первые письма с `since` по этапам ящика — теми же шагами, что считает дневной лимит."""
    found = await session.execute(
        select(SenderModel.stage, func.count())
        .join(MessageModel, MessageModel.sender_id == SenderModel.id)
        .where(MessageModel.sent_at >= since, MessageModel.step.in_(DAILY_STEPS))
        .group_by(SenderModel.stage)
    )
    return dict(found.tuples().all())


async def _facts(session: AsyncSession, moment: datetime) -> _Facts:
    boxes = (await session.scalars(select(SenderModel).order_by(SenderModel.id))).all()
    return _Facts(
        moment,
        boxes,
        await limits.sending_domains(session),
        await OutreachRepository(session).sent_today(now=moment, first_only=True),
        await _month_letters(session, month_start(moment)),
        await followup_caps.today_by_box(session, moment),
    )


def _joined(*parts: str | None) -> str:
    """Предложения строки: пустых нет, в конце точка не нужна — строка стоит одна в ячейке."""
    return ". ".join(part for part in parts if part)


def _domains_words(stage: Stage, facts: _Facts) -> str | None:
    """Лимит домена и что закрыто: слова, а не лимит строки: у каждого домена он свой."""
    rows = [row for row in facts.domains.values() if row.stage is stage]
    if not rows:
        return None
    shut = [row for row in rows if limits.domain_shut(row, stage, facts.moment) is not None]
    paused = sum(1 for row in shut if row.paused_at is not None)
    young = len(shut) - paused
    limit = sorted({row.daily_limit for row in rows})
    each = str(limit[0]) if len(limit) == 1 else f"{limit[0]}–{limit[-1]}"
    parts = (
        f"Домены: лимит {each} в день",
        f"на паузе {paused}" if paused else None,
        f"на выдержке {young}" if young else None,
    )
    return ", ".join(part for part in parts if part)


def _first_row(
    lane: _Lane, own: Sequence[SenderModel], found: capacity.Capacity, facts: _Facts
) -> LimitRow:
    """Первые письма всех ящиков этапа за сутки: лимит направления и мощность дня."""
    enabled = [box for box in own if box.enabled]
    warming = sum(1 for box in enabled if not rules.warmup_state(box, now=facts.moment).finished)
    limit = cfg.direction_limit(lane.stage.value)
    return replace(
        BLANK,
        key=f"{lane.prefix}.first",
        task=lane.task,
        service=SERVICE,
        action="Первые письма",
        scope=f"все ящики {lane.whose}",
        unit=UNIT,
        per_day=limit,
        used_day=sum(facts.sent.get(box.id, 0) for box in own),
        used_month=facts.month.get(lane.stage, 0),
        cost_note=COST_NOTE,
        now=_joined(
            found.unread,
            None if limit is not None else "Лимита направления нет: держат ящики и домены",
            f"Сегодня могут {found.days[0].letters}: ящиков {found.boxes}, в разгоне {warming}",
            _domains_words(lane.stage, facts),
            f"{lane.window_words} {lane.window.words}",
            NOT_YET,
        ),
        source=f".env OUTREACH_{lane.stage.value.upper()}_DAILY_LIMIT",
    )


def _steps_words(own: Sequence[SenderModel], facts: _Facts) -> str:
    """Ступени разгона и что они дают ящикам сегодня."""
    steps = " → ".join(str(step) for step in rules.WARMUP_STEPS)
    now = sorted(
        {rules.warmup_state(box, now=facts.moment).allowance for box in own if box.enabled}
    )
    today = "" if not now else f", сегодня {now[0]}" + (f"–{now[-1]}" if len(now) > 1 else "")
    return f"Разгон шагом {rules.WARMUP_STEP_DAYS} дн.: {steps}{today}"


def _box_row(lane: _Lane, own: Sequence[SenderModel], facts: _Facts) -> LimitRow:
    """Первые письма с одного ящика: дневной кап и ступени разгона, выбранное — самого занятого."""
    hourly = HOUR_MIN // lane.gap_min if lane.autosend else None
    pace = (
        "Автоотправка выключена: письма уходят кнопкой"
        if hourly is None
        else f"Пауза {lane.gap_min} мин с разбросом: в час около {hourly}"
    )
    return replace(
        BLANK,
        key=f"{lane.prefix}.box",
        task=lane.task,
        service=SERVICE,
        action="Первые письма с ящика",
        scope="один ящик",
        unit=UNIT,
        per_session=hourly,
        session_hours=None if hourly is None else 1,
        per_day=cfg.DAILY_CAP_PER_SENDER,
        used_day=max((facts.sent.get(box.id, 0) for box in own), default=0),
        cost_note=COST_NOTE,
        now=_joined(
            _steps_words(own, facts),
            pace,
            f"{lane.window_words} {lane.window.words}",
            "Выбрано — самый занятый ящик",
            NEVER,
        ),
        source=f".env OUTREACH_DAILY_CAP_PER_SENDER, {lane.gap_setting}",
    )


def _followup_row(lane: _Lane, own: Sequence[SenderModel], facts: _Facts) -> LimitRow:
    """Добивки с одного ящика: свои потолки, часовой и суточный, — дневной лимит первых писем их
    не считает. Выбрано — у самого занятого ящика, как в строке ящика: потолок у каждого свой."""
    return replace(
        BLANK,
        key=f"{lane.prefix}.followup",
        task=lane.task,
        service=SERVICE,
        action="Добивки с ящика",
        scope="один ящик",
        unit=UNIT,
        per_session=cfg.FOLLOWUP_PER_SENDER_PER_HOUR,
        session_hours=1,
        per_day=cfg.FOLLOWUP_PER_SENDER_PER_DAY,
        used_day=max((facts.chained.get(box.id, 0) for box in own), default=0),
        cost_note=COST_NOTE,
        now=_joined(
            "В лимит первых писем не входят: свой час и свои сутки",
            "Сверх суточного потолка добивка ждёт завтра",
            "Выбрано — самый занятый ящик",
            NEVER,
        ),
        source=".env OUTREACH_FOLLOWUP_PER_SENDER_PER_HOUR, OUTREACH_FOLLOWUP_PER_SENDER_PER_DAY",
    )


async def _lane_rows(session: AsyncSession, lane: _Lane, facts: _Facts) -> list[LimitRow]:
    own = [box for box in facts.boxes if box.stage is lane.stage]
    found = await capacity.capacity(session, lane.stage, [facts.moment.date()])
    return [
        _first_row(lane, own, found, facts),
        _box_row(lane, own, facts),
        _followup_row(lane, own, facts),
    ]


async def rows(session: AsyncSession) -> list[LimitRow]:
    """Строки почты всех этапов на эту минуту. Менять нечего: источник только для чтения."""
    facts = await _facts(session, datetime.now(UTC))
    return [row for lane in _lanes() for row in await _lane_rows(session, lane, facts)]
