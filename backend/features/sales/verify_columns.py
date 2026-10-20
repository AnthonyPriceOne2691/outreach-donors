"""Колонки лида для перепроверки адреса по сроку: Hunter «ещё проверяет» (202) — и временный
отказ, пока такая перепроверка идёт.

Своим модулем, а не в `models.py`: тот стоит у предела длины файла, а колонки — одна забота
(`verify_again.py`). Вердикт лида (`verification_status`, `verified_at`) они не трогают: прежний
вердикт держат правила первого письма и второго `unknown` (`unconfirmed.py`), а «ещё проверяет» —
не вердикт, а срок.

**Предел — временем от первого «ещё проверяет»** (`verify_since`), а не числом заходов: нажатие
«Повторить сейчас» или «Очистить» раньше срока окно не сокращает и лида не отклоняет.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, text
from sqlalchemy.orm import Mapped, mapped_column

#: Задача перепроверки ищет лидов по сроку: частичный индекс — только у тех, у кого срок есть.
VERIFY_AGAIN_INDEX = Index(
    "idx_sales_leads_verify_again",
    "verify_again_at",
    postgresql_where=text("verify_again_at IS NOT NULL"),
)


class VerifyAgainMixin:
    """С какого момента Hunter «ещё проверяет» и когда очистка спросит снова сама."""

    #: Первое «ещё проверяет» подряд: от него — окно перепроверок; пусто — перепроверки нет.
    verify_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Когда перепроверка пойдёт сама (`verify_again.py`); пусто — отложенной перепроверки нет.
    verify_again_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
