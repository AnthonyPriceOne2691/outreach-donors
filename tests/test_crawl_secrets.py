"""Ключ сервиса рендеринга и пароль прокси — нигде: ни в записи обхода, ни в журнале.

Находки ревью 12.10.2026. Шаблон с опечаткой (`https:/хост?api_key={key}…`) проходил
проверку, а защита исходящих отказывала ему текстом с адресом целиком — вместе с ключом;
это `ValueError`, и он ронял обход, а текст уходил в причину сбоя, видную на экране обходов.
Пароль прокси с неэкранированным «#» резал адрес: кусок пароля становился хостом в имени
прокси и в тексте отказа. Теперь битые настройки — отказ на старте обходчика словами,
без секрета, а то, что всё же дошло до вызова, наружу идёт только видом исключения.
"""

from __future__ import annotations

import json
import logging

import pytest
from backend.config import crawl as crawl_cfg
from backend.config import storage
from backend.config.startup_checks import ConfigError, check_crawl
from backend.features.core.models.crawl import CrawlRunModel
from backend.workers import crawl_jobs, crawler
from tests.conftest import TEST_DSN
from tests.test_crawl_jobs import _queued, _site
from tests.test_crawl_render import SHELL
from tests.test_send_race import committed_sessions

KEY = "sk-live-Secret-Render-Key"  # pragma: allowlist secret
PASSWORD = "Pa55-word-with"  # pragma: allowlist secret
BROKEN_PROXY = f"http://user:{PASSWORD}#tail@gate.example.test:7777"  # pragma: allowlist secret
BROKEN_TEMPLATE = "https:/render.example.test?api_key={key}&url={url}"
GOOD_PROXY = "http://u:good-pass@ok.example.test:1"  # pragma: allowlist secret
ENCODED_PROXY = "http://user:p%23ss@gate.example.test:7777"  # pragma: allowlist secret


@pytest.fixture
def paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Платные уровни заданы целиком: потолок, цены, ключ сервиса."""
    monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", 50.0)
    monkeypatch.setattr(crawl_cfg, "PROXY_USD_PER_GB", 5.0)
    monkeypatch.setattr(crawl_cfg, "RENDER_USD_PER_1K", 1.0)
    monkeypatch.setattr(crawl_cfg, "RENDER_API_KEY", KEY)
    monkeypatch.setattr(crawl_cfg, "PROXY_URLS", ())


class TestStartup:
    @pytest.mark.usefixtures("paid")
    def test_broken_proxy_address_stops_the_crawler_without_the_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl_cfg, "RENDER_API_URL", "https://render.example.test/?u={url}")
        monkeypatch.setattr(crawl_cfg, "PROXY_URLS", (GOOD_PROXY, BROKEN_PROXY))

        with pytest.raises(ConfigError) as refused:
            check_crawl()

        said = str(refused.value)
        assert "CRAWL_PROXY_URLS: адрес №2" in said
        assert "закодируйте" in said
        assert "адрес №1" not in said
        for secret in (PASSWORD, "tail", "gate.example.test", "good-pass"):
            assert secret not in said

    @pytest.mark.usefixtures("paid")
    def test_template_without_a_host_stops_the_crawler_without_the_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl_cfg, "RENDER_API_URL", BROKEN_TEMPLATE)

        with pytest.raises(ConfigError) as refused:
            check_crawl()

        assert "CRAWL_RENDER_API_URL: в адресе API сервиса рендеринга нет хоста" in str(
            refused.value
        )
        assert KEY not in str(refused.value)

    def test_empty_and_good_settings_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(crawl_cfg, "PROXY_URLS", ())
        monkeypatch.setattr(crawl_cfg, "RENDER_API_KEY", "")
        check_crawl()
        monkeypatch.setattr(crawl_cfg, "PROXY_URLS", (ENCODED_PROXY,))
        monkeypatch.setattr(crawl_cfg, "RENDER_API_KEY", KEY)
        monkeypatch.setattr(
            crawl_cfg, "RENDER_API_URL", "https://render.example.test/?k={key}&u={url}"
        )
        check_crawl()

    @pytest.mark.usefixtures("paid")
    def test_the_crawler_service_refuses_to_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(crawl_cfg, "PROXY_URLS", (BROKEN_PROXY,))
        monkeypatch.setattr(crawler, "check_storage", lambda: None)
        monkeypatch.setattr(crawler, "setup_logging", lambda: None)

        def no_worker(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("обходчик не должен подняться с битым адресом прокси")

        monkeypatch.setattr(crawler, "CrawlWorker", no_worker)

        with pytest.raises(ConfigError):
            crawler.main()


@pytest.mark.usefixtures("paid")
async def test_a_broken_template_leaves_the_key_nowhere(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Обход до правки шаблона (задача, поставленная раньше выкатки проверки): пустая
    страница доходит до сервиса, а ключа нет ни в записи обхода, ни в журнале."""
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(crawl_cfg, "DELAY_SEC", 0.0)
    monkeypatch.setattr(crawl_cfg, "BROWSER_ENABLED", False)
    monkeypatch.setattr(crawl_cfg, "RENDER_API_URL", BROKEN_TEMPLATE)
    monkeypatch.setattr(crawl_jobs, "runs_queue", _NoQueue)
    crawl_jobs._STOP.clear()
    site = _site(monkeypatch)
    site.pages["/p1"] = SHELL
    async with committed_sessions() as factory:
        run_id = await _queued(factory)

        with caplog.at_level(logging.DEBUG):
            await crawl_jobs._crawl(run_id)

        async with factory() as session:
            run = await session.get(CrawlRunModel, run_id)
    assert run is not None
    assert run.degradation is not None
    assert "нет хоста" in run.degradation["render"]
    record = json.dumps([run.stats, run.degradation], ensure_ascii=False)
    assert KEY not in record
    assert KEY not in caplog.text


class _NoQueue:
    def enqueue(self, *_args: object, **_kwargs: object) -> object:
        return type("Job", (), {"id": "judge"})()
