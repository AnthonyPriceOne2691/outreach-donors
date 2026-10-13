"""Месячный потолок проверки адресов продаж — в пределах месячных проверок тарифа Hunter.

Ключ Hunter общий с донорами, а счётчик проверок у Hunter свой, отдельный от поисков адресов
доноров (живой замер): поиски доноров потолок не трогают. Без потолка большая очистка или сбор
базы выбрали бы проверки месяца целиком, и проверка адресов встала бы до следующего месяца.
Потолок — настройкой (`SALES_VERIFIER_MONTH_CAP`), числа в коде нет: у живого проверяльщика он
обязателен, у выдуманного (`fixture`) не нужен — тот не платит.

**Разбор — при чтении, как у `SALES_AGENT_MAX_TURNS`**: опечатка в настройке не роняет общий
старт процессов, а останавливает платную часть очистки словами — лиды ждут следующей очистки
в прежнем статусе с причиной, бесплатные правила очистки им применены.

**Счёт — по журналу расхода**: единицы `sales_verify` с первого числа месяца — той же границей
и тем же чтением, что у капа Ahrefs (`runs/spending.SpendingRepository.since_month_start`).
Проход бронирует место до запроса (он идёт в несколько потоков) и возвращает его, если
вердикта нет: отказ провайдера единиц не стоит.

**Потолок выбран** — `MonthCapError` (квота: повтор следующим адресом не поможет), платная часть
прохода встаёт словами «потолок проверки адресов на месяц выбран: N из M», лиды ждут в
прежнем статусе; следующий месяц продолжит.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.features.contacts.provider import ProviderError, ProviderQuotaError
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs.spending import SpendingRepository
from backend.features.sales.verifier import FIXTURE, OPERATION, EmailVerifier, Verdict

SETTING = "SALES_VERIFIER_MONTH_CAP"
NOT_SET = f"потолок проверки адресов на месяц не задан — задайте {SETTING}"


class MonthCapError(ProviderQuotaError):
    """Свой месячный потолок проверок выбран или не задан: платная часть прохода встаёт."""


def month_cap() -> int | str:
    """Потолок проверок на месяц — числом, или почему его нет — словами."""
    raw = cfg.VERIFIER_MONTH_CAP
    if not raw:
        return NOT_SET
    if not raw.isdigit():
        return f"{SETTING} «{raw}» — нужно целое число проверок адреса в месяц, не меньше нуля"
    return int(raw)


async def month_spent(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Проверок адреса (единиц `sales_verify`) с первого числа месяца — по журналу расхода."""
    spending = await SpendingRepository(session).since_month_start(stages=EVERY_STAGE, now=now)
    return sum(article.units for article in spending.articles if article.operation == OPERATION)


def reached(used: int, cap: int) -> str:
    return f"потолок проверки адресов на месяц выбран: {used} из {cap} — следующий месяц продолжит"


@dataclass(frozen=True, slots=True)
class Month:
    """Проверки адресов в этом месяце — для окна очистки и сметы сбора."""

    spent: int
    cap: int | str

    @property
    def words(self) -> str:
        """«в этом месяце N из M» — или почему потолка нет."""
        if isinstance(self.cap, str):
            return self.cap
        return f"в этом месяце {self.spent} из {self.cap}"

    @property
    def sentence(self) -> str:
        """Строкой окна: «Проверок адреса в этом месяце N из M» — или почему потолка нет."""
        if isinstance(self.cap, str):
            return self.cap[:1].upper() + self.cap[1:]
        return f"Проверок адреса {self.words}"


async def month(session: AsyncSession, *, now: datetime | None = None) -> Month:
    return Month(await month_spent(session, now=now), month_cap())


class MonthCapped:
    """Живой проверяльщик под месячным потолком: тот же `verify`, пока проверок меньше потолка."""

    def __init__(self, inner: EmailVerifier, *, cap: int | str, spent: int) -> None:
        self.name = inner.name
        self._inner = inner
        self._cap = cap
        self.used = spent

    async def verify(self, email: str) -> Verdict:
        if isinstance(self._cap, str):
            raise MonthCapError(self._cap)
        if self.used >= self._cap:
            raise MonthCapError(reached(self.used, self._cap))
        self.used += 1  # место — до запроса: проход идёт в несколько потоков
        try:
            verdict = await self._inner.verify(email)
        except ProviderError:
            self.used -= 1  # вердикта нет — единица не списана
            raise
        self.used += verdict.units - 1
        return verdict

    @property
    def month(self) -> Month:
        """Проверки этого месяца с учётом сделанных проходом — для итога консоли."""
        return Month(self.used, self._cap)


async def capped(
    session: AsyncSession, verifier: EmailVerifier, *, now: datetime | None = None
) -> EmailVerifier:
    """Живой проверяльщик — под месячным потолком по журналу; выдуманный — как есть."""
    if verifier.name == FIXTURE or isinstance(verifier, MonthCapped):
        return verifier
    return MonthCapped(verifier, cap=month_cap(), spent=await month_spent(session, now=now))
