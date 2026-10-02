"""Команда `contacts`: что печатает отчёт поиска по базе.

Отчёт — единственное место, где человек видит, как сработала лестница:
сколько доменов вошло на каждую ступень, сколько адресов она дала, сколько
стоила. Число, которого в нём нет, для человека не существует — так было
с «сайт не ответил»: такие домены уходили в «без контакта» молча.
"""

from __future__ import annotations

import argparse

import pytest
from backend.cli import contact_search
from backend.features.contacts.search import SearchReport

COUNTERS = {
    "mx_checked": 10,
    "mx_stopped": 1,
    "mx_unknown": 2,
    "pages_entered": 9,
    "pages_found": 4,
    "rdap_entered": 0,
    "provider_entered": 3,
    "provider_found": 1,
    "form_only": 1,
    "manual_queued": 1,
    "not_found": 2,
    "no_answer": 3,
    "rejected_emails": 5,
}


def _report(**counters: int) -> SearchReport:
    return SearchReport(pending=10, walked=10, saved=5, counters={**COUNTERS, **counters})


class TestTheReport:
    def test_silent_sites_are_counted_apart_from_not_found(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        contact_search._print_report(_report())
        said = capsys.readouterr().out
        assert "Без контакта:          2" in said
        assert "Сайт не ответил:       3 — повторим" in said

    def test_disabled_rdap_names_itself(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """«вошло 0» читалось как «работала и не нашла», а она не работала вовсе."""
        monkeypatch.setattr(contact_search.cfg, "RDAP_ENABLED", False)
        contact_search._print_report(_report())
        assert "выключена настройкой (CONTACTS_RDAP_ENABLED)" in capsys.readouterr().out

    def test_enabled_rdap_is_counted(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(contact_search.cfg, "RDAP_ENABLED", True)
        contact_search._print_report(_report(rdap_entered=4, rdap_found=1, rdap_failed=2))
        assert "2. RDAP              вошло 4, нашли 1, не ответил 2" in capsys.readouterr().out

    def test_paid_refusals_are_loud(self, capsys: pytest.CaptureFixture[str]) -> None:
        contact_search._print_report(_report(provider_refused=2))
        assert "ОТКАЗАЛ 2" in capsys.readouterr().out


class TestTheCommand:
    @pytest.fixture
    def searched(self, monkeypatch: pytest.MonkeyPatch) -> list[SearchReport]:
        """Поиск подменён: команда проверяется на печати, а не на лестнице."""
        reports: list[SearchReport] = []

        async def search(_session: object, **_kwargs: object) -> SearchReport:
            return reports.pop(0)

        monkeypatch.setattr(contact_search, "search_contacts", search)
        monkeypatch.setattr(contact_search, "check_storage", lambda: None)
        return reports

    @staticmethod
    def _args() -> argparse.Namespace:
        return argparse.Namespace(limit=10, browser=False, paid_first=False, no_paid=False)

    async def test_nothing_to_do_is_said(
        self, searched: list[SearchReport], capsys: pytest.CaptureFixture[str]
    ) -> None:
        searched.append(SearchReport())
        assert await contact_search.cmd_contacts(self._args()) == 0
        assert "Доноров, которым нужен контакт, нет" in capsys.readouterr().out

    async def test_a_pass_prints_its_notes_and_report(
        self, searched: list[SearchReport], capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = _report()
        report.notes.append("Платный сервис не настроен — идём по бесплатным ступеням.")
        searched.append(report)
        assert await contact_search.cmd_contacts(self._args()) == 0
        said = capsys.readouterr().out
        assert "Взято в работу: 10 донор(ов) без контакта" in said
        assert "Платный сервис не настроен" in said
        assert "Сайт не ответил:       3" in said
