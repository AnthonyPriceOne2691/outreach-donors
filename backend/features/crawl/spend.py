"""Расход платного обхода Этапа 2: прокси и сервис рендеринга — в журнал и под свой потолок.

Расход Этапа 2 на обход и прокси считается отдельно от Этапа 1 и держится своим потолком:
иначе один донор за защитой съест месяц. Уровни каскада выше браузера платные: резидентный
прокси берёт за гигабайт трафика, сервис рендеринга — за вызов. До 12.10.2026 их трата
не писалась никуда: прокси был одним адресом из настройки, и обход, которому сайт
отказывал, платил бы за каждую страницу молча.

**Без своего потолка платные уровни не зовутся вовсе.** Потолок — то, что держит трату;
нет его — держать нечем. Отказ — словами в записи обхода, имя настройки — в журнале.

**Строка расхода — на каждый платный вызов, сразу и своей транзакцией.** Обходчиков
несколько, потолок общий: трата, отложенная до конца пачки, была бы невидима соседям,
и обходчики выбрали бы потолок все разом. Падение процесса теряет не больше
одного вызова.

**Потолок спрашивается до вызова** — по журналу, тем же числом, которое показывает экран
«Расход» (`runs/spending.crawl_spent_this_month`). Выбран — платный уровень не зовётся,
а обход идёт дальше бесплатными и пишет отказ в запись обхода (`Degradation`). Отказ
запоминается до конца обхода: месяц назад не вернётся, а сумма и предупреждение на каждой
закрытой странице были бы одной строкой тысячу раз.

**Цена не задана — уровень не зовётся:** потолок трат, цены которых не знаем, ничего
не держит.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import crawl as cfg
from backend.features.core import usage
from backend.features.core.domain import Stage
from backend.features.runs.spending import crawl_spent_this_month

logger = logging.getLogger(__name__)

PROXY_OPERATION = "crawl_proxy"
RENDER_OPERATION = "crawl_render"

#: Чья трата — словами отказа.
WHAT = {PROXY_OPERATION: "трафика прокси", RENDER_OPERATION: "вызова сервиса рендеринга"}
#: Какой настройкой задаётся цена — для журнала, а не для отказа: поднимает администратор.
PRICE_SETTINGS = {
    PROXY_OPERATION: "CRAWL_PROXY_USD_PER_GB",
    RENDER_OPERATION: "CRAWL_RENDER_USD_PER_1K",
}

GIGABYTE = 1_000_000_000
#: Столбец единиц — `integer`: один вызов дальше не уйдёт и с запасом.
MAX_UNITS = 2**31 - 1


@dataclass(frozen=True, slots=True)
class CrawlCost:
    """Один платный вызов: операция, единицы её счёта и деньги, если цена известна."""

    operation: str
    units: int
    amount_usd: Decimal | None


def cost_of(
    operation: str, units: int, *, proxy_usd_per_gb: float | None, render_usd_per_1k: float | None
) -> CrawlCost:
    """Цена вызова: трафик прокси — по цене гигабайта, вызов сервиса — по цене тысячи."""
    units = max(0, min(units, MAX_UNITS))
    if operation == PROXY_OPERATION:
        price = None if proxy_usd_per_gb is None else units * proxy_usd_per_gb / GIGABYTE
    else:
        price = None if render_usd_per_1k is None else units * render_usd_per_1k / 1000
    return CrawlCost(operation, units, None if price is None else _usd(price))


def _usd(value: float) -> Decimal:
    """До десятитысячной — точность столбца денег (`DECIMAL(10, 4)`) — и вверх.

    Страница через прокси стоит доли цента: 40 КБ по $8 за гигабайт — $0,00032,
    а 5 КБ — $0,00004, которые обычное округление обратило бы в ноль. Двадцать
    тысяч таких страниц в месяц — доллары, которых журнал бы не увидел, а свой
    потолок недосчитал бы. Вверх — перебор не больше $0,0001 на вызов.
    """
    return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_UP)


class CrawlSpending(Protocol):
    """Куда обход сообщает о платных вызовах и у кого спрашивает разрешения."""

    async def refusal(self, operation: str) -> str | None:
        """Почему платный вызов сейчас нельзя; `None` — можно."""
        ...

    async def spent(self, operation: str, units: int) -> None:
        """Записать трату: байты трафика прокси или вызовы сервиса."""
        ...


class JournalSpending:
    """Журнал расхода и свой потолок Этапа 2 — для обхода из очереди.

    Сессия — своя на каждый вызов: обход держит базу на пачках и ударах о жизни,
    и трата не должна ждать пачки или откатываться вместе с ней.
    """

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        cap_usd: float | None = None,
        proxy_usd_per_gb: float | None = None,
        render_usd_per_1k: float | None = None,
    ) -> None:
        self._factory = factory
        self._cap = cap_usd if cap_usd is not None else cfg.MONTH_CAP_USD
        self._proxy_price = (
            proxy_usd_per_gb if proxy_usd_per_gb is not None else cfg.PROXY_USD_PER_GB
        )
        self._render_price = (
            render_usd_per_1k if render_usd_per_1k is not None else cfg.RENDER_USD_PER_1K
        )
        self._refused: dict[str, str] = {}

    def _price(self, operation: str) -> float | None:
        return self._proxy_price if operation == PROXY_OPERATION else self._render_price

    async def refusal(self, operation: str) -> str | None:
        """Почему платный вызов сейчас нельзя; `None` — можно. Отказ запоминается."""
        known = self._refused.get(operation)
        if known is not None:
            return known
        if self._cap is None:
            refused: str | None = self._closed(
                "у Этапа 2 не задан свой потолок — прокси и рендеринг не зовутся, "
                "пока его не задаст администратор",
                setting="CRAWL_MONTH_CAP_USD",
            )
        elif self._price(operation) is None:
            refused = self._closed(
                f"цена {WHAT[operation]} не задана — тратить вслепую нельзя; "
                "задать её может администратор",
                setting=PRICE_SETTINGS[operation],
            )
        else:
            refused = await self._over(self._cap)
        if refused is not None:
            self._refused[operation] = refused
        return refused

    def _closed(self, words: str, *, setting: str) -> str:
        """Отказ без суммы: человеку — слова, журналу — имя настройки (один раз на обход)."""
        logger.warning("платный уровень обхода не зовётся: %s", setting, extra={"setting": setting})
        return words

    async def _over(self, cap: float) -> str | None:
        """Выбран ли потолок: сумма за месяц — по журналу, тем же числом, что на экране."""
        async with self._factory() as session:
            spent = await crawl_spent_this_month(session)
        if spent < Decimal(str(cap)):
            return None
        logger.warning(
            "потолок Этапа 2 на обход выбран",
            extra={"setting": "CRAWL_MONTH_CAP_USD", "spent": str(spent), "cap": cap},
        )
        reached = (
            f"потолок Этапа 2 на обход выбран: {spent:.2f} из {cap:.2f} $ — "
            "платные уровни до следующего месяца; поднять потолок может администратор"
        )
        # Потолок общий на оба уровня: выбран для одного — выбран и для другого.
        self._refused.update(dict.fromkeys(WHAT, reached))
        return reached

    async def spent(self, operation: str, units: int) -> None:
        cost = cost_of(
            operation,
            units,
            proxy_usd_per_gb=self._proxy_price,
            render_usd_per_1k=self._render_price,
        )
        async with self._factory() as session:
            usage.record(
                session,
                operation=cost.operation,
                units=cost.units,
                amount_usd=cost.amount_usd,
                stage=Stage.ADVERTISERS,
            )
            await session.commit()
