"""Мост почты к модулю продаж (`core/stages.py`): чей сбой — почты или модуля.

Модуль отвечает в своей точке сохранения: упавший запрос модуля не ломает транзакцию почты, а
его ошибка — «не подключены» с причиной. Точку не поставить — транзакция почты уже прервана своим
запросом или база недоступна — это сбой почты: он всплывает как есть, модуль не спрошен, и журнал
не пишет «ошибка модуля продаж» (ревью стыков R7, 7.6). SQLAlchemy ставит точку лениво — первым
запросом внутри неё, — и без запроса до вопроса её сбой доставался модулю.

Модуль — подставной, своим классом: общему тесту файлы продаж не нужны.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import pytest
from backend.features.core import stages
from backend.features.core.domain import Stage
from backend.features.core.stages import MailPolicy
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

Ask = Callable[[AsyncSession], Awaitable[object]]


class _Module:
    """Подставной модуль продаж: помнит, о чём его спросили; базу не трогает."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def connected(self, _session: AsyncSession) -> bool:
        self.asked.append("connected")
        return True

    async def policy(self, _session: AsyncSession) -> MailPolicy:
        self.asked.append("policy")
        return MailPolicy(watch=True)

    async def threads_to(self, _session: AsyncSession, email: str) -> tuple[int, ...]:
        self.asked.append(f"threads_to {email}")
        return (7,)


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> _Module:
    """Подставной модуль подключён тем же входом, что настоящий; после теста — прежний."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = _Module()
    stages.register_sales(lambda: found)
    return found


#: Входы моста, которые ошибку модуля глотают («нет», пусто) или отдают «не подключены».
ENTRIES: dict[str, Ask] = {
    "connected": stages.sales_connected,
    "policy": lambda session: stages.mail_policy(session, Stage.SALES, "Сторож почты продаж"),
    "threads_to": lambda session: stages.sales_threads_to(session, "jane@lead.example.test"),
}


@pytest.mark.parametrize("ask", ENTRIES.values(), ids=ENTRIES.keys())
async def test_an_aborted_mail_transaction_is_raised_as_is_and_the_module_is_not_asked(
    session: AsyncSession, module: _Module, caplog: pytest.LogCaptureFixture, ask: Ask
) -> None:
    with pytest.raises(DBAPIError):
        await session.execute(text("SELECT 1 FROM made_up_table_of_the_mail_itself"))

    with caplog.at_level(logging.INFO, logger=stages.__name__), pytest.raises(DBAPIError):
        await ask(session)

    assert module.asked == [], "модуль спросили при прерванной транзакции почты"
    assert "ошибка модуля продаж" not in caplog.text
