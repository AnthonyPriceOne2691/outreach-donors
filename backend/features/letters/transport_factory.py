"""Какой транспорт выбран настройкой.

Отдельным модулем, как и у источников выдачи, и по той же причине:
здесь знание обо всех транспортах сразу, а сами они друг о друге
не знают. Пока выбор жил в `transport.py`, боевой транспорт не мог
импортировать оттуда `Outgoing`, не замкнув круг.

**Транспорт живёт один проход или один запрос — и закрывается.** Боевой
держит свой HTTP-клиент с пулом соединений. Процесс добивок собирал
транспорт на каждый проход, сервер — на каждое нажатие «отправить»,
и ни один его не закрывал: соединения копились до конца процесса.
Собирать заново всё равно надо (настройки отправки должны доезжать без
перезапуска), поэтому закрытие — рядом со сборкой, в `in_use`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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


@asynccontextmanager
async def in_use(transport: Transport) -> AsyncIterator[Transport]:
    """Транспорт на время одного прохода или запроса — закрытый после.

    Закрывается то, что умеет закрываться: у нулевого транспорта и у
    подставных в тестах закрывать нечего, и требовать от них этого значило
    бы писать пустые методы ради формы.
    """
    try:
        yield transport
    finally:
        close = getattr(transport, "aclose", None)
        if close is not None:
            await close()
