"""Строка панели лимитов о юнитах Ahrefs: наш кап, лимит ключа и смена существующим путём.

Кап — от ключа, и читается он там же, где у всех (`runs/ahrefs_cap.key_cap`): панель не считает
второй раз. Снимок ключа — последний ответ Ahrefs: провайдера панель не зовёт. Цикл у капа свой, а не
календарный месяц, поэтому колонка «месяц» у этой строки — цикл ключа, и `now` называет его конец.

**Просчёт — в юнитах.** Цены юнита в коде нет: платят тарифом, и деньгами здесь считать нечего.

Смена — тем же сеттером, что на карточке: границы от единицы до размера ключа, строка истории, запись
журнала. «Весь лимит ключа» панель не задаёт: поле числовое, для него есть карточка на вкладке
«Лимиты».
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from backend.config import ahrefs as ahrefs_cfg
from backend.features.core.limit_rows import LimitRefusedError, LimitRow, Quote
from backend.features.ops import cap_journal
from backend.features.ops.limit_fields import BLANK, PER_MONTH, SAME, day_start, refused
from backend.features.runs.ahrefs_cap import KeyCap, cap_refusal, key_cap, set_ahrefs_cap
from backend.features.runs.spending import ahrefs_spent_since
from backend.shared.numbers import spaced

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.core.models.access import UserModel

KEY = "outreach.ahrefs.units"

#: Платят тарифом: цены юнита в коде нет, и ноль сказал бы «бесплатно».
COST_NOTE = "Платят тарифом Ahrefs, цены юнита в коде нет — считаем юнитами"

#: Во что упрёмся раньше — словами строки.
BINDING = {
    "cap": "упор — наш кап",
    "key": "упор — лимит ключа",
    "workspace": "упор — лимит пространства",
}


def _now(cap: KeyCap) -> str:
    if cap.units_cap is None:
        return f"Кап неизвестен: {cap.unknown}. Менять не от чего"
    chosen = "весь лимит ключа" if cap.chosen is None else f"не больше {spaced(cap.chosen)}"
    above = ", задано выше ключа — действует ключ" if cap.above_key else ""
    binding = BINDING.get(cap.binding or "", "")
    return (
        f"Задано: {chosen}{above}. Цикл ключа до {cap.cycle.next_reset:%d.%m}, осталось "
        f"{spaced(cap.left)}, {binding}. Запросов в минуту до {ahrefs_cfg.RATE_LIMIT_PER_MIN}"
    )


async def rows(session: AsyncSession) -> list[LimitRow]:
    """Строка капа: кап и расход нашего цикла — одним чтением, как у карточки и сторожа."""
    moment = datetime.now(UTC)
    cap = await key_cap(session, now=moment)
    return [
        replace(
            BLANK,
            key=KEY,
            service="Ahrefs",
            action="Юниты: метрики, страны, списки, DR кандидатов",
            scope="ключ, общий с соседней системой",
            unit="юнитов",
            per_month=cap.units_cap,
            used_day=await ahrefs_spent_since(session, day_start(moment)),
            used_month=cap.spent,
            cost_note=COST_NOTE,
            now=_now(cap),
            source="снимок ключа Ahrefs" if cap.chosen is None else "поле: вкладка «Лимиты»",
            editable=frozenset() if cap.size is None else frozenset({PER_MONTH}),
        )
    ]


def _quoted(cap: KeyCap, units: int) -> Quote:
    """Новый кап в границах ключа: что останется и что будет, если выбрано уже больше."""
    if cap.size is None:
        return refused(f"Кап не задан: {cap.unknown} — «не больше N» задать не от чего")
    why = cap_refusal(units, cap.size)
    if why is not None:
        return refused(why)
    if units == cap.chosen:
        return refused(SAME)
    left = max(0, units - cap.spent)
    after = f"останется {spaced(left)}" if left else "кап уже выбран, прогоны встанут до обнуления"
    words = (
        f"Кап {spaced(units)} юнитов за цикл ключа: выбрано нами {spaced(cap.spent)}, {after}. "
        f"Лимит ключа — {spaced(cap.size)}. {COST_NOTE}."
    )
    return Quote(cost_usd=None, words=words, refusal=None, accepted=units)


async def quote(session: AsyncSession, _key: str, _field: str, value: int) -> Quote:
    """Просчёт нового капа. Ничего не пишет."""
    return _quoted(await key_cap(session), value)


async def change(
    session: AsyncSession, key: str, field: str, value: int, author: UserModel
) -> None:
    """Смена существующим путём: сама повторяет просчёт, строка истории и журнал — как у карточки."""
    quoted = await quote(session, key, field, value)
    if quoted.refusal is not None:
        raise LimitRefusedError(quoted.refusal)
    done = await set_ahrefs_cap(session, quoted.accepted, author=author)
    await cap_journal.ahrefs_cap(session, done, author)
