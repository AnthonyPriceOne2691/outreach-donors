"""Чекпойнт прогона по файлу: рваный хвост стоит строки, а не прогона.

Процесс убивают посреди записи — Ctrl-C дважды, нехватка памяти, закрытый
ноутбук. Последняя строка чекпойнта остаётся недописанной, иногда посреди
многобайтного знака: строки пишутся с `ensure_ascii=False`. Ревью #118
нашло два следствия, и оба хуже, чем потеря одной строки:

1. следующая запись приклеивалась к обрывку и пропадала вместе с ним —
   уже пройденный домен шёл заново на каждом запуске;
2. обрыв посреди знака ронял чтение файла целиком (`UnicodeDecodeError`):
   каждое возобновление кончалось выходом 2, помогал только `--restart`,
   а он стирает весь прогресс.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from backend.cli.contact_sweep import EXIT_OK, cmd_contacts_file
from backend.features.contacts import file_sweep
from backend.features.contacts import sweep_checkpoint as file_sweep_checkpoint
from tests.contacts_sweep_fakes import Web, cli_args, install, page, write

SITES = {
    "site.com": {"/": page('<a href="mailto:ads@site.com">почта</a>')},
    "shop.de": {"/": page('<a href="mailto:info@shop.de">почта</a>')},
}


def _line(host: str) -> bytes:
    row = {"host": host, "status": "not_found", "rejected": "заглушка вместо адреса"}
    return json.dumps({"host": host, "row": row}, ensure_ascii=False).encode() + b"\n"


def _torn_inside_a_letter(path: Path) -> Path:
    """Чекпойнт, где запись последнего домена оборвана посреди буквы «п»."""
    tail = _line("пример.рф")
    cut = tail.index("п".encode()) + 1  # первый байт двухбайтной буквы
    path.write_bytes(_line("site.com") + tail[:cut])
    return path


class TestTornInsideALetter:
    def test_done_hosts_reads_what_is_whole(self, tmp_path: Path) -> None:
        checkpoint = _torn_inside_a_letter(tmp_path / "c.jsonl")
        assert file_sweep.done_hosts(checkpoint) == {"site.com"}

    def test_rows_are_read_up_to_the_tear(self, tmp_path: Path) -> None:
        checkpoint = _torn_inside_a_letter(tmp_path / "c.jsonl")
        assert [row["host"] for row in file_sweep.rows_from_checkpoint(checkpoint)] == ["site.com"]

    async def test_resume_goes_on_instead_of_exit_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, Web(SITES))
        source = write(tmp_path / "list.csv", "host\nsite.com\nshop.de\n")
        checkpoint = _torn_inside_a_letter(tmp_path / "c.jsonl")

        assert await cmd_contacts_file(cli_args(source, "--checkpoint", str(checkpoint))) == EXIT_OK
        assert file_sweep.done_hosts(checkpoint) == {"site.com", "shop.de"}


class TestNextLineAfterATear:
    async def test_next_record_is_not_glued_to_the_tear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Запись после обрывка начинается с новой строки, иначе пропадает с ним."""
        install(monkeypatch, Web(SITES))
        checkpoint = tmp_path / "c.jsonl"
        checkpoint.write_bytes(_line("site.com") + b'{"host": "half')

        await file_sweep.sweep(["shop.de"], checkpoint=checkpoint)

        assert file_sweep.done_hosts(checkpoint) == {"site.com", "shop.de"}

    async def test_whole_file_is_left_as_it_was(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Целый хвост лишним переводом строки не портится."""
        install(monkeypatch, Web(SITES))
        checkpoint = tmp_path / "c.jsonl"
        checkpoint.write_bytes(_line("site.com"))

        await file_sweep.sweep(["shop.de"], checkpoint=checkpoint)

        assert b"\n\n" not in checkpoint.read_bytes()
        assert file_sweep.done_hosts(checkpoint) == {"site.com", "shop.de"}


class TestWrittenToDisk:
    """Обещание «на диск» — правда: после строки чекпойнта вызывается fsync.

    `flush` отдаёт строку системе и спасает от убитого процесса; от потери
    питания спасает только `fsync`. Ревью #126 нашло, что докстринг обещал
    диск, а код останавливался на системе.
    """

    async def test_line_is_synced_after_it_is_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "list.checkpoint.jsonl"
        synced: list[str] = []
        real_fsync = file_sweep_checkpoint.os.fsync

        def spy(fd: int) -> None:
            synced.append(path.read_text(encoding="utf-8"))
            real_fsync(fd)

        monkeypatch.setattr(file_sweep_checkpoint.os, "fsync", spy)

        await file_sweep_checkpoint.Checkpoint(path).add("site.com", {"status": "found"})

        assert len(synced) == 1
        assert json.loads(synced[0])["host"] == "site.com"


class TestBrokenLineIsNamedOnce:
    """Ревью #127: чекпойнт читают несколько раз за запуск, и одна битая строка
    звучала предупреждением на каждое чтение. Теперь — одно предупреждение
    на строку, остальное ниже уровнем."""

    async def test_three_readers_one_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "list.checkpoint.jsonl"
        path.write_text(
            '{"host": "a.com", "row": {"status": "found"}}\n{"host": "b.c\n',
            encoding="utf-8",
        )
        caplog.set_level("DEBUG", logger="backend.features.contacts.sweep_checkpoint")

        file_sweep.done_hosts(path)
        file_sweep.rows_from_checkpoint(path)
        file_sweep_checkpoint.retry_counts(path)

        named = [r for r in caplog.records if "не разобрана" in r.getMessage()]
        assert [r.levelname for r in named] == ["WARNING", "DEBUG", "DEBUG"]
