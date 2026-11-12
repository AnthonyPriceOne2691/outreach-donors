"""Строки панели лимитов от модуля продаж: проверка адреса, поиск адреса, выдача сбора и ходы агента.

Договор строки — `core/limit_rows.py`, собирает панель `api/settings/limit_panel.py`: модуль подходит
сам функциями `rows`, `quote` и `change`. Модель и почта продаж — общими строками (`ops/limit_model`,
`ops/limit_mail`): у них один код с аутричем, здесь только то, что есть лишь у продаж.

**Числа — те же, что держат работу.** Потолок проверок — `core/hunter_cap.verify_month_cap`, выбрано —
`verify_cap.month_spent`: этими же числами отказывает очистка. Панель не считает второй раз. Потолок
проверок общий: его тратят очистка продаж и проверка адреса перед первым письмом донору и
рекламодателю (`contacts/address_check.py`), своей строки у той нет — поэтому строка считает все
операции проверки (`hunter_cap.VERIFY_OPERATIONS`) и за месяц, и за сегодня. Поиск адреса общий с
донорами, поэтому его строка берёт только записи с этапом продаж.

**Меняется только потолок проверок.** Он и есть денежный рычаг, и у него уже есть поле на вкладке
«Лимиты»: панель идёт тем же сеттером и тем же журналом (`ops/cap_journal`). Поиск и выдачу держит смета
прогона, её подтверждает человек, а не месячное число. Предел ходов агента — требование к качеству, а
не к расходу, поэтому менять его с экрана незачем.

**Строка без числа говорит почему** (`now`): потолка нет, настройка негодна, платный поиск выключен.
Провайдеров строки не зовут: журнал расхода и снимок учётки Hunter.

**Просчёт не платит и не обещает денег.** Проверки платятся тарифом, цены проверки в коде нет, поэтому
ответ — в проверках: сколько останется и какая это доля тарифа по снимку учётки. Потолок не выше уже
выбранного — допустимый выбор, и слова говорят, что проверки встанут до конца месяца. Новое значение
встаёт сразу, но идущий проход очистки доходит со старым: он держит прочитанное на старте
(`verify_cap`), и слова просчёта стоят рядом со словами окна «действует с этой минуты».
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from backend.config import sales as sales_cfg
from backend.config import serp as serp_cfg
from backend.config.startup_checks import ConfigError
from backend.features.contacts.hunter_account import AccountSnapshot
from backend.features.contacts.hunter_account import snapshot as hunter_snapshot
from backend.features.contacts.search import CONTACTS_OPERATION
from backend.features.core import hunter_cap
from backend.features.core.domain import Stage
from backend.features.core.limit_rows import LimitRefusedError, LimitRow, Quote
from backend.features.core.models.ops import UsageRecordModel
from backend.features.ops import cap_journal
from backend.features.ops.limit_fields import (
    BLANK,
    PER_MONTH,
    SALES,
    SAME,
    day_start,
    refused,
    spent,
    usd,
)
from backend.features.ops.limit_services import READ_ONLY
from backend.features.runs.spending import month_start
from backend.features.sales import agent_turns, segment_estimate, verify_cap
from backend.features.sales.segment_serp import SERP_OPERATION
from backend.features.serp.funds import MIN_BALANCE_USD, dollars
from backend.shared.numbers import spaced

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.core.models.access import UserModel

logger = logging.getLogger(__name__)

HUNTER, ONE_RUN = "Hunter", "один прогон сбора"

#: Откуда потолок проверок: поле на экране или запасная настройка сервера.
FROM_SCREEN = "поле: вкладка «Лимиты»"
FROM_ENV = f".env {hunter_cap.SETTING}"

#: Охват строки проверки: потолок общий — его тратит и проверка перед первым письмом.
VERIFY_SCOPE = "все проверки адреса: продажи и аутрич"

#: Платят тарифом: кредиты общего счёта учётки, а не деньги за штуку (`okf/unit-economy.md`).
VERIFY_COST = "Платят тарифом Hunter: проверка — половина кредита общего счёта"
SEARCH_COST = "Платят тарифом Hunter: поиск — кредит общего счёта"

#: Что держит трату, когда месячного числа нет: смету запускает человек.
BY_ESTIMATE = "каждый прогон идёт по смете, её подтверждает человек"

#: Когда новый потолок доходит до очистки: окно панели говорит «с этой минуты», и это верно для
#: всего, кроме прохода, который уже идёт.
RUNNING_KEEPS_OLD = "Идущий проход очистки дойдёт со старым потолком, следующий возьмёт новый"


def _month_words(month: verify_cap.Month) -> str:
    """Что потолок делает в этом месяце. Числа нет — слова, почему (`Month.sentence`)."""
    if isinstance(month.cap, str):
        return month.sentence
    if month.cap == 0:
        return "Проверки адресов остановлены потолком"
    if month.spent >= month.cap:
        return "Потолок месяца выбран: платная часть очистки стоит до первого числа"
    return f"Проверок на этот месяц осталось {spaced(month.cap - month.spent)}"


def _verify_now(month: verify_cap.Month, found: AccountSnapshot | None) -> str:
    """Потолок месяца и учётка: границы разные, потолок считается с первого числа."""
    account = (
        "снимка учётки ещё нет"
        if found is None
        else f"на учётке осталось {spaced(int(found.account.verifications.remaining))}"
    )
    return f"{_month_words(month)}; {account}"


async def _verify_row(session: AsyncSession, moment: datetime) -> LimitRow:
    month = await verify_cap.month(session, now=moment)
    stored = await hunter_cap.stored_verify_cap(session)
    checks = UsageRecordModel.operation.in_(hunter_cap.VERIFY_OPERATIONS)
    today = (await spent(session, day_start(moment), checks))[0]
    return replace(
        BLANK,
        key="sales.hunter.verify",
        task=SALES,
        service=HUNTER,
        action="Проверка адреса",
        scope=VERIFY_SCOPE,
        unit="проверок",
        per_month=month.cap if isinstance(month.cap, int) else None,
        used_day=today,
        used_month=month.spent,
        cost_note=VERIFY_COST,
        now=_verify_now(month, await hunter_snapshot(session)),
        source=FROM_ENV if stored is None else FROM_SCREEN,
        editable=frozenset({PER_MONTH}),
    )


def _search_now() -> str:
    paid = "включён" if segment_estimate.paid_search_on() else "выключен"
    return f"Своего потолка нет: {BY_ESTIMATE}. Платный поиск сбора {paid}. {READ_ONLY}"


async def _search_row(session: AsyncSession, today: datetime, month: datetime) -> LimitRow:
    """Поиск адреса общий с донорами: строка берёт только записи с этапом продаж."""
    rule = (
        UsageRecordModel.operation == CONTACTS_OPERATION,
        UsageRecordModel.stage == Stage.SALES,
    )
    return replace(
        BLANK,
        key="sales.hunter.search",
        task=SALES,
        service=HUNTER,
        action="Поиск адреса при сборе базы",
        scope=ONE_RUN,
        unit="поисков",
        used_day=(await spent(session, today, *rule))[0],
        used_month=(await spent(session, month, *rule))[0],
        cost_note=SEARCH_COST,
        now=_search_now(),
        source=".env SALES_SEGMENT_PAID_CONTACTS",
    )


async def _serp_row(session: AsyncSession, month: datetime) -> LimitRow:
    """Выдача сбора: единица журнала — доллар, поэтому расход стоит деньгами."""
    paid = (await spent(session, month, UsageRecordModel.operation == SERP_OPERATION))[1]
    return replace(
        BLANK,
        key="sales.serp.buy",
        task=SALES,
        service="Выдача",
        action="Покупка выдачи при сборе базы",
        scope=ONE_RUN,
        unit="ключей",
        per_session=serp_cfg.MAX_KEYWORDS_PER_RUN,
        spent_month_usd=usd(paid),
        cost_note="Месячного лимита нет: цену прогона называет смета",
        now=(
            f"Потолка денег нет: {BY_ESTIMATE}. На счету меньше {dollars(MIN_BALANCE_USD)} или "
            f"выдача дороже остатка — прогон не запускается. {READ_ONLY}"
        ),
        source=".env SERP_MAX_KEYWORDS_PER_RUN",
    )


def _turns() -> tuple[int | None, str]:
    """Предел ходов агента и что он значит. Настройка негодна — числа нет, слова те же, что у
    ответа лида (`agent_turns.beyond`)."""
    try:
        limit = agent_turns.max_turns()
    except ConfigError as exc:
        logger.warning("панель лимитов: предел ходов агента негоден", extra={"error": str(exc)})
        return None, f"{exc}; черновика агента нет — отвечает человек"
    if not sales_cfg.AGENT_ENABLED:
        return limit, f"Агент продаж выключен: черновиков нет. {READ_ONLY}"
    return limit, f"Дальше предела отвечает человек. {READ_ONLY}"


def _turns_row() -> LimitRow:
    limit, words = _turns()
    return replace(
        BLANK,
        key="sales.agent.turns",
        task=SALES,
        service="Агент продаж",
        action="Черновики на ответы лида",
        scope="один диалог",
        unit="ответов",
        per_session=limit,
        cost_note="Черновик платится токенами: они в строке модели продаж",
        now=words,
        source=".env SALES_AGENT_MAX_TURNS",
    )


async def rows(session: AsyncSession) -> list[LimitRow]:
    """Строки продаж на эту минуту: журнал расхода и снимок учётки, без вызовов провайдеров."""
    moment = datetime.now(UTC)
    month = month_start(moment)
    return [
        await _verify_row(session, moment),
        await _search_row(session, day_start(moment), month),
        await _serp_row(session, month),
        _turns_row(),
    ]


async def _tariff(session: AsyncSession) -> int | None:
    """Проверок тарифа по снимку учётки — граница потолка, как у карточки на вкладке «Лимиты»;
    снимка нет — `None`, и граница только от опечатки (`hunter_cap.refusal`)."""
    found = await hunter_snapshot(session)
    return None if found is None else int(found.account.verifications.available)


def _effect(cap: int, chosen: int) -> str:
    """Что новый потолок делает с этим месяцем."""
    words = hunter_cap.cap_words(cap)
    if cap == 0:
        return f"Потолок {words}: платная часть очистки встанет"
    if cap <= chosen:
        return f"Потолок {words}: выбрано уже {spaced(chosen)}, проверки встанут до конца месяца"
    return f"Потолок {words}: выбрано {spaced(chosen)}, останется {spaced(cap - chosen)}"


def _share(cap: int, tariff: int | None) -> str:
    """Доля тарифа. Потолок выше нуля в тарифе без проверок не пройдёт границу: делить есть на что."""
    if tariff is None:
        return "Снимка учётки ещё нет: долю тарифа не назвать"
    return f"Это {round(cap * 100 / tariff)} % проверок тарифа"


async def quote(session: AsyncSession, _key: str, _field: str, value: int) -> Quote:
    """Просчёт нового потолка проверок. Ничего не пишет."""
    tariff = await _tariff(session)
    why = hunter_cap.refusal(value, tariff)
    if why is not None:
        return refused(why)
    if value == await hunter_cap.verify_month_cap(session):
        return refused(SAME)
    effect = _effect(value, await verify_cap.month_spent(session))
    parts = [effect, *([_share(value, tariff)] if value else [])]
    words = ". ".join([*parts, RUNNING_KEEPS_OLD, VERIFY_COST])
    return Quote(cost_usd=None, words=f"{words}.", refusal=None, accepted=value)


async def change(
    session: AsyncSession, key: str, field: str, value: int, author: UserModel
) -> None:
    """Смена существующим путём: сама повторяет просчёт, строка истории и журнал — как у карточки.
    Фиксирует вызывающий."""
    quoted = await quote(session, key, field, value)
    if quoted.refusal is not None:
        raise LimitRefusedError(quoted.refusal)
    done = await hunter_cap.set_verify_cap(
        session, quoted.accepted, ceiling=await _tariff(session), author=author
    )
    await cap_journal.hunter_verify_cap(session, done, author)
