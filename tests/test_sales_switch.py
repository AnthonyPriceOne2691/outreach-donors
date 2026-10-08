"""Выключатель продаж для тестов путей, которые живут только при включённых продажах.

`SALES_ENABLED` по умолчанию выключен — в настройках и в наборе тестов. При выключенных
продажах разбор вида ответа моделью и передача лида стоят (`sales/replies.py`,
`sales/handoff.py`). Модуль, который проверяет эти пути, включает продажи этой фикстурой:
имя, импортированное в модуль тестов, включает её для каждого его теста (`autouse`), как
подпись адреса ответа `secret`. Выключенные продажи — `tests/test_sales_enabled_switch.py`.
"""

from __future__ import annotations

import pytest
from backend.config import sales as sales_cfg


@pytest.fixture(autouse=True)
def sales_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Продажи включены (`SALES_ENABLED`): ответы разбирает модель, лидов передают."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
