"""Прогон по файлу: неокончательный исход не записывается пройденным.

`PageFetcher` превращает обрыв связи, таймауты и отказы 401/403/429
в обычное «адреса нет». До правки прогон по файлу писал такой исход
в чекпойнт как окончательный, и легла связь — легли все домены этого
часа: возобновление печатало «уже пройдены» и не делало ни одного
запроса. Ревью #118, находка 5.

Проверяется поэтому не только «не записал пройденным», но и обратная
сторона: сайт, который открылся и честно ничего не показал, — пройден,
иначе прогон повторял бы его вечно.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from backend.cli.contact_sweep import EXIT_OK, cmd_contacts_file
from backend.features.contacts import file_sweep, mx
from tests.contacts_sweep_fakes import (
    SLOW,
    FakeRenderer,
    Moved,
    Web,
    cli_args,
    install,
    page,
    write,
)

WITH_EMAIL = page('<a href="mailto:ads@site.com">почта</a>')
NOTHING = page("ни адреса, ни ссылок")


async def _walk(web: Web, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """Пройти site.com и вернуть его строку итога."""
    install(monkeypatch, web)
    checkpoint = tmp_path / "c.jsonl"
    await file_sweep.sweep(["site.com"], checkpoint=checkpoint)
    (row,) = file_sweep.rows_from_checkpoint(checkpoint)
    row["done"] = "yes" if "site.com" in file_sweep.done_hosts(checkpoint) else "no"
    return row


class TestNotFinal:
    async def test_network_down_is_not_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, Web({}))
        checkpoint = tmp_path / "c.jsonl"

        report = await file_sweep.sweep(["site.com", "shop.de"], checkpoint=checkpoint)

        assert file_sweep.done_hosts(checkpoint) == set()
        assert {row["status"] for row in file_sweep.rows_from_checkpoint(checkpoint)} == {"retry"}
        assert report.retry == 2

    async def test_site_that_closed_the_door(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = await _walk(Web({"site.com": 403, "www.site.com": 403}), monkeypatch, tmp_path)
        assert (row["status"], row["done"]) == ("retry", "no")
        assert "403" in row["retry_reason"]

    async def test_timeout_after_the_home_opened(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Обход оборван на середине: адрес мог быть на странице, которой не дождались."""
        site = {"/": page('<a href="/contact/">Контакты</a>'), "/contact/": SLOW}
        row = await _walk(Web({"site.com": site}), monkeypatch, tmp_path)
        assert (row["status"], row["done"]) == ("retry", "no")
        assert "/contact/" in row["retry_reason"]

    async def test_browser_that_saw_nothing_does_not_close_the_question(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, Web({"site.com": 403}), renderer=FakeRenderer())
        checkpoint = tmp_path / "c.jsonl"
        await file_sweep.sweep(["site.com"], checkpoint=checkpoint, use_browser=True)
        assert file_sweep.done_hosts(checkpoint) == set()


