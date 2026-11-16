"""Итог одного прохода добивок (`letters/followups.send_due`) — числами и строкой журнала.

Отдельным модулем, а не в `followups.py`: тот у предела длины (16.11.2026), а итог прохода растёт
вместе с причинами, по которым добивка не уходит сейчас, — у каждой своя строка, почему и когда.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PassReport:
    """Что сделал один проход по цепочкам."""

    sent: int = 0
    postponed: int = 0
    """Отложено: ящик стоит или почта отказала временно. Цепочка жива."""
    capped: Counter[int] = field(default_factory=Counter)
    """Из отложенных — до завтра, по ящикам: ящик выбрал суточный потолок (`followup_caps`)."""
    stopped: int = 0
    """Остановлено: донор в стоп-листе или в тексте добивки запрещённое. Больше ему не пишем."""
    unknown: int = 0
    """Почта не ответила после отправки: ушла ли добивка — неизвестно. Не
    повторяется: срок погашен, письмо «отправляется» до события платформы."""
    waiting: int = 0
    """Подошли, но у этапа нет цепочки: срок цел, добивка ждёт подключения этапа."""
    resting: int = 0
    """Подошли, но ящик переписки отдыхает между письмами (`followup_pace.py`): срок — на конец
    его паузы, проход их не захватывал. В «отложено» не входят: ящик пишет, просто не подряд."""
    free_at: datetime | None = None
    """Когда освободится первый из ящиков, чьи добивки ждут паузы (`resting`)."""

    @property
    def as_report(self) -> str:
        capped = ""
        if self.capped:  # по ящикам: потолок у каждого свой, и видно, чей завал переезжает
            boxes = ", ".join(f"ящик №{box} — {n}" for box, n in sorted(self.capped.items()))
            capped = f" (из них до завтра по суточному потолку — {self.capped.total()}: {boxes})"
        said = (
            f"отправлено {self.sent}, отложено {self.postponed}{capped}, "
            f"остановлено {self.stopped}, исход неизвестен {self.unknown}"
        )
        if self.resting:
            # Минута — по UTC, как всё время журнала: «ждут паузы» без неё читалось бы поломкой.
            nearest = (
                "" if self.free_at is None else f", первый освободится в {self.free_at:%H:%M} UTC"
            )
            said += f", ждут паузы ящика между письмами {self.resting}{nearest}"
        return f"{said}, ждут этапа {self.waiting}" if self.waiting else said
