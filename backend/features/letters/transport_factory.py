"""Какой транспорт выбран настройкой.

Отдельным модулем, как и у источников выдачи, и по той же причине:
здесь знание обо всех транспортах сразу, а сами они друг о друге
не знают. Пока выбор жил в `transport.py`, боевой транспорт не мог
импортировать оттуда `Outgoing`, не замкнув круг.
"""

from __future__ import annotations

from backend.config import outreach as cfg
from backend.features.letters.sendgrid import SendGridTransport
from backend.features.letters.transport import NullTransport, Transport, TransportError


def build_transport(name: str | None = None) -> Transport:
    """Транспорт по настройке. Неизвестное имя — отказ, а не заглушка."""
    chosen = (name or cfg.TRANSPORT).strip().lower()

    if chosen == "null":
        return NullTransport()

    if chosen == "sendgrid":
        # Ключа может не быть — тогда транспорт сам скажет, чего ждёт.
        return SendGridTransport()

    raise TransportError(
        f"Транспорт «{chosen}» неизвестен. Бывают: null (ничего не шлёт), sendgrid"
    )
