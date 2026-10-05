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


def build_transport(name: str | None = None, *, stage: str | None = None) -> Transport:
    """Транспорт по настройке — общей учётки или учётки этапа (`cfg.mail_account`).

    Неизвестное имя — отказ, а не заглушка.
    """
    chosen = (name or cfg.TRANSPORT).strip().lower()

    if chosen == "null":
        return NullTransport()

    if chosen == "sendgrid":
        # Ключа может не быть — тогда транспорт сам скажет, чего ждёт.
        return SendGridTransport(account=cfg.mail_account(stage))

    raise TransportError(
        f"Транспорт «{chosen}» неизвестен. Бывают: null (ничего не шлёт), sendgrid"
    )


class Transports:
    """Транспорты по этапам на один проход или запрос (`transport.ByStage`).

    Транспорт этапа собирается при первом его письме: у направления бывает
    своя учётка платформы, и проход добивок, где письма разных этапов, шлёт
    каждое своей. Закрываются все разом — `in_use(Transports())`.
    """

    def __init__(self, name: str | None = None) -> None:
        self._name = name
        self._built: dict[str, Transport] = {}

    def for_stage(self, stage: str) -> Transport:
        if stage not in self._built:
            self._built[stage] = build_transport(self._name, stage=stage)
        return self._built[stage]

    async def aclose(self) -> None:
        built, self._built = list(self._built.values()), {}
        for transport in built:
            await _close(transport)


async def _close(thing: object) -> None:
    """Закрыть то, что умеет закрываться: у нулевого транспорта и у
    подставных в тестах закрывать нечего, и требовать от них этого значило
    бы писать пустые методы ради формы."""
    close = getattr(thing, "aclose", None)
    if close is not None:
        await close()


@asynccontextmanager
async def in_use[T](transport: T) -> AsyncIterator[T]:
    """Транспорт (или их набор по этапам) на время одного прохода или запроса —
    закрытый после."""
    try:
        yield transport
    finally:
        await _close(transport)
