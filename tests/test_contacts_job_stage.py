"""Поиск адресов задачей очереди: этап разбирается явно, без «иначе доноры».

До 07.10.2026 этап, не названный рекламодателями, шёл по очереди доноров:
этап, добавленный в перечисление позже, молча искал бы адреса донорам и платил
бы за их ступени. Теперь у каждого этапа своя ветка, а этап без поиска
получает постоянный отказ (`StageWithoutContactsError`): задача кончается
причиной, а не тремя повторами очереди.

«Этап, добавленный позже» здесь — перечисление `_LaterStage` вместо
`jobs.Stage`: в настоящем перечислении такого этапа нет, и проверить ветку
по-другому нельзя. Поиск, база и очередь подменены — тело задачи с настоящей
базой гоняют `test_api_contacts.py::TestTheJobBody`.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from types import SimpleNamespace
from typing import Any

import pytest
from backend.features.core.domain import Stage
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.runs.failures import is_permanent
from backend.workers import jobs


class _LaterStage(StrEnum):
    """Перечисление этапов, в которое добавили этап без поиска адресов."""

    DONORS = "donors"
    ADVERTISERS = "advertisers"
    LATER = "later"


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


@pytest.fixture
def later(monkeypatch: pytest.MonkeyPatch) -> type[_LaterStage]:
    monkeypatch.setattr(jobs, "Stage", _LaterStage)
    return _LaterStage


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

    async def test_a_stage_without_search_is_refused_before_the_database(
        self, searched: dict[str, list[Any]], later: type[_LaterStage]
    ) -> None:
        with pytest.raises(jobs.StageWithoutContactsError, match="У этапа «later» нет поиска"):
            await jobs._search_contacts(5, False, False, None, later.LATER)  # type: ignore[arg-type]

        # Ни движка базы, ни ступеней: платить за чужой этап нечем.
        assert searched["engines"] == []
        assert searched["search"] == []


class TestTheJobNamesItsSearch:
    """`_contacts_what`: подпись для журнала — тоже по ветке на этап."""

    def test_known_stages(self) -> None:
        assert jobs._contacts_what(None, Stage.ADVERTISERS) == "поиск адресов рекламодателей"
        assert jobs._contacts_what(None, Stage.DONORS) == "поиск контактов"
        assert jobs._contacts_what(7, Stage.DONORS) == "поиск адреса донора №7"

    def test_a_stage_without_search_is_named_not_refused(self, later: type[_LaterStage]) -> None:
        """Подпись считается до `_settled`: брось она исключение, отказ ушёл бы
        мимо него, и очередь повторила бы его трижды."""
        label = jobs._contacts_what(None, later.LATER)

        assert label == "поиск адресов этапа «later»"


class TestTheRefusalIsTheOutcome:
    def test_the_refusal_is_permanent(self) -> None:
        assert is_permanent(jobs.StageWithoutContactsError(Stage.DONORS))

    def test_job_of_a_stage_without_search_ends_with_the_reason(
        self,
        searched: dict[str, list[Any]],
        later: type[_LaterStage],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=jobs.__name__):
            result = jobs.find_contacts(stage=later.LATER.value)

        assert result["permanent"] is True
        assert result["error"].startswith("StageWithoutContactsError: У этапа «later» нет поиска")
        # Итог задачи, а не падение: в очередь на повтор не уходит, причину
        # повтора запоминать незачем.
        assert searched["remembered"] == []
        assert searched["engines"] == []
        assert searched["search"] == []
        [record] = [r for r in caplog.records if r.name == jobs.__name__]
        assert record.levelno == logging.WARNING
        assert record.getMessage().startswith("поиск адресов этапа «later» не выполнена: ")

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
            raise jobs.StageWithoutContactsError("later")

        with caplog.at_level(logging.WARNING, logger=jobs.__name__):
            result = jobs._settled(refused, what="поиск адресов")

        assert result["permanent"] is True
        assert searched["remembered"] == []
        assert "поиск адресов не выполнена: StageWithoutContactsError" in caplog.text
