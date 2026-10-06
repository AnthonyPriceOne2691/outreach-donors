"""Консольный замер обхода (`outreach crawl`): что видит человек и что ложится в базу.

Прибор ходит по живым сайтам; здесь сайты поддельные, а печать, запись
и коды выхода — настоящие. Код выхода важен скрипту, который зовёт прибор:
«обошли и ничего не нашли» и «нас не пустили» — разные ответы.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.cli import crawl_probe
from backend.config import crawl as crawl_cfg
from backend.config import storage
from backend.features.core.domain import CrawlOutcome, StopReason
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl.links import OutLink
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.robots import RobotsStatus
from sqlalchemy import select
from tests.conftest import TEST_DSN
from tests.test_crawl_progress import MAP_ROBOTS, _article
from tests.test_crawl_walk import HOST, FakeSite, _page, _urlset
from tests.test_send_race import committed_sessions

DEAD = "dead.example.test"


@pytest.fixture(autouse=True)
def _wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(crawl_cfg, "DELAY_SEC", 0.0)
    monkeypatch.setattr(crawl_cfg, "BROWSER_ENABLED", False)


def _web(monkeypatch: pytest.MonkeyPatch) -> FakeSite:
    """Живой донор с картой и мёртвый, у которого не читается даже robots.txt."""
    site = FakeSite(
        {
            "/": _page("https://home-adv.example/"),
            "/p0": _article("https://adv.example/offer"),
            "/p1": _article("https://adv.example/other", "https://other-adv.example/"),
        },
        robots=MAP_ROBOTS,
        sitemap=_urlset(f"https://{HOST}/p0", f"https://{HOST}/p1"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == DEAD:
            return httpx.Response(503, text="лежит")
        return site.handler(request)

    def client(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(crawl_probe, "guarded_client", client)
    return site


def _args(**overrides: Any) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    crawl_probe.add_parser(parser.add_subparsers(dest="command"))
    values = vars(parser.parse_args(["crawl"]))
    values.update(overrides)
    return argparse.Namespace(**values)


async def test_measure_prints_saves_and_writes_the_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _web(monkeypatch)
    out_file = tmp_path / "замер.json"

    async with committed_sessions() as factory:
        code = await crawl_probe.cmd_crawl(_args(domains=[HOST, DEAD], save=True, json=out_file))
        async with factory() as session:
            runs = (await session.execute(select(CrawlRunModel))).scalars().all()

    out = capsys.readouterr().out
    assert code == 0
    assert f"=== {HOST} — обойдён" in out
    assert "список страниц: sitemap (дочитан: да)" in out
    assert "внешних ссылок: 4 на 3 доменов" in out
    assert f"=== {DEAD} — не открылся вовсе" in out
    assert "Доля закрытых страниц: 0.0%" in out
    assert "Кому доноры ставят ссылки" in out
    assert f"не открылся вовсе — это поломка, а не защита: {DEAD}" in out
    assert "Записано проходов: 2" in out
    assert sorted((run.host, run.outcome) for run in runs) == [
        (DEAD, CrawlOutcome.FAILED),
        (HOST, CrawlOutcome.OK),
    ]
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert [item["host"] for item in saved] == [HOST, DEAD]


async def test_nothing_opened_is_not_a_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _web(monkeypatch)

    code = await crawl_probe.cmd_crawl(_args(domains=[DEAD]))

    assert code == crawl_probe.EXIT_NOTHING_CRAWLED
    # robots.txt — служебный запрос, страниц не открыто ни одной: доли нет.
    assert "Ни одного запроса не сделано" in capsys.readouterr().out


async def test_hand_list_and_base_choice_argue(capsys: pytest.CaptureFixture[str]) -> None:
    code = await crawl_probe.cmd_crawl(_args(domains=[HOST], from_base=3))

    assert code == crawl_probe.EXIT_NOTHING_CRAWLED
    assert "Либо домены списком, либо `--from-base N`" in capsys.readouterr().out


async def test_empty_base_says_what_to_do_first(capsys: pytest.CaptureFixture[str]) -> None:
    async with committed_sessions():
        code = await crawl_probe.cmd_crawl(_args(from_base=2))

    out = capsys.readouterr().out
    assert code == crawl_probe.EXIT_NOTHING_CRAWLED
    assert "Отбор доноров: к обходу: 0" in out
    assert "сначала прогон Этапа 1" in out


def test_report_names_the_slow_site_arithmetic(capsys: pytest.CaptureFixture[str]) -> None:
    """Пауза сайта × страницы упирается в срок — прибор считает это за человека."""
    report = CrawlReport(
        host=HOST,
        outcome=CrawlOutcome.PARTIAL,
        stop_reason=StopReason.TIMEOUT,
        pages=[f"https://{HOST}/"],
        links=[
            OutLink(
                page_url=f"https://{HOST}/",
                url="https://adv.example/",
                target_host="adv.example",
                target_root="adv.example",
                anchor="оффер",
                anchor_key="оффер",
                nofollow=False,
                sponsored=True,
                ugc=False,
                in_body=False,
            )
        ],
        robots_status=RobotsStatus.RULES,
        crawl_delay=10.0,
        source="links",
        slowed_down=True,
        degradation={"browser": "не установлен"},
        health={"requests": 4, "blocked": 1, "blocked_share": 0.25},
    )

    crawl_probe._print_report(report)
    crawl_probe._print_summary([], as_browser=True)

    out = capsys.readouterr().out
    assert ", пауза 10.0 с" in out
    assert "список страниц: links" in out
    assert "(в теле статьи 0, рядом 1)" in out
    assert "темп снижался:  да" in out
    assert "сайт просит паузу 10 с" in out
    assert "НЕ СРАБОТАЛ уровень browser: не установлен" in out
    assert "Ни одного запроса не сделано" in out
