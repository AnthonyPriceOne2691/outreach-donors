"""Поиск адресов задачей очереди: этап разбирается целиком, без «иначе доноры».

До 07.10.2026 этап, не названный рекламодателями, шёл по очереди доноров:
этап продаж (`Stage.SALES`, #205) молча искал бы адреса донорам и платил бы
за их ступени. Теперь у каждого этапа своя ветка (`match` с `assert_never`:
новое значение — ошибка mypy), а этап без поиска получает постоянный отказ
(`StageWithoutContactsError`): задача кончается причиной, а не тремя
повторами очереди.

Поиск, база и очередь подменены — тело задачи с настоящей базой гоняют
`test_api_contacts.py::TestTheJobBody`.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from backend.features.core.domain import Stage
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.runs.failures import is_permanent
from backend.workers import jobs

#: Этапы без поиска адресов: задача им отказывает.
REFUSED = frozenset({Stage.SALES})


class _Session:
    """Сессия базы, которой поиск не пользуется: он подменён."""

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def commit(self) -> None:
        return None


class _Engine:
    async def dispose(self) -> None:
        return None


@pytest.fixture
def searched(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Поиск подменён: запоминает, с какой очередью его позвали; движок
    базы — сколько раз его заводили."""
    seen: dict[str, list[Any]] = {"search": [], "engines": [], "remembered": []}

    async def search(_session: object, **kwargs: Any) -> SimpleNamespace:
        seen["search"].append(kwargs)
        return SimpleNamespace(as_dict=lambda: {"walked": 0})

    def engine(dsn: str) -> _Engine:
        seen["engines"].append(dsn)
        return _Engine()

    monkeypatch.setattr(jobs, "search_contacts", search)
    monkeypatch.setattr(jobs, "create_async_engine", engine)
    monkeypatch.setattr(jobs, "async_sessionmaker", lambda _engine, **_kw: _Session)
    monkeypatch.setattr(jobs, "check_storage", lambda: None)
    monkeypatch.setattr(jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(jobs, "get_current_job", lambda: SimpleNamespace(id="job-7"))
    monkeypatch.setattr(
        jobs, "remember_job_error", lambda job_id, text: seen["remembered"].append((job_id, text))
    )
    return seen


class TestEachStageItsQueue:
    """`_search_contacts`: ветка `match` на каждый этап."""

    async def test_donors_go_by_the_default_queue(self, searched: dict[str, list[Any]]) -> None:
        await jobs._search_contacts(5, False, False, None, Stage.DONORS)

        [call] = searched["search"]
        assert call["queue"] is None  # очередь доноров — умолчание `search_contacts`
        assert call["limit"] == 5

    async def test_advertisers_go_by_their_own_queue(self, searched: dict[str, list[Any]]) -> None:
        await jobs._search_contacts(5, False, False, None, Stage.ADVERTISERS)

        [call] = searched["search"]
        assert isinstance(call["queue"], AdvertiserContactRepository)

    async def test_sales_are_refused_before_the_database(
        self, searched: dict[str, list[Any]]
    ) -> None:
        with pytest.raises(jobs.StageWithoutContactsError, match="У этапа «sales» нет поиска"):
            await jobs._search_contacts(5, False, False, None, Stage.SALES)

        # Ни движка базы, ни ступеней: платить за чужой этап нечем.
        assert searched["engines"] == []
        assert searched["search"] == []

    @pytest.mark.parametrize("stage", list(Stage))
    async def test_every_stage_has_its_branch(
        self, searched: dict[str, list[Any]], stage: Stage
    ) -> None:
        """Каждый этап либо ищет своей очередью, либо получает отказ словами —
        до `assert_never` не доходит ни один. Новый этап без решения роняет тест
        и на исполнении, не только в mypy."""
        if stage in REFUSED:
            with pytest.raises(jobs.StageWithoutContactsError):
                await jobs._search_contacts(1, False, False, None, stage)
            assert searched["search"] == []
        else:
            await jobs._search_contacts(1, False, False, None, stage)
            assert len(searched["search"]) == 1


class TestTheJobNamesItsSearch:
    """`_contacts_what`: подпись для журнала — тоже по ветке на этап."""

    def test_each_stage(self) -> None:
        assert jobs._contacts_what(None, Stage.ADVERTISERS) == "поиск адресов рекламодателей"
        assert jobs._contacts_what(None, Stage.DONORS) == "поиск контактов"
        assert jobs._contacts_what(7, Stage.DONORS) == "поиск адреса донора №7"
        # Этап без поиска называется, а не отказывает: подпись считается до
        # `_settled`, и брошенный здесь отказ очередь повторила бы трижды.
        assert jobs._contacts_what(None, Stage.SALES) == "поиск адресов продаж"


class TestTheRefusalIsTheOutcome:
    def test_the_refusal_is_permanent(self) -> None:
        assert is_permanent(jobs.StageWithoutContactsError(Stage.SALES))

    def test_job_of_a_stage_without_search_ends_with_the_reason(
        self, searched: dict[str, list[Any]], caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=jobs.__name__):
            result = jobs.find_contacts(stage=Stage.SALES.value)

        assert result["permanent"] is True
        assert result["error"].startswith("StageWithoutContactsError: У этапа «sales» нет поиска")
        # Итог задачи, а не падение: в очередь на повтор не уходит, причину
        # повтора запоминать незачем.
        assert searched["remembered"] == []
        assert searched["engines"] == []
        assert searched["search"] == []
        [record] = [r for r in caplog.records if r.name == jobs.__name__]
        assert record.levelno == logging.WARNING
        assert record.getMessage().startswith("поиск адресов продаж не выполнена: ")

    def test_job_of_a_known_stage_searches(self, searched: dict[str, list[Any]]) -> None:
        result = jobs.find_contacts(limit=3, stage=Stage.ADVERTISERS.value)

        assert result == {"walked": 0}
        [call] = searched["search"]
        assert isinstance(call["queue"], AdvertiserContactRepository)
        assert call["limit"] == 3


class TestGivenUp:
    """`_given_up` — общий итог задачи, которую повтор не исправит."""

    def test_the_reason_goes_to_the_log_and_to_the_screen(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=jobs.__name__):
            result = jobs._given_up(ValueError("нет такого этапа"), what="поиск адресов")

        assert result == {"error": "ValueError: нет такого этапа", "permanent": True}
        assert "поиск адресов не выполнена: ValueError: нет такого этапа" in caplog.text

    def test_settled_gives_up_on_a_permanent_refusal_without_a_retry(
        self, searched: dict[str, list[Any]], caplog: pytest.LogCaptureFixture
    ) -> None:
        def refused() -> dict[str, Any]:
            raise jobs.StageWithoutContactsError(Stage.SALES)

        with caplog.at_level(logging.WARNING, logger=jobs.__name__):
            result = jobs._settled(refused, what="поиск адресов")

        assert result["permanent"] is True
        assert searched["remembered"] == []
        assert "поиск адресов не выполнена: StageWithoutContactsError" in caplog.text
