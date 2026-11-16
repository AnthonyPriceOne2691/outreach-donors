"""Пауза ящика между письмами у прохода добивок: ящик доноров и рекламодателей шлёт их по одной.

Слово владельца 16.11.2026. Потолки добивок (`outreach/followup_caps.py`) держат объём, а не
темп: часовой запас ящик выбирал за несколько минут — в начале окна и в начале каждого часа
десятки добивок с ящиков уходили всплеском, и в такой пачке сервер получателя придержал письма
ящика за «high spam score». Почта судит рассылку по темпу ящика, а не только по объёму.

**Темп — как у автоотправки первых писем** (`autosend.py`, `core/window.Pace`): с ящика — не больше
добивки за проход, следующая — не раньше паузы `OUTREACH_FOLLOWUP_GAP_MIN` (по умолчанию 15 минут,
почему так — у настройки) с разбросом ±50%. Пауза меряется от последнего письма ящика любого вида,
отданного почте любым путём, — первого (кнопкой, пачкой, проходом), добивки, перезапроса цены, —
чтобы два письма с ящика не уходили подряд. Письмо «отправляется» отдано с минуты захвата, как у
автоотправки. Ответ в переписке паузы не задаёт: собеседник написал сам, и отнимать у ящика, где
человек ведёт разговор, темп добивок незачем — то же правило, что у часового лейна
(`followup_caps.this_hour_of`).

**Разброс у каждого письма свой и один на всех проходах**: зерно — ящик и письмо (`seeded`). Новый
разброс на каждом проходе раз в минуту сводил бы паузу к её нижнему краю. Генератор разброса
подставляемый (`Gaps.draw`): тест ставит свой.

**Добивка ящика на паузе не захватывается и не теряется** (`Rest`). Захват берёт добивки только тех
ящиков, что сейчас не отдыхают, а подошедшим добивкам отдыхающих ящиков проход переносит срок на
конец паузы (`followups.Chain.hold`: срок добивки пишет модуль цепочки). Срок, оставленный в
прошлом, сторож «Добивки стоят» (`ops/stage_silence.py` — срок прошёл больше часа назад) принял бы
за остановленный проход — а утренний завал ящик теперь отдаёт часами. Отложенные за выходные к утру
понедельника расходятся той же паузой: по письму с ящика, а не все в одну минуту.

**Потолки — верхняя граница, как были**: захваченную добивку судят часовой и суточный потолки
ящика (`waits`, здесь же: то же «может ли ящик отдать добивку сейчас»). При паузе 15 минут ±50%
часовой запас в 10 писем не выбрать.

**Пауза — у ящиков всех этапов** (`PACED`), продаж тоже: самый крупный всплеск утра 16.11.2026 был
как раз у добивок продаж, и пауза с разбросом — их же просьба (согласовано: сессия продаж, 16.11).
Первым письмам продаж темп по-прежнему задаёт их автоотправка; пауза добивки меряется и от них.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import MessageModel, SenderModel
from backend.features.core.window import Pace
from backend.features.letters.followup_report import PassReport
from backend.features.outreach.followup_caps import chain_followup, sent_since
from backend.features.outreach.repository import DAILY_STEPS

if TYPE_CHECKING:
    from backend.features.letters.followups import Chain, Claimed

logger = logging.getLogger(__name__)

#: Этапы, чьи ящики шлют добивки с паузой — все (шапка модуля).
PACED = frozenset({Stage.DONORS, Stage.ADVERTISERS, Stage.SALES})
#: Разброс паузы — доля паузы в обе стороны, как у автоотправки первых писем.
SPREAD = 0.5
#: На сколько откладывается добивка, если отправить её сейчас нельзя:
#: ящик на паузе, почта отказала временно, лейн выбран. Час — потому что
#: все три причины проходят сами, и спрашивать чаще значит долбить базу.
POSTPONE = timedelta(hours=1)

#: Генератор разброса: ящик и письмо → доля паузы в [−1, 1].
Draw = Callable[[int, int], float]


def seeded(box_id: int, letter_id: int) -> float:
    """Разброс паузы ящика после письма — с зерном из ящика и письма: на всех проходах один."""
    return random.Random(f"followup:{box_id}:{letter_id}").uniform(-1.0, 1.0)  # noqa: S311 — не секрет


@dataclass(frozen=True, slots=True)
class Gaps:
    """Пауза ящика после письма: `pace.gap` с разбросом ±`pace.spread`; разброс даёт `draw`."""

    pace: Pace
    draw: Draw = seeded

    def after(self, box_id: int, letter_id: int) -> timedelta:
        """Пауза ящика `box_id` после письма `letter_id`."""
        return self.pace.gap * (1 + self.pace.spread * self.draw(box_id, letter_id))

    @property
    def longest(self) -> timedelta:
        """Дольше пауза не бывает: письма, ушедшие раньше, ящик уже не держат."""
        return self.pace.gap * (1 + self.pace.spread)


def gaps() -> Gaps:
    """Пауза из настроек (`OUTREACH_FOLLOWUP_GAP_MIN`); ноль — паузы нет, только потолки."""
    return Gaps(Pace(timedelta(minutes=cfg.FOLLOWUP_GAP_MIN), SPREAD))


def _counted() -> ColumnElement[bool]:
    """Письмо, после которого ящик отдыхает: первое, перезапрос цены, добивка цепочки — не ответ."""
    return or_(MessageModel.step.in_(DAILY_STEPS), chain_followup())


@dataclass(slots=True)
class Rest:
    """Паузы ящиков в проходе добивок: номер ящика → до какой минуты он отдыхает."""

    gaps: Gaps
    #: Ящики, чьи добивки ждут паузы (этапы `PACED`); остальных проход не держит.
    paced: frozenset[int] = frozenset()
    until: dict[int, datetime] = field(default_factory=dict)

    @classmethod
    async def load(cls, session: AsyncSession, now: datetime, gaps: Gaps) -> Rest:
        """Паузы на минуту `now`: от последнего письма каждого ящика, отданного почте."""
        boxes = await session.scalars(select(SenderModel.id).where(SenderModel.stage.in_(PACED)))
        rest = cls(gaps, frozenset(boxes.all()))
        if not rest.paced or gaps.longest <= timedelta(0):
            return rest
        handed = func.coalesce(MessageModel.sent_at, MessageModel.updated_at)
        rows = await session.execute(
            select(MessageModel.sender_id, MessageModel.id, handed)
            .where(MessageModel.sender_id.in_(rest.paced), _counted())
            .where(sent_since(now - gaps.longest))
            .order_by(handed, MessageModel.id)
        )
        for box, letter, at in rows.tuples():  # по времени: последнее письмо ящика — последним
            rest.took(box, letter, at)
        return rest

    def took(self, box: int | None, letter: int, at: datetime) -> None:
        """Ящик `box` отдал почте письмо `letter` в минуту `at` — отдыхает паузой после него."""
        if box is not None and box in self.paced:
            self.until[box] = at + self.gaps.after(box, letter)

    def busy(self, now: datetime) -> dict[int, datetime]:
        """Ящики, которые в минуту `now` ещё отдыхают, — и до какой минуты."""
        return {box: until for box, until in self.until.items() if until > now}

    def free(self, now: datetime) -> tuple[ColumnElement[bool], ...]:
        """Условие захвата: ящик переписки сейчас не отдыхает. Ящик удалён — захват берёт добивку, и
        проход скажет, почему она ждёт (`waits`); отдыхающих нет — условия нет."""
        busy = self.busy(now)
        if not busy:
            return ()
        return (or_(MessageModel.sender_id.is_(None), MessageModel.sender_id.not_in(busy)),)


async def waits(session: AsyncSession, chain: Chain, claimed: Claimed, report: PassReport) -> bool:
    """Добивке сейчас не уйти — ящик переписки удалён или выбрал запас, суточный (ждать до завтра)
    или часовой: срок возвращается, и `True`. Ящик удалён — проход ещё и говорит почему."""
    delay = POSTPONE
    if claimed.sender_id is None:
        # Письма теряют номер ящика, когда ящик удаляют. С другого ящика
        # добивка не уходит (`mailbox.py`): она ждёт, и проход говорит почему.
        logger.warning(
            "добивки: %s — ящик переписки неизвестен (удалён): добивка ждёт, "
            "с другого ящика она не уйдёт",
            claimed.host,
        )
    elif (till_tomorrow := await chain.day_wait(claimed.sender_id)) is not None:
        delay = till_tomorrow
        report.capped[claimed.sender_id] += 1
    elif await chain.sent_this_hour(claimed.sender_id) < cfg.FOLLOWUP_PER_SENDER_PER_HOUR:
        return False
    await chain.restore(claimed, delay=delay)
    await session.commit()
    report.postponed += 1
    return True


def pace() -> Pace | None:
    """Пауза ящика для плана рассылки (`forecast_plan.Chains.clock`); ноль в настройке — `None`."""
    found = gaps().pace
    return found if found.gap > timedelta(0) else None
