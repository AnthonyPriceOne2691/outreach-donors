"""Команда `contacts-file`: чей итог и куда он пишется.

Ревью #118, находка 15: CSV собирался из всего чекпойнта, а не из доменов
текущего файла, а пути по умолчанию брались из `source.with_suffix(...)` —
`list.csv` и `list.txt` делили один чекпойнт и один итог. Второй прогон
молча дописывал в свой файл чужие домены, а первый итог затирался.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from backend.cli.contact_sweep import EXIT_BAD_INPUT, EXIT_OK, cmd_contacts_file
from tests.contacts_sweep_fakes import Web, cli_args, install, page, write

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
