"""Прогон по списку доменов из файла: главное здесь — переживание обрыва.

Прогон по нескольким тысячам доменов идёт часами и обрывается. Проверяется
поэтому не только «контакты нашлись», но и то, что повторный запуск не
проходит домены заново, а итоговый CSV полон и после продолжения. Собери
его из памяти процесса — и продолженный прогон отдал бы половину, причём
выглядело бы это как законный результат.
"""

from __future__ import annotations

import csv
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from backend.features.contacts import file_sweep, mx
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.messengers import FoundHandle, MessengerKind, Trust
from backend.features.contacts.quality import Candidate
from backend.features.core.domain import ContactSource, ContactStatus, PageKind

HOME_WITH_EMAIL = """
<html><body>
  <a href="mailto:ads@one.test">напишите</a>
  <a href="https://t.me/adsdesk">Telegram</a>
</body></html>
"""
HOME_ONLY_HANDLE = """
<html><body>
  <p>Почты нет, пишите @tgowner или в скайп: live:.cid.99</p>
  <a href="https://wa.me/79161234567">WhatsApp</a>
</body></html>
"""


def _site(pages: dict[str, str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.host or "")
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, html=body)

    return httpx.MockTransport(handler)


@pytest.fixture
def _pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Две площадки: у первой адрес, у второй только мессенджеры и нет MX."""
    transport = _site({"one.test": HOME_WITH_EMAIL, "two.test": HOME_ONLY_HANDLE})

    @asynccontextmanager
    async def client(**_kwargs: object) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=transport) as http:
            yield http

    @asynccontextmanager
    async def renderer() -> AsyncIterator[None]:
        yield None

    async def route(host: str, **_kwargs: object) -> mx.MailRoute:
        # У второй площадки почты нет вовсе — ровно случай, ради которого
        # прогон по файлу не останавливается на MX.
        return mx.MailRoute.MX if host == "one.test" else mx.MailRoute.NONE

    monkeypatch.setattr(file_sweep, "guarded_client", client)
    monkeypatch.setattr(file_sweep, "PlaywrightRenderer", renderer)
    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


class TestReadingTheList:
    def _write(self, path: Path, text: str) -> Path:
        path.write_text(text, encoding="utf-8")
        return path

    def test_our_export_with_semicolons(self, tmp_path: Path) -> None:
        source = self._write(
            tmp_path / "donors.csv",
            "host_key;dr\nhttps://WWW.News.example.com/path;40\ntwo.test;10\n",
        )
        assert file_sweep.read_hosts(source) == ["news.example.com", "two.test"]

    def test_foreign_export_with_commas(self, tmp_path: Path) -> None:
        source = self._write(tmp_path / "list.csv", "name,domain\nОдин,one.test\nДва,two.test\n")
        assert file_sweep.read_hosts(source) == ["one.test", "two.test"]

    def test_repeated_domain_is_taken_once(self, tmp_path: Path) -> None:
        source = self._write(
            tmp_path / "d.csv", "host\none.test\nwww.one.test\nhttp://one.test/x\n"
        )
        assert file_sweep.read_hosts(source) == ["one.test"]

    def test_unknown_column_says_what_is_in_the_file(self, tmp_path: Path) -> None:
        source = self._write(tmp_path / "d.csv", "площадка;dr\none.test;5\n")
        with pytest.raises(ValueError, match="площадка"):
            file_sweep.read_hosts(source)

    def test_column_can_be_named(self, tmp_path: Path) -> None:
        source = self._write(tmp_path / "d.csv", "площадка;dr\none.test;5\n")
        assert file_sweep.read_hosts(source, column="площадка") == ["one.test"]


class TestRowOfResult:
    def _result(self, *handles: FoundHandle) -> LadderResult:
        return LadderResult(
            host="one.test",
            status=ContactStatus.FOUND,
            contact=Candidate("ads@one.test", ContactSource.PAGE, PageKind.MONEY),
            candidates=(Candidate("ads@one.test", ContactSource.PAGE, PageKind.MONEY),),
            source=ContactSource.PAGE,
            handles=handles,
        )

    def test_explicit_and_guessed_do_not_mix(self) -> None:
        """Догадка не должна попасть в колонку, из которой шлют письма.

        `@nick` в подвале бывает аккаунтом в другой сети или просто
        обращением; выгрузка «всё, что нашли» иначе приведёт к переписке
        с чужими людьми.
        """
        row = file_sweep.row_of(
            self._result(
                FoundHandle(MessengerKind.TELEGRAM, "adsdesk", Trust.EXPLICIT, PageKind.MONEY),
                FoundHandle(MessengerKind.TELEGRAM, "maybe", Trust.GUESSED, PageKind.HOME),
            )
        )
        assert row["telegram"] == "adsdesk"
        assert row["guessed"] == "telegram:maybe"

    def test_page_of_the_handle_is_kept(self) -> None:
        row = file_sweep.row_of(
            self._result(
                FoundHandle(
                    MessengerKind.SKYPE,
                    "ads",
                    Trust.EXPLICIT,
                    PageKind.MONEY,
                    "https://x/advertise",
                )
            )
        )
        assert row["handles_page"] == "https://x/advertise"

    def test_empty_result_is_a_legal_row(self) -> None:
        row = file_sweep.row_of(LadderResult(host="x.test", status=ContactStatus.NOT_FOUND))
        assert row["host"] == "x.test"
        assert row["status"] == "not_found"
        assert row["email"] == ""
        assert row["telegram"] == ""


@pytest.mark.asyncio
@pytest.mark.usefixtures("_pages")
class TestSweep:
    async def test_walks_and_writes_both_kinds_of_contact(self, tmp_path: Path) -> None:
        checkpoint = tmp_path / "c.jsonl"
        report = await file_sweep.sweep(["one.test", "two.test"], checkpoint=checkpoint)
        assert report.walked == 2
        assert report.with_email == 1
        assert report.with_handle == 1

        rows = {row["host"]: row for row in file_sweep.rows_from_checkpoint(checkpoint)}
        assert rows["one.test"]["email"] == "ads@one.test"
        # Подвал с телеграмом стоит на каждой странице — в колонке он один.
        assert rows["one.test"]["telegram"] == "adsdesk"
        assert rows["two.test"]["whatsapp"] == "79161234567"

    async def test_domain_without_mail_is_still_walked(self, tmp_path: Path) -> None:
        """Ради этого прогон по файлу не останавливается на MX.

        MX отвечает на вопрос «дойдёт ли письмо», а телеграм в подвале от
        него не зависит. У поиска по базе поведение прежнее.
        """
        report = await file_sweep.sweep(["two.test"], checkpoint=tmp_path / "c.jsonl")
        assert report.walked == 1
        assert report.with_handle == 1

    async def test_second_run_does_not_walk_again(self, tmp_path: Path) -> None:
        checkpoint = tmp_path / "c.jsonl"
        await file_sweep.sweep(["one.test"], checkpoint=checkpoint)
        done = file_sweep.done_hosts(checkpoint)
        assert done == {"one.test"}

        pending = [host for host in ("one.test", "two.test") if host not in done]
        second = await file_sweep.sweep(pending, checkpoint=checkpoint)
        assert second.walked == 1

        rows = file_sweep.rows_from_checkpoint(checkpoint)
        assert [row["host"] for row in rows] == ["one.test", "two.test"]

    async def test_csv_is_built_from_the_checkpoint(self, tmp_path: Path) -> None:
        """Итог собирается из файла, а не из памяти: иначе продолженный
        прогон отдал бы только вторую половину, и это выглядело бы законно."""
        checkpoint = tmp_path / "c.jsonl"
        out = tmp_path / "out.csv"
        await file_sweep.sweep(["one.test"], checkpoint=checkpoint)
        await file_sweep.sweep(["two.test"], checkpoint=checkpoint)

        written = file_sweep.write_csv(file_sweep.rows_from_checkpoint(checkpoint), out)
        assert written == 2
        with out.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        assert [row["host"] for row in rows] == ["one.test", "two.test"]
        assert list(rows[0]) == list(file_sweep.OUTPUT_COLUMNS)

    async def test_broken_checkpoint_line_costs_one_domain_not_the_file(
        self, tmp_path: Path
    ) -> None:
        """Прогон убили на середине записи — остальное читается."""
        checkpoint = tmp_path / "c.jsonl"
        await file_sweep.sweep(["one.test"], checkpoint=checkpoint)
        with checkpoint.open("a", encoding="utf-8") as handle:
            handle.write('{"host": "half')
        assert file_sweep.done_hosts(checkpoint) == {"one.test"}

    async def test_falling_domain_leaves_no_line_and_returns_next_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Домен, на котором упали, не записан — значит будет пройден снова.

        Записать его «пройденным» значило бы похоронить домен молча.
        """
        checkpoint = tmp_path / "c.jsonl"

        async def boom(self: object, host: str) -> LadderResult:
            raise RuntimeError("сайт ответил чем-то невообразимым")

        monkeypatch.setattr("backend.features.contacts.ladder.ContactLadder.find", boom)
        report = await file_sweep.sweep(["one.test"], checkpoint=checkpoint)
        assert report.walked == 0
        assert file_sweep.done_hosts(checkpoint) == set()