class TestFinal:
    """Обратная сторона: что пройдено, то пройдено — иначе повтор вечный."""

    async def test_open_site_without_contacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = await _walk(Web({"site.com": {"/": NOTHING}}), monkeypatch, tmp_path)
        assert (row["status"], row["done"]) == ("not_found", "yes")
        assert row["retry_reason"] == ""

    async def test_apex_without_a_record_but_www_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отказ первого вида главной — это поиск рабочего вида, а не обрыв обхода."""
        row = await _walk(Web({"www.site.com": {"/": NOTHING}}), monkeypatch, tmp_path)
        assert (row["status"], row["done"]) == ("not_found", "yes")

    async def test_home_that_answers_404(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Сервер ответил по существу — повтор ответа не изменит."""
        row = await _walk(Web({"site.com": {}, "www.site.com": {}}), monkeypatch, tmp_path)
        assert row["done"] == "yes"

    async def test_home_that_is_not_a_page_then_dead_www(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2xx без HTML лестница не читает и пробует `www.` — его отказ не обрыв обхода."""
        web = Web({"site.com": {"/": 200}})
        row = await _walk(web, monkeypatch, tmp_path)
        assert row["done"] == "yes"

    async def test_found_address_even_if_a_page_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        site = {"/": page('<a href="mailto:ads@site.com">a</a><a href="/about/">О нас</a>')}
        row = await _walk(Web({"site.com": {**site, "/about/": SLOW}}), monkeypatch, tmp_path)
        assert (row["status"], row["done"]) == ("found", "yes")

    async def test_site_moved_to_another_domain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Обход идёт по новому домену, и его ответы — ответы этого сайта."""
        web = Web({"site.com": {"/": Moved("https://newsite.com/")}, "newsite.com": {"/": NOTHING}})
        row = await _walk(web, monkeypatch, tmp_path)
        assert (row["status"], row["done"]) == ("not_found", "yes")

    async def test_closed_site_opened_by_the_browser(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        renderer = FakeRenderer({"https://site.com/": NOTHING})
        install(monkeypatch, Web({"site.com": 403}), renderer=renderer)
        checkpoint = tmp_path / "c.jsonl"
        await file_sweep.sweep(["site.com"], checkpoint=checkpoint, use_browser=True)
        assert file_sweep.done_hosts(checkpoint) == {"site.com"}


class TestResume:
    async def test_after_the_network_is_back_the_domain_is_walked_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ровно сценарий ревью: обрыв, восстановление, возобновление."""
        web = Web({})
        install(monkeypatch, web)
        source = write(tmp_path / "list.csv", "host\nsite.com\n")
        out = tmp_path / "out.csv"
        assert await cmd_contacts_file(cli_args(source, "--out", str(out))) == EXIT_OK

        web.sites["site.com"] = {"/": WITH_EMAIL}
        web.requested.clear()
        assert await cmd_contacts_file(cli_args(source, "--out", str(out))) == EXIT_OK

        assert web.requested, "возобновление не сделало ни одного запроса"
        with out.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        assert [(row["host"], row["status"], row["email"]) for row in rows] == [
            ("site.com", "found", "ads@site.com")
        ]


class TestNameThatDoesNotExist:
    """Найдено живым прогоном: домена нет — повтор ответа не изменит.

    «Имени нет» (MX — `none`) — ответ работающего DNS; при обрыве связи
    ступень MX говорит «неизвестно». Поэтому мёртвое имя, не ответившее
    по HTTP ни разу, пройдено, а не повторяется на каждом запуске вечно.
    """

    async def test_dead_name_is_done(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        install(monkeypatch, Web({}), routes={"site.com": mx.MailRoute.NONE})
        checkpoint = tmp_path / "c.jsonl"
        await file_sweep.sweep(["site.com"], checkpoint=checkpoint)
        (row,) = file_sweep.rows_from_checkpoint(checkpoint)
        assert (row["status"], row["mail_route"]) == ("not_found", "none")
        assert file_sweep.done_hosts(checkpoint) == {"site.com"}

    async def test_unknown_dns_with_silent_site_is_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DNS молчит и сайт молчит — это похоже на нашу связь, а не на их домен."""
        install(monkeypatch, Web({}), routes={"site.com": mx.MailRoute.UNKNOWN})
        checkpoint = tmp_path / "c.jsonl"
        await file_sweep.sweep(["site.com"], checkpoint=checkpoint)
        assert file_sweep.done_hosts(checkpoint) == set()

    async def test_name_without_mail_whose_www_refuses_is_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Апекс без записей, а `www.` отвечает 403: сайт есть, он закрылся."""
        web = Web({"www.site.com": 403})
        install(monkeypatch, web, routes={"site.com": mx.MailRoute.NONE})
        checkpoint = tmp_path / "c.jsonl"
        await file_sweep.sweep(["site.com"], checkpoint=checkpoint)
        assert file_sweep.done_hosts(checkpoint) == set()
