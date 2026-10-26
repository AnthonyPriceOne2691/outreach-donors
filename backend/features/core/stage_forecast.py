"""Мост к модулю продаж для прогноза рассылки: запас первых писем, пояса и застрявшие письма очереди.

Своим модулем рядом с мостом (`stages.py`): мост стоит у предела длины файла, а вопросы прогноза
— отдельная забота с теми же правилами. Почта модуль продаж не импортирует (контракт
`mail-does-not-know-sales`): прогноз (`letters/forecast.py`) спрашивает здесь, модуль отвечает в
своей точке сохранения (`stages.quietly`). Не подключён, упал или так ещё не отвечает — `None`
строкой журнала: прогноз говорит «не знаю», а не «ноль», и экран не падает из-за модуля продаж.

**Ответы — своим протоколом и своим загрузчиком** (`SalesForecast`, `register_forecast`): модуль
продаж подключает их сам при загрузке пакета, рядом с ответами почте (`stages.register_sales`), —
те стоят у предела длины файла, а ответы прогнозу живут своим модулем продаж. Не подключил — прогноз
без них; общий прогноз проверяется типами и без коммита продаж.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from backend.features.core.stages import quietly

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SalesForecast(Protocol):
    """Что прогноз рассылки спрашивает у модуля продаж (модуль `sales/forecast_answers.py`)."""

    async def supply(self, session: AsyncSession) -> int:
        """Сколько первых писем сборка ещё напишет: лидов, которым она напишет первое письмо
        сейчас, по всем гипотезам — правилом самой сборки, без писем, уже стоящих в очереди."""

    async def queue_zones(self, session: AsyncSession) -> Mapping[str | None, int]:
        """Пояса получателей первых писем продаж в очереди → писем: пояс — первый известный (лида,
        страны, гипотезы — `window.zone_of`); `None` — пояс неизвестен."""

    async def queue_stuck(self, session: AsyncSession) -> Mapping[str, int]:
        """Первые письма продаж в очереди, которые не уйдут, пока что-то не поправят, — причина
        словами → писем: адрес лида держит проверка, пояс получателя неизвестен."""


@dataclass(slots=True)
class _Registry:
    """Загрузчик ответов модуля продаж прогнозу. `None` — модуль их не подключил."""

    load: Callable[[], SalesForecast] | None = None


_ANSWERS = _Registry()


def register_forecast(load: Callable[[], SalesForecast]) -> None:
    """Подключить ответы модуля продаж прогнозу рассылки: `load` отдаёт их (`SalesForecast`)."""
    _ANSWERS.load = load


async def _ask[T](
    session: AsyncSession, what: str, ask: Callable[[SalesForecast], Awaitable[T]], unasked: str
) -> T | None:
    """Ответ прогнозу — в точке сохранения моста (`stages.quietly`): модуль продаж не подключён,
    упал или ответов прогнозу не подключил — `None`."""
    load = _ANSWERS.load
    if load is None:
        return None
    return await quietly(session, what, lambda _mail: ask(load()), unasked)


async def sales_supply(session: AsyncSession) -> int | None:
    """Сколько первых писем сборка продаж ещё напишет (`SalesForecast.supply`); `None` — спросить
    не вышло, и прогноз говорит «запас не узнать», а не «ноль»."""
    return await _ask(
        session,
        "Запас писем продаж",
        lambda answers: answers.supply(session),
        "прогноз рассылки без запаса сборки",
    )


async def sales_zones(session: AsyncSession) -> dict[str | None, int] | None:
    """Пояса получателей первых писем продаж в очереди (`SalesForecast.queue_zones`); `None` —
    спросить не вышло, и часов окна получателя прогноз не считает."""
    found = await _ask(
        session,
        "Пояса очереди продаж",
        lambda answers: answers.queue_zones(session),
        "прогноз рассылки без поясов очереди",
    )
    return None if found is None else dict(found)


async def sales_stuck(session: AsyncSession) -> dict[str, int] | None:
    """Застрявшие первые письма очереди продаж (`SalesForecast.queue_stuck`): причина → писем; пусто
    — таких нет; `None` — спросить не вышло, и прогноз считает очередь целиком."""
    found = await _ask(
        session,
        "Застрявшие письма очереди продаж",
        lambda answers: answers.queue_stuck(session),
        "прогноз рассылки считает очередь целиком",
    )
    return None if found is None else dict(found)
