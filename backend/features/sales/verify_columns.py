"""Колонки лида для перепроверки адреса, у которого Hunter «ещё проверяет» (202).

Своим модулем, а не в `models.py`: тот стоит у предела длины файла, а колонки — одна забота
(`verify_again.py`). Вердикт лида (`verification_status`, `verified_at`) они не трогают: прежний
вердикт держат правила первого письма и второго `unknown` (`unconfirmed.py`), а «ещё проверяет» —
не вердикт, а срок и счёт.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column


class VerifyAgainMixin:
    """Сколько раз подряд Hunter ответил «ещё проверяет» и когда очистка спросит снова сама."""

    #: Ответов «ещё проверяет» подряд — у лида, чья перепроверка отложена; иначе ноль.
    verify_tries: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    #: Когда перепроверка пойдёт сама (`verify_again.py`); пусто — отложенной перепроверки нет.
    verify_again_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
