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

import httpx
import pytest
from backend.cli.contact_sweep import EXIT_NO_NETWORK, EXIT_OK, cmd_contacts_file
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


class TestNothingOpenedAndSomethingFailed:
    """Ревью #126: ответ без страницы засчитывается, только если не отказал ни
    один вид главной. «Апекс 404, `www` молчит» — ещё не ответ: `www` мог
    открыться при повторе. Цена — предел попыток (`TestGiveUp`)."""

    async def test_apex_404_and_www_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = await _walk(Web({"site.com": {}, "www.site.com": SLOW}), monkeypatch, tmp_path)
        assert (row["status"], row["done"]) == ("retry", "no")
        assert "нет ответа" in row["retry_reason"]

    async def test_home_that_is_not_a_page_and_www_down(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2xx без HTML лестница не читает и пробует `www.` — а тот не ответил."""
        row = await _walk(Web({"site.com": {"/": 200}}), monkeypatch, tmp_path)
        assert (row["status"], row["done"]) == ("retry", "no")


class TestGiveUp:
    """Повтор ограничен: на третий раз «повторить» записывается «недоступен».

    Предела не было, и сайт, который не отвечает никогда, шёл заново на каждом
    возобновлении вечно. Пройденным он становится с причиной — это не «адреса
    нет», а «проверить не удалось». Считаются только попытки при живой сети:
    сайт мёртв, а контрольные адреса отвечают (`TestNoNetwork` — обратное).
    """

    async def test_third_retry_becomes_unreachable_and_stops(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        web = Web({})
        install(monkeypatch, web)
        checkpoint = tmp_path / "c.jsonl"
        for _ in range(file_sweep.MAX_ATTEMPTS):
            assert "site.com" not in file_sweep.done_hosts(checkpoint)
            await file_sweep.sweep(["site.com"], checkpoint=checkpoint)

        (row,) = file_sweep.rows_from_checkpoint(checkpoint)
        assert row["status"] == file_sweep.UNREACHABLE
        assert row["retry_reason"].startswith(f"сдались после {file_sweep.MAX_ATTEMPTS} попыток")
        assert "site.com" in file_sweep.done_hosts(checkpoint)
        assert "site.com" not in file_sweep.retry_hosts(checkpoint)

    async def test_attempts_survive_a_new_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Счёт попыток — по чекпойнту, а не по памяти процесса: каждый запуск
        CLI — новый процесс, и без этого предел не наступил бы никогда."""
        web = Web({})
        install(monkeypatch, web)
        source = write(tmp_path / "list.csv", "host\nsite.com\n")
        out = tmp_path / "out.csv"
        for _ in range(file_sweep.MAX_ATTEMPTS):
            assert await cmd_contacts_file(cli_args(source, "--out", str(out))) == EXIT_OK

        web.requested.clear()
        assert await cmd_contacts_file(cli_args(source, "--out", str(out))) == EXIT_OK
        assert not web.requested, "сдавшийся домен пошёл снова"
        assert web.probes, "сеть перед прогоном не проверялась"
        with out.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        assert [row["status"] for row in rows] == [file_sweep.UNREACHABLE]


class TestNoNetwork:
    """Ревью #128: попытка без сети — не попытка.

    Ноутбук без VPN или закрытый выход сервера — и предел попыток за три
    запуска похоронил бы весь список. Сети нет на старте — прогон не
    начинается; пропала посреди — останавливается, а домен, не ответивший
    ничем, в чекпойнт не пишется.
    """

    async def test_three_runs_without_network_burn_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        web = Web({})
        web.network = False
        install(monkeypatch, web)
        source = write(tmp_path / "list.csv", "host\nsite.com\n")
        out = tmp_path / "out.csv"
        checkpoint = tmp_path / "progress.jsonl"
        args = ("--out", str(out), "--checkpoint", str(checkpoint))
        for _ in range(file_sweep.MAX_ATTEMPTS):
            assert await cmd_contacts_file(cli_args(source, *args)) == EXIT_NO_NETWORK
        assert not web.requested, "без сети прогон всё же пошёл по сайтам"
        assert file_sweep.rows_from_checkpoint(checkpoint) == []

        web.network = True
        web.sites["site.com"] = {"/": WITH_EMAIL}
        assert await cmd_contacts_file(cli_args(source, *args)) == EXIT_OK
        (row,) = file_sweep.rows_from_checkpoint(checkpoint)
        assert (row["status"], row["email"]) == ("found", "ads@site.com")

    async def test_network_lost_mid_run_writes_nothing_for_the_silent_domain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class DropsAfterFirstSite(Web):
            def __call__(self, request: httpx.Request) -> httpx.Response:
                response = super().__call__(request)
                if request.url.host == "a.com":
                    self.network = False
                return response

        web = DropsAfterFirstSite({"a.com": {"/": WITH_EMAIL}, "b.com": {"/": WITH_EMAIL}})
        install(monkeypatch, web)
        checkpoint = tmp_path / "c.jsonl"

        with pytest.raises(file_sweep.NetworkDownError) as lost:
            await file_sweep.sweep(["a.com", "b.com"], checkpoint=checkpoint, concurrency=1)

        assert file_sweep.done_hosts(checkpoint) == {"a.com"}
        assert [r["host"] for r in file_sweep.rows_from_checkpoint(checkpoint)] == ["a.com"]
        assert lost.value.report is not None
        assert lost.value.report.walked == 1


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
