"""Отчёт сборки очереди продаж (`queue.build`): числа, причины ожидания и письма по одному.

Своим модулем, а не в `queue.py`: сборка стоит у предела длины файла, а отчёт — отдельная
забота: что сборка сделала и как это сказать экрану, консоли и журналу. Здесь же — счёт
письма в отчёт (`QueueReport.tally`): встало в очередь — новое или собранное заново; вне
коридора после всех попыток — серия лидов, и на `corridor.STREAK` сборка встаёт словами.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from backend.features.sales import corridor

logger = logging.getLogger(__name__)


@dataclass
class QueueReport:
    """Что получилось — числами и словами, для консоли и строки задачи."""

    campaign_id: int
    prepared: int = 0
    #: Письма в очереди, собранные заново: цепочка сменилась или подпись, адрес.
    refreshed: int = 0
    tokens_spent: int = 0
    off_corridor: int = 0
    #: Почему лиды ждут: причина → сколько.
    waiting: Counter[str] = field(default_factory=Counter)
    #: Сборка остановлена потолком расхода на модель: причина словами.
    stopped: str | None = None
    #: Почему зоны остались шаблонными: причина → сколько раз (как у сборки доноров).
    notes: Counter[str] = field(default_factory=Counter)
    #: Письма, которые писала модель, — по порядку сборки.
    letters: list[corridor.LetterLine] = field(default_factory=list)
    #: Лидов подряд вне коридора после всех попыток — счёт останова, в итог не идёт.
    streak: int = field(default=0, repr=False)

    def totals(self) -> dict[str, Any]:
        """Числа и причины без писем по одному — так сборку пишет журнал: имена лидов ему
        не нужны."""
        return {
            "campaign_id": self.campaign_id,
            "prepared": self.prepared,
            "refreshed": self.refreshed,
            "tokens_spent": self.tokens_spent,
            "off_corridor": self.off_corridor,
            "waiting": dict(self.waiting),
            "stopped": self.stopped,
            "notes": dict(self.notes),
        }

    def as_dict(self) -> dict[str, Any]:
        """Итог задачи для экрана: числа, причины и письма по одному."""
        return {**self.totals(), "letters": [line.as_dict() for line in self.letters]}

    def tally(
        self,
        lead_id: int,
        name: str | None,
        rebuilt: bool,
        tried: corridor.Tried,
        why: str | None,
    ) -> None:
        """Итог письма в отчёт. Встало в очередь — новое или собранное заново (`rebuilt`), и
        серия лидов вне коридора прерывается. Вне коридора после всех попыток — серия растёт, и
        на `STREAK` сборка встаёт словами: не даёт шаблон. Не встало по другой причине — серию
        не трогает."""
        off = tried.letter is None  # тогда и `why` — вне коридора (`queue._store`)
        reason = corridor.waiting_reason(tried.differences) if off else why
        self.letters.append(corridor.line(lead_id, name, tried, reason=reason))
        if why is None:
            self.streak = 0
            if rebuilt:
                self.refreshed += 1
            else:
                self.prepared += 1
            return
        self.waiting[why] += 1
        if not off:
            return
        self.off_corridor += 1
        self.streak += 1
        if self.streak >= corridor.STREAK and self.stopped is None:
            self.stopped = corridor.streak_stop()
            logger.warning("продажи: сборка остановлена", extra={"reason": self.stopped})
