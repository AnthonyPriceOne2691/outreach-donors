"""Кап Ahrefs на экране «Расход»: ключ, цикл, наш кап и его смены (решение владельца 16.10.2026).

Своим файлом: схемы расхода (`schemas.py`) — у предела длины, а ключ с капом — своя тема.
Экран называет, а не считает: «осталось» и доля — тем же правилом, что у тревог сторожа
(`runs/ahrefs_cap.KeyCap`), и число на экране не расходится с тем, о чём скажет сторож.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator
from pydantic_core import PydanticCustomError

from backend.features.runs.ahrefs_cap import CapChange, KeyCap, cap_limits


class CapLimits(BaseModel):
    """Допустимое «не больше N»: целые от `min` до `max` включительно — до размера ключа."""

    min: int
    max: int


class CapBody(BaseModel):
    """Новый кап Ahrefs: «не больше `units_cap`» юнитов за цикл ключа, пусто (`null`) — весь
    лимит ключа. Границы проверяет сама смена (`runs/ahrefs_cap.cap_refusal`) — тем же
    правилом, что экран получает в `AhrefsKeyCard.limits`: второй копии чисел здесь нет."""

    units_cap: int | None

    @field_validator("units_cap", mode="before")
    @classmethod
    def _whole(cls, value: object) -> object:
        """Кап — целое число или пусто: дробь, строка и «да/нет» — отказ словами, а не именем
        поля."""
        if value is None or (isinstance(value, int) and not isinstance(value, bool)):
            return value
        raise PydanticCustomError(
            "cap_not_whole",
            "Кап — целое число юнитов за цикл ключа, например 5\u00a0000, или весь лимит ключа",
        )


class CapChangeCard(BaseModel):
    """Смена капа: что поставили, кто и когда. `units_cap` пусто — весь лимит ключа;
    `set_by` пусто — учётку удалили."""

    id: int
    units_cap: int | None
    set_by: str | None
    set_at: datetime

    @classmethod
    def of(cls, change: CapChange) -> CapChangeCard:
        return cls(
            id=change.id,
            units_cap=change.units_cap,
            set_by=change.set_by,
            set_at=change.set_at,
        )


class AhrefsKeyCard(BaseModel):
    """Ключ Ahrefs и наш кап: размер ключа и когда спросили, цикл, сколько выбрано нами и
    всего, сколько осталось, что задано на экране и кто менял."""

    #: Размер ключа за цикл: свой лимит, а своего нет — лимит пространства; `None` — неизвестен.
    size: int | None
    #: Свой лимит у ключа; `False` — своего нет, и ключ упирается в лимит пространства.
    own_limit: bool
    #: Выбрано на ключе всего — вместе с соседней системой на общем ключе.
    key_used: int | None
    #: Пространство: когда своего лимита у ключа нет, упираемся в него.
    workspace_limit: int | None
    workspace_used: int | None
    #: Когда Ahrefs ответил; `None` — не отвечал ни разу.
    asked_at: datetime | None
    #: Цикл капа: с какого момента считается наш расход, когда обнуление и от ключа ли цикл
    #: (`False` — календарный месяц: Ahrefs не назвал дату обнуления).
    cycle_start: datetime
    next_reset: datetime
    by_key: bool
    #: Потрачено нами за цикл — по своему журналу, все этапы.
    spent_by_us: int
    #: Действующий кап; `None` — размер ключа неизвестен (`unknown` — почему).
    cap: int | None
    #: Задано на экране: «не больше N»; `None` — весь лимит ключа.
    chosen: int | None
    #: Заданное «не больше N» выше лимита ключа — действует ключ.
    above_key: bool
    #: Осталось до того, во что упрёмся раньше (`binding`: «cap», «key» или «workspace»).
    left: int
    binding: str | None
    #: Выбрано от действующего капа — целыми процентами, с округлением вниз.
    used_percent: int | None
    unknown: str | None
    #: Допустимое «не больше N»; `None` — размер ключа неизвестен, задать не от чего.
    limits: CapLimits | None
    #: Последние смены, новые первыми; пусто — на экране не меняли: весь лимит ключа.
    changes: list[CapChangeCard]

    @classmethod
    def of(cls, cap: KeyCap, changes: list[CapChange]) -> AhrefsKeyCard:
        quota = None if cap.key is None else cap.key.quota
        bounds = cap_limits(cap.size)
        return cls(
            size=cap.size,
            own_limit=quota is not None and quota.key_limit is not None,
            key_used=None if quota is None else quota.key_used,
            workspace_limit=None if quota is None else quota.workspace_limit,
            workspace_used=None if quota is None else quota.workspace_used,
            asked_at=None if cap.key is None else cap.key.asked_at,
            cycle_start=cap.cycle.start,
            next_reset=cap.cycle.next_reset,
            by_key=cap.cycle.by_key,
            spent_by_us=cap.spent,
            cap=cap.units_cap,
            chosen=cap.chosen,
            above_key=cap.above_key,
            left=cap.left,
            binding=cap.binding,
            used_percent=cap.used_percent,
            unknown=cap.unknown,
            limits=None if bounds is None else CapLimits(min=bounds[0], max=bounds[1]),
            changes=[CapChangeCard.of(change) for change in changes],
        )
