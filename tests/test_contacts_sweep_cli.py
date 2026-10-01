"""Команда `contacts-file`: чей итог и куда он пишется.

Ревью #118, находка 15: CSV собирался из всего чекпойнта, а не из доменов
текущего файла, а пути по умолчанию брались из `source.with_suffix(...)` —
`list.csv` и `list.txt` делили один чекпойнт и один итог. Второй прогон
молча дописывал в свой файл чужие домены, а первый итог затирался.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import signal
from pathlib import Path

import pytest
from backend.cli.contact_sweep import EXIT_BAD_INPUT, EXIT_OK, cmd_contacts_file
from backend.cli.main import EXIT_CANCELLED, main
from backend.features.contacts import file_sweep
from backend.features.contacts.ladder import ContactLadder, LadderResult
from tests.contacts_sweep_fakes import FakeRenderer, Web, cli_args, install, page, write

SITES = {
    "site.com": {"/": page('<a href="mailto:ads@site.com">почта</a>')},
    "shop.de": {"/": page('<a href="mailto:info@shop.de">почта</a>')},
    "news.org": {"/": page('<a href="mailto:desk@news.org">почта</a>')},
}


def _hosts(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [row["host"] for row in csv.DictReader(handle, delimiter=";")]


def _results(folder: Path) -> list[list[str]]:
    """Домены каждого итога в папке — сколько бы их ни было и как бы ни назывались."""
    return sorted(_hosts(path) for path in folder.glob("*contacts*.csv"))


class TestWhoseResult:
    async def test_same_name_other_extension_keeps_its_own_progress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, Web(SITES))
        first = write(tmp_path / "list.csv", "host\nsite.com\n")
        second = write(tmp_path / "list.txt", "shop.de\n")

        assert await cmd_contacts_file(cli_args(first)) == EXIT_OK
        assert await cmd_contacts_file(cli_args(second)) == EXIT_OK

        assert _results(tmp_path) == [["shop.de"], ["site.com"]]

    async def test_shared_checkpoint_gives_each_file_only_its_domains(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, Web(SITES))
        shared = tmp_path / "shared.jsonl"
        first = write(tmp_path / "a.csv", "host\nsite.com\n")
        second = write(tmp_path / "b.csv", "host\nshop.de\n")

        for source in (first, second):
            args = cli_args(source, "--checkpoint", str(shared), "--out", f"{source}.out")
            assert await cmd_contacts_file(args) == EXIT_OK

        assert _hosts(Path(f"{second}.out")) == ["shop.de"]
        assert _hosts(Path(f"{first}.out")) == ["site.com"]

    async def test_probe_with_a_limit_keeps_the_walked_rest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--limit` ограничивает обход, а не итог: проба не затирает пройденное."""
        install(monkeypatch, Web(SITES))
        source = write(tmp_path / "list.csv", "host\nsite.com\nshop.de\nnews.org\n")
        out = tmp_path / "out.csv"
        assert await cmd_contacts_file(cli_args(source, "--out", str(out))) == EXIT_OK

        limited = cli_args(source, "--out", str(out), "--limit", "1")
        assert await cmd_contacts_file(limited) == EXIT_OK

        assert _hosts(out) == ["site.com", "shop.de", "news.org"]

    async def test_rows_follow_the_order_of_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Чекпойнт пишется в порядке окончания, итог читают рядом со списком."""
        install(monkeypatch, Web(SITES))
        source = write(tmp_path / "list.csv", "host\nsite.com\nshop.de\nnews.org\n")
        checkpoint = tmp_path / "c.jsonl"
        earlier = {"host": "news.org", "status": "not_found"}
        checkpoint.write_text(json.dumps({"host": "news.org", "row": earlier}) + "\n")
        out = tmp_path / "out.csv"

        args = cli_args(source, "--checkpoint", str(checkpoint), "--out", str(out))
        assert await cmd_contacts_file(args) == EXIT_OK

        assert _hosts(out) == ["site.com", "shop.de", "news.org"]


class TestCheckpointOfTheOldName:
    """Ревью #130: имена по умолчанию сменились (`list.checkpoint.jsonl` →
    `list.csv.checkpoint.jsonl`), и прогон, начатый прежней версией, после
    выкатки пошёл бы с нуля молча. Прежний чекпойнт подхватывается — строкой."""

    LEGACY = '{"host": "site.com", "row": {"host": "site.com", "status": "found"}}\n'

    async def test_run_started_before_the_rename_goes_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        web = Web(SITES)
        install(monkeypatch, web)
        source = write(tmp_path / "list.csv", "host\nsite.com\nshop.de\n")
        legacy = tmp_path / "list.checkpoint.jsonl"
        legacy.write_text(self.LEGACY)

        assert await cmd_contacts_file(cli_args(source)) == EXIT_OK

        assert web.requested, "прогон не пошёл вовсе"
        assert not [url for url in web.requested if "site.com" in url], "пройденное пошло снова"
        assert f"Чекпойнт прежнего имени: {legacy}" in capsys.readouterr().out
        assert file_sweep.done_hosts(legacy) == {"site.com", "shop.de"}

    async def test_restart_starts_under_the_new_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        web = Web(SITES)
        install(monkeypatch, web)
        source = write(tmp_path / "list.csv", "host\nsite.com\n")
        legacy = tmp_path / "list.checkpoint.jsonl"
        legacy.write_text(self.LEGACY)

        assert await cmd_contacts_file(cli_args(source, "--restart")) == EXIT_OK

        assert [url for url in web.requested if "site.com" in url]
        assert legacy.read_text() == self.LEGACY
        assert file_sweep.done_hosts(tmp_path / "list.csv.checkpoint.jsonl") == {"site.com"}


class TestRestart:
    async def test_bad_input_does_not_cost_the_progress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--restart` с файлом, который не читается, — отказ, а не стёртый чекпойнт."""
        install(monkeypatch, Web(SITES))
        source = write(tmp_path / "list.csv", "площадка;dr\nsite.com;5\n")
        checkpoint = tmp_path / "c.jsonl"
        progress = '{"host": "site.com", "row": {"host": "site.com", "status": "found"}}\n'
        checkpoint.write_text(progress)

        args = cli_args(source, "--checkpoint", str(checkpoint), "--restart")
        assert await cmd_contacts_file(args) == EXIT_BAD_INPUT

        assert checkpoint.read_text() == progress


