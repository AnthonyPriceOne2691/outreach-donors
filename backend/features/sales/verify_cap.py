"""Месячный потолок проверки адресов: очистка продаж — в пределах проверок тарифа Hunter.

Учётка Hunter общая с донорами и соседней системой: у тарифа с общим счётом поиск и проверка
тратят одни кредиты (`okf/unit-economy.md`). Потолок считает только проверки адреса, поиски его не
трогают. Без потолка большая очистка или сбор базы выбрали бы счёт учётки целиком, и вместе с
проверкой адресов встал бы и поиск.
Потолок задаёт человек на экране «Расход» (`core/hunter_cap.py`, там же запасной путь из
окружения), числа в коде нет: у живого проверяльщика он обязателен, у выдуманного (`fixture`)
не нужен — тот не платит.

**Потолок один на все проверки адреса.** Его тратят очистка продаж и проверка перед первым письмом
донору и рекламодателю (`contacts/address_check.py`, с 11.11.2026), поэтому счёт очистки — сумма
по всем операциям проверки (`core/hunter_cap.VERIFY_OPERATIONS`), та же, что у проверки перед
письмом и у сторожа Hunter. Считай очистка только свои — вместе они выбрали бы потолок и сверх
него всё, что ушло на проверку перед письмом.

**Потолка нет — слова, а не падение**: негодное значение не роняет общий старт процессов, а
останавливает платную часть очистки — лиды ждут следующей очистки в прежнем статусе с причиной,
бесплатные правила очистки им применены.

**Счёт — по журналу расхода**: единицы операций проверки адреса с первого числа месяца — тем же
чтением, что у сторожа Hunter (`runs/spending.units_this_month`).
Проход бронирует место до запроса (он идёт в несколько потоков) и возвращает его, если
вердикта нет: отказ провайдера единиц не стоит.

**Проход держит потолок, прочитанный на старте** (`capped`): новое значение с «Расхода» действует
со следующего прохода. Выбранное он тоже читает на старте и дальше прибавляет свои проверки: что
за время прохода потратит проверка перед письмом, увидит следующий проход — перебор не больше
этого числа.

**Потолок выбран** — `MonthCapError` (квота: повтор следующим адресом не поможет), платная часть
прохода встаёт словами «потолок проверки адресов на месяц выбран: N из M», лиды ждут в
прежнем статусе; следующий месяц продолжит.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.provider import ProviderError, ProviderQuotaError
from backend.features.core.hunter_cap import VERIFY_OPERATIONS, verify_month_cap
from backend.features.runs.spending import units_this_month
from backend.features.sales.verifier import FIXTURE, EmailVerifier, Verdict


class MonthCapError(ProviderQuotaError):
    """Свой месячный потолок проверок выбран или не задан: платная часть прохода встаёт."""


async def month_spent(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Проверок адреса с первого числа месяца — по журналу расхода: очистки продаж и проверки
    перед первым письмом донору и рекламодателю вместе, потолок у них один."""
    spent = 0
    for operation in VERIFY_OPERATIONS:
        spent += await units_this_month(session, operation, now=now)
    return spent


def reached(used: int, cap: int) -> str:
    return f"потолок проверки адресов на месяц выбран: {used} из {cap} — следующий месяц продолжит"


@dataclass(frozen=True, slots=True)
class Month:
    """Проверки адреса этого месяца под общим потолком — для окна очистки и сметы сбора."""

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
    return Month(await month_spent(session, now=now), await verify_month_cap(session))


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
    cap = await verify_month_cap(session)
    return MonthCapped(verifier, cap=cap, spent=await month_spent(session, now=now))
