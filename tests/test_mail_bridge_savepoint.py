"""Мост почты к модулю продаж (`core/stages.py`): чей сбой — почты или модуля.

Модуль отвечает в своей точке сохранения: упавший запрос модуля не ломает транзакцию почты, а
его ошибка — «не подключены» с причиной. Точку не поставить — транзакция почты уже прервана своим
запросом или база недоступна — это сбой почты: он всплывает как есть, модуль не спрошен, и журнал
не пишет «ошибка модуля продаж» (ревью стыков R7, 7.6). SQLAlchemy ставит точку лениво — первым
запросом внутри неё, — и без запроса до вопроса её сбой доставался модулю.

Отказ пачке продаж называет, чего им не хватает (ревью стыков R1, A4; 7.7): мост спрашивает модуль
`missing`. Модуль без этого ответа — до PR продаж с ним — спрашивается по-старому, `connected`,
и отказ ему — прежний, без перечня.

Модуль — подставной, своим классом: общему тесту файлы продаж не нужны.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import pytest
from backend.features.core import stages
from backend.features.core.domain import Stage
from backend.features.core.stages import SALES_NOT_CONNECTED, MailPolicy, SalesNotConnectedError
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


# --- 7.7: отказ пачке называет, чего не хватает --------------------------------------------------

#: Что спрашивает отправка очереди пачкой — как в маршруте.
BATCH = "Очередь писем не отправлена"


class _Lacking(_Module):
    """Модуль, который отвечает, чего не хватает продажам (`SalesMail.missing`)."""

    def __init__(self, lacking: list[str], *, broken: bool = False) -> None:
        super().__init__()
        self.lacking = lacking
        self.broken = broken

    async def missing(self, _session: AsyncSession) -> list[str]:
        self.asked.append("missing")
        if self.broken:
            raise RuntimeError("выдуманная поломка модуля: missing")
        return self.lacking


def _plugged(monkeypatch: pytest.MonkeyPatch, found: _Module) -> _Module:
    monkeypatch.setattr(stages._SALES, "load", None)
    stages.register_sales(lambda: found)
    return found


async def test_the_batch_refusal_names_what_sales_lack(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = _plugged(
        monkeypatch,
        _Lacking(["модуль продаж выключен — включает администратор", "нет ящиков продаж"]),
    )

    with pytest.raises(SalesNotConnectedError) as refused:
        await stages.check_connected(session, Stage.SALES, BATCH)

    assert str(refused.value) == (
        f"{BATCH}: {SALES_NOT_CONNECTED} — "
        "модуль продаж выключен — включает администратор; нет ящиков продаж"
    )
    assert found.asked == ["missing"]


async def test_nothing_lacking_lets_the_batch_go(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = _plugged(monkeypatch, _Lacking([]))

    await stages.check_connected(session, Stage.SALES, BATCH)

    assert found.asked == ["missing"]


async def test_a_broken_answer_of_what_is_lacking_is_said_in_words_and_logged(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Отказ видит оператор на кнопке пачки: текст исключения модуля (SQL, пути кода) — в журнал."""
    _plugged(monkeypatch, _Lacking([], broken=True))

    with (
        caplog.at_level(logging.WARNING, logger=stages.__name__),
        pytest.raises(SalesNotConnectedError) as refused,
    ):
        await stages.check_connected(session, Stage.SALES, BATCH)

    assert str(refused.value) == f"{BATCH}: {SALES_NOT_CONNECTED} — {stages.MISSING_BROKEN}"
    assert "выдуманная поломка модуля: missing" in caplog.text


@pytest.mark.parametrize(("connected", "refused"), [(True, False), (False, True)])
async def test_a_module_without_the_answer_is_asked_as_before(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, connected: bool, refused: bool
) -> None:
    """До PR продаж с `missing` — прежний вопрос «подключены ли» и прежний отказ без перечня."""
    found = _plugged(monkeypatch, _Module())

    async def answer(_session: AsyncSession) -> bool:
        found.asked.append("connected")
        return connected

    monkeypatch.setattr(found, "connected", answer)

    if refused:
        with pytest.raises(SalesNotConnectedError, match=f"^{BATCH}: {SALES_NOT_CONNECTED}$"):
            await stages.check_connected(session, Stage.SALES, BATCH)
    else:
        await stages.check_connected(session, Stage.SALES, BATCH)
    assert found.asked == ["connected"]
