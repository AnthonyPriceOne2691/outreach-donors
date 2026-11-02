"""Строки панели лимитов о внешних сервисах аутрича: только чтение, числа журнала и настроек.

Hunter для поиска адреса, выдача, платный обход Этапа 2 и сами обходы. Менять их с экрана нечем: всё
это настройки сервера, и причина — в `now` каждой строки. Провайдеров строки не зовут: расход — журнал
(`usage_records`), остаток Hunter — снимок сторожа, остаток выдачи — то, что последним услышал сторож
этого процесса: снимка у него нет, а карточка на вкладке «Лимиты» спрашивает провайдера сама.

**Чего нет, того нет.** У поиска адреса нет своего потолка, у выдачи — месячного: в строке пусто, а
что держит трату — словами. Ориентир темпа «Форм» (`CONTACTS_MANUAL_CAP`) строкой не стоит: он ничего
не держит (`contacts/forms`).

**Потолок обхода — деньгами, а поле договора целое.** Некруглый потолок целым полем не сказать без
округления, поэтому месяц тогда пуст, а точное число — в стоимости: выбрать потолок целиком и значит
заплатить его.

Этап 2 в ключах — `outreach.advertisers.*`: панель закрывает их без права этапа
(`api/settings/limit_panel.stage_of`).
"""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from backend.config import contacts as contacts_cfg
from backend.config import crawl as crawl_cfg
from backend.config import serp as serp_cfg
from backend.features.contacts.hunter_account import snapshot as hunter_snapshot
from backend.features.contacts.search import CONTACTS_OPERATION
from backend.features.core.domain import Stage
from backend.features.core.models.ops import UsageRecordModel
from backend.features.crawl import deep
from backend.features.ops import funds_watch
from backend.features.ops.limit_fields import BLANK, HOUR_SEC, day_start, spent, usd
from backend.features.runs.spending import crawl_spent_this_month, month_start
from backend.features.serp.funds import MIN_BALANCE_USD, dollars
from backend.features.serp.modes import UnknownModeError, mode_of, serp_cost_usd
from backend.shared.numbers import spaced

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.core.limit_rows import LimitRow

logger = logging.getLogger(__name__)

#: Операция журнала — покупка выдачи прогоном доноров: единица — доллар (`core/usage.py`).
SERP_OPERATION = "serp_search"

#: Почему строка только для чтения.
READ_ONLY = "Меняется настройкой сервера"

#: Обход свой, платить не за что; DR новых доменов — юниты ключа, у них своя строка.
CRAWL_COST = "Обход идёт своими обходчиками; DR новых доменов — юнитами Ahrefs"


async def _spent(session: AsyncSession, operation: str, since: datetime) -> tuple[int, Decimal]:
    """Единицы и деньги операции с `since` — без этапа продаж: у него свои строки."""
    return await spent(
        session,
        since,
        UsageRecordModel.operation == operation,
        UsageRecordModel.stage.is_distinct_from(Stage.SALES),
    )


async def _hunter_now(session: AsyncSession) -> str:
    """Что держит поиск адреса: своего потолка нет, есть флаг и остаток учётки по снимку."""
    paid = "Платный поиск включён" if contacts_cfg.PAID_SEARCH_ENABLED else "Платный поиск выключен"
    found = await hunter_snapshot(session)
    left = (
        "снимка учётки ещё нет"
        if found is None
        else f"у учётки осталось поисков {spaced(int(found.account.searches.remaining))}"
    )
    return f"Своего потолка нет. {paid}, {left}. {READ_ONLY}"


async def _hunter_row(session: AsyncSession, today: datetime, month: datetime) -> LimitRow:
    return replace(
        BLANK,
        key="outreach.hunter.search",
        service="Hunter",
        action="Поиск адреса: доноры и рекламодатели",
        scope="учётка, общая с соседней системой",
        unit="поисков",
        used_day=(await _spent(session, CONTACTS_OPERATION, today))[0],
        used_month=(await _spent(session, CONTACTS_OPERATION, month))[0],
        cost_note="Платят тарифом Hunter: поиск — кредит общего счёта",
        now=await _hunter_now(session),
        source=".env CONTACTS_PAID_SEARCH_ENABLED",
    )


def _serp_cost_note() -> str:
    """Месячного лимита нет: цена — прогона на все ключи. Режим с опечаткой откажет сам на смете."""
    keys = serp_cfg.MAX_KEYWORDS_PER_RUN
    try:
        mode = mode_of(None)
    except UnknownModeError as exc:
        logger.warning("панель лимитов: режим выдачи не разобран (%s)", exc)
        return "Месячного лимита нет; цену прогона называет смета"
    price = serp_cost_usd(keys, serp_cfg.DEPTH_PAGES, mode)
    return f"Месячного лимита нет. Прогон на {spaced(keys)} ключей — около {dollars(price)}"