class TestPathsAreCheckedBeforeTheWalk:
    """Неверный путь всплывал через часы — на записи итога или первой строки."""

    @pytest.mark.parametrize("flag", ["--out", "--checkpoint"])
    async def test_missing_folder(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        flag: str,
    ) -> None:
        web = Web(SITES)
        install(monkeypatch, web)
        source = write(tmp_path / "list.csv", "host\nsite.com\n")
        missing = tmp_path / "нет-такой-папки" / "file"

        assert await cmd_contacts_file(cli_args(source, flag, str(missing))) == EXIT_BAD_INPUT

        assert web.requested == []
        assert "нет-такой-папки" in capsys.readouterr().out

    async def test_result_over_the_list_itself(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--out` на сам список затёр бы его итогом."""
        web = Web(SITES)
        install(monkeypatch, web)
        source = write(tmp_path / "list.csv", "host\nsite.com\n")

        assert await cmd_contacts_file(cli_args(source, "--out", str(source))) == EXIT_BAD_INPUT

        assert web.requested == []
        assert source.read_text() == "host\nsite.com\n"


class TestInterrupt:
    def test_ctrl_c_leaves_the_result_of_what_was_walked(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Ctrl-C посреди прогона: итог по пройденному на диске и ни слова о базе.

        Через настоящую точку входа и настоящий сигнал: отмену задачи
        доставляет сам `asyncio.run`, и проверяется ровно его путь.
        """
        install(monkeypatch, Web(SITES))
        monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)
        real_find = ContactLadder.find

        async def interrupted(ladder: ContactLadder, host: str) -> LadderResult:
            if host == "shop.de":
                os.kill(os.getpid(), signal.SIGINT)
                await asyncio.sleep(5)  # отмена приходит на этом ожидании
            return await real_find(ladder, host)

        monkeypatch.setattr(ContactLadder, "find", interrupted)
        source = write(tmp_path / "list.csv", "host\nsite.com\nshop.de\nnews.org\n")
        out = tmp_path / "out.csv"

        code = main(["contacts-file", str(source), "--out", str(out), "--concurrency", "1"])

        printed = capsys.readouterr()
        assert code == EXIT_CANCELLED
        assert _hosts(out) == ["site.com"]
        assert "в базе" not in printed.out + printed.err


class TestBrowserOnlyWhenAsked:
    async def test_no_browser_is_started_without_the_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Chromium — секунды запуска и сотни мегабайт: без просьбы он не поднимается."""
        browser = install(monkeypatch, Web(SITES), renderer=FakeRenderer())
        source = write(tmp_path / "list.csv", "host\nsite.com\n")

        assert await cmd_contacts_file(cli_args(source)) == EXIT_OK

        assert browser.started == []

    async def test_setting_turns_it_on_like_for_the_database_search(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        renderer = FakeRenderer({"https://site.com/": page("закрыто для запросов")})
        install(monkeypatch, Web({"site.com": 403}), renderer=renderer)
        monkeypatch.setattr("backend.config.contacts.BROWSER_ENABLED", True)
        source = write(tmp_path / "list.csv", "host\nsite.com\n")

        assert await cmd_contacts_file(cli_args(source)) == EXIT_OK

        assert renderer.opened

    async def test_browser_that_did_not_start_is_said(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        install(monkeypatch, Web(SITES), renderer=None)
        source = write(tmp_path / "list.csv", "host\nsite.com\n")

        assert await cmd_contacts_file(cli_args(source, "--browser")) == EXIT_OK

        assert "Браузер не поднялся" in capsys.readouterr().out


class TestNoManualQueueInAFileRun:
    async def test_form_only_domains_do_not_wait_for_next_month(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Ручной очереди у прогона по файлу нет: форма — колонка, а не заявка.

        Потолок месяца исчерпан (здесь — нулевой), и до правки каждый домен
        с формой давал в лог «ждёт следующего месяца» — про домены, которые
        ничего не ждут.
        """
        monkeypatch.setattr("backend.config.contacts.MANUAL_QUEUE_MONTHLY_CAP", 0)
        caplog.set_level(logging.INFO, logger="backend.features.contacts")
        form = page('<form class="wpcf7"><input name="email"></form>')
        install(monkeypatch, Web({"site.com": {"/": form}}))
        checkpoint = tmp_path / "c.jsonl"

        await file_sweep.sweep(["site.com"], checkpoint=checkpoint)

        (row,) = file_sweep.rows_from_checkpoint(checkpoint)
        assert (row["status"], row["has_form"]) == ("form_only", "true")
        assert not [record for record in caplog.records if "ручной очереди" in record.getMessage()]


class TestNumbersSurviveExcel:
    def test_digits_only_cell_is_written_as_text(self, tmp_path: Path) -> None:
        """Excel читает `0612345678` как 612345678, а `79161234567890` — как 7,92E+13.

        Ячейка из одних цифр уходит формулой-строкой `="…"`: текст и в Excel,
        и в Google Таблицах. Ячейка с несколькими номерами и так текст.
        """
        out = tmp_path / "out.csv"
        row = {
            "host": "site.com",
            "phone": "0612345678",
            "whatsapp": "79161234567890",
            "viber": "79161234567; 79031234567",
            "telegram": "sitedesk",
        }

        file_sweep.write_csv([row], out)

        with out.open(encoding="utf-8-sig", newline="") as handle:
            (cells,) = list(csv.DictReader(handle, delimiter=";"))
        assert cells["phone"] == '="0612345678"'
        assert cells["whatsapp"] == '="79161234567890"'
        assert cells["viber"] == "79161234567; 79031234567"
        assert (cells["host"], cells["telegram"]) == ("site.com", "sitedesk")
