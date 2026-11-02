"""Договор панели лимитов: строка панели, ответ просчёта и источник строк — только типы.

Требование другой стороны: одна панель на сервис, строки разделены по задачам (Продажи, Аутрич); у
строки — сервис и действие, лимиты на сессию, день и месяц, стоимость, текущее ограничение, а то, что
можно менять, меняется кнопкой просчёта: сколько будет стоить новый лимит — до подтверждения.

Договор согласован с модулем продаж: его строки стоят в `sales/limits.py`, общие — в `ops/limit_*.py`,
а собирает их `api/settings/limit_panel.py` без моста. Форма `LimitRow` и `Quote` без согласования не
меняется: экран и модуль продаж читают её независимо.

**Чего нет — пусто, а не ноль**: нет лимита — `None` и слова в `now`; платят тарифом — `cost_usd`
`None` и слова в `cost_note`. Ноль в этих полях значил бы «лимит ноль» и «бесплатно».

**Строки не зовут провайдеров** (`rows`): только журнал расхода и снимки. Панель читают рядом с
просчётом, и чужой таймаут не должен стоить экрана.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.core.models.access import UserModel


@dataclass(frozen=True, slots=True)
class LimitRow:
    """Строка панели: один лимит одной задачи — и всё, что о нём нужно решать."""

    #: Стабильный ключ, например `sales.hunter.verify`: по нему просчёт и смена. Начало ключа
    #: называет этап, если он один (`sales.`, `outreach.donors.`, `outreach.advertisers.`).
    key: str
    #: Задача: «Продажи», «Аутрич».
    task: str
    service: str
    action: str
    #: Охват: «все ящики продаж», «один ящик», «один диалог».
    scope: str
    #: Единица лимитов и выбранного: «токенов», «писем», «$».
    unit: str
    per_session: int | None
    #: Сколько часов в сессии; `None` — сессия не часами (прогон, диалог).
    session_hours: int | None
    per_day: int | None
    per_month: int | None
    used_day: int | None
    used_month: int | None
    #: Потрачено за месяц деньгами — у счётчика без лимита и единиц, например выдача.
    spent_month_usd: Decimal | None
    #: Стоимость месяца при выборке лимита целиком.
    cost_usd: Decimal | None
    #: Слова к стоимости; платят тарифом — `cost_usd` пусто, а не ноль.
    cost_note: str | None
    #: Текущее ограничение словами.
    now: str | None
    #: Откуда значение: поле, `.env` или код.
    source: str
    #: Что можно менять с экрана — по состоянию; пусто — причина в `now`.
    editable: frozenset[str]


@dataclass(frozen=True, slots=True)
class Quote:
    """Ответ просчёта: сколько будет стоить новый лимит, ничего не записав."""

    cost_usd: Decimal | None
    #: Что значит новое значение и какой ценой — словами экрана.
    words: str
    #: Почему значение не годится; `None` — годится.
    refusal: str | None
    #: Что подтверждается на деле, после округления.
    accepted: int


class LimitRefusedError(ValueError):
    """Смена не годится: текст говорит почему. Бросает `change` источника — тем же правилом, что
    `Quote.refusal`, потому что смена сама повторяет просчёт."""


class RowSource(Protocol):
    """Откуда панель берёт строки. Модуль подходит сам: функцией `rows`."""

    async def rows(self, session: AsyncSession, /) -> list[LimitRow]:
        """Строки на эту минуту — по журналу расхода и снимкам, без вызовов провайдеров."""
        ...


class LimitSource(RowSource, Protocol):
    """Источник, у которого строки меняются с экрана: `rows`, `quote` и `change` модуля.

    `field` — поле строки: `per_session`, `per_day` или `per_month`. Панель зовёт оба метода только
    для поля из `LimitRow.editable`.
    """

    async def quote(self, session: AsyncSession, key: str, field: str, value: int, /) -> Quote:
        """Просчёт нового значения. Ничего не пишет."""
        ...

    async def change(
        self, session: AsyncSession, key: str, field: str, value: int, author: UserModel, /
    ) -> None:
        """Смена: сама повторяет `quote` и бросает `LimitRefusedError` словами отказа. История и
        журнал — в существующих сеттерах; фиксирует вызывающий, одной транзакцией со сменой."""
        ...
