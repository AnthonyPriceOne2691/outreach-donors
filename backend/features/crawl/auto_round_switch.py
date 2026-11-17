"""Выключатель автокруга рекламодателей: включён ли, сколько писем в сутки, кто и когда — строками
истории.

Слово владельца 17.11.2026: три кнопки после ночи — «Перевести в рекламодатели», «Собрать очередь» и
«Отправить очередь» с «дальше — сама» — сервис жмёт сам (`auto_round.py`), но только когда человек
его включил. **В базе, а не в окружении**, как полосы судьи (`judge_bands.py`): выключатель должен
быть под рукой у того, кто видит очередь, и по строкам видно, кто разрешил сервису отправлять самому
и кто это остановил. Строка на каждую смену, действует последняя; строк нет — выключен, потолок —
`DEFAULT_LETTERS`.

**Суточный потолок писем — числом рядом с выключателем**: столько писем круг собирает за сутки, не
больше (`limit` сборки). Границы — `FEWEST` и `MOST`, у кнопки сборки те же.

**От чьего имени.** Письма, которые уходят сами, пишутся в журнал с тем, кто включил режим очереди
(`letters/autosend.py`); режим автокруга включает круг — с тем, кто включил выключатель (`by_id`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import AutoRoundSwitchModel

#: Писем в сутки, пока потолок не меняли: десятки офферов в день — заметный поток, а не всплеск;
#: ящики рекламодателей выпускают их за рабочий день получателя с паузами.
DEFAULT_LETTERS = 100
#: Границы суточного потолка — как у сборки кнопкой (`api/letters/schemas.LIMIT_MAX`): больше
#: сборка за раз не берёт.
FEWEST = 1
MOST = 500


@dataclass(frozen=True, slots=True)
class Switch:
    """Автокруг сейчас: включён ли, потолок писем в сутки и кто так поставил. Строк нет —
    выключен, потолок по умолчанию, ставившего нет."""

    enabled: bool = False
    letters: int = DEFAULT_LETTERS
    #: Кто поставил действующую строку — почта и номер учётки; учётку удалили — пусто.
    set_by: str | None = None
    by_id: int | None = None
    set_at: datetime | None = None
    #: С какой минуты включён без перерыва: смена потолка перерывом не считается. Выключен — пусто.
    on_since: datetime | None = None


async def _on_since(session: AsyncSession) -> datetime | None:
    """Начало нынешнего включения: первая строка после последней «выключен»."""
    off = select(func.coalesce(func.max(AutoRoundSwitchModel.id), 0)).where(
        AutoRoundSwitchModel.enabled.is_(False)
    )
    found: datetime | None = await session.scalar(
        select(func.min(AutoRoundSwitchModel.set_at)).where(
            AutoRoundSwitchModel.id > off.scalar_subquery()
        )
    )
    return found


async def state(session: AsyncSession) -> Switch:
    """Выключатель как он стоит — по последней строке."""
    row = (
        await session.execute(
            select(AutoRoundSwitchModel, UserModel.email)
            .outerjoin(UserModel, UserModel.id == AutoRoundSwitchModel.set_by)
            .order_by(AutoRoundSwitchModel.id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return Switch()
    last, email = row
    return Switch(
        enabled=last.enabled,
        letters=last.letters,
        set_by=email,
        by_id=last.set_by,
        set_at=last.set_at,
        on_since=await _on_since(session) if last.enabled else None,
    )


class LettersOutOfRangeError(ValueError):
    """Суточный потолок писем вне границ сборки."""


async def set_switch(
    session: AsyncSession, *, on: bool, letters: int, by: int | None
) -> tuple[Switch, Switch]:
    """Включить, выключить или сменить потолок — новой строкой истории. Ответ — как стояло до смены
    и как стало. То же, что стоит, строки не заводит: история — о сменах. Без фиксации: её делает
    вызывающий вместе с записью журнала."""
    if not FEWEST <= letters <= MOST:
        raise LettersOutOfRangeError(
            f"Писем в сутки — от {FEWEST} до {MOST}, пришло {letters}: больше сборка за раз не берёт"
        )
    was = await state(session)
    if (was.enabled, was.letters) == (on, letters):
        return was, was
    session.add(AutoRoundSwitchModel(enabled=on, letters=letters, set_by=by))
    await session.flush()
    return was, await state(session)
