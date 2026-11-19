"""Восстановление и права копий: `scripts/restore.sh`, `scripts/backup.sh` (аудит 19.11.2026).

Скрипты идут настоящим bash с подставным `docker` (`tests/ops_fakes.py`). Вопросы теста:
- копия нынешней базы снимается до остановки пишущих — в свой каталог, вне ротации бэкапов;
  не снялась — не тронуто ничего; битая база — только с явным `--without-current-copy`;
- дамп разворачивается одной транзакцией, и упавший pg_restore оставляет базу прежней —
  а скрипт называет шаг и команду, которой поднять остановленных;
- копии на машине читает только владелец: дамп — та же база контактов и переписки.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from tests.ops_fakes import Host

DUMP = "PGDMP-копия, из которой восстанавливают"

#: Удачный проход целиком: копия нынешней базы и её схема, остановка, восстановление,
#: миграции, запуск и две строки итога.
WHOLE = [
    "compose exec pg_dump",
    "compose exec psql",
    "compose stop",
    "compose exec pg_restore",
    "compose run",
    "compose start",
    "compose exec psql",
    "compose exec psql",
]


@pytest.fixture
def host(tmp_path: Path) -> Host:
    machine = Host(tmp_path).install()
    machine.write_settings(".env", "")
    machine.write_settings(".env.ops", "")
    machine.env["BEFORE_RESTORE_DIR"] = str(tmp_path / "до восстановления")
    return machine


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Каталог копии, из которой восстанавливают, — как его оставляет `offsite.sh fetch`."""
    folder = tmp_path / "скачанные копии" / "2026-11-01-030000"
    folder.mkdir(parents=True)
    (folder / "outreach.dump").write_text(DUMP, encoding="utf-8")
    (folder / "alembic_version.txt").write_text("5946299ccd9a\n", encoding="utf-8")
    return folder


def _steps(host: Host) -> list[str]:
    return [str(call["key"]) for call in host.calls("docker")]


def _before(host: Host) -> list[Path]:
    folder = Path(host.env["BEFORE_RESTORE_DIR"])
    return sorted(folder.iterdir()) if folder.exists() else []


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestRestore:
    def test_without_yes_nothing_is_touched_and_the_plan_is_said(
        self, host: Host, source: Path
    ) -> None:
        done = host.run("restore.sh", str(source))

        assert done.returncode == 0, done.stderr
        assert host.calls("docker") == []
        assert "0. снята копия нынешней базы" in done.stdout
        assert "одной транзакцией" in done.stdout

    def test_the_current_base_is_copied_before_the_writers_stop(
        self, host: Host, source: Path
    ) -> None:
        done = host.run("restore.sh", str(source), "--yes")

        assert done.returncode == 0, done.stderr
        assert _steps(host) == WHOLE
        [copy] = _before(host)
        assert (copy / "outreach.dump").read_text(encoding="utf-8").startswith("PGDMP")
        assert (copy / "alembic_version.txt").read_text(encoding="utf-8").strip()
        assert not copy.is_relative_to(source.parent), "копия — вне каталога копий и его ротации"
        assert f"scripts/restore.sh {copy} --yes" in done.stdout, "как вернуть — словами"

    def test_the_dump_goes_in_as_one_transaction(self, host: Host, source: Path) -> None:
        done = host.run("restore.sh", str(source), "--yes")

        assert done.returncode == 0, done.stderr
        [restore] = [c for c in host.calls("docker") if c["key"] == "compose exec pg_restore"]
        assert {"--clean", "--if-exists", "--single-transaction"} <= set(restore["argv"])
        assert restore["stdin"] == DUMP

    def test_a_relative_path_is_read_from_where_it_was_named(
        self, host: Host, source: Path
    ) -> None:
        """Скрипт уходит в корень проекта, и относительный путь до этого указывал бы
        на другой каталог."""
        done = host.run("restore.sh", str(source.relative_to(host.root)), "--yes")

        assert done.returncode == 0, done.stderr
        [restore] = [c for c in host.calls("docker") if c["key"] == "compose exec pg_restore"]
        assert restore["stdin"] == DUMP

    def test_the_copy_is_readable_by_its_owner_only(self, host: Host, source: Path) -> None:
        host.run("restore.sh", str(source), "--yes")

        [copy] = _before(host)
        assert _mode(copy) == 0o700
        assert {_mode(file) for file in copy.iterdir()} == {0o600}

    @pytest.mark.parametrize(
        "answer",
        [
            {"code": 1, "err": "pg_dump: error: could not read block 7 in file\n"},
            {"code": 0, "out": "Error: no such service: postgres\n"},
        ],
        ids=["дамп упал", "не архив pg_dump"],
    )
    def test_no_copy_means_nothing_is_touched(
        self, host: Host, source: Path, answer: dict[str, object]
    ) -> None:
        host.scenario["compose exec pg_dump"] = answer

        done = host.run("restore.sh", str(source), "--yes")

        assert done.returncode != 0
        assert _steps(host) == ["compose exec pg_dump"], "ни остановки, ни восстановления"
        assert _before(host) == [], "недоснятой копии не остаётся"
        assert "восстановление не начато" in done.stderr

    def test_a_broken_base_is_replaced_only_when_asked_by_name(
        self, host: Host, source: Path
    ) -> None:
        """Нынешняя база битая и дамп с неё не снимается: подсказка в отказе ведёт к флагу."""
        host.scenario["compose exec pg_dump"] = {"code": 1, "err": "pg_dump: error\n"}

        refused = host.run("restore.sh", str(source), "--yes")
        done = host.run("restore.sh", str(source), "--yes", "--without-current-copy")

        assert "--yes --without-current-copy" in refused.stderr
        assert done.returncode == 0, done.stderr
        assert _steps(host)[1:] == WHOLE[2:], "второй проход — без копии, остальное как всегда"

    @pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
    def test_a_failed_restore_says_the_base_stayed_and_how_to_start_again(
        self, host: Host, source: Path, locale: str
    ) -> None:
        """В двух локалях: у bash 3.2 в UTF-8 `$ИМЯ»` — другое имя (`test_backup_offsite`)."""
        host.locale = locale
        host.scenario["compose exec pg_restore"] = {"code": 1, "err": "pg_restore: error\n"}

        done = host.run("restore.sh", str(source), "--yes")

        assert done.returncode != 0
        assert "compose start" not in _steps(host), "остановленные ждут человека"
        assert "оборвалось на шаге «восстановление»" in done.stderr
        assert "база осталась прежней" in done.stderr
        [copy] = _before(host)
        assert str(copy) in done.stderr
        assert "Поднять остановленных: docker compose start api worker " in done.stderr

    def test_an_unknown_argument_is_refused_before_anything(self, host: Host, source: Path) -> None:
        done = host.run("restore.sh", str(source), "--yes", "--no-copy")

        assert done.returncode == 2
        assert host.calls("docker") == []
        assert "--no-copy" in done.stderr


def test_a_backup_is_readable_by_its_owner_only(host: Host, tmp_path: Path) -> None:
    """Каталог копии и её файлы — только владельцу: машина общая с соседней системой."""
    host.env["BACKUP_DIR"] = str(tmp_path / "копии")
    host.env["BACKUP_UPLOAD"] = ""

    done = host.run("backup.sh")

    assert done.returncode == 0, done.stderr
    [copy] = sorted(Path(host.env["BACKUP_DIR"]).iterdir())
    assert _mode(copy) == 0o700
    assert {_mode(file) for file in copy.iterdir()} == {0o600}
