"""Хватит ли кредитов Hunter на поиск адресов — до нажатия «найти» и в самом нажатии (25.11.2026).

«найти» ищет всем ждущим одним нажатием, и тысячи сайтов — это тысячи платных запросов. Экран
сначала спрашивает смету (`GET …/budget`): живой остаток учётки, запас, проверки адресов до конца
месяца и доли идущих поисков (`contacts/hunter_budget.py`). Хватает — человек подтверждает; не
хватает — отказ и предложение «хватит примерно на M сайтов». Нажатие проверяет то же ещё раз
(`refusal`): между сметой и нажатием кредиты мог занять автокруг или поиск доноров.

Платная ступень выключена флагом или ключа нет — кредиты не нужны: смета говорит «хватит» всем.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.contacts.schemas import ContactsBudget
from backend.config import contacts as cfg
from backend.features.contacts import hunter_budget
from backend.shared.net.url_guard import guarded_client
from backend.shared.numbers import spaced

#: Сколько ждать ответа Hunter об остатке у кнопки: дальше — снимок сторожа.
ASK_TIMEOUT_SEC = 10.0


async def estimate(session: AsyncSession, sites: int) -> ContactsBudget:
    """Смета поиска стольким сайтам по кредитам Hunter."""
    if not (cfg.PAID_SEARCH_ENABLED and cfg.HUNTER_API_KEY):
        return ContactsBudget(sites=sites, paid=False, affordable=sites, enough=True)
    async with guarded_client(timeout=ASK_TIMEOUT_SEC) as http:
        counted = await hunter_budget.budget(session, http)
    if isinstance(counted, str):
        return ContactsBudget(sites=sites, paid=True, affordable=0, enough=False, unknown=counted)
    need = hunter_budget.needed(sites)
    return ContactsBudget(
        sites=sites,
        paid=True,
        needed=need,
        free=counted.free,
        affordable=min(sites, hunter_budget.affordable(counted.free)),
        enough=need <= counted.free,
        account_left=counted.account_left,
        reserve=counted.reserve,
        checks_hold=counted.checks_hold,
        held=counted.held,
        asked_at=counted.asked_at,
        live=counted.live,
    )


def refusal(counted: ContactsBudget) -> str | None:
    """Почему поиск стольким сайтам не ставится — словами; `None` — ставится."""
    if counted.enough:
        return None
    if counted.unknown is not None:
        return f"Поиск не поставлен: {counted.unknown}."
    if counted.affordable == 0:
        return (
            f"Свободных кредитов Hunter нет: на учётке {spaced(counted.account_left or 0)}, "
            f"неприкосновенный запас {spaced(counted.reserve or 0)}, под проверку адресов до конца "
            f"месяца {spaced(counted.checks_hold or 0)}, держат идущие поиски "
            f"{spaced(counted.held or 0)}."
        )
    return (
        f"Кредитов Hunter хватит примерно на {spaced(counted.affordable)} сайтов из "
        f"{spaced(counted.sites)}: свободно {spaced(counted.free)}."
    )


async def checked(session: AsyncSession, sites: int) -> None:
    """Нажатие «найти»: кредитов не хватает — отказ 409 словами, сколько можно."""
    said = refusal(await estimate(session, sites))
    if said is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, said)
