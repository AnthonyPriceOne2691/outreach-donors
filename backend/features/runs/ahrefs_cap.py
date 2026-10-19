"""Кап юнитов Ahrefs: действующий — от ключа, заданный на «Расходе» — его смены.

**Кап — от ключа** (решение владельца 16.10.2026: «сервис должен владеть текущей ситуацией
по ключу, а не данными, которые зашиты жёстко»). Действующий кап — размер ключа из снимка
(`runs/ahrefs_key.py`): свой лимит ключа, а своего нет — лимит пространства. На «Расходе»
его можно только сузить: «весь лимит ключа» (по умолчанию — кап следует за ключом, когда
лимит двигают в Ahrefs) или «не больше N». Заданный N выше нынешнего ключа — действует
ключ, и экран это говорит (`KeyCap.above_key`). До 16.10 кап и его граница жили
в окружении (`AHREFS_UNITS_CAP`, `AHREFS_MONTHLY_UNITS`), а месяц капа был календарным.

**Цикл капа — цикл ключа**: наш расход считается с последнего обнуления ключа
(`ahrefs_key.cycle_of`), а не с первого числа.

**Одна функция действующего капа** (`key_cap`) — у всех, кто его читает: смета и потолок
прогона, остаток капа (`budget.cap_left`), экран «Расход» и «Обзор», тревоги сторожа,
пакет DR Этапа 2, консоль и докупка стран. Кап, прочитанный мимо неё, разошёлся бы
с экраном молча.

**Размер ключа неизвестен — кап неизвестен** (`KeyCap.unknown`): Ahrefs ещё не ответил ни
разу или не назвал лимитов. Тратить тогда не от чего — `budget.cap_left` отказывает
словами, а экраны называют причину.

**Границы смены — от единицы до размера ключа из снимка.** Кап выше ключа ничего не
ограничивает, нулевой — не потолок трат, а их запрет без единого слова о том, почему
прогоны стоят. Снимка нет — «не больше N» задать не от чего, отказ словами
(`CapUnknownError`); «весь лимит ключа» — можно всегда.

**Смена — под замком.** Прежняя настройка для журнала («было → стало») читается под
рекомендательной блокировкой транзакции: две смены разом иначе обе записали бы одно
и то же «было», и журнал врал бы о второй. Замок снимается фиксацией или откатом.

**Смена не правит прошлое.** Строка заводится новая, прежние остаются — по ним видно,
кто, когда и что ставил. Прогон, поставленный до смены, держит потолок, обещанный при
нажатии, но не больше остатка капа к своему старту (`budget.ceiling_at_start`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.access import UserModel
from backend.features.core.models.ops import AhrefsCapModel
from backend.features.runs.ahrefs_key import NOT_ASKED, Cycle, KeySnapshot, cycle_of, snapshot
from backend.features.runs.spending import ahrefs_spent_since
from backend.shared.numbers import spaced

#: Ключ замка смены капа — «AHRCAP» байтами. Смены редки и коротки: замок один на все.
CAP_LOCK = 0x414852434150

#: Наименьший кап. Ноль — не кап, а запрет тратить.
MIN_CAP = 1

#: Сколько последних смен отдаёт экран: «кто и когда менял» — о недавнем. Вся история —
#: в журнале действий, с «было → стало».
RECENT = 5

#: «Весь лимит ключа» словами — в журнале и в консоли.
WHOLE_KEY = "весь лимит ключа"


class CapRangeError(ValueError):
    """Кап за границами. Текст говорит, что не так и какое допустимо."""


class CapUnknownError(RuntimeError):
    """«Не больше N» задать не от чего: размер ключа неизвестен. Текст говорит почему."""


@dataclass(frozen=True, slots=True)
class CapChange:
    """Смена капа: что поставили, кто и когда. `units_cap` пусто — весь лимит ключа."""

    id: int
    units_cap: int | None
    #: Почта учётки. Пусто — учётку удалили, а строка смены осталась.
    set_by: str | None
    set_at: datetime


@dataclass(frozen=True, slots=True)
class KeyCap:
    """Кап Ahrefs в цикле ключа и сколько из него потрачено нами — одним чтением.

    Кап, ключ и расход читаются вместе: экран и тревога называют их рядом, и остаток,
    посчитанный от другого чтения капа, мог бы с ними не сойтись."""

    #: Задано на «Расходе»: «не больше N»; `None` — весь лимит ключа.
    chosen: int | None
    #: Снимок ключа; `None` — Ahrefs ещё не ответил ни разу.
    key: KeySnapshot | None
    cycle: Cycle
    #: Потрачено нами в цикле — по своему журналу, все этапы.
    spent: int

    @property
    def size(self) -> int | None:
        """Размер ключа за цикл — свой лимит или лимит пространства; `None` — неизвестен."""
        return None if self.key is None else self.key.quota.size

    @property
    def unknown(self) -> str | None:
        """Почему кап неизвестен — словами; `None` — известен."""
        return NOT_ASKED if self.key is None else self.key.unknown

    @property
    def units_cap(self) -> int | None:
        """Действующий кап: размер ключа, а заданный ниже — он; размер неизвестен — `None`."""
        size = self.size
        if size is None or self.chosen is None:
            return size
        return min(self.chosen, size)

    @property
    def above_key(self) -> bool:
        """Заданное «не больше N» выше нынешнего ключа — действует ключ, и экран это говорит."""
        size = self.size
        return self.chosen is not None and size is not None and self.chosen > size

    @property
    def cap_left(self) -> int:
        """Остаток нашего капа — кап минус наш расход за цикл; выбранный — ноль, а не минус.
        Кап неизвестен — тоже ноль: тратить не от чего."""
        cap = self.units_cap
        return 0 if cap is None else max(0, cap - self.spent)

    def _lefts(self) -> dict[str, int]:
        """Остатки по всему, во что можно упереться: наш кап, лимит ключа, лимит пространства
        (по снимку — они общие с соседней системой)."""
        lefts = {"cap": self.cap_left}
        if self.key is not None:
            for name, left in (
                ("key", self.key.quota.key_left),
                ("workspace", self.key.quota.workspace_left),
            ):
                if left is not None:
                    lefts[name] = left
        return lefts

    @property
    def binding(self) -> str | None:
        """Во что упрёмся раньше: «cap», «key» или «workspace»; поровну — наш кап. Кап
        неизвестен — `None`."""
        if self.units_cap is None:
            return None
        lefts = self._lefts()
        return min(lefts, key=lefts.__getitem__)

    @property
    def left(self) -> int:
        """Осталось до того, во что упрёмся раньше (`binding`); кап неизвестен — ноль."""
        return 0 if self.units_cap is None else min(self._lefts().values())

    @property
    def used_percent(self) -> int | None:
        """Сколько выбрано от действующего капа, целыми процентами вниз, — по тому, во что
        упрёмся раньше: остаток — меньший из остатков капа, ключа и пространства. Целыми,
        а не долей: 0,29 × 100 в плавающей точке — 28,999…, и порог 80 % на ровном числе
        сравнивался бы не с тем. Кап неизвестен — `None`; нулевой ключ — выбран весь."""
        cap = self.units_cap
        if cap is None:
            return None
        return 100 if cap <= 0 else (cap - self.left) * 100 // cap


def cap_limits(size: int | None) -> tuple[int, int] | None:
    """Допустимое «не больше N» — от `MIN_CAP` до размера ключа включительно; размер
    неизвестен — `None`: задать не от чего."""
    return None if size is None else (MIN_CAP, size)


def cap_words(units_cap: int | None) -> str | int:
    """Настройка капа для журнала: число — числом, «весь лимит ключа» — словами."""
    return WHOLE_KEY if units_cap is None else units_cap


def cap_refusal(units_cap: int, size: int) -> str | None:
    """Почему «не больше `units_cap`» не годится при ключе размером `size` — словами;
    `None` — годится."""
    if MIN_CAP <= units_cap <= size:
        return None
    why = (
        "выше лимита ключа Ahrefs кап ничего не ограничивает — для этого есть «весь лимит ключа»"
        if units_cap > size
        else "кап меньше единицы не ограничивал бы траты, а запрещал их"
    )
    return (
        f"Кап {spaced(units_cap)} не годится: допустимо от {spaced(MIN_CAP)} "
        f"до {spaced(size)} юнитов за цикл ключа — {why}"
    )


async def chosen_cap(session: AsyncSession) -> int | None:
    """Что задано на «Расходе»: последняя смена; смен нет — весь лимит ключа (`None`)."""
    return await session.scalar(
        select(AhrefsCapModel.units_cap).order_by(AhrefsCapModel.id.desc()).limit(1)
    )


async def key_cap(session: AsyncSession, *, now: datetime | None = None) -> KeyCap:
    """Действующий кап, его цикл и наш расход в цикле — одним чтением (`KeyCap`)."""
    key = await snapshot(session)
    cycle = cycle_of(None if key is None else key.quota.reset_at, now or datetime.now(UTC))
    return KeyCap(
        chosen=await chosen_cap(session),
        key=key,
        cycle=cycle,
        spent=await ahrefs_spent_since(session, cycle.start),
    )


async def cap_changes(session: AsyncSession, *, limit: int = RECENT) -> list[CapChange]:
    """Последние смены капа, новые первыми: что поставили, кто и когда."""
    rows = await session.execute(
        select(AhrefsCapModel.id, AhrefsCapModel.units_cap, UserModel.email, AhrefsCapModel.set_at)
        .outerjoin(UserModel, UserModel.id == AhrefsCapModel.set_by)
        .order_by(AhrefsCapModel.id.desc())
        .limit(limit)
    )
    return [
        CapChange(id=row_id, units_cap=cap, set_by=email, set_at=at)
        for row_id, cap, email, at in rows.tuples().all()
    ]


@dataclass(frozen=True, slots=True)
class CapSet:
    """Итог смены: что было задано до неё, размер ключа на минуту смены и сама смена."""

    was: int | None
    #: Размер ключа из снимка; `None` — неизвестен (так бывает только у «весь лимит ключа»).
    size: int | None
    change: CapChange


async def set_ahrefs_cap(
    session: AsyncSession, units_cap: int | None, *, author: UserModel
) -> CapSet:
    """Новый кап — с этой минуты: «не больше `units_cap`», `None` — весь лимит ключа.

    Без коммита: смена и запись журнала ложатся одной транзакцией вызывающего. «Не больше
    N» — в границах ключа из снимка: за ними — `CapRangeError`, снимка нет —
    `CapUnknownError`, оба до записи. То же, что действует, — тоже смена со строкой
    и записью журнала: отказа «менять нечего» здесь нет, его держит экран
    (`settings/AhrefsCapModal`: то же не отправляется).
    """
    key = await snapshot(session)
    size = None if key is None else key.quota.size
    if units_cap is not None:
        if key is None or size is None:
            unknown = NOT_ASKED if key is None else key.unknown
            raise CapUnknownError(f"Кап не задан: {unknown} — «не больше N» задать не от чего")
        refusal = cap_refusal(units_cap, size)
        if refusal is not None:
            raise CapRangeError(refusal)
    await session.execute(select(func.pg_advisory_xact_lock(CAP_LOCK)))
    was = await chosen_cap(session)
    row = AhrefsCapModel(units_cap=units_cap, set_by=author.id)
    session.add(row)
    await session.flush()
    change = CapChange(id=row.id, units_cap=row.units_cap, set_by=author.email, set_at=row.set_at)
    return CapSet(was=was, size=size, change=change)
