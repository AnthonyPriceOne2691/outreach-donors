"""Адаптер выдачи: проводка отложенного режима.

Отложенный режим — это две фазы и куча мест, где ключ может потеряться
молча: задачу отклонили, результат не дождались, ответ пришёл не в том
порядке. Проверяется именно это, а не «нашлись ли ссылки».
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from backend.features.serp.dataforseo import (
    DataForSeoProvider,
    SerpError,
    UnknownCountryError,
    language_code,
    location_code,
)

pytestmark = pytest.mark.asyncio


def _task(task_id: str, keyword: str, *, status: int = 20100) -> dict[str, Any]:
    return {
        "id": task_id,
        "status_code": status,
        "status_message": "Task Created." if status == 20100 else "Отказ",
        "data": {"tag": keyword},
    }


def _result(items: list[dict[str, Any]], *, status: int = 20000) -> dict[str, Any]:
    return {
        "status_code": 20000,
        "tasks": [{"status_code": status, "status_message": "Ok.", "result": [{"items": items}]}],
    }


def _organic(position: int, url: str) -> dict[str, Any]:
    return {"type": "organic", "rank_absolute": position, "url": url}


class Provider:
    """Провайдер-заглушка: отвечает по сценарию и считает обращения."""

    def __init__(
        self,
        *,
        results: dict[str, list[dict[str, Any]]] | None = None,
        refuse: set[str] | None = None,
        queue_rounds: int = 0,
    ) -> None:
        self.results = results or {}
        self.refuse = refuse or set()
        self.queue_rounds = queue_rounds
        self.posts = 0
        self.gets: list[str] = []
        self._ids: dict[str, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("task_post"):
            self.posts += 1
            keywords = [row["keyword"] for row in json.loads(request.content)]
            tasks = []
            for number, keyword in enumerate(keywords):
                if keyword in self.refuse:
                    tasks.append(_task("", keyword, status=40501))
                    continue
                task_id = f"task-{number}-{keyword}"
                self._ids[task_id] = keyword
                tasks.append(_task(task_id, keyword))
            return httpx.Response(200, json={"status_code": 20000, "cost": 0.05, "tasks": tasks})

        task_id = request.url.path.rsplit("/", 1)[-1]
        self.gets.append(task_id)
        keyword = self._ids.get(task_id, "")

        if self.queue_rounds and self.gets.count(task_id) <= self.queue_rounds:
            return httpx.Response(
                200,
                json={"status_code": 20000, "tasks": [{"status_code": 40602, "result": None}]},
            )
        return httpx.Response(200, json=_result(self.results.get(keyword, [])))


def _provider(handler: Any) -> DataForSeoProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://dfs")
    return DataForSeoProvider(client, sandbox=True, login="l", password="p")


class TestWiring:
    async def test_each_keyword_gets_its_own_results(self) -> None:
        """Сопоставление идёт по метке, а не по порядку в ответе: порядок
        провайдер не обещает."""
        site = Provider(
            results={
                "первый": [_organic(1, "https://a.com/")],
                "второй": [_organic(2, "https://b.com/")],
            }
        )
        out = await _provider(site).search(["первый", "второй"], "us")

        assert [r.url for r in out["первый"]] == ["https://a.com/"]
        assert [r.url for r in out["второй"]] == ["https://b.com/"]

    async def test_keyword_without_results_is_present_and_empty(self) -> None:
        """Иначе «по ключу ничего не нашлось» не отличить от «ключ потерялся»."""
        out = await _provider(Provider(results={"пусто": []})).search(["пусто"], "us")
        assert out == {"пусто": []}

    async def test_duplicates_are_asked_once(self) -> None:
        site = Provider(results={"один": [_organic(1, "https://a.com/")]})
        out = await _provider(site).search(["один", "один", " один "], "us")

        assert list(out) == ["один"]
        assert len(site.gets) == 1

    async def test_only_organic_taken(self) -> None:
        """В выдаче есть карты, реклама и «люди также спрашивают» — доноров
        там нет."""
        items = [
            {"type": "paid", "rank_absolute": 1, "url": "https://ad.com/"},
            _organic(2, "https://real.com/"),
            {"type": "people_also_ask", "rank_absolute": 3},
        ]
        out = await _provider(Provider(results={"к": items})).search(["к"], "us")
        assert [r.url for r in out["к"]] == ["https://real.com/"]

    async def test_depth_limits_results(self) -> None:
        items = [_organic(i, f"https://site{i}.com/") for i in range(1, 25)]
        out = await _provider(Provider(results={"к": items})).search(["к"], "us", depth_pages=1)
        assert len(out["к"]) == 10

    async def test_large_batch_is_split(self) -> None:
        """Больше сотни задач за раз провайдер не принимает."""
        site = Provider()
        await _provider(site).search([f"ключ-{i}" for i in range(150)], "us")
        assert site.posts == 2

    async def test_cost_is_counted(self) -> None:
        provider = _provider(Provider(results={"к": []}))
        await provider.search(["к"], "us")
        assert provider.spent > 0


class TestFailures:
    async def test_refused_task_does_not_hide_the_others(self) -> None:
        site = Provider(results={"живой": [_organic(1, "https://a.com/")]}, refuse={"битый"})
        out = await _provider(site).search(["битый", "живой"], "us")

        assert out["битый"] == []  # отказ виден по логу, а ключ остаётся в ответе
        assert [r.url for r in out["живой"]] == ["https://a.com/"]

    async def test_all_tasks_refused_is_loud(self) -> None:
        site = Provider(refuse={"а", "б"})
        with pytest.raises(SerpError, match="отклонил все задачи"):
            await _provider(site).search(["а", "б"], "us")

    async def test_provider_error_is_loud(self) -> None:
        def broken(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="упало")

        with pytest.raises(SerpError, match="500"):
            await _provider(broken).search(["к"], "us")

    async def test_not_json_is_loud(self) -> None:
        def html(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>заглушка</html>")

        with pytest.raises(SerpError, match="JSON"):
            await _provider(html).search(["к"], "us")

    async def test_unknown_country_refuses_before_spending(self) -> None:
        """Промахнуться страной хуже, чем не начать: соберём доноров
        не того рынка и заплатим за них."""
        site = Provider()
        with pytest.raises(UnknownCountryError, match="не в карте"):
            await _provider(site).search(["к"], "зз")
        assert site.posts == 0

    async def test_zero_depth_refused(self) -> None:
        with pytest.raises(ValueError, match="минимум одна"):
            await _provider(Provider()).search(["к"], "us", depth_pages=0)


class TestWaiting:
    async def test_task_in_queue_is_polled_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.features.serp.dataforseo.cfg.POLL_INTERVAL_S", 0)
        site = Provider(results={"к": [_organic(1, "https://a.com/")]}, queue_rounds=2)

        out = await _provider(site).search(["к"], "us")

        assert [r.url for r in out["к"]] == ["https://a.com/"]
        assert len(site.gets) == 3  # два раза «в очереди», третий — готово

    async def test_never_ready_leaves_keyword_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Не дождались — ключ остаётся пустым, но это видно в логе,
        а не выглядит как «ничего не нашлось»."""
        monkeypatch.setattr("backend.features.serp.dataforseo.cfg.POLL_INTERVAL_S", 0)
        monkeypatch.setattr("backend.features.serp.dataforseo.cfg.POLL_ATTEMPTS", 2)
        site = Provider(results={"к": [_organic(1, "https://a.com/")]}, queue_rounds=99)

        out = await _provider(site).search(["к"], "us")

        assert out["к"] == []
        assert len(site.gets) == 2


class TestCountries:
    async def test_known_countries(self) -> None:
        assert location_code("us") == 2840
        assert location_code(" GB ") == 2826

    async def test_language_follows_country(self) -> None:
        assert language_code("de") == "de"
        assert language_code("br") == "pt"

    async def test_unknown_language_falls_back_to_english(self) -> None:
        """Язык интерфейса влияет на выдачу слабее региона — английский
        как умолчание безопаснее отказа."""
        assert language_code("us") == "en"
        assert language_code("зз") == "en"
