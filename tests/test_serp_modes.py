"""Режимы покупки выдачи: цена против ожидания (`serp/modes.py`).

Проверка на проде 14.10.2026: шесть минут из одиннадцати прогон сбора ждал выдачу
в отложенной очереди. Слово владельца: «в первую очередь скорость, по умолчанию
быстрый» — умолчание синхронное, медленный и дешёвый выбирают на запуске.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.runs.planning import gather_candidates
from backend.features.serp import factory
from backend.features.serp.dataforseo import DataForSeoProvider
from backend.features.serp.modes import (
    EXPECTED_WAIT_S,
    SerpMode,
    UnknownModeError,
    mode_of,
    page_price_usd,
)
from backend.features.serp.protocol import SerpResult, SerpWatch


class TestModeOf:
    def test_default_is_fast(self) -> None:
        assert mode_of(None) is SerpMode.LIVE
        assert mode_of("  ") is SerpMode.LIVE

    def test_written_mode_is_read_without_case(self) -> None:
        assert mode_of("STANDARD") is SerpMode.STANDARD
        assert mode_of(" priority ") is SerpMode.PRIORITY

    def test_unknown_mode_is_refused_in_words(self) -> None:
        """Молча взять другой режим нельзя: цена у них разная втрое."""
        with pytest.raises(UnknownModeError, match="известные: standard, priority, live"):
            mode_of("turbo")

    def test_default_follows_the_setting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.features.serp.modes.cfg.MODE", "standard")
        assert mode_of(None) is SerpMode.STANDARD


class TestPrices:
    def test_page_prices_rise_with_speed(self) -> None:
        cheap, middle, fast = (page_price_usd(mode) for mode in SerpMode)
        assert (cheap, middle, fast) == pytest.approx((0.0006, 0.0012, 0.002))

    def test_every_mode_names_its_wait(self) -> None:
        assert set(EXPECTED_WAIT_S) == set(SerpMode)
        assert EXPECTED_WAIT_S[SerpMode.LIVE] < EXPECTED_WAIT_S[SerpMode.STANDARD]


class TestFactory:
    def test_mode_reaches_the_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(factory.cfg, "PROVIDER", "dataforseo")
        monkeypatch.setattr(factory.cfg, "LOGIN", "l")
        monkeypatch.setattr(factory.cfg, "PASSWORD", "p")

        chosen = factory.build_provider(object(), mode=SerpMode.STANDARD)  # type: ignore[arg-type]
        default = factory.build_provider(object())  # type: ignore[arg-type]

        assert isinstance(chosen, DataForSeoProvider)
        assert chosen.mode is SerpMode.STANDARD
        assert default.mode is SerpMode.LIVE  # type: ignore[attr-defined]


class _Serp:
    """Источник с новой подписью: запоминает, что ему передали."""

    name = "fake"
    spent = 0.0

    def __init__(self) -> None:
        self.watch: SerpWatch | None = None

    async def search(
        self, keywords: Any, country: str, **kwargs: Any
    ) -> dict[str, list[SerpResult]]:
        self.watch = kwargs.get("watch")
        return {k: [SerpResult(position=1, url=f"https://{k}.com/")] for k in keywords}


class TestGatherPassesTheWatch:
    @pytest.mark.asyncio
    async def test_watch_reaches_the_source(self) -> None:
        serp, watch = _Serp(), SerpWatch()
        await gather_candidates(serp, ["a"], "us", watch=watch)  # type: ignore[arg-type]
        assert serp.watch is watch

    @pytest.mark.asyncio
    async def test_old_sources_are_called_as_before(self) -> None:
        """Заглушки выдачи знают прежнюю подпись: без `watch` его и не передают."""

        class Old:
            name, spent = "old", 0.0

            async def search(self, keywords: Any, country: str, *, depth_pages: int = 1) -> Any:
                return {k: [] for k in keywords}

        found = await gather_candidates(Old(), ["a"], "us")  # type: ignore[arg-type]
        assert found.empty_keywords == ["a"]
