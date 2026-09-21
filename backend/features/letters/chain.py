"""Сроки цепочки: когда уходит следующее письмо и сколько их всего.

Отдельный модуль без зависимостей намеренно. Срок назначает сама
отправка — иначе третий вызывающий однажды забудет это сделать, и
цепочка молча не начнётся, — а рассылает добивки отдельный проход.
Обоим нужны одни и те же правила, и лежать они должны там, откуда
видны обоим.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.config import outreach as cfg
from backend.features.core.domain import MessageStatus

#: Шаг первого письма. Добивки — всё, что дальше.
FIRST_STEP = 0

#: Сколько писем в цепочке всего: первое и две добивки. Потолок жёсткий
#: и живёт здесь, а не в настройках: четвёртое письмо человеку, который
#: трижды промолчал, — это не настойчивость, а жалоба на спам.
MAX_STEPS = 3

#: В каких состояниях письмо ещё ждёт добивки. «Отправляется» сюда
#: не входит: пока исход неизвестен, следующего письма быть не может.
CHAINABLE = (MessageStatus.SENT, MessageStatus.DELIVERED)


def cadence(days: list[int] | None) -> tuple[int, ...]:
    """Сроки добивок рассылки, в днях от предыдущего письма.

    Пусто — умолчание настроек: так выглядят рассылки, заведённые
    до того, как сроки стали свойством рассылки.
    """
    if not days:
        return cfg.FOLLOWUP_DAYS
    return tuple(int(day) for day in days)


def due_after(sent_at: datetime, *, step: int, days: list[int] | None) -> datetime | None:
    """Когда уходит добивка после письма шага `step`. `None` — цепочка кончилась.

    Срок считается от отправки предыдущего письма, а не от начала
    рассылки: письма уходят не в один день — очередь согласовывают
    руками, а ящики выбирают дневной лимит.
    """
    upcoming = step + 1
    if upcoming >= MAX_STEPS:
        return None
    schedule = cadence(days)
    if upcoming > len(schedule):
        return None
    return sent_at + timedelta(days=schedule[upcoming - 1])