def _serp_now() -> str:
    """Что держит выдачу. Остаток — последний, что слышал сторож этого процесса: не слышал — не
    называем, провайдера панель не спрашивает."""
    heard = funds_watch.SEEN.balance
    funds = "" if heard is None else f"На счету {dollars(heard)} по последнему опросу. "
    return (
        f"Потолка денег нет: держат ключи прогона и глубина до {serp_cfg.MAX_DEPTH_PAGES} страниц. "
        f"{funds}На счету меньше {dollars(MIN_BALANCE_USD)} — прогоны не идут. {READ_ONLY}"
    )


async def _serp_row(session: AsyncSession, month: datetime) -> LimitRow:
    return replace(
        BLANK,
        key="outreach.donors.serp.buy",
        service="Выдача",
        action="Покупка выдачи в прогоне",
        scope="один прогон",
        unit="ключей",
        per_session=serp_cfg.MAX_KEYWORDS_PER_RUN,
        spent_month_usd=usd((await _spent(session, SERP_OPERATION, month))[1]),
        cost_note=_serp_cost_note(),
        now=_serp_now(),
        source=".env SERP_MAX_KEYWORDS_PER_RUN",
    )


def _price_words() -> str:
    """Цены платных уровней: не задана — уровень не зовётся (`crawl/spend.py`)."""
    proxy, render = crawl_cfg.PROXY_USD_PER_GB, crawl_cfg.RENDER_USD_PER_1K
    unpriced = "— цена не задана, не зовётся"
    return (
        f"Прокси {unpriced if proxy is None else f'{dollars(proxy)} за ГБ'}, "
        f"рендеринг {unpriced if render is None else f'{dollars(render)} за 1000'}"
    )


async def _paid_crawl_row(session: AsyncSession) -> LimitRow:
    cap = crawl_cfg.MONTH_CAP_USD
    whole = cap is not None and float(cap).is_integer()
    return replace(
        BLANK,
        key="outreach.advertisers.crawl.paid",
        service="Прокси и рендеринг",
        action="Платный обход Этапа 2",
        scope="все обходы Этапа 2",
        unit="$",
        per_month=int(cap) if cap is not None and whole else None,
        spent_month_usd=usd(await crawl_spent_this_month(session)),
        cost_usd=None if cap is None else usd(cap),
        cost_note="Потолок месяца — деньгами"
        if cap is not None
        else "Потолка нет — нечего тратить",
        now=(
            f"Потолка нет: платные уровни не зовутся. {READ_ONLY}"
            if cap is None
            else f"{_price_words()}. {READ_ONLY}"
        ),
        source=".env CRAWL_MONTH_CAP_USD",
    )


def _crawl_row() -> LimitRow:
    """Обычный обход одного донора: потолок страниц и срока — настройки сервера."""
    return replace(
        BLANK,
        key="outreach.advertisers.crawl.regular",
        service="Обход",
        action="Обход донора",
        scope="один донор",
        unit="страниц",
        per_session=crawl_cfg.MAX_PAGES_PER_DONOR,
        session_hours=math.ceil(crawl_cfg.MAX_SECONDS_PER_DONOR / HOUR_SEC),
        cost_note=CRAWL_COST,
        now=(
            f"Попыток до {spaced(crawl_cfg.MAX_ATTEMPTS_PER_DONOR)}; сайту с долгой паузой срок до "
            f"{math.ceil(crawl_cfg.MAX_SECONDS_SLOW / HOUR_SEC)} ч. {READ_ONLY}"
        ),
        source=".env CRAWL_MAX_PAGES, CRAWL_MAX_SECONDS",
    )


async def _deep_row(session: AsyncSession) -> LimitRow:
    """Глубокий обход: потолки в десять раз выше обычных, зато разом их не больше предела."""
    return replace(
        BLANK,
        key="outreach.advertisers.crawl.deep",
        service="Обход",
        action="Глубокий обход донора",
        scope="один донор",
        unit="страниц",
        per_session=crawl_cfg.MAX_PAGES_DEEP,
        session_hours=math.ceil(crawl_cfg.MAX_SECONDS_DEEP / HOUR_SEC),
        cost_note=CRAWL_COST,
        now=(
            f"Разом не больше {crawl_cfg.DEEP_MAX_RUNNING}: идёт и ждёт {await deep.going(session)}, "
            f"в очереди загрузки {await deep.waiting(session)}. {READ_ONLY}"
        ),
        source=".env CRAWL_DEEP_MAX_RUNNING, CRAWL_MAX_PAGES_DEEP",
    )


async def rows(session: AsyncSession) -> list[LimitRow]:
    """Строки сервисов на эту минуту. Менять нечего: источник только для чтения."""
    moment = datetime.now(UTC)
    month = month_start(moment)
    return [
        await _hunter_row(session, day_start(moment), month),
        await _serp_row(session, month),
        await _paid_crawl_row(session),
        _crawl_row(),
        await _deep_row(session),
    ]
